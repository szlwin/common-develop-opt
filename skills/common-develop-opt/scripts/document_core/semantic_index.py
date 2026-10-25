#!/usr/bin/env python3
"""Build a deterministic, rebuildable domain semantic index from authority artifacts."""
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
from typing import Any
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "compat"))
from document_core import simple_yaml

COLLECTIONS=['terms','entities','valueObjects','aggregates','invariants','stateMachines','services','policies','events']

def load(path:Path)->dict[str,Any]:
    text=path.read_text(encoding='utf-8'); data=json.loads(text) if path.suffix=='.json' else simple_yaml.loads(text)
    if not isinstance(data,dict): raise ValueError(f'{path}: mapping required')
    return data

def build(domain_map:dict[str,Any]|None,bm:dict[str,Any])->dict[str,Any]:
    contexts={}
    if domain_map:
        for bc in domain_map.get('boundedContexts',[]) or []:
            contexts[bc['id']]={k:[] for k in COLLECTIONS}
    term_usage={}; invariant_usage={}
    for coll in COLLECTIONS:
        for item in bm.get(coll,[]) or []:
            if not isinstance(item,dict) or not item.get('id'): continue
            ctx=item.get('contextRef') or (bm.get('module') or {}).get('boundedContextRef')
            if ctx:
                contexts.setdefault(ctx,{k:[] for k in COLLECTIONS})[coll].append(item['id'])
            if coll=='invariants':
                for term in item.get('termRefs',[]) or []: term_usage.setdefault(term,[]).append(item['id'])
                invariant_usage.setdefault(item['id'],[]).extend(item.get('appliesToRefs',[]) or [])
            if coll=='aggregates':
                for inv in item.get('invariantIds',[]) or []: invariant_usage.setdefault(inv,[]).append(item['id'])
    for mapping in (contexts,):
        for _,v in mapping.items():
            for k in COLLECTIONS: v[k]=sorted(set(v[k]))
    term_usage={k:sorted(set(v)) for k,v in sorted(term_usage.items())}
    invariant_usage={k:sorted(set(v)) for k,v in sorted(invariant_usage.items())}
    return {'schemaVersion':1,'authority':'DERIVED','kind':'domain_semantic_index','contexts':dict(sorted(contexts.items())),'termUsage':term_usage,'invariantUsage':invariant_usage}

def main(argv=None):
    ap=argparse.ArgumentParser(); ap.add_argument('--business-model',required=True); ap.add_argument('--domain-map',default=''); ap.add_argument('--output',default=''); ap.add_argument('--json',action='store_true'); a=ap.parse_args(argv)
    try:
        out=build(load(Path(a.domain_map)) if a.domain_map else None,load(Path(a.business_model)))
        if a.output: Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(out,ensure_ascii=False,indent=2) if a.json or not a.output else f"WROTE {a.output}")
        return 0
    except Exception as exc: print(f'ERROR: {exc}',file=sys.stderr); return 1
if __name__=='__main__': raise SystemExit(main())
