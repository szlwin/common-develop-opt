"""Verify bounded Reference navigation through real routes and the reader."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from reference_policy import load_catalog, reading_plan, validate_reading_policy
from runtime import context_resolver
from document_core.reference_reader import read_resource, ReferenceReadError


class ReferenceReadingPolicyTests(unittest.TestCase):
    def test_every_task_route_has_one_primary_and_bounded_upgrades(self):
        routing = context_resolver.load_routing()
        for route in routing['routes']:
            for task_type in route['taskTypes']:
                with self.subTest(task=task_type):
                    routed = context_resolver.resolve_context(task_type)
                    plan = reading_plan(ROOT, routed)
                    self.assertTrue((ROOT / plan['primary']['path']).is_file())
                    self.assertLessEqual(len(plan['upgradeChain']), 3)
                    self.assertEqual(len(routed['resources']) - 1, len(plan['supportingResourceIds']))
                    for edge in plan['upgradeChain']:
                        self.assertTrue((ROOT / edge['target']).is_file())
                        self.assertEqual('CONDITION_NOT_REQUESTED', edge['omissionReason'])

    def test_ai_context_exposes_reading_plan(self):
        run = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/ai_context.py'),
                              'resolve', '--task-type', 'implementation_plan', '--json'],
                             capture_output=True, text=True, cwd=ROOT.parent)
        self.assertEqual(0, run.returncode, run.stderr)
        plan = json.loads(run.stdout)['referenceReadingPlan']
        self.assertTrue(plan['primary']['path'].endswith('/implementation-plan.md'))
        self.assertEqual(2, len(plan['upgradeChain']))

    def test_entry_does_not_expand_detail_parts(self):
        result = read_resource('implementation-plan', entry=True)
        self.assertNotIn('### TASK-01:', result['content'])
        self.assertLessEqual(result['contentBytes'], result['maxReadBytes'])
        alias = read_resource('implementation-plan-core')
        self.assertEqual(['implementation-plan-quick-rules'], alias['selectedSectionIds'])
        detail = read_resource('implementation-plan', query='Plan Review', max_sections=1)
        self.assertIn('Plan Review', detail['content'])

    def test_utf8_budget_rejects_incomplete_output(self):
        with self.assertRaisesRegex(ReferenceReadError, 'REFERENCE_READ_BUDGET_EXCEEDED'):
            read_resource('implementation-plan', entry=True, max_bytes=100)
        with self.assertRaisesRegex(ReferenceReadError, 'positive'):
            read_resource('implementation-plan', entry=True, max_bytes=-1)
        with self.assertRaisesRegex(ReferenceReadError, 'cannot be combined'):
            read_resource('implementation-plan', entry=True, full=True)

    def test_complete_catalog_is_valid(self):
        validate_reading_policy(ROOT, load_catalog(ROOT))

    def test_bad_metadata_and_missing_replacements_are_rejected(self):
        catalog = load_catalog(ROOT)
        mutations = [('maxReadBytes', 0, 'BUDGET_INVALID'),
                     ('deprecatedBy', 'references/missing.md', 'REPLACEMENT_MISSING'),
                     ('requires', ['references/missing.md'], 'DEPENDENCY_MISSING'),
                     ('nextOn', [{'when': 'ALWAYS', 'target': catalog['entries'][1]['target']}], 'UPGRADE_INVALID'),
                     ('role', 'anything', 'ROLE_INVALID')]
        for field, value, error in mutations:
            with self.subTest(field=field):
                invalid = copy.deepcopy(catalog)
                invalid['entries'][0][field] = value
                with self.assertRaisesRegex(ValueError, error):
                    validate_reading_policy(ROOT, invalid)

    def test_upgrade_cycles_and_unregistered_documents_are_rejected(self):
        catalog = load_catalog(ROOT)
        item = catalog['entries'][0]
        item['nextOn'] = [{'when': 'DETAIL_REQUIRED', 'target': item['target']}]
        with self.assertRaisesRegex(ValueError, 'REFERENCE_CYCLE'):
            validate_reading_policy(ROOT, catalog)
        catalog = load_catalog(ROOT)
        catalog['documents'].pop()
        with self.assertRaisesRegex(ValueError, 'COVERAGE_MISMATCH'):
            validate_reading_policy(ROOT, catalog)


if __name__ == '__main__':
    unittest.main()
