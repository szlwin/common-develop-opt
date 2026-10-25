import json,tempfile,unittest,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import engineering_graph,impact_analysis,review_session

class GraphImpactAnalysisTests(unittest.TestCase):
 def graph(self):
  return {'schemaVersion':1,'authority':'DERIVED','kind':'engineering_knowledge_graph','nodes':[{'id':'INV-X','type':'INVARIANT','name':'I','sourceRefs':['bm'],'contextRef':'BC-A'},{'id':'AGG-X','type':'AGGREGATE','name':'A','sourceRefs':['bm'],'contextRef':'BC-A'},{'id':'API-X','type':'API','name':'API','sourceRefs':['api'],'contextRef':'BC-B'}],'relationships':[{'id':'R1','from':'INV-X','to':'AGG-X','type':'AFFECTS','reason':'affects','sourceRefs':['bm'],'propagation':'DEFAULT'},{'id':'R2','from':'AGG-X','to':'API-X','type':'EXPOSES','reason':'exposes','sourceRefs':['dep'],'propagation':'CONDITIONAL'}]}
 def test_graph_impact_produces_explainable_risks(self):
  with tempfile.TemporaryDirectory() as td:
   gp=Path(td)/'g.json'; gp.write_text(json.dumps(self.graph()))
   gi=impact_analysis._graph_impact(gp,['INV-X'])
   self.assertTrue(gi['crossContext']); self.assertIn('api_contract',gi['requiredRiskTemplates']); self.assertIn('cross_module_integration',gi['requiredRiskTemplates'])
 def test_review_session_consumes_plan_risks(self):
  plan={'requiredRiskTemplates':['api_contract'],'minimumRevalidation':{'reviews':[]},'unaffected':{'reviewCriteria':[]},'provenance':{'revision':'R1'}}
  out=review_session.plan_session(phase='code_review',revision='R1',explicit_risks=[],risk_report=None,impact_plan=plan)
  self.assertTrue(any(a.get('riskType')=='api_contract' for a in out['axes']))
if __name__=='__main__': unittest.main()

class WKGraphParserTests(unittest.TestCase):
 def test_wk_impact_accepts_graph_arguments(self):
  import wk
  args=wk.build_parser().parse_args(['impact','-g','ProjectManagerAgent','--task-dir','/tmp/t','--revision','R1','--change-class','PRODUCTION','--graph','/tmp/g.json','--changed-ref','INV-X'])
  self.assertEqual('/tmp/g.json',args.graph); self.assertEqual(['INV-X'],args.changed_ref)
