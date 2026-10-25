"""Cross-directory metadata, retirement, and regeneration safety checks."""
import copy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / 'scripts'))
from checks.generate_resource_index import write_index
from checks._check_skill import manifest
from resource_catalog import INDEX_PATH, build_index, validate_index
from resource_governance import validate_catalog_schema
from resource_paths import clear_cache, resource_path
from schema_protocol import resource_metadata, metadata_inventory, ProtocolError


class AssetGovernanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(clear_cache)
        self.root = Path(self.tmp.name).resolve()
        for folder in ('assets/runtime', 'assets/package', 'references'):
            (self.root / folder).mkdir(parents=True)
        shutil.copyfile(PACKAGE / 'assets/package/resource-catalog.schema.json',
                        self.root / 'assets/package/resource-catalog.schema.json')
        self.catalog = json.loads((PACKAGE / 'assets/asset-catalog.json').read_text())
        self.catalog['entries'] = [
            {'logicalPath': f'assets/{name}.schema.json', 'target': f'assets/runtime/{name}.schema.json',
             'category': 'runtime', 'kind': 'schema'} for name in ('old', 'new')]
        for name in ('old', 'new'):
            self.save(f'assets/runtime/{name}.schema.json', {
                '$schema': 'https://json-schema.org/draft/2020-12/schema', '$id': f'urn:test:{name}',
                'type': 'object', 'properties': {'schemaVersion': {'const': 2}}})
        self.save('references/reference-catalog.json', {'entries': [], 'categories': {}})
        self.save('references/history.json', {'entries': []})
        self.save_catalog()
        write_index(self.root)

    def save(self, relative, value):
        (self.root / relative).write_text(json.dumps(value), encoding='utf-8')

    def save_catalog(self):
        self.save('assets/asset-catalog.json', self.catalog)

    def remove_old(self):
        self.catalog['entries'].pop(0)
        (self.root / 'assets/runtime/old.schema.json').unlink()
        self.save_catalog()

    def declare_retirement(self):
        self.catalog['retirements'] = [{
            'logicalPath': 'assets/old.schema.json', 'target': 'assets/runtime/old.schema.json',
            'retiredAt': '2026-09-14T00:00:00Z', 'replacement': 'assets/runtime/new.schema.json',
            'reason': 'Superseded schema', 'historyRefs': ['references/history.json#/entries/0']}]
        self.save('references/history.json', {'entries': [{
            'target': 'assets/runtime/old.schema.json',
            'retirementRef': 'assets/asset-catalog.json#/retirements/0'}]})
        self.save_catalog()

    def test_query_reads_canonical_target_and_distinguishes_instance_version(self):
        result = resource_metadata('assets/old.schema.json', root=self.root)
        self.assertEqual('assets/runtime/old.schema.json', result['path'])
        self.assertEqual('NOT_DECLARED', result['fields']['schemaVersion']['status'])
        self.assertEqual('NOT_DECLARED', result['fields']['owner']['status'])
        self.assertEqual({'const': 2}, result['instanceVersionConstraints']['schemaVersion']['constraint'])
        self.save('assets/runtime/old.schema.json', {'schemaVersion': 3, '$id': 'urn:test:old',
                                                   'owner': 'maintainer', 'sourceSemantics': 'CANONICAL'})
        updated = resource_metadata('assets/old.schema.json', root=self.root)
        self.assertEqual(3, updated['fields']['schemaVersion']['value'])
        self.assertTrue(updated['fields']['owner']['source'].endswith('#/owner'))

    def test_metadata_rejects_duplicate_identity_missing_id_and_invalid_version(self):
        for value, error in [({'$id': 'urn:test:new'}, 'SCHEMA_ID_CONFLICT'),
                             ({}, 'SCHEMA_ID_MISSING'),
                             ({'$id': 'urn:test:old', 'schemaVersion': True}, 'ASSET_METADATA_INVALID')]:
            with self.subTest(error=error):
                self.save('assets/runtime/old.schema.json', value)
                with self.assertRaisesRegex(ProtocolError, error):
                    metadata_inventory(self.root)

    def test_catalog_rejects_copied_metadata_and_inverted_authority(self):
        for key in ('checksum', 'schemaVersion', '$id', 'owner', 'sourceSemantics'):
            invalid = copy.deepcopy(self.catalog)
            invalid['entries'][0][key] = 'duplicate'
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'CATALOG_SCHEMA_INVALID'):
                validate_catalog_schema(self.root, invalid)
        invalid = copy.deepcopy(self.catalog)
        invalid['authorityPolicy']['resourceIndex'] = 'CANONICAL'
        with self.assertRaisesRegex(ValueError, 'CATALOG_SCHEMA_INVALID'):
            validate_catalog_schema(self.root, invalid)

    def test_missing_target_duplicate_logical_path_and_duplicate_authority_rejected(self):
        for field, value, error in [('target', 'assets/runtime/missing.json', 'TARGET_MISSING'),
                                    ('logicalPath', 'assets/new.schema.json', 'CATALOG_DUPLICATE'),
                                    ('target', 'assets/runtime/new.schema.json', 'CATALOG_DUPLICATE')]:
            original = self.catalog['entries'][0][field]
            self.catalog['entries'][0][field] = value
            self.save_catalog()
            with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, error):
                build_index(self.root)
            self.catalog['entries'][0][field] = original

    def test_unrecorded_delete_cannot_be_hidden_by_regeneration(self):
        before = (self.root / INDEX_PATH).read_bytes()
        self.remove_old()
        with self.assertRaisesRegex(ValueError, 'RESOURCE_RETIREMENT_REQUIRED'):
            write_index(self.root)
        self.assertEqual(before, (self.root / INDEX_PATH).read_bytes())

    def test_manifest_witness_survives_missing_index(self):
        with patch.object(manifest, 'ROOT', self.root):
            (self.root / 'FILE_MANIFEST.sha256').write_text(manifest.manifest_content())
        self.remove_old()
        (self.root / INDEX_PATH).unlink()
        with self.assertRaisesRegex(ValueError, 'RESOURCE_RETIREMENT_REQUIRED'):
            write_index(self.root)

    def test_recorded_retirement_roundtrips_and_old_address_is_diagnostic(self):
        self.remove_old()
        self.declare_retirement()
        write_index(self.root)
        validate_index(self.root)
        for address in ('assets/old.schema.json', 'assets/runtime/old.schema.json'):
            with self.subTest(address=address), self.assertRaisesRegex(ValueError, 'RESOURCE_RETIRED'):
                resource_path(address, root=self.root)
        self.assertEqual(self.root / 'assets/runtime/new.schema.json',
                         resource_path('assets/new.schema.json', root=self.root))

    def test_invalid_retirement_history_date_and_replacement_are_rejected(self):
        self.remove_old()
        for field, value, error in [('retiredAt', 'yesterday', 'CATALOG_SCHEMA_INVALID|RETIREMENT_DATE_INVALID'),
                                    ('replacement', 'assets/missing.json', 'TARGET_MISSING'),
                                    ('historyRefs', ['references/history.json#/entries/9'], 'HISTORY_INVALID')]:
            self.declare_retirement()
            self.catalog['retirements'][0][field] = value
            self.save_catalog()
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, error):
                write_index(self.root)
        self.declare_retirement()
        self.save('references/history.json', {'entries': [{'target': 'assets/runtime/old.schema.json'}]})
        with self.assertRaisesRegex(ValueError, 'HISTORY_MISMATCH'):
            write_index(self.root)

    def test_retirement_history_cannot_be_erased(self):
        self.remove_old()
        self.declare_retirement()
        write_index(self.root)
        self.catalog['retirements'] = []
        self.save_catalog()
        with self.assertRaisesRegex(ValueError, 'RETIREMENT_HISTORY_CHANGED'):
            write_index(self.root)


if __name__ == '__main__':
    unittest.main()
