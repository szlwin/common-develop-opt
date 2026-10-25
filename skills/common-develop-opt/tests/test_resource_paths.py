"""Catalog aliases must work without physical aliases and fail closed on drift."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import resource_paths as paths
import schema_protocol


class ResourcePathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        (self.root / 'assets/runtime').mkdir(parents=True)
        self.target = self.root / 'assets/runtime/example.schema.json'
        self.target.write_text('{"$schema":"https://json-schema.org/draft/2020-12/schema","$id":"urn:example","type":"object"}')
        self.catalog = {'entries': [{'logicalPath': 'assets/example.schema.json', 'target': 'assets/runtime/example.schema.json'}]}
        self.save()
        self.addCleanup(paths.catalog_map.cache_clear)

    def save(self):
        (self.root / 'assets/asset-catalog.json').write_text(json.dumps(self.catalog))
        paths.catalog_map.cache_clear()

    def test_logical_and_formal_paths_read_same_entity_without_alias(self):
        self.assertFalse((self.root / 'assets/example.schema.json').exists())
        for value in ('assets/example.schema.json', './assets/example.schema.json#properties', 'assets/runtime/example.schema.json'):
            self.assertEqual(self.target, paths.resource_path(value, root=self.root))

    def test_all_declared_resources_have_formal_targets(self):
        # Original resources plus the catalog schema and compatibility boundary.
        for kind, count in [('assets', 63), ('references', 83)]:
            mapping = paths.catalog_map(paths.SKILL_ROOT, kind)
            self.assertEqual(count, len(mapping))
            for old, target in mapping.items():
                self.assertEqual(paths.SKILL_ROOT / target, paths.resource_path(old))

    def test_traversal_absolute_and_windows_addresses_rejected(self):
        for value in ('../assets/example.schema.json', '/assets/example.schema.json', 'assets/../../outside', 'C:/assets/a', r'assets\..\a', ''):
            with self.subTest(value=value), self.assertRaises(ValueError):
                paths.resource_path(value, root=self.root)

    def test_unknown_name_does_not_guess_basename(self):
        missing = paths.resource_path('assets/wrong/example.schema.json', root=self.root)
        self.assertFalse(missing.exists())
        self.assertNotEqual(self.target, missing)

    def test_missing_target_does_not_fallback_to_old_entity(self):
        self.target.unlink()
        (self.root / 'assets/example.schema.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'TARGET_MISSING'):
            paths.resource_path('assets/example.schema.json', root=self.root)

    def test_duplicate_logical_path_rejected(self):
        self.catalog['entries'].append(copy.deepcopy(self.catalog['entries'][0])); self.save()
        with self.assertRaisesRegex(ValueError, 'DUPLICATE'):
            paths.catalog_map(self.root, 'assets')

    def test_cycle_rejected(self):
        other = self.root / 'assets/other.json'; other.write_text('{}')
        self.catalog['entries'] = [dict(logicalPath='assets/other.json', target='assets/runtime/example.schema.json'), dict(logicalPath='assets/runtime/example.schema.json', target='assets/other.json')]
        self.save()
        with self.assertRaisesRegex(ValueError, 'CHAIN_OR_CYCLE'):
            paths.catalog_map(self.root, 'assets')

    def test_external_target_and_undeclared_alias_rejected(self):
        self.catalog['entries'][0]['target'] = '../outside'; self.save()
        with self.assertRaises(ValueError): paths.catalog_map(self.root, 'assets')
        self.catalog['entries'][0]['target'] = 'assets/runtime/example.schema.json'; self.save()
        (self.root / 'assets/other.json').symlink_to(self.target)
        with self.assertRaisesRegex(ValueError, 'UNDECLARED_RESOURCE_ALIAS'):
            paths.resource_path('assets/other.json', root=self.root)

    def test_unknown_path_cannot_traverse_directory_alias(self):
        (self.root / 'assets/old').symlink_to(self.root / 'assets/runtime', target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'UNDECLARED_RESOURCE_ALIAS'):
            paths.resource_path('assets/old/example.schema.json', root=self.root)

    def test_formal_target_may_not_be_symlink(self):
        self.target.unlink(); self.target.symlink_to(self.root / 'missing')
        with self.assertRaisesRegex(ValueError, 'NONFORMAL_TARGET'):
            paths.catalog_map(self.root, 'assets')

    def test_duplicate_schema_names_and_ids_rejected(self):
        for name, expected in [('example.schema.json', 'SCHEMA_NAME_CONFLICT'), ('different.schema.json', 'SCHEMA_ID_CONFLICT')]:
            with self.subTest(name=name):
                other = self.root / 'assets' / name; other.write_bytes(self.target.read_bytes())
                schema_protocol.bundle.cache_clear()
                with patch.object(schema_protocol, 'formal_entities', return_value=iter([self.target, other])):
                    with self.assertRaisesRegex(schema_protocol.ProtocolError, expected): schema_protocol.bundle()
                other.unlink()
        schema_protocol.bundle.cache_clear()


if __name__ == '__main__': unittest.main()
