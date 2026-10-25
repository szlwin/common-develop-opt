import json, tempfile, unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import domain_governance

class DomainGovernanceTests(unittest.TestCase):
    def sample_map(self):
        return {'schemaVersion':1,'artifact':'domain_context_map','system':{'id':'SYSTEM-DEMO','name':'Demo'},'domains':[{'id':'DOMAIN-SALES','name':'Sales'}],'boundedContexts':[{'id':'BC-ORDER','name':'Order','domainRef':'DOMAIN-SALES'},{'id':'BC-FULFILL','name':'Fulfill','domainRef':'DOMAIN-SALES'}],'relationships':[{'id':'CTXREL-ORDER-FULFILL','fromContext':'BC-ORDER','toContext':'BC-FULFILL','type':'PROVIDES_TO','rationale':'order feeds fulfillment'}],'translations':[{'id':'CTXMAP-ORDER-FULFILL','fromContext':'BC-ORDER','fromTerm':'TERM-ORDER','toContext':'BC-FULFILL','toTerm':'TERM-FULFILLMENT-ORDER','rationale':'explicit ACL translation'}]}
    def sample_bm(self):
        return {'schemaVersion':1,'artifact':'business_model','module':{'code':'ORD','name':'Order','domainRef':'DOMAIN-SALES','boundedContextRef':'BC-ORDER'},'revision':'BM-R01','baseRevision':'BM-R00','terms':[{'id':'TERM-ORDER','name':'Order','definition':'order','contextRef':'BC-ORDER'},{'id':'TERM-FULFILLMENT-ORDER','name':'Order','definition':'fulfillment order','contextRef':'BC-FULFILL'}],'scenarios':[],'entities':[],'valueObjects':[],'aggregates':[{'id':'AGG-ORDER','name':'Order','root':'Order','members':[],'invariantIds':['INV-ORDER'],'transactionBoundary':'one order','traceIds':['TR-X'],'contextRef':'BC-ORDER'}],'invariants':[{'id':'INV-ORDER','statement':'valid','trigger':'submit','failure':'reject','traceIds':['TR-X'],'termRefs':['TERM-ORDER'],'appliesToRefs':['AGG-ORDER'],'contextRef':'BC-ORDER'}],'stateMachines':[],'services':[],'policies':[],'events':[],'businessErrors':[],'traceability':[]}
    def test_valid_context_map_and_business_model(self):
        out=domain_governance.validate_map(self.sample_map(),self.sample_bm()); self.assertEqual('PASSED',out['status'])
    def test_bad_context_ref_fails(self):
        bm=self.sample_bm(); bm['aggregates'][0]['contextRef']='BC-MISSING'
        out=domain_governance.validate_map(self.sample_map(),bm); self.assertEqual('FAILED',out['status'])
    def test_governance_is_optional(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(domain_governance.locate(Path(td)))
if __name__=='__main__': unittest.main()
