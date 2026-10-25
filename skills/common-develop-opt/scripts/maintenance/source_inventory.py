from __future__ import annotations
import argparse, ast, os, subprocess, sys, json
from pathlib import Path
from inspection_io import *

def main():
    ap=argparse.ArgumentParser(description='Inventory CLI, imports and state-writing entry points')
    ap.add_argument('--root', required=True); ap.add_argument('--output', required=True)
    args=ap.parse_args(); root=Path(args.root).resolve(); scripts=root/'scripts'
    modules=[]; writes=[]; clis=[]
    for path in sorted(scripts.rglob('*.py')):
        try: tree=ast.parse(path.read_text(encoding='utf-8'))
        except SyntaxError as exc: modules.append({"path":rel(root,path),"parse":"FAILED","error":str(exc)}); continue
        imports=[]
        for node in ast.walk(tree):
            if isinstance(node,ast.Import): imports += [a.name for a in node.names]
            elif isinstance(node,ast.ImportFrom): imports.append('.'*node.level+(node.module or ''))
            elif isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.name.startswith('command_'): clis.append({"path":rel(root,path),"function":node.name,"line":node.lineno})
            elif isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr in {'write_text','write_bytes','unlink','replace','rename'}: writes.append({"path":rel(root,path),"line":node.lineno,"operation":node.func.attr})
        modules.append({"path":rel(root,path),"lines":len(path.read_text(encoding='utf-8').splitlines()),"imports":sorted(set(imports))})
    facilities=[]
    for term in ('engineering_graph','current_manifest','reference_resolution','task_events','manual_review','evidence','git_checkpoint'):
        hits=[]
        for p in files(root):
            if p.suffix in {'.py','.md','.json'} and term in p.read_text(errors='ignore'): hits.append(rel(root,p))
        facilities.append({"name":term,"references":hits[:100],"count":len(hits)})
    runtime = None
    probe = root/'scripts'/'maintenance'/'runtime_probe.py'
    if probe.is_file():
        run = subprocess.run([sys.executable, '-B', str(probe), '--root', str(root), '--output', str(root/'.runtime-probe.json')], capture_output=True, text=True)
        try: runtime=json.loads((root/'.runtime-probe.json').read_text()); (root/'.runtime-probe.json').unlink()
        except Exception: runtime={'status':'UNKNOWN','error':run.stderr or 'probe failed'}
    result=base(root,'PASSED',inventory={"modules":modules,"cliFunctions":clis,"writeCalls":writes,"facilities":facilities,"runtimeProbe":runtime,"counts":{"modules":len(modules),"cliFunctions":len(clis),"writeCalls":len(writes)},"evidenceScope":"STATIC_PLUS_CONTROLLED_RUNTIME_IMPORT","limitations":["command_* is not a complete CLI registry","writeCalls omit open/json.dump/shutil/subprocess and wrapper methods","runtime probe verifies imports only, not all execution paths or write authority","facility hits are text references, not proof of call paths or authority"]})
    dump(result,args.output,[root])
if __name__=='__main__': main()
