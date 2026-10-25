"""Protocol consumption, scoped discovery and impact adapter boundaries."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from artifact_identity import identity
from dependency_graph import inspect
from diagnostic_io import result
from discover_dependencies import discover
from impact_adapter import build_adapter
from document_core.impact_analysis import ImpactAnalysisError, build_plan, load_adapter_result
from read_session import ReadSession
from schema_protocol import ProtocolError, validate
from consistency_fixture import make_project
from maintenance.inspection_report import summarize

class ProtocolAndDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name).resolve()
        self.profile,self.records=make_project(self.root,history=2)

    def edit(self,index,operation):
        path=self.root/'current'/f'{index}.artifact.json'
        value=json.loads(path.read_text()); operation(value)
        path.write_text(json.dumps(value))

    def test_protocols_are_consumed_by_services(self):
        valid=discover(self.root,self.profile,'ART-00000')
        self.assertEqual('PASSED',valid['status'])
        validate('artifact-identity',valid['identities'][0])
        validate('dependency-graph',valid['graph'])
        validate('diagnostic-result',valid)
        with self.assertRaises(ProtocolError): result('INVENTED_SUCCESS')
        bad=copy.deepcopy(valid['graph']);bad['nodes'][0]['artifactId']=' '
        self.assertEqual('FAILED',inspect(bad)['status'])
        spec=json.loads((self.root/'current/0.artifact.json').read_text());spec['schemaVersion']=True
        self.assertEqual('UNKNOWN',identity(spec,self.root)['status'])

    def test_catalog_closure_and_history_isolation(self):
        observed=discover(self.root,self.profile,'ART-00000')
        self.assertEqual('PASSED',observed['status'])
        self.assertEqual(4,observed['declarationCount'])
        self.assertEqual(8,observed['measurements']['filesRead'])
        self.assertEqual(['ART-00000','ART-00001','ART-00002','ART-00003'],observed['analysis']['impact'][-1]['path'])
        self.assertEqual('SCOPED_DECLARATIONS',observed['coverage'])

    def test_missing_declaration_cannot_become_complete(self):
        (self.root/'current/3.artifact.json').unlink()
        observed=discover(self.root,self.profile,'ART-00000')
        self.assertEqual('UNKNOWN',observed['status'])
        self.assertIn('REQUIRED_ARTIFACT_MISSING',{x['code'] for x in observed['diagnostics']})

    def test_related_broken_declaration_is_not_ignored(self):
        (self.root/'current/extra.artifact.yaml').write_text('notes: |\n  unsupported')
        self.assertEqual('UNKNOWN',discover(self.root,self.profile)['status'])

    def test_omitted_dependency_field_and_duplicate_id(self):
        self.edit(0,lambda x:x.pop('dependencies'))
        self.assertEqual('UNKNOWN',discover(self.root,self.profile)['status'])
        make_project(self.root)
        (self.root/'current/duplicate.artifact.json').write_bytes((self.root/'current/0.artifact.json').read_bytes())
        self.assertEqual('UNKNOWN',discover(self.root,self.profile)['status'])

    def test_missing_target_and_bad_reference(self):
        self.edit(1,lambda x:x['dependencies'][0].update(artifactId='MISSING'))
        self.assertEqual('UNKNOWN',discover(self.root,self.profile)['status'])
        make_project(self.root)
        self.edit(1,lambda x:x['dependencies'][0].update(reference='../data/0.json#DOES-NOT-EXIST'))
        self.assertEqual('FAILED',discover(self.root,self.profile)['status'])

    def test_wrong_source_and_revision_are_rejected(self):
        other=self.root/'data/other.json';other.write_bytes((self.root/'data/0.json').read_bytes())
        self.edit(1,lambda x:x['dependencies'][0].update(reference='../data/other.json#ART-00000'))
        self.assertEqual('FAILED',discover(self.root,self.profile)['status'])
        make_project(self.root)
        path=self.root/'data/0.json';value=json.loads(path.read_text());value['revision']='R2';path.write_text(json.dumps(value))
        self.assertEqual('FAILED',discover(self.root,self.profile)['status'])

    def test_supplied_graph_cannot_omit_edges_or_nodes(self):
        graph=discover(self.root,self.profile)['graph'];graph['edges'].pop()
        observed=discover(self.root,self.profile,expected_graph=graph)
        self.assertEqual('FAILED',observed['status'])
        self.assertTrue(observed['diagnostics'][0]['missingEdges'])

    def test_warm_cache_invalidates_after_same_revision_edit(self):
        session=ReadSession()
        before=discover(self.root,self.profile,session=session)
        self.assertEqual('PASSED',before['status'])
        prior=session.files_read
        self.assertEqual('PASSED',discover(self.root,self.profile,session=session)['status'])
        self.assertEqual(prior,session.files_read)
        path=self.root/'data/0.json';value=json.loads(path.read_text());value['text']='modified';path.write_text(json.dumps(value))
        self.assertEqual('STALE',discover(self.root,self.profile,session=session)['status'])
        self.assertGreater(session.files_read,prior)

    def test_mutation_during_read_is_unknown(self):
        class MutatingReader(ReadSession):
            def read_bytes(inner,path):
                value=super().read_bytes(path)
                if Path(path).name=='3.json':
                    (self.root/'data/0.json').write_text('{}')
                return value
        self.assertEqual('UNKNOWN',discover(self.root,self.profile,session=MutatingReader())['status'])

    def test_profile_escape_limits_and_symlink_are_rejected(self):
        for profile in (dict(self.profile,roots=['../outside']),dict(self.profile,maxDeclarations=1),dict(self.profile,requiredArtifactIds=[])):
            self.assertEqual('UNKNOWN',discover(self.root,profile)['status'])
        (self.root/'current/link').symlink_to(self.root/'data',target_is_directory=True)
        self.assertEqual('UNKNOWN',discover(self.root,self.profile)['status'])

    def test_document_tree_without_declarations_is_explicitly_unsupported(self):
        document = self.root / 'version' / 'V_TEST' / 'requirement' / 'REQ-UNDECLARED'
        document.mkdir(parents=True)
        (document / 'requirement.md').write_text('# REQ-UNDECLARED\n', encoding='utf-8')
        profile = {'schemaVersion': 1, 'roots': ['version'],
                   'requiredArtifactIds': ['REQ-UNDECLARED']}
        observed = discover(self.root, profile)
        self.assertEqual('UNKNOWN', observed['status'])
        self.assertEqual('NO_SUPPORTED_DECLARATIONS', observed['code'])
        self.assertEqual('UNSUPPORTED_LAYOUT', observed['coverage'])
        self.assertEqual('UNSUPPORTED_LAYOUT', observed['diagnostics'][0]['code'])

    def test_removed_legacy_layout_is_rejected(self):
        profile = {'schemaVersion': 1, 'layout': 'legacy', 'roots': ['.'], 'requiredArtifactIds': ['REQ-LEGACY']}
        observed = discover(self.root, profile)
        self.assertEqual('UNKNOWN', observed['status'])
        self.assertEqual('DISCOVERY_PROFILE_INVALID', observed['code'])

    def test_dependency_reference_must_bind_target_identity(self):
        (self.root/'data/0.json').write_text(json.dumps([{'id':'ART-00000','artifactType':'requirement','revision':'R1'}, {'id':'WRONG-ID','artifactType':'requirement','revision':'R1'}]))
        self.edit(1, lambda x: x['dependencies'][0].update(reference='../data/0.json#WRONG-ID'))
        observed=discover(self.root, self.profile)
        self.assertEqual('FAILED', observed['status'])
        self.assertIn('DEPENDENCY_REFERENCE_ID_MISMATCH',{x['code'] for x in observed['diagnostics']})

    def test_adapter_keeps_review_and_permission_authority(self):
        observation=discover(self.root,self.profile,'ART-00000')
        adapter=build_adapter(observation,'ART-00000','R1','R2')
        self.assertEqual('MEDIUM',adapter['confidence'])
        self.assertEqual(4,len(adapter['affectedArtifacts']))
        self.assertEqual([],adapter['affectedReviewCriteria'])
        self.assertEqual([],adapter['affectedProfiles'])
        self.assertNotIn('status',adapter)
        observation['status']='UNKNOWN'
        self.assertEqual('UNKNOWN',build_adapter(observation,'ART-00000','R1','R2')['confidence'])

    def test_current_adapter_is_consumed_by_incremental_plan(self):
        observation=discover(self.root,self.profile,'ART-00000')
        adapter=build_adapter(observation,'ART-00000','R1','R2')
        path=self.root/'adapter.json';path.write_text(json.dumps(adapter))
        loaded=load_adapter_result(path)
        plan=build_plan(task_dir=self.root/'task',phase='code_review',base_revision='R1',revision='R2',
                        change_class='SEMANTIC_AUTHORITY',adapter_result=loaded)
        self.assertTrue(plan['adapter']['used'])
        self.assertEqual(adapter['affectedArtifacts'],plan['adapter']['affectedArtifacts'])

    def test_current_adapter_rejects_protocol_extensions(self):
        observation=discover(self.root,self.profile,'ART-00000')
        adapter=build_adapter(observation,'ART-00000','R1','R2')
        adapter['compatibilityField']=True
        path=self.root/'adapter.json';path.write_text(json.dumps(adapter))
        with self.assertRaises(ImpactAnalysisError):
            load_adapter_result(path)

    def test_cli_failed_discovery_does_not_replace_adapter(self):
        profile=self.root/'profile.json';profile.write_text(json.dumps(self.profile))
        (self.root/'current/0.artifact.json').write_text('{broken')
        with tempfile.TemporaryDirectory() as output_dir:
            adapter=Path(output_dir)/'adapter.json';adapter.write_text('existing')
            report=Path(output_dir)/'report.json'
            script=Path(__file__).resolve().parents[1]/'scripts/impact_adapter.py'
            run=subprocess.run([sys.executable,'-B',str(script),'--root',str(self.root),'--profile',str(profile),'--changed','ART-00000','--base-revision','R1','--revision','R2','--adapter-output',str(adapter),'--output',str(report)],capture_output=True,text=True)
            self.assertEqual(2,run.returncode,run.stderr)
            self.assertEqual('existing',adapter.read_text())
            self.assertFalse(json.loads(report.read_text())['adapterWritten'])

    def test_invalid_report_target_prevents_adapter_overwrite(self):
        profile=self.root/'profile.json';profile.write_text(json.dumps(self.profile))
        with tempfile.TemporaryDirectory() as output_dir:
            adapter=Path(output_dir)/'adapter.json';adapter.write_text('existing')
            script=Path(__file__).resolve().parents[1]/'scripts/impact_adapter.py'
            run=subprocess.run([sys.executable,'-B',str(script),'--root',str(self.root),'--profile',str(profile),'--changed','ART-00000','--base-revision','R1','--revision','R2','--adapter-output',str(adapter),'--output',str(self.root/'forbidden-report.json')],capture_output=True,text=True)
            self.assertEqual(2,run.returncode)
            self.assertEqual('existing',adapter.read_text())
            self.assertFalse((self.root/'forbidden-report.json').exists())

    def test_maintenance_summary_never_invents_readiness(self):
        for status in ('UNKNOWN','CAPTURED','UNRECOGNIZED'):
            self.assertEqual('UNKNOWN',summarize({'a':{'status':status}})['status'])
        observed=summarize({'a':{'status':'PASSED'}})
        self.assertEqual('OBSERVATION_ONLY',observed['authority'])
        self.assertNotIn('next',observed)

if __name__=='__main__':unittest.main()
