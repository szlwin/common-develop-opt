"""Retained public facades dispatch once and preserve their original CLI boundary."""
import ast
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import compat_adapter


class CompatDispatchTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='compat dispatch ')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        old_path = sys.path[:]
        old_modules = set(sys.modules)
        old_cache = compat_adapter._LOADED.copy()

        def restore():
            sys.path[:] = old_path
            for name in set(sys.modules) - old_modules:
                if name.startswith('_common_develop_compat_'):
                    sys.modules.pop(name, None)
            compat_adapter._LOADED.clear()
            compat_adapter._LOADED.update(old_cache)
        self.addCleanup(restore)

    def test_cli_after_import_does_not_repeat_module_initialization(self):
        path = self.root / 'once.py'
        marker = self.root / 'loads.txt'
        path.write_text('from pathlib import Path\n'
            'marker=Path(' + repr(str(marker)) + ')\n'
            'marker.write_text((marker.read_text() if marker.exists() else "") + "load\\n")\n'
            'def main(): return 7\n')
        before = compat_adapter.load(path)
        with self.assertRaises(SystemExit) as caught:
            compat_adapter.execute(path)
        self.assertEqual(7, caught.exception.code)
        self.assertEqual('load\n', marker.read_text())
        self.assertIs(before, compat_adapter.load(path))

    def test_explicit_module_api_restores_sys_path(self):
        path = self.root / 'explicit.py'
        path.write_text('def answer(value): return value + 1\n')
        before = list(sys.path)
        module = compat_adapter.load_module(path)
        self.assertEqual(3, compat_adapter.invoke(path, 'answer', 2))
        self.assertIs(module, compat_adapter.load_module(path))
        self.assertEqual(before, sys.path)

    def test_module_replacing_sys_modules_is_not_reloaded(self):
        path = self.root / 'redirect.py'
        path.write_text('import sys,types\n'
            'TOKEN=object()\n'
            'def main(): return 4\n'
            'sys.modules[__name__]=types.ModuleType("shared_api")\n')
        first = compat_adapter.load(path)
        self.assertIs(first['TOKEN'], compat_adapter.load(path)['TOKEN'])
        with self.assertRaises(SystemExit) as caught:
            compat_adapter.execute(path)
        self.assertEqual(4, caught.exception.code)

    def test_failed_load_can_be_retried_after_repair(self):
        path = self.root / 'repair.py'
        path.write_text('raise ValueError("broken")\n')
        with self.assertRaisesRegex(ValueError, 'broken'):
            compat_adapter.load(path)
        self.assertNotIn(path.resolve(), compat_adapter._LOADED)
        path.write_text('def main(): return 0\n')
        self.assertEqual(0, compat_adapter.load(path)['main']())

    def test_packages_with_same_script_name_do_not_share_cache(self):
        targets = []
        for i in (1, 2):
            path = self.root / str(i) / 'entry.py'
            path.parent.mkdir()
            path.write_text('import sys\nVALUE=' + str(i) + '\n'
                            'def main(): return sys.modules[__name__].VALUE\n')
            targets.append(path)
        for path, expected in ((targets[0], 1), (targets[1], 2), (targets[0], 1)):
            self.assertEqual(expected, compat_adapter.load(path)['main']())

    def test_all_public_dispatchers_match_the_original_main_guard(self):
        facades = [p for p in (ROOT / 'scripts').glob('*.py')
                   if 'from compat_adapter import' in p.read_text()
                   and any((ROOT / 'scripts' / home / p.name).exists()
                           for home in ('runtime', 'checks'))]
        explicit_facades = [p for p in (ROOT / 'scripts').glob('*.py')
                            if p not in facades and '__main__' in p.read_text()
                            and ('document_core' in p.read_text() or 'from document_core' in p.read_text())]
        public_scripts = {p.name for p in facades + explicit_facades}
        self.assertEqual(len(public_scripts), len(facades) + len(explicit_facades))
        for path in facades:
            with self.subTest(script=path.name):
                body = ast.parse(path.read_text())
                calls = [n for n in ast.walk(body) if isinstance(n, ast.Call)
                         and isinstance(n.func, ast.Name) and n.func.id == 'execute']
                self.assertEqual(1, len(calls))
                keywords = {k.arg: ast.literal_eval(k.value) for k in calls[0].keywords}
                entry = keywords.get('entry', 'main')
                target_path = next(ROOT / 'scripts' / home / path.name
                                   for home in ('runtime', 'checks')
                                   if (ROOT / 'scripts' / home / path.name).is_file())
                target = ast.parse(target_path.read_text())
                guards = [n for n in target.body if isinstance(n, ast.If) and '__name__' in ast.unparse(n.test)]
                self.assertEqual(1, len(guards))
                self.assertEqual('raise SystemExit(' + entry + '())', ast.unparse(guards[0].body[0]))

    def test_review_error_wrappers_preserve_stderr_and_exit_code(self):
        for script, args in (
            ('review_contract.py', ['show', '--profile', 'missing-profile', '-g', 'ProjectManagerAgent']),
            ('review_session.py', ['validate', '--file', str(self.root / 'missing.json'), '-g', 'ProjectManagerAgent']),
        ):
            with self.subTest(script=script):
                # Runtime implementations are package modules now; the public
                # contract is the facade itself: fail closed with exit 1, a
                # clean diagnostic on stderr and never a raw traceback.
                result = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts' / script), *args],
                    cwd=self.root, capture_output=True, text=True)
                self.assertEqual(1, result.returncode, result.stdout + result.stderr)
                self.assertNotIn('Traceback', result.stderr, result.stderr)
                self.assertTrue(result.stderr.strip(), result.stderr)

if __name__ == '__main__':
    unittest.main()
