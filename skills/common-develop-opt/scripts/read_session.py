"""Invocation-scoped stable reads with measured, invalidating reuse."""
import json
from pathlib import Path
from diagnostic_io import _object

class ReadSession:
    def __init__(self):
        self.cache = {}
        self.files_read = 0
        self.bytes_read = 0
        self.cache_hits = 0
        self.observed = {}

    def begin(self):
        self.observed = {}

    @staticmethod
    def fingerprint(path):
        stat = path.stat()
        return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)

    def read_bytes(self, path):
        path = Path(path).resolve()
        before = self.fingerprint(path)
        if path in self.observed and self.observed[path] != before:
            raise ValueError('INPUT_CHANGED_DURING_INSPECTION: ' + str(path))
        self.observed[path] = before
        stored = self.cache.get(path)
        if stored and stored[0] == before:
            self.cache_hits += 1
            return stored[1]
        content = path.read_bytes()
        self.files_read += 1
        self.bytes_read += len(content)
        if self.fingerprint(path) != before:
            raise ValueError('INPUT_CHANGED_DURING_READ: ' + str(path))
        self.cache[path] = (before, content)
        return content

    def load(self, path):
        text = self.read_bytes(path).decode('utf-8')
        if Path(path).suffix.lower() in {'.yaml', '.yml'}:
            from document_core import simple_yaml
            return simple_yaml.loads(text)
        return json.loads(text, object_pairs_hook=_object,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError('NON_FINITE_JSON')))

    def verify(self):
        for path, signature in self.observed.items():
            if self.fingerprint(path) != signature:
                raise ValueError('INPUT_CHANGED_DURING_INSPECTION: ' + str(path))

    def metrics(self):
        return {'filesRead': self.files_read, 'bytesRead': self.bytes_read, 'cacheHits': self.cache_hits}
