"""Behavioral rejection probes for the full checker and candidate services."""
from pathlib import Path
import json
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class CheckerAdaptationTests(unittest.TestCase):
    def test_full_checker_rejects_semantic_faults_with_fresh_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            package = Path(d)/'package'
            shutil.copytree(ROOT, package, symlinks=True,
                            ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache'))
            summary = package/'references/specialist/onepage-html.md'
            authority = package/'references/authority-map.json'
            original_summary = summary.read_text()
            original_authority = authority.read_text()
            cases = [('summary', 'onepage-html.md retains duplicate lifecycle or prototype rules'), ('rule', 'rules.identity'),
                     ('topics', 'missing required topics'), ('missing-topic', 'gitCheckpoint')]
            for case, expected in cases:
                with self.subTest(case=case):
                    summary.write_text(original_summary)
                    value = json.loads(original_authority)
                    if case == 'summary':
                        summary.write_text(original_summary + '\n页面行为演示\n')
                    elif case == 'rule':
                        value['rules']['identity'] = 'scripts/missing-identity.py'
                    elif case == 'topics':
                        value['topics'] = {}
                    else:
                        value['topics'].pop('gitCheckpoint')
                    authority.write_text(json.dumps(value, ensure_ascii=False))
                    run = subprocess.run([sys.executable, '-B', str(package/'scripts/check_skill.py'),
                                          '-g', 'ProjectManagerAgent', '--write-manifest', '--json'],
                                         capture_output=True, text=True, cwd=d)
                    self.assertNotEqual(0, run.returncode, run.stdout + run.stderr)
                    errors = json.loads(run.stdout)['errors']
                    self.assertTrue(any(expected in error for error in errors), errors)
                    self.assertFalse(any('FILE_MANIFEST' in error for error in errors), errors)

    def test_summary_is_included_in_semantic_rule_checks(self):
        self.probe('''
import tempfile
from pathlib import Path
from unittest.mock import patch
from checks._check_skill import layout, invariant_checks
from checks._check_skill.model import CheckResult
import reference_reader
with tempfile.TemporaryDirectory() as d:
 root=Path(d);refs=root/'references';refs.mkdir()
 topic=refs/'topic.md';topic.write_text('# Topic\\n`wk -gp` 无需 checkpoint\\n[Details](topic.detail.md)')
 (refs/'topic.detail.md').write_text('# Topic\\n## Detail {#detail}\\nValid details\\n')
 with patch.object(layout,'ROOT',root),patch.object(reference_reader,'ROOT',root):
  text=layout.read_check_text(topic)
  assert '无需 checkpoint' in text,text
  assert text.count('Valid details')==1,text
  item={'id':'PUSH','owner':'owner','referenceFiles':[], 'ownerPhrases':[],
        'forbiddenReferencePhrases':[], 'triggerPattern':'wk -gp'}
  result=CheckResult()
  invariant_checks._check_invariant(item,{'owner':'<!-- common-develop-invariant: PUSH -->','topic':text},result)
  assert any('without its marker' in e for e in result.errors),result.errors
''')

    def test_authority_rules_and_required_topics_cannot_disappear(self):
        self.probe('''
import copy,json,tempfile
from pathlib import Path
from unittest.mock import patch
from checks._check_skill import classified_resources as checker
from checks._check_skill.model import CheckResult
original=json.loads((checker.ROOT/'references/authority-map.json').read_text())
with tempfile.TemporaryDirectory() as d:
 root=Path(d);(root/'references').mkdir();(root/'assets').mkdir()
 (root/'assets/asset-catalog.json').write_text('{"entries":[]}')
 (root/'references/reference-catalog.json').write_text('{"categories":{}}')
 for mutation,expected in [('rule','rules.identity'),('topics','missing required topics'),('missing-topic','gitCheckpoint')]:
  value=copy.deepcopy(original)
  if mutation=='rule':value['rules']['identity']='scripts/does-not-exist.py'
  elif mutation=='topics':value['topics']={}
  else:value['topics'].pop('gitCheckpoint')
  (root/'references/authority-map.json').write_text(json.dumps(value))
  with patch.object(checker,'ROOT',root):
   result=CheckResult();checker.check_classified_resources(result)
  assert any(expected in e for e in result.errors),(expected,result.errors)
''')

    def probe(self, source):
        prefix = f'import sys;sys.path.insert(0,{str(ROOT / "scripts")!r});'
        run = subprocess.run([sys.executable, '-B', '-c', prefix + source], text=True, capture_output=True)
        self.assertEqual(0, run.returncode, run.stdout + run.stderr)

    def test_layout_rejects_missing_implementation_and_external_alias(self):
        self.probe('''
import tempfile
from pathlib import Path
from unittest.mock import patch
from checks._check_skill import layout
with tempfile.TemporaryDirectory() as d:
 root=Path(d);(root/'scripts').mkdir()
 entry=root/'scripts/entry.py';entry.write_text('from compat_adapter import execute')
 with patch.object(layout,'ROOT',root):
  for expected in ('ADAPTER_IMPLEMENTATION_MISSING','CHECK_PATH_OUTSIDE_PACKAGE'):
   try: layout.checked_path(entry)
   except ValueError as e: assert expected in str(e),str(e)
   else: raise AssertionError(expected)
   entry.unlink();entry.symlink_to('/etc/hosts')
''')

    def test_missing_detail_and_duplicate_stable_ids_are_rejected(self):
        self.probe('''
import tempfile
from pathlib import Path
from unittest.mock import patch
import reference_reader as reader
with tempfile.TemporaryDirectory() as d:
 root=Path(d);topic=root/'topic.md';topic.write_text('# Topic\\n[Detail](topic.detail.md)')
 with patch.object(reader,'ROOT',root):
  try: reader.resource_sections(topic)
  except reader.ReferenceReadError: pass
  else: raise AssertionError('missing detail accepted')
  detail=root/'topic.detail.md';detail.write_text('## One {#one}\\n')
  part=root/'topic.detail.part-2.md';part.write_text('## Duplicate {#one}\\n')
  try: reader.resource_sections(topic)
  except reader.ReferenceReadError: pass
  else: raise AssertionError('duplicate stable id accepted')
''')

    def test_services_reject_unauthorized_agent_before_writing(self):
        self.probe('''
import tempfile
from pathlib import Path
from application_services import seal_candidate, submit_review
with tempfile.TemporaryDirectory() as d:
 root=Path(d);out=root/'out.json'
 seal=seal_candidate(root,{},out,root/'review.json',agent='DevelopAgent')
 review=submit_review({}, {}, out, 'APPROVED', agent='DevelopAgent')
 assert seal['code']==review['code']=='AGENT_NOT_AUTHORIZED'
 assert not list(root.iterdir())
''')

    def test_yaml_characterization_and_shared_implementation(self):
        self.probe('''
from document_core import simple_yaml as shared
assert shared.loads("v: 'it''s'")['v']=="it's"
assert shared.loads(r"v: '\\u0041'")['v']==r'\\u0041'
assert shared.loads(r'v: "\\u0041"')['v']=='A'
for bad in ('{"x":1,"x":2}', '[NaN]', "v: 'one' 'two'"):
 try: shared.loads(bad)
 except ValueError: pass
 else: raise AssertionError(bad)
''')

    def test_package_identity_rejects_wrong_version_and_unprepared_release(self):
        self.probe('''
import json,tempfile,shutil
from pathlib import Path
from checks._check_skill.layout import ROOT
from checks._check_skill.package_identity import validate_identity
assert not validate_identity(ROOT),validate_identity(ROOT)
assert 'RELEASE_NOT_PREPARED' in validate_identity(ROOT,profile='release')
with tempfile.TemporaryDirectory() as d:
 root=Path(d)
 for name in ('assets/package.json','assets/package/release-contract.json','assets/runtime/runtime-contract.json','CHANGELOG.md'):
  dest=root/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/name,dest)
 path=root/'assets/package.json';value=json.loads(path.read_text());value['version']='99.0';path.write_text(json.dumps(value))
 assert 'PACKAGE_IDENTITY_MISMATCH' in validate_identity(root)
''')

    def test_schema_and_permission_corruption_are_rejected(self):
        self.probe('''
import copy,jsonschema
from document_core import agent_context
from checks._check_skill.command_permission_checks import check_command_permission_consistency
from checks._check_skill.model import CheckResult
contract=copy.deepcopy(agent_context.RUNTIME_CONTRACT)
for commands in contract['scriptPermissionAssignments'].values():
 if 'service_cli.seal' in commands: commands.remove('service_cli.seal')
result=CheckResult();check_command_permission_consistency(contract,result)
assert any('service_cli.seal' in error for error in result.errors),result.errors
try: jsonschema.Draft202012Validator.check_schema({'type':'not-a-type'})
except jsonschema.SchemaError: pass
else: raise AssertionError('invalid schema accepted')
''')
