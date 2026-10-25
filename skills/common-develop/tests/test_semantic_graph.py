import unittest,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import semantic_index, engineering_graph

class SemanticGraphTests(unittest.TestCase):
 def dm(self): return {'system':{'id':'SYSTEM-X','name':'X'},'domains':[{'id':'DOMAIN-D','name':'D'}],'boundedContexts':[{'id':'BC-A','name':'A','domainRef':'DOMAIN-D'},{'id':'BC-B','name':'B','domainRef':'DOMAIN-D'}],'relationships':[{'id':'CTXREL-A-B','fromContext':'BC-A','toContext':'BC-B','type':'PROVIDES_TO','rationale':'A feeds B'}],'translations':[]}
 def bm(self): return {'module':{'code':'A','name':'A','boundedContextRef':'BC-A'},'terms':[{'id':'TERM-ORDER','name':'Order','contextRef':'BC-A'}],'entities':[],'valueObjects':[],'aggregates':[{'id':'AGG-ORDER','name':'Order','contextRef':'BC-A','invariantIds':['INV-ORDER']}],'invariants':[{'id':'INV-ORDER','statement':'valid','contextRef':'BC-A','termRefs':['TERM-ORDER'],'appliesToRefs':['AGG-ORDER']}],'stateMachines':[],'services':[],'policies':[],'events':[]}
 def test_semantic_index_deterministic(self):
  self.assertEqual(semantic_index.build(self.dm(),self.bm()),semantic_index.build(self.dm(),self.bm()))
  self.assertIn('AGG-ORDER',semantic_index.build(self.dm(),self.bm())['contexts']['BC-A']['aggregates'])
 def test_graph_has_domain_and_semantic_relations(self):
  g=engineering_graph.build(self.dm(),self.bm(),None); self.assertEqual('PASSED',engineering_graph.validate(g)['status'])
  types={r['type'] for r in g['relationships']}; self.assertTrue({'CONTAINS','OWNS','PROTECTED_BY','USES_TERM'}.issubset(types))
 def test_graph_impact_explains_path(self):
  dep={'nodes':[{'id':'SVC-ORDER','type':'MODULE','name':'OrderSvc','module':'A','refs':['x']},{'id':'CASE-ORDER','type':'TEST_CASE','name':'Case','module':'A','refs':['x']}],'relationships':[{'id':'REL-IMPL','from':'AGG-ORDER','to':'SVC-ORDER','type':'IMPLEMENTED_BY','direction':'DIRECTED','rationale':'impl','conditions':[],'traceIds':['TR-X']},{'id':'REL-VER','from':'SVC-ORDER','to':'CASE-ORDER','type':'VERIFIED_BY','direction':'DIRECTED','rationale':'test','conditions':[],'traceIds':['TR-X']}],'impactPolicies':[],'crossModuleImplementations':[]}
  g=engineering_graph.build(self.dm(),self.bm(),dep); out=engineering_graph.impact(g,['INV-ORDER']); refs={x['ref'] for x in out}; self.assertIn('CASE-ORDER',refs)
if __name__=='__main__': unittest.main()

class SemanticGraphQueryTests(unittest.TestCase):
 def test_owner_and_consumer_queries(self):
  g={'relationships':[{'id':'O','from':'BC-A','to':'AGG-X','type':'OWNS'},{'id':'C','from':'BC-B','to':'BC-A','type':'CONSUMES_FROM'}]}
  self.assertEqual('BC-A',engineering_graph.owners(g,'AGG-X')[0]['from'])
  self.assertEqual('C',engineering_graph.consumers(g,'BC-A')[0]['id'])
