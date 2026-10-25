#!/usr/bin/env python3
"""Optional, project-agnostic Domain / Bounded Context governance validator."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
from typing import Any
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "compat"))
from document_core import simple_yaml
from resource_paths import asset_path
try:
    import jsonschema
except ImportError:  # pragma: no cover
    jsonschema=None

ROOT=Path(__file__).resolve().parents[2]
SCHEMA=asset_path('assets/documents/structured-docs/domain-context-map.schema.json')
BM_SCHEMA=asset_path('assets/documents/structured-docs/business-model.schema.json')

class DomainGovernanceError(ValueError): pass

def _load(path: Path)->dict[str,Any]:
    if not path.is_file(): raise DomainGovernanceError(f'missing file: {path}')
    text=path.read_text(encoding='utf-8')
    try: data=json.loads(text) if path.suffix.lower()=='.json' else simple_yaml.loads(text)
    except Exception as exc: raise DomainGovernanceError(f'{path}: invalid structured content: {exc}') from exc
    if not isinstance(data,dict): raise DomainGovernanceError(f'{path}: root must be mapping')
    return data

def _validate_schema(data:dict[str,Any], schema_path:Path)->None:
    if jsonschema is None: raise DomainGovernanceError('jsonschema is required')
    schema=json.loads(schema_path.read_text(encoding='utf-8'))
    try: jsonschema.Draft202012Validator(schema).validate(data)
    except jsonschema.ValidationError as exc: raise DomainGovernanceError(f'schema validation failed: {exc.message}') from exc

def locate(project_root:Path)->Path|None:
    roots=[project_root]
    if project_root.name!='project_doc': roots.append(project_root/'project_doc')
    for root in roots:
        p=root/'docs/_domain/domain_context_map.yaml'
        if p.is_file(): return p
    return None

def validate_map(domain_map:dict[str,Any], business_model:dict[str,Any]|None=None)->dict[str,Any]:
    _validate_schema(domain_map,SCHEMA)
    errors=[]; warnings=[]
    domain_items=[x for x in domain_map.get('domains',[]) if isinstance(x,dict)]
    context_items=[x for x in domain_map.get('boundedContexts',[]) if isinstance(x,dict)]
    domains={x['id'] for x in domain_items}; contexts={x['id'] for x in context_items}
    if len(domains) != len(domain_items): errors.append('duplicate domain id')
    if len(contexts) != len(context_items): errors.append('duplicate bounded context id')
    module_owner={}
    for bc in context_items:
        for module in bc.get('moduleRefs',[]) or []:
            if module in module_owner and module_owner[module] != bc.get('id'):
                errors.append(f'module {module} is owned by multiple bounded contexts: {module_owner[module]}, {bc.get("id")}')
            module_owner[module]=bc.get('id')
    for bc in domain_map.get('boundedContexts',[]):
        if bc.get('domainRef') not in domains: errors.append(f"bounded context {bc.get('id')} references unknown domain {bc.get('domainRef')}")
    for rel in domain_map.get('relationships',[]):
        for k in ('fromContext','toContext'):
            if rel.get(k) not in contexts: errors.append(f"relationship {rel.get('id')} references unknown context {rel.get(k)}")
    if business_model is not None:
        _validate_schema(business_model,BM_SCHEMA)
        terms=[t for t in business_model.get('terms',[]) if isinstance(t,dict)]
        term_context={t.get('id'):t.get('contextRef') for t in terms}
        term_keys={}
        for term in terms:
            key=(str(term.get('contextRef') or ''), str(term.get('name') or ''))
            if key in term_keys and term_keys[key] != term.get('definition'):
                errors.append(f'conflicting ubiquitous-language term in context {key[0]}: {key[1]}')
            term_keys[key]=term.get('definition')
        for coll in ('terms','entities','valueObjects','aggregates','invariants','stateMachines','services','policies','events','businessErrors'):
            for item in business_model.get(coll,[]) or []:
                if isinstance(item,dict) and item.get('contextRef') and item['contextRef'] not in contexts:
                    errors.append(f"{item.get('id')} references unknown context {item.get('contextRef')}")
        for inv in business_model.get('invariants',[]) or []:
            for term in inv.get('termRefs',[]) or []:
                if term not in term_context: errors.append(f"invariant {inv.get('id')} references unknown term {term}")
            for term in inv.get('termRefs',[]) or []:
                if inv.get('contextRef') and term_context.get(term) and inv['contextRef']!=term_context[term]:
                    warnings.append(f"invariant {inv.get('id')} uses cross-context term {term}; explicit translation should exist")
        translations={(x.get('fromContext'),x.get('fromTerm'),x.get('toContext'),x.get('toTerm')) for x in domain_map.get('translations',[]) if isinstance(x,dict)}
        for tr in domain_map.get('translations',[]):
            if tr.get('fromContext') not in contexts or tr.get('toContext') not in contexts: continue
            if tr.get('fromTerm') not in term_context: errors.append(f"translation {tr.get('id')} references unknown fromTerm {tr.get('fromTerm')}")
            elif term_context.get(tr.get('fromTerm')) and term_context[tr.get('fromTerm')]!=tr.get('fromContext'): errors.append(f"translation {tr.get('id')} fromTerm context mismatch")
            if tr.get('toTerm') not in term_context: errors.append(f"translation {tr.get('id')} references unknown toTerm {tr.get('toTerm')}")
            elif term_context.get(tr.get('toTerm')) and term_context[tr.get('toTerm')]!=tr.get('toContext'): errors.append(f"translation {tr.get('id')} toTerm context mismatch")
    return {'status':'FAILED' if errors else 'PASSED','errors':errors,'warnings':warnings,'domains':len(domains),'boundedContexts':len(contexts),'governanceActive':True}

def main(argv=None)->int:
    ap=argparse.ArgumentParser(); sub=ap.add_subparsers(dest='command',required=True)
    v=sub.add_parser('validate'); v.add_argument('--project-root',default='.'); v.add_argument('--domain-map',default=''); v.add_argument('--business-model',default=''); v.add_argument('--json',action='store_true')
    args=ap.parse_args(argv)
    try:
        p=Path(args.domain_map) if args.domain_map else locate(Path(args.project_root).resolve())
        if p is None:
            out={'status':'PASSED','governanceActive':False,'reason':'DOMAIN_GOVERNANCE_NOT_DECLARED'}
        else:
            bm=_load(Path(args.business_model)) if args.business_model else None
            out=validate_map(_load(p),bm); out['path']=str(p)
        print(json.dumps(out,ensure_ascii=False,indent=2) if args.json else f"DOMAIN {out['status']} active={out.get('governanceActive')}")
        return 0 if out['status']=='PASSED' else 2
    except (DomainGovernanceError,OSError,ValueError) as exc:
        print(f'ERROR: {exc}',file=sys.stderr); return 1
if __name__=='__main__': raise SystemExit(main())
