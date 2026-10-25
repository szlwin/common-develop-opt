"""Anonymous adversarial regressions; imports exercise the candidate services."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from artifact_identity import identity, compare
from diagnostic_io import digest_value, write_json
from dependency_graph import inspect
from reference_resolver import resolve
from document_core import simple_yaml


class ArtifactConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.spec = {'artifactId': 'REQ-1', 'artifactType': 'requirement', 'revision': 'R1',
                     'category': 'WORKING_SOURCE', 'inputs': [{'path': 'req.md'}],
                     'lifecycleBinding': {'task': 'T', 'phase': 'analysis', 'iteration': 'I'}}
        self.put('req.md', '# Requirement\n')
        self.graph = {'schemaVersion': 1, 'nodes': [{'artifactId': x} for x in ['REQ', 'MODEL', 'FLOW', 'IMPACT', 'NOTE']],
                      'edges': [{'from': a, 'to': b, 'kind': k, 'revision': 'R1', 'contentDigest': 'a'*64}
                                for a, b, k in [('REQ','MODEL','consumes'), ('MODEL','FLOW','generatedFrom'), ('FLOW','IMPACT','implements'), ('REQ','NOTE','contextual')]]}
        self.artifacts = [{'artifactId': x['artifactId'], 'revision':'R1', 'contentDigest':'a'*64} for x in self.graph['nodes']]

    def put(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    def test_same_revision_uses_compare(self):
        before = identity(self.spec, self.root)
        self.put('req.md', '# Requirement changed\n')
        self.assertEqual('STALE', compare(identity(self.spec, self.root), before)['status'])

    def test_missing_and_duplicate_inputs(self):
        for inputs, status in [([], 'UNKNOWN'), ([{'path':'missing'}], 'UNKNOWN'), ([{'path':'req.md'}]*2, 'FAILED')]:
            self.assertEqual(status, identity(dict(self.spec, inputs=inputs), self.root)['status'])

    def test_deterministic_multi_file_order(self):
        self.put('b.md','second')
        a = dict(self.spec, sourceLocator='req.md', inputs=[{'path':'req.md'},{'path':'b.md'}])
        b = dict(a, inputs=list(reversed(a['inputs'])))
        self.assertEqual(identity(a,self.root), identity(b,self.root))
        del a['sourceLocator']; del b['sourceLocator']
        self.assertEqual(identity(a,self.root), identity(b,self.root))

    def test_primary_source_change_invalidates(self):
        self.put('b.md', 'second')
        spec = dict(self.spec, inputs=[{'path':'req.md'}, {'path':'b.md'}])
        before = identity(dict(spec, sourceLocator='req.md'), self.root)
        after = identity(dict(spec, sourceLocator='b.md'), self.root)
        change = compare(after, before)
        self.assertEqual('STALE', change['status'])
        self.assertIn('sourceLocator', change['changedFields'])

    def test_external_identity_must_be_complete_and_consistent(self):
        valid = identity(self.spec, self.root)
        for key in valid['artifact']:
            broken = copy.deepcopy(valid)
            del broken['artifact'][key]
            with self.subTest(missing=key):
                self.assertEqual('UNKNOWN', compare(broken, broken)['status'])
        for field, value in [('digestAlgorithm','unknown'), ('schemaVersion',True), ('contentDigest','a'*64)]:
            broken = copy.deepcopy(valid); broken['artifact'][field] = value
            self.assertEqual('UNKNOWN', compare(broken, broken)['status'])
        for broken in (None, [], {'status':'PASSED','artifact':{}}):
            self.assertEqual('UNKNOWN', compare(valid, broken)['status'])

    def test_lifecycle_and_type_changes_invalidate(self):
        before = identity(self.spec, self.root)
        after = identity(dict(self.spec, artifactType='model'), self.root)
        self.assertEqual('STALE', compare(after,before)['status'])

    def test_dependencies_must_have_real_digest_shape(self):
        bad = dict(self.spec, dependencies=[{'artifactId':'MODEL','kind':'consumes','revision':'R1'}])
        self.assertEqual('UNKNOWN',identity(bad,self.root)['status'])

    def test_identity_detaches_mutable_input_declarations(self):
        spec = dict(self.spec, dependencies=[{'artifactId':'MODEL','kind':'consumes','revision':'R1','contentDigest':'a'*64}])
        before = identity(spec,self.root)
        saved = copy.deepcopy(before)
        spec['dependencies'][0]['contentDigest'] = 'b'*64
        self.assertEqual(saved, before)
        self.assertEqual('STALE', compare(identity(spec,self.root),before)['status'])

    def test_new_revision_never_implies_compatibility(self):
        self.assertEqual('CHANGED',compare(identity(dict(self.spec,revision='R2'),self.root),identity(self.spec,self.root))['status'])

    def test_yaml_escaped_id(self):
        self.put('f.yaml','id: "FLOW-\\u004fRDER"\nartifactType: flow\nrevision: R1\n')
        self.assertEqual('PASSED',resolve(self.root,'f.yaml#FLOW-ORDER','flow','R1')['status'])

    def test_yaml_single_quotes_preserve_backslash(self):
        self.put('f.yaml', "id: 'FLOW-\\u004fRDER'\nartifactType: flow\nrevision: R1\n")
        self.assertEqual('FAILED',resolve(self.root,'f.yaml#FLOW-ORDER','flow','R1')['status'])
        self.assertEqual(r'FLOW-\u004fRDER', simple_yaml.loads((self.root/'f.yaml').read_text())['id'])
        self.assertEqual("it's", simple_yaml.loads("value: 'it''s'")['value'])
        with self.assertRaises(ValueError): simple_yaml.loads("value: 'it' 's'")

    def test_yaml_inline_duplicate_and_nonfinite_rejected(self):
        for value in ('{"id":"ONE","id":"TWO"}', '{"value":NaN}'):
            self.put('f.yaml', 'data: '+value+'\n')
            self.assertEqual('FAILED',resolve(self.root,'f.yaml')['status'])
        self.put('f.yaml', 'id: "FLOW-\\x41"\n')
        self.assertEqual('UNKNOWN',resolve(self.root,'f.yaml')['status'])

    def test_duplicate_id_rejected(self):
        self.put('d.json','[{"id":"DUP-1"},{"id":"DUP-1"}]')
        self.assertEqual('FAILED',resolve(self.root,'d.json#DUP-1')['status'])

    def test_duplicate_json_keys_rejected(self):
        self.put('d.json','{"id":"ONE","id":"TWO"}')
        self.assertEqual('FAILED',resolve(self.root,'d.json')['status'])

    def test_empty_key_pointer(self):
        self.put('d.json',json.dumps({'':{'artifactType':'model','revision':'R2'},'revision':'R1'}))
        self.assertEqual('PASSED',resolve(self.root,'d.json#/','model','R2')['status'])

    def test_pointer_rejects_illegal_escape_and_accepts_literal_tilde(self):
        self.put('d.json',json.dumps({'a~':1,'a~2':2,'a/':3}))
        for pointer in ('/a~','/a~2'):
            self.assertEqual('FAILED',resolve(self.root,'d.json#'+pointer)['status'])
        for pointer in ('/a~0','/a~02','/a~1'):
            self.assertEqual('PASSED',resolve(self.root,'d.json#'+pointer)['status'])

    def test_missing_type_or_revision_is_unknown(self):
        self.put('d.json','{"artifactType":"model"}')
        self.assertEqual('UNKNOWN',resolve(self.root,'d.json','model','R2')['status'])
        self.assertEqual('UNKNOWN',resolve(self.root,'req.md','requirement','R1')['status'])

    def test_stale_model_revision(self):
        self.put('d.json','{"artifactType":"model","revision":"R1"}')
        self.assertEqual('TARGET_REVISION_MISMATCH',resolve(self.root,'d.json','model','R2')['code'])

    def test_unsupported_yaml_is_unknown(self):
        self.put('f.yaml','notes: |\n  text\n')
        self.assertEqual('UNKNOWN',resolve(self.root,'f.yaml')['status'])

    def test_markdown_unicode_and_explicit_anchor(self):
        self.put('d.md','# 业务模型\n<a id="stable"/>\n```\n# fake\n```\n')
        self.assertEqual('PASSED',resolve(self.root,'d.md#业务模型')['status'])
        self.assertEqual('PASSED',resolve(self.root,'d.md#stable')['status'])
        self.assertEqual('FAILED',resolve(self.root,'d.md#fake')['status'])

    def test_markdown_filename_is_not_a_real_anchor(self):
        self.put('ghost.md','# Actual\n')
        self.assertEqual('FAILED',resolve(self.root,'ghost.md#ghost')['status'])
        self.assertEqual('PASSED',resolve(self.root,'ghost.md#actual')['status'])
        self.assertEqual('PASSED',resolve(self.root,'ghost.md')['status'])

    def test_declared_scope_ignores_unrelated_draft(self):
        self.put('draft.yaml','notes: [broken')
        self.assertEqual('PASSED',resolve(self.root,'req.md')['status'])
        self.assertEqual('FAILED',resolve(self.root,'draft.yaml')['status'])

    def test_path_escape_and_symlink(self):
        self.assertEqual('FAILED',resolve(self.root,'../escape.json')['status'])
        self.put('x','data')
        (self.root/'link').symlink_to(self.root.parent)
        self.assertEqual('FAILED',identity(dict(self.spec,inputs=[{'path':'link/outside'}]),self.root)['status'])

    def test_graph_missing_identities_never_current(self):
        for facts in [None, [], self.artifacts[:1]]:
            value=inspect(self.graph,'REQ',facts)
            self.assertEqual('UNKNOWN',value['drift']['status'])

    def test_invalid_graph_nodes_and_duplicate_identity(self):
        self.assertEqual('FAILED',inspect({'schemaVersion':1,'nodes':[False],'edges':[]})['status'])
        self.assertEqual('FAILED',inspect(self.graph,artifacts=self.artifacts+self.artifacts)['status'])

    def test_malformed_graph_and_identity_values(self):
        for kind in ([], {}, None):
            graph = copy.deepcopy(self.graph); graph['edges'][0]['kind'] = kind
            self.assertEqual('FAILED', inspect(graph)['status'])
        facts = copy.deepcopy(self.artifacts); facts[0]['revision'] = ['R1']
        self.assertEqual('UNKNOWN', inspect(self.graph, artifacts=facts)['status'])

    def test_contextual_skip_and_transitive_paths(self):
        value=inspect(self.graph,'REQ',self.artifacts)
        self.assertEqual({'MODEL','FLOW','IMPACT'}, {x['artifactId'] for x in value['impact']})
        self.assertEqual(['REQ','MODEL','FLOW','IMPACT'],next(x['path'] for x in value['impact'] if x['artifactId']=='IMPACT'))

    def test_stale_edge_and_order_independence(self):
        facts=copy.deepcopy(self.artifacts); facts[0]['contentDigest']='b'*64
        value=inspect(self.graph,'REQ',facts)
        self.assertEqual('STALE',value['status'])
        shuffled=dict(self.graph,edges=list(reversed(self.graph['edges'])))
        self.assertEqual(value['impact'],inspect(shuffled,'REQ',facts)['impact'])

    def test_cycle_terminates_and_supersedes_does_not_invalidate(self):
        graph=copy.deepcopy(self.graph)
        graph['edges'] += [{'from':'IMPACT','to':'REQ','kind':'consumes','revision':'R1','contentDigest':'a'*64}, {'from':'MODEL','to':'NOTE','kind':'supersedes'}]
        self.assertEqual(3,len(inspect(graph,'REQ')['impact']))

    def test_output_cannot_overwrite_inputs_or_source(self):
        with self.assertRaises(ValueError): write_json({},self.root/'req.md',[self.root])
        link=self.root/'report-link'; link.symlink_to(self.root/'req.md')
        with self.assertRaises(ValueError): write_json({},link,[self.root/'req.md'])

    def test_output_hardlinks_never_modify_input_inode(self):
        source = self.put('source/req.json', '{"original":true}')
        target = self.root/'report.json'
        os.link(source, target)
        with self.assertRaises(ValueError): write_json({'report':True}, target, [source])
        write_json({'report':True}, target, [source.parent])
        self.assertEqual('{"original":true}', source.read_text())
        self.assertEqual({'report':True}, json.loads(target.read_text()))
        self.assertFalse(source.samefile(target))

    def test_cli_failure_exit_and_no_input_mutation(self):
        source=self.put('spec.json',json.dumps(dict(self.spec,inputs=[])))
        output=Path(self.temp.name).parent / (self.root.name+'-report.json')
        self.addCleanup(lambda: output.unlink(missing_ok=True))
        script=Path(__file__).resolve().parents[1]/'scripts/artifact_identity.py'
        run=subprocess.run([sys.executable,'-B',str(script),'--root',str(self.root),'--spec',str(source),'--output',str(output)],capture_output=True,text=True)
        self.assertEqual(2,run.returncode)
        self.assertEqual('UNKNOWN',json.loads(output.read_text())['status'])
        self.assertEqual('# Requirement\n',(self.root/'req.md').read_text())

if __name__=='__main__': unittest.main()
