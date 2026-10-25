"""Shared diagnostic serialization and read-only CLI boundaries."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from schema_protocol import package_metadata, schema, validate

TOOL_VERSION = package_metadata()['version']
DIGEST_ALGORITHM = package_metadata()['digestAlgorithm']
EDGE_KINDS = frozenset(schema('artifact-identity')['$defs']['kind']['enum'])


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest_value(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def digest_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def is_digest(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def result(status, **extra):
    return validate('diagnostic-result', {'schemaVersion': 1, 'toolVersion': TOOL_VERSION, 'status': status, **extra})


def project_path(raw, root, source=None):
    if not isinstance(raw, str) or not raw.strip() or Path(raw).is_absolute():
        raise ValueError('RELATIVE_PATH_REQUIRED')
    root = Path(root).resolve()
    candidate = ((Path(source).resolve().parent if source else root) / raw).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError('REFERENCE_OUTSIDE_ROOT')
    return candidate


def _object(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError('DUPLICATE_JSON_KEY: ' + key)
        out[key] = value
    return out


def load_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=_object,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError('NON_FINITE_JSON')))


def load_structured(path):
    from document_core import simple_yaml
    path = Path(path)
    if path.suffix.lower() == '.json':
        return load_json(path)
    if path.suffix.lower() in {'.yaml', '.yml'}:
        return simple_yaml.loads(path.read_text(encoding='utf-8'))
    raise ValueError('UNSUPPORTED_REFERENCE_FORMAT')


def digest_manifest(records):
    return digest_value(sorted(records, key=canonical))


def output_path(output, protected=()):
    """Check report boundaries before an operation creates any output."""
    target = Path(output).resolve()
    package = Path(__file__).resolve().parents[1]
    for path in (package, *map(Path, protected)):
        path = path.resolve()
        if target == path or (path.is_dir() and target.is_relative_to(path)):
            raise ValueError('OUTPUT_OVERLAPS_INPUT')
        if target.exists() and path.is_file() and target.samefile(path):
            raise ValueError('OUTPUT_OVERLAPS_INPUT')
    if target.exists() and not target.is_file():
        raise ValueError('OUTPUT_FILE_REQUIRED')
    return target


def write_json(value, output, protected=()):
    """Output is a report; never allow it to overwrite inputs or protected roots."""
    target = output_path(output, protected)
    serialized = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + '\n'
    target.parent.mkdir(parents=True, exist_ok=True)
    # Replace the output directory entry, never mutate an existing inode that
    # could also be linked from inside a protected source directory.
    descriptor, temporary = tempfile.mkstemp(prefix='.diagnostic-report-', dir=target.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            stream.write(serialized)
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)


def finish_cli(operation, output, protected):
    try:
        output_path(output, protected)
    except (ValueError, OSError) as exc:
        print(canonical(result('FAILED', code='OUTPUT_REJECTED', diagnostic=str(exc))))
        return 2
    try:
        value = validate('diagnostic-result', operation())
    except (ValueError, TypeError, KeyError, OSError) as exc:
        value = result('FAILED', code='INVALID_INPUT', diagnostic=str(exc))
    try:
        write_json(value, output, protected)
    except (ValueError, OSError) as exc:
        print(canonical(result('FAILED', code='OUTPUT_REJECTED', diagnostic=str(exc))))
        return 2
    return 0 if value['status'] in {'PASSED', 'CURRENT', 'CONSISTENT'} else 2
