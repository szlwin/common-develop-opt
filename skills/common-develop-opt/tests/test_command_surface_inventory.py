"""Ensure source drift fails even when decision counts remain unchanged."""
import json
from pathlib import Path
import subprocess
import sys
import unittest

PACKAGE = Path(__file__).resolve().parents[1]


class CommandSurfaceInventoryTests(unittest.TestCase):
    def probe(self, code):
        prefix = f"import sys;sys.path.insert(0,{str(PACKAGE / 'scripts')!r});"
        run = subprocess.run([sys.executable, "-B", "-c", prefix + code],
                             text=True, capture_output=True)
        self.assertEqual(0, run.returncode, run.stdout + run.stderr)

    def test_inventory_export_matches_cli_discovery(self):
        def query(*options):
            run = subprocess.run(
                [sys.executable, "-B", str(PACKAGE / "scripts/cli_help.py"),
                 "list", "--json", *options], text=True, capture_output=True,
            )
            self.assertEqual(0, run.returncode, run.stdout + run.stderr)
            return json.loads(run.stdout)

        discovery = query()
        inventory = query("--inventory")
        for key in ("version", "source", "counts", "entries", "digest"):
            self.assertEqual(discovery["commandSurface"][key], inventory[key])
        self.assertEqual(39, len(discovery["entrypoints"]))
        self.assertEqual(72, len(inventory["entries"]))
        self.assertNotIn("legacy_layout", {item["script"] for item in inventory["entries"]})

    def test_contract_digest_detects_same_count_path_and_id_changes(self):
        self.probe('''
import copy
from runtime._core import capability_packs as packs
contract = packs.load_contract()
surface = packs.command_surface(contract)
reordered = copy.deepcopy(contract)
reordered['commandSurface']['internalScriptIds'].reverse()
assert packs.command_surface(reordered)['digest'] == surface['digest']
for mutation in ('path', 'id', 'digest', 'missing-digest'):
 broken = copy.deepcopy(contract)
 if mutation == 'path': broken['entryLayer']['cliEntrypoints'][0] = 'scripts/runtime/wk.py'
 elif mutation == 'id': broken['commandSurface']['internalScriptIds'][0] = 'another_internal'
 elif mutation == 'digest': broken['commandSurface']['digest'] = '0' * 64
 else: broken['commandSurface'].pop('digest')
 assert packs.command_surface(broken)['counts'] == surface['counts']
 assert any('commandSurface.digest' in error for error in packs.validate_contract(broken)), mutation
''')

    def test_inventory_rejects_stale_content_even_with_copied_digest(self):
        self.probe('''
import copy, json, tempfile
from pathlib import Path
from runtime._core import capability_packs as packs
from checks._check_skill.release_checks import check_command_surface_inventory
from checks._check_skill.model import CheckResult
expected = packs.command_surface_inventory(packs.load_contract())
with tempfile.TemporaryDirectory() as directory:
 path = Path(directory) / 'inventory.json'
 path.write_text(json.dumps(expected))
 result = CheckResult()
 check_command_surface_inventory(path, result)
 assert not result.errors, result.errors
 assert result.details['commandSurfaceInventory']['digest'] == expected['digest']
 for mutation in ('counts', 'id', 'path', 'decision', 'pointer', 'digest', 'version', 'missing', 'extra'):
  value = copy.deepcopy(expected)
  if mutation == 'counts': value['counts']['INTERNAL'] += 1
  elif mutation == 'id': value['entries'][0]['script'] = 'another_cli'
  elif mutation == 'path': value['entries'][0]['path'] = 'scripts/runtime/acceptance.py'
  elif mutation == 'decision': value['entries'][0]['v1Decision'] = 'INTERNAL'
  elif mutation == 'pointer': value['entries'][0]['sourcePointer'] = '/entryLayer/cliEntrypoints'
  elif mutation == 'digest': value['digest'] = '0' * 64
  elif mutation == 'version': value['version'] = 2
  elif mutation == 'missing': value['entries'].pop()
  else: value['retiredEntries'] = ['legacy_layout']
  path.write_text(json.dumps(value))
  result = CheckResult()
  check_command_surface_inventory(path, result)
  assert any('COMMAND_SURFACE_INVENTORY_DRIFT' in error for error in result.errors), mutation
 for invalid in ('[]', '{invalid'):
  path.write_text(invalid)
  result = CheckResult()
  check_command_surface_inventory(path, result)
  assert any('COMMAND_SURFACE_INVENTORY_INVALID' in error for error in result.errors), invalid
 path.unlink()
 result = CheckResult()
 check_command_surface_inventory(path, result)
 assert any('COMMAND_SURFACE_INVENTORY_INVALID' in error for error in result.errors)
''')
