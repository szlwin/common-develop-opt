"""Controlled read-only runtime import probe for the package entry points."""
from __future__ import annotations
import argparse, importlib, json, os, subprocess, sys
from pathlib import Path


def probe(root):
    modules=['artifact_identity','reference_resolver','dependency_graph','discover_dependencies','impact_adapter','publish_candidate','review_submit','document_core.evidence_validity','application_services']
    code='import json, importlib, builtins; names='+repr(modules)+'; out=[]\n_orig_open=builtins.open\ndef guarded_open(file, mode="r", *a, **kw):\n\n if any(x in mode for x in ("w","a","x","+") ): raise RuntimeError("WRITE_BLOCKED_BY_PROBE")\n return _orig_open(file, mode, *a, **kw)\nbuiltins.open=guarded_open\nfor n in names:\n\n try:\n  m=importlib.import_module(n); out.append({"module":n,"origin":getattr(m,"__file__","")})\n except Exception as e: out.append({"module":n,"error":type(e).__name__+":"+str(e)})\nprint(json.dumps(out))'
    env=dict(os.environ); env['PYTHONPATH']=str(Path(root)/'scripts')
    run=subprocess.run([sys.executable,'-B','-c',code],env=env,capture_output=True,text=True)
    try: entries=json.loads(run.stdout)
    except Exception: entries=[]
    clean = run.returncode == 0 and all('origin' in x for x in entries)
    return {'schemaVersion':1,'authority':'OBSERVATION_ONLY','status':'PASSED' if clean else 'UNKNOWN','entries':entries,'writesExecuted':False,'writePolicy':'DENY_DURING_IMPORT'}


if __name__=='__main__':
    ap=argparse.ArgumentParser(); ap.add_argument('--root',required=True); ap.add_argument('--output',required=True); a=ap.parse_args()
    Path(a.output).write_text(json.dumps(probe(Path(a.root).resolve()),ensure_ascii=False,indent=2)+'\n')
