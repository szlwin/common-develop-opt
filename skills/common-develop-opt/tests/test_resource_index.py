"""Behavioral cache invalidation, portable index, and full-check regressions."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import resource_paths as paths
import resource_catalog as catalog
from checks.generate_resource_index import write_index


class ResourceIndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / 'package'
        (self.root / 'assets/runtime').mkdir(parents=True)
        (self.root / 'references/core').mkdir(parents=True)
        self.target = self.root / 'assets/runtime/a.json'
        self.target.write_text('{}')
        (self.root / 'references/core/a.md').write_text('# A')
        self.document = {'schemaVersion': 1, 'entries': [
            {'logicalPath': 'assets/a.json', 'target': 'assets/runtime/a.json'}]}
        self.save_catalog()
        (self.root / 'references/reference-catalog.json').write_text(json.dumps({
            'entries': [{'source': 'references/a.md', 'target': 'references/core/a.md'}],
            'categories': {'core': ['a.md']}}))
        write_index(self.root)
        self.addCleanup(paths.clear_cache)

    def save_catalog(self):
        (self.root / catalog.CATALOGS['assets']).write_text(json.dumps(self.document))

    def lookup(self):
        return paths.asset_path('assets/a.json', root=self.root)

    def edit_index(self, mutate, *, resign=False):
        file = self.root / catalog.INDEX_PATH
        value = json.loads(file.read_text())
        mutate(value)
        if resign:
            value['digest'] = catalog.digest({k:v for k,v in value.items() if k != 'digest'})
        file.write_text(json.dumps(value))

    def test_generated_index_matches_catalog_and_is_read_only(self):
        index = paths.get_index(self.root)
        self.assertEqual('GENERATED', index.origin)
        self.assertEqual(self.target, self.lookup())
        with self.assertRaises(TypeError): index.logical_paths['assets/a.json'] = 'x'
        with self.assertRaises(TypeError): index.metadata['assets/runtime/a.json']['kind'] = 'x'
        self.assertEqual(catalog.build_index(self.root)['digest'], catalog.validate_index(self.root))

    def test_hot_lookup_does_not_read_or_traverse_catalog(self):
        self.lookup()
        with patch.object(paths, 'read_sources', side_effect=AssertionError('reread')), patch.object(paths, 'build_section', side_effect=AssertionError('rebuild')):
            for _ in range(1000): self.assertEqual(self.target, self.lookup())

    def test_mapping_edit_refreshes_without_clear_even_same_size_and_mtime(self):
        self.lookup()
        file = self.root / catalog.CATALOGS['assets']
        stat = file.stat()
        next_target = self.target.with_name('b.json'); next_target.write_text('{}')
        self.document['entries'][0]['target'] = 'assets/runtime/b.json'
        self.save_catalog()
        os.utime(file, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.assertEqual(stat.st_size, file.stat().st_size)
        self.assertEqual(next_target, self.lookup())
        self.assertEqual('REBUILT_STALE', paths.get_index(self.root).origin)
        with self.assertRaisesRegex(ValueError, 'STALE'): catalog.validate_index(self.root)

    def test_missing_index_rebuilds_memory_without_disk_write(self):
        (self.root / catalog.INDEX_PATH).unlink()
        self.assertEqual(self.target, self.lookup())
        self.assertEqual('REBUILT_MISSING', paths.get_index(self.root).origin)
        self.assertFalse((self.root / catalog.INDEX_PATH).exists())

    def test_index_replace_is_detected_after_warmup(self):
        self.lookup()
        self.edit_index(lambda v: v.update(digest='bad'))
        with self.assertRaisesRegex(ValueError, 'CORRUPT'): self.lookup()

    def test_null_invalid_json_and_version_rejected(self):
        file = self.root / catalog.INDEX_PATH
        original = file.read_text()
        for text in ('null', '{', '{"schemaVersion":999}'):
            with self.subTest(text=text):
                file.write_text(text)
                with self.assertRaisesRegex(ValueError, 'CORRUPT'): self.lookup()
                file.write_text(original)

    def test_forged_mapping_with_consistent_self_digest_rejected(self):
        self.edit_index(lambda v: v['catalogs']['assets']['logicalPaths'].update({'assets/a.json':'assets/runtime/b.json'}), resign=True)
        with self.assertRaisesRegex(ValueError, 'mapping mismatch'): self.lookup()

    def test_missing_selected_target_is_rejected_after_warmup(self):
        self.lookup(); self.target.unlink()
        with self.assertRaisesRegex(ValueError, 'TARGET_MISSING'): self.lookup()

    def test_selected_target_symlink_is_rejected_after_warmup(self):
        self.lookup(); self.target.unlink()
        self.target.symlink_to(self.root / 'references/core/a.md')
        with self.assertRaisesRegex(ValueError, 'ALIAS'): self.lookup()

    def test_target_content_drift_is_detected_by_full_gate(self):
        self.lookup(); self.target.write_text('{"changed":true}')
        with self.assertRaisesRegex(ValueError, 'STALE'): catalog.validate_index(self.root)
        write_index(self.root)
        catalog.validate_index(self.root)

    def test_added_or_removed_entity_is_detected_by_full_gate(self):
        extra = self.root / 'assets/runtime/extra.json'; extra.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'STALE'): catalog.validate_index(self.root)
        write_index(self.root)
        extra.unlink()
        with self.assertRaisesRegex(ValueError, 'STALE'): catalog.validate_index(self.root)

    def test_portable_deterministic_index_survives_copy_mtime_change(self):
        copy = self.root.parent / 'installed'
        shutil.copytree(self.root, copy, copy_function=shutil.copyfile)
        before = (copy / catalog.INDEX_PATH).read_bytes()
        self.assertEqual('GENERATED', paths.get_index(copy).origin)
        write_index(copy)
        self.assertEqual(before, (copy / catalog.INDEX_PATH).read_bytes())

    def test_regeneration_is_idempotent(self):
        path = self.root / catalog.INDEX_PATH
        stat = path.stat(); data = path.read_bytes()
        write_index(self.root)
        self.assertEqual(data, path.read_bytes())
        self.assertEqual(stat.st_mtime_ns, path.stat().st_mtime_ns)

    def test_roots_are_isolated(self):
        other = self.root.parent / 'other'
        shutil.copytree(self.root, other)
        self.assertNotEqual(paths.get_index(other).root, paths.get_index(self.root).root)
        self.assertEqual(other / 'assets/runtime/a.json', paths.asset_path('assets/a.json', root=other))

    def test_concurrent_first_calls_share_one_index(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: paths.get_index(self.root), range(16)))
        self.assertTrue(all(item is results[0] for item in results))

    def test_package_and_manifest_changes_invalidate_cache(self):
        initial = paths.get_index(self.root)
        (self.root / 'assets/package.json').write_text('{"version":"new"}')
        changed = paths.get_index(self.root)
        self.assertIsNot(initial, changed)
        self.assertEqual('REBUILT_STALE', changed.origin)
        (self.root / 'FILE_MANIFEST.sha256').write_text('changed')
        self.assertIsNot(changed, paths.get_index(self.root))

    def test_wrappers_do_not_hide_invalid_input(self):
        for value in ('/etc/passwd', '../a', 'C:/a'):
            for function in (paths.asset_path, paths.reference_path):
                with self.assertRaises(ValueError): function(value, root=self.root)

    def test_generator_cli_from_arbitrary_cwd(self):
        output = self.root.parent / 'index.json'
        env = dict(os.environ)
        env.pop('PYTHONPATH', None)
        result = subprocess.run([sys.executable, '-B', str(paths.SKILL_ROOT / 'scripts/checks/generate_resource_index.py'),
                                 '--root', str(self.root), '--output', str(output)],
                                cwd=self.tmp.name, env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(catalog.build_index(self.root), json.loads(output.read_text()))

    def test_relocated_generator_defaults_to_its_own_package(self):
        installed = self.root / 'scripts'
        (installed / 'checks').mkdir(parents=True)
        for relative in ('checks/generate_resource_index.py', 'resource_catalog.py', 'resource_governance.py'):
            shutil.copyfile(paths.SKILL_ROOT / 'scripts' / relative, installed / relative)
        (self.root / catalog.INDEX_PATH).unlink()
        env = dict(os.environ)
        env.pop('PYTHONPATH', None)
        run = subprocess.run(
            [sys.executable, '-B', str(installed / 'checks/generate_resource_index.py')],
            cwd=self.tmp.name, env=env, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(0, run.returncode, run.stderr)
        self.assertEqual(str(self.root / catalog.INDEX_PATH), json.loads(run.stdout)['output'])
        self.assertEqual(catalog.build_index(self.root), json.loads((self.root / catalog.INDEX_PATH).read_text()))


if __name__ == '__main__': unittest.main()
