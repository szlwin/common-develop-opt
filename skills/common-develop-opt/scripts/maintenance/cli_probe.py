from __future__ import annotations
import argparse, os, sys, tempfile
from pathlib import Path
from inspection_io import base, dump, run, rel
from diagnostic_io import project_path

DEFAULTS=['scripts/wk.py','scripts/check_skill.py','scripts/reference_resolution.py','scripts/business_flow.py']

def main():
    ap=argparse.ArgumentParser(description='Probe read-only CLI help paths without project data or lifecycle writes')
    ap.add_argument('--root', required=True); ap.add_argument('--output', required=True); ap.add_argument('--script', action='append', dest='scripts')
    args=ap.parse_args(); root=Path(args.root).resolve(); probes=[]
    with tempfile.TemporaryDirectory(prefix='common-develop-opt-behavior-') as cwd:
        for value in args.scripts or DEFAULTS:
            try:
                path=project_path(value,root)
            except ValueError as exc:
                probes.append({'script':value,'status':'FAILED','reason':str(exc)})
                continue
            if not path.is_file():
                probes.append({"script":value,"status":"UNKNOWN","reason":"script not found"}); continue
            env=os.environ.copy(); env['PYTHONDONTWRITEBYTECODE']='1'
            result=run([sys.executable,str(path),'--help'],cwd=cwd,env=env)
            result.update({"script":value,"status":"PASSED" if result['returnCode']==0 else "FAILED"})
            probes.append(result)
    status='PASSED' if all(p['status']=='PASSED' for p in probes) else ('UNKNOWN' if any(p['status']=='UNKNOWN' for p in probes) else 'FAILED')
    dump(base(root,status,probes=probes,scope='help-only; no lifecycle, project, install or git mutation'),args.output,[root])
if __name__=='__main__': main()
