"""Probe the full checker and negative cases without modifying the package."""
import json
from pathlib import Path
import subprocess
import sys
import unittest

PACKAGE = Path(__file__).resolve().parents[1]


class CheckerRegressionTests(unittest.TestCase):
    def test_command_surface_handles_malformed_owners_and_pack_support(self):
        self.probe('''
import copy
from runtime._core import capability_packs
from checks import cli_help
contract = capability_packs.load_contract()
for field, value in [('entryLayer', 'invalid'), ('packs', ['invalid']), ('packs', 42)]:
 broken = copy.deepcopy(contract)
 broken[field] = value
 assert capability_packs.validate_contract(broken), (field, value)
 try:
  cli_help.declared_cli_paths(broken)
 except cli_help.CliHelpError:
  pass
 else:
  raise AssertionError((field, value))
broken = copy.deepcopy(contract)
broken['entryLayer']['supportCliEntrypoints'].remove('scripts/service_cli.py')
broken['packs'][0]['supportCliEntrypoints'] = ['scripts/service_cli.py']
assert any('supportCliEntrypoints' in error for error in capability_packs.validate_contract(broken))
try:
 cli_help.declared_cli_paths(broken)
except cli_help.CliHelpError:
 pass
else:
 raise AssertionError('unsupported pack CLI declaration was accepted')
''')

    def test_command_surface_rejects_conflicting_and_missing_decisions(self):
        self.probe('''
import copy
from runtime._core import capability_packs
from checks import cli_help
contract = capability_packs.load_contract()
surface = capability_packs.command_surface(contract)
assert surface['counts'] == {'KEEP': 39, 'INTERNAL': 25, 'MERGE': 8}
assert {item['path'] for item in surface['entries'] if item['v1Decision'] == 'KEEP'} == set(cli_help.declared_cli_paths(contract))
assert cli_help.resolve_cli_path('case_doc', contract) == 'scripts/case_doc.py'
try:
 cli_help.resolve_cli_path('document_facts', contract)
except cli_help.CliHelpError:
 pass
else:
 raise AssertionError('internal module was exposed as CLI')
for mutation in ('conflict', 'missing', 'duplicate'):
 broken = copy.deepcopy(contract)
 if mutation == 'conflict': broken['commandSurface']['internalScriptIds'].append('wk')
 elif mutation == 'missing': broken['entryLayer']['cliEntrypoints'].remove('scripts/wk.py')
 else: broken['entryLayer']['cliEntrypoints'].append('scripts/wk.py')
 assert capability_packs.validate_contract(broken), mutation
 try:
  cli_help.declared_cli_paths(broken)
 except cli_help.CliHelpError:
  pass
 else:
  raise AssertionError(mutation)
''')

    def probe(self, code):
        run = subprocess.run([sys.executable, '-B', '-c',
                              'import sys;sys.path.insert(0,' + repr(str(PACKAGE/'scripts')) + ');' + code],
                             text=True, capture_output=True)
        self.assertEqual(0, run.returncode, run.stdout + run.stderr)

    def test_bad_authority_path_is_rejected(self):
        self.probe('''
import tempfile,json
from pathlib import Path
from unittest.mock import patch
from checks._check_skill import classified_resources as check
from checks._check_skill.model import CheckResult
with tempfile.TemporaryDirectory() as d:
 root=Path(d);(root/'references').mkdir();(root/'assets').mkdir()
 (root/'references/authority-map.json').write_text(json.dumps({'topics':{'lifecycle':'missing.md'},'manuals':{},'authoritativeSources':{}}))
 (root/'references/reference-catalog.json').write_text(json.dumps({'categories':{}}))
 (root/'assets/asset-catalog.json').write_text(json.dumps({'entries':[]}))
 with patch.object(check,'ROOT',root):
  result=CheckResult();check.check_classified_resources(result)
 assert any('missing.md' in error for error in result.errors),result.errors
''')

    def test_manifest_digest_drift_is_rejected(self):
        self.probe('''
import tempfile,json
from pathlib import Path
from unittest.mock import patch
from checks._check_skill import manifest
from checks._check_skill.model import CheckResult
with tempfile.TemporaryDirectory() as d:
 root=Path(d);(root/'source.txt').write_text('first')
 (root/'assets').mkdir();(root/'references').mkdir()
 (root/'assets/asset-catalog.json').write_text(json.dumps({'entries':[]}))
 (root/'references/reference-catalog.json').write_text(json.dumps({'entries':[],'categories':{}}))
 with patch.object(manifest,'ROOT',root):
  manifest.write_manifest();(root/'source.txt').write_text('changed')
  result=CheckResult();manifest.check_manifest(result)
 assert result.details['manifest']['changedFiles']==['./source.txt']
 assert result.errors
''')

    def test_full_checker_reports_real_failures_instead_of_shallow_pass(self):
        run = subprocess.run([sys.executable, '-B', str(PACKAGE/'scripts/check_skill.py'),
                              '-g', 'ProjectManagerAgent', '--json'], text=True, capture_output=True)
        payload = json.loads(run.stdout)
        self.assertNotIn('CHECKER_EXECUTION_FAILED', str(payload['errors']))
        self.assertIn('manifest', payload['details'])
        self.assertIn('commandPermissionConsistency', payload['details'])
        self.assertIn('workModeCombinationCheck', payload['details'])
        self.assertEqual(1 if payload['errors'] else 0, run.returncode)

    def test_v2_capability_permissions_are_read_from_pack_contract(self):
        self.probe('''
import copy
from unittest.mock import patch
from document_core import agent_context
from runtime._core import capability_packs
from checks._check_skill.model import CheckResult
from checks._check_skill.release_checks import check_capability_pack_contract

pack = capability_packs.load_contract()
runtime = copy.deepcopy(agent_context.RUNTIME_CONTRACT)
runtime['scriptPermissions'] = {
    key: value for key, value in runtime['scriptPermissions'].items()
    if not key.startswith('capability_packs.')
}
with patch.object(capability_packs, 'load_contract', return_value=pack):
    result = CheckResult()
    check_capability_pack_contract(runtime, result)
assert not any('missing DECLARED_AGENT' in error for error in result.errors), result.errors

broken = copy.deepcopy(pack)
broken['scriptPermissions'].pop('capability_packs.list')
with patch.object(capability_packs, 'load_contract', return_value=broken):
    result = CheckResult()
    check_capability_pack_contract(runtime, result)
assert any('capability_packs.list' in error for error in result.errors), result.errors
''')
