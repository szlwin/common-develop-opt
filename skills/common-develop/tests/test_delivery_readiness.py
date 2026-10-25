import tempfile,unittest,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import delivery_readiness

class DeliveryReadinessTests(unittest.TestCase):
 def write_env(self,root,body):
  Path(root,'project_env.md').write_text('```yaml common-develop-capabilities\n'+body+'\n```\n')
 def test_no_capability_gate_is_empty_pass(self):
  with tempfile.TemporaryDirectory() as td:
   self.write_env(td,'capabilities:\n  lint:\n    available: true\n    command: lint')
   p=delivery_readiness.plan(Path(td)); self.assertEqual('PASSED',p['status']); self.assertEqual([],p['gates']); self.assertEqual('wk delivery',p['canonicalEntrypoint'])
 def test_arbitrary_required_gate(self):
  with tempfile.TemporaryDirectory() as td:
   self.write_env(td,'capabilities:\n  artifactIntegrity:\n    available: true\n    deliveryGate:\n      gateId: integrity\n      requiredForCompletion: true\n      evidencePolicy: EXPLICIT_EVIDENCE_REFS')
   p=delivery_readiness.plan(Path(td)); self.assertEqual('integrity',p['gates'][0]['gateId'])
   bad=delivery_readiness.evaluate(p,{}); self.assertEqual('BLOCKED',bad['status'])
   ok=delivery_readiness.evaluate(p,{'results':{'integrity':{'status':'PASSED','evidenceRefs':['EVD-1']}}}); self.assertEqual('PASSED',ok['status']); self.assertEqual('wk delivery',ok['canonicalEntrypoint'])
 def test_unavailable_optional_gate_is_applicability_only(self):
  with tempfile.TemporaryDirectory() as td:
   self.write_env(td,'capabilities:\n  x:\n    available: false\n    deliveryGate:\n      gateId: x\n      requiredForCompletion: false')
   p=delivery_readiness.plan(Path(td)); self.assertEqual([],p['gates']); self.assertFalse(p['applicabilityDecisions'][0]['applicable'])
if __name__=='__main__': unittest.main()

class DeliveryWKParserTests(unittest.TestCase):
 def test_wk_delivery_is_generic_orchestration_command(self):
  import wk
  args=wk.build_parser().parse_args(['delivery','-g','ProjectManagerAgent','--action','plan','--project-root','.'])
  self.assertEqual('plan',args.action)
