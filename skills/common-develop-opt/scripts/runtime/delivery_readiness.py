#!/usr/bin/env python3
"""Generic capability-driven Delivery Readiness planner/evaluator.

The public orchestration entry point is ``wk delivery``; this module is its
shared implementation library and does not define a second command surface.
"""
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
from typing import Any
from runtime import project_environment
from resource_paths import asset_path

CONTRACT=asset_path('assets/package/delivery-readiness-contract.json')
CANONICAL_ENTRYPOINT = 'wk delivery'

def plan(project_root:Path,version:str='')->dict[str,Any]:
    env=project_environment.project_capabilities(project_root,version=version)
    if env.get('status')=='BLOCKED':
        return {'schemaVersion':1,'authority':'DERIVED','kind':'delivery_readiness_plan','status':'BLOCKED','gates':[],'applicabilityDecisions':[],'blockers':['project capability declarations conflict']}
    gates=[]; decisions=[]; blockers=[]
    for name,cap in sorted((env.get('capabilities') or {}).items()):
        if not isinstance(cap,dict): continue
        gate=cap.get('deliveryGate')
        if not isinstance(gate,dict): continue
        gate_id=str(gate.get('gateId') or name)
        required=bool(gate.get('requiredForCompletion',False))
        available=cap.get('available') is True
        if not available:
            decisions.append({'gateId':gate_id,'capabilityRef':name,'applicable':False,'reason':'CAPABILITY_NOT_AVAILABLE','requiredForCompletion':required})
            if required: blockers.append(f'required delivery capability unavailable: {name}')
            continue
        decisions.append({'gateId':gate_id,'capabilityRef':name,'applicable':True,'reason':'DECLARED_AVAILABLE_CAPABILITY','requiredForCompletion':required})
        gates.append({'gateId':gate_id,'capabilityRef':name,'requiredForCompletion':required,'evidencePolicy':str(gate.get('evidencePolicy') or 'EXPLICIT_EVIDENCE_REFS'),'failureSeverity':str(gate.get('failureSeverity') or 'P1'),'verifier':str(gate.get('verifier') or cap.get('adapterCommand') or cap.get('command') or 'PROJECT_DEFINED')})
    return {'schemaVersion':1,'authority':'DERIVED','kind':'delivery_readiness_plan','status':'BLOCKED' if blockers else 'PASSED','projectRoot':str(project_root.resolve()),'version':version,'gates':gates,'applicabilityDecisions':decisions,'blockers':blockers,'canonicalEntrypoint':CANONICAL_ENTRYPOINT}

def evaluate(plan_value:dict[str,Any],results:dict[str,Any]|None)->dict[str,Any]:
    result_map=(results or {}).get('results') if isinstance((results or {}).get('results'),dict) else {}
    checks=[]; blockers=[]; pending=[]
    for gate in plan_value.get('gates',[]) or []:
        gid=gate['gateId']; actual=result_map.get(gid) if isinstance(result_map.get(gid),dict) else None
        if actual is None:
            status='MISSING'
            if gate.get('requiredForCompletion'): blockers.append(f'required delivery gate missing result: {gid}')
            else: pending.append(gid)
            evidence=[]
        else:
            status=str(actual.get('status') or 'UNKNOWN').upper(); evidence=list(actual.get('evidenceRefs') or [])
            if status=='PASSED' and not evidence: blockers.append(f'delivery gate {gid} PASSED without Evidence refs')
            if gate.get('requiredForCompletion') and status!='PASSED': blockers.append(f'required delivery gate not PASSED: {gid}={status}')
        checks.append({'gateId':gid,'status':status,'requiredForCompletion':bool(gate.get('requiredForCompletion')),'evidenceRefs':evidence})
    blockers=list(dict.fromkeys(list(plan_value.get('blockers') or [])+blockers))
    status='BLOCKED' if blockers else 'PASSED'
    return {'schemaVersion':1,'authority':'DERIVED','kind':'delivery_readiness_evaluation','status':status,'checks':checks,'applicabilityDecisions':list(plan_value.get('applicabilityDecisions') or []),'blockers':blockers,'pendingOptionalGates':pending,'canonicalEntrypoint':CANONICAL_ENTRYPOINT}

def main(argv=None):
    ap=argparse.ArgumentParser(); sub=ap.add_subparsers(dest='cmd',required=True)
    p=sub.add_parser('plan'); p.add_argument('--project-root',required=True); p.add_argument('--version',default=''); p.add_argument('--output',default=''); p.add_argument('--json',action='store_true')
    v=sub.add_parser('verify'); v.add_argument('--plan',required=True); v.add_argument('--results',default=''); v.add_argument('--json',action='store_true')
    a=ap.parse_args(argv)
    try:
        if a.cmd=='plan': out=plan(Path(a.project_root),a.version)
        else:
            pv=json.loads(Path(a.plan).read_text()); rv=json.loads(Path(a.results).read_text()) if a.results else {}; out=evaluate(pv,rv)
        if getattr(a,'output',''): Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps(out,ensure_ascii=False,indent=2) if getattr(a,'json',False) or not getattr(a,'output','') else out['status'])
        return 2 if out['status']=='BLOCKED' else 0
    except Exception as exc: print(f'ERROR: {exc}',file=sys.stderr); return 1
if __name__=='__main__': raise SystemExit(main())
