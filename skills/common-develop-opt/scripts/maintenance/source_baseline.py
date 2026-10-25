from __future__ import annotations
import argparse, platform, sys
from pathlib import Path
from inspection_io import *

def main():
    ap=argparse.ArgumentParser(description='Create a read-only, reproducible Skill source baseline')
    ap.add_argument('--root', required=True); ap.add_argument('--output', required=True)
    args=ap.parse_args(); root=Path(args.root).resolve()
    if not (root/'SKILL.md').is_file(): raise SystemExit('root must contain SKILL.md')
    rows=[{"path":rel(root,p),"size":p.stat().st_size,"sha256":digest(p)} for p in files(root)]
    git=run(['git','status','--short'], cwd=root.parent)
    rev=run(['git','rev-parse','HEAD'], cwd=root.parent)
    result=base(root,'PASSED', baseline={"fileCount":len(rows),"files":rows,"gitHead":rev['stdout'].strip() if rev['returnCode']==0 else None,"gitStatus":git['stdout'].splitlines()}, environment={"python":sys.version,"platform":platform.platform()}, mutationPolicy={"writesRoot":False,"writesProject":False,"writesIndex":False})
    dump(result,args.output,[root])
if __name__=='__main__': main()
