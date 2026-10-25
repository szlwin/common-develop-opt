#!/usr/bin/env python3
"""Build/query a derived Engineering Knowledge Graph over existing authority artifacts."""
from __future__ import annotations
import argparse,collections,json,sys
from pathlib import Path
from typing import Any
if __package__:
    from . import simple_yaml
else:
    from document_core import simple_yaml

BM_TYPES={'terms':'TERM','entities':'ENTITY','valueObjects':'VALUE_OBJECT','aggregates':'AGGREGATE','invariants':'INVARIANT','stateMachines':'STATE_MACHINE','services':'DOMAIN_SERVICE','policies':'BUSINESS_RULE','events':'EVENT'}
PROPAGATION={'CONTAINS':'BOUNDARY','OWNS':'BOUNDARY','CONSUMES':'BOUNDARY','TRANSLATES_TO':'BOUNDARY','PROTECTED_BY':'DEFAULT','USES_TERM':'DEFAULT','IMPLEMENTED_BY':'DEFAULT','VERIFIED_BY':'DEFAULT','AFFECTS':'DEFAULT','TRIGGERS':'DEFAULT','DEPENDS_ON':'CONDITIONAL','PROVIDES_TO':'BOUNDARY','CONSUMES_FROM':'BOUNDARY','COLLABORATES_WITH':'BOUNDARY','UPSTREAM_OF':'BOUNDARY','DOWNSTREAM_OF':'BOUNDARY'}

def load(path:Path|None)->dict[str,Any]|None:
    if not path: return None
    text=path.read_text(encoding='utf-8'); data=json.loads(text) if path.suffix=='.json' else simple_yaml.loads(text)
    if not isinstance(data,dict): raise ValueError(f'{path}: mapping required')
    return data

def _rel_id(prefix:str,*parts:str)->str: return prefix+'-'+'-'.join(str(p).replace('_','-') for p in parts)

def build(domain_map=None,business_model=None,dependency_impact=None,artifact_relations=None)->dict[str,Any]:
    nodes={}; rels={}
    def node(i,t,n,src,ctx=''):
        if not i:return
        v={'id':i,'type':t,'name':n or i,'sourceRefs':[src] if src else []}
        if ctx:v['contextRef']=ctx
        nodes[i]=v if i not in nodes else {**nodes[i],**{k:v[k] for k in v if k!='sourceRefs'},'sourceRefs':sorted(set(nodes[i].get('sourceRefs',[])+v['sourceRefs']))}
    def rel(i,f,t,ty,reason,src=''):
        if not f or not t:return
        rels[i]={'id':i,'from':f,'to':t,'type':ty,'reason':reason,'sourceRefs':[src] if src else [],'propagation':PROPAGATION.get(ty,'CONDITIONAL')}
    if domain_map:
        sysv=domain_map.get('system') or {}; node(sysv.get('id'),'SYSTEM',sysv.get('name'),'domain_context_map.yaml')
        for d in domain_map.get('domains',[]) or []:
            node(d.get('id'),'DOMAIN',d.get('name'),'domain_context_map.yaml'); rel(_rel_id('REL-CONTAINS',sysv.get('id','SYSTEM'),d.get('id','')),sysv.get('id'),d.get('id'),'CONTAINS','system contains domain','domain_context_map.yaml')
        for bc in domain_map.get('boundedContexts',[]) or []:
            node(bc.get('id'),'BOUNDED_CONTEXT',bc.get('name'),'domain_context_map.yaml',bc.get('id','')); rel(_rel_id('REL-CONTAINS',bc.get('domainRef',''),bc.get('id','')),bc.get('domainRef'),bc.get('id'),'CONTAINS','domain contains bounded context','domain_context_map.yaml')
        for r in domain_map.get('relationships',[]) or []:
            rel(r.get('id'),r.get('fromContext'),r.get('toContext'),r.get('type'),r.get('rationale','context relationship'),'domain_context_map.yaml')
        for tr in domain_map.get('translations',[]) or []:
            rel(tr.get('id'),tr.get('fromTerm'),tr.get('toTerm'),'TRANSLATES_TO',tr.get('rationale','context translation'),'domain_context_map.yaml')
    if business_model:
        module_ctx=(business_model.get('module') or {}).get('boundedContextRef','')
        for coll,typ in BM_TYPES.items():
            for item in business_model.get(coll,[]) or []:
                if not isinstance(item,dict):continue
                i=item.get('id'); ctx=item.get('contextRef') or module_ctx; node(i,typ,item.get('name') or item.get('statement') or i,'business_model.yaml',ctx)
                if ctx: rel(_rel_id('REL-OWNS',ctx,i),ctx,i,'OWNS','bounded context owns semantic fact','business_model.yaml')
                if coll=='aggregates':
                    for inv in item.get('invariantIds',[]) or []: rel(_rel_id('REL-PROTECTED',i,inv),i,inv,'PROTECTED_BY','aggregate protected by invariant','business_model.yaml')
                if coll=='invariants':
                    for term in item.get('termRefs',[]) or []: rel(_rel_id('REL-USES-TERM',i,term),i,term,'USES_TERM','invariant uses ubiquitous-language term','business_model.yaml')
                    for target in item.get('appliesToRefs',[]) or []: rel(_rel_id('REL-AFFECTS',i,target),i,target,'AFFECTS','invariant applies to semantic target','business_model.yaml')
    if dependency_impact:
        for n in dependency_impact.get('nodes',[]) or []: node(n.get('id'),n.get('type'),n.get('name'), 'dependency_impact.yaml')
        for r in dependency_impact.get('relationships',[]) or []: rel(r.get('id'),r.get('from'),r.get('to'),r.get('type'),r.get('rationale','dependency relationship'),'dependency_impact.yaml')
    if artifact_relations:
        kind_types={
            'requirement':'REQUIREMENT','feature':'FEATURE','page':'PAGE','page_action':'PAGE_ACTION',
            'design':'DESIGN','business_flow':'PROCESS','flow_step':'PROCESS_STEP',
            'business_model':'BUSINESS_MODEL','cross_module':'CROSS_MODULE',
            'validation':'TEST_CASE','module':'MODULE',
        }
        for n in artifact_relations.get('nodes',[]) or []:
            if not isinstance(n,dict): continue
            refs=n.get('sourceRefs') or []
            node(n.get('id'),kind_types.get(n.get('kind'),str(n.get('kind') or 'ARTIFACT').upper()),n.get('name'),str(refs[0]) if refs else n.get('documentRef',''))
        for r in artifact_relations.get('relationships',[]) or []:
            if not isinstance(r,dict): continue
            refs=r.get('sourceRefs') or []
            rel(r.get('id'),r.get('from'),r.get('to'),r.get('type'),f"derived from {', '.join(str(x) for x in refs)}",str(refs[0]) if refs else 'artifact_relations')
    return {'schemaVersion':1,'authority':'DERIVED','kind':'engineering_knowledge_graph','nodes':[nodes[k] for k in sorted(nodes)],'relationships':[rels[k] for k in sorted(rels)]}

def validate(g):
    ids={n['id'] for n in g.get('nodes',[])}; errors=[]
    for r in g.get('relationships',[]):
        if r['from'] not in ids: errors.append(f"{r['id']}: unknown from {r['from']}")
        if r['to'] not in ids: errors.append(f"{r['id']}: unknown to {r['to']}")
    return {'status':'FAILED' if errors else 'PASSED','errors':errors,'nodeCount':len(ids),'relationshipCount':len(g.get('relationships',[]))}

def neighbors(g,ref):
    return sorted([r for r in g.get('relationships',[]) if r['from']==ref or r['to']==ref],key=lambda r:r['id'])


def owners(g,ref):
    return sorted([r for r in g.get('relationships',[]) if r.get('type')=='OWNS' and r.get('to')==ref],key=lambda r:r['id'])

def consumers(g,ref):
    relation_types={'CONSUMES','CONSUMES_FROM','PROVIDES_TO'}
    return sorted([r for r in g.get('relationships',[]) if r.get('type') in relation_types and (r.get('from')==ref or r.get('to')==ref)],key=lambda r:r['id'])

def path(g,start,end):
    adj=collections.defaultdict(list)
    for r in g.get('relationships',[]): adj[r['from']].append((r['to'],r));
    q=collections.deque([(start,[])]); seen={start}
    while q:
        cur,p=q.popleft()
        if cur==end:return p
        for nxt,r in sorted(adj[cur],key=lambda x:x[1]['id']):
            if nxt not in seen: seen.add(nxt); q.append((nxt,p+[r]))
    return []

def impact(g,changed,max_depth=8):
    outgoing=collections.defaultdict(list)
    for r in g.get('relationships',[]): outgoing[r['from']].append(r)
    impacted={x:{'ref':x,'depth':0,'reasonPath':[]} for x in changed}; q=collections.deque((x,0,[]) for x in changed)
    while q:
        cur,depth,path0=q.popleft()
        if depth>=max_depth: continue
        for r in sorted(outgoing[cur],key=lambda x:x['id']):
            if r.get('propagation')=='NO_PROPAGATION': continue
            nxt=r['to']; np=path0+[r['id']]
            if nxt not in impacted or depth+1<impacted[nxt]['depth']:
                impacted[nxt]={'ref':nxt,'depth':depth+1,'reasonPath':np}; q.append((nxt,depth+1,np))
    return [impacted[k] for k in sorted(impacted)]


def main(argv=None):
    ap=argparse.ArgumentParser(); sub=ap.add_subparsers(dest='cmd',required=True)
    b=sub.add_parser('build'); b.add_argument('--domain-map',default=''); b.add_argument('--business-model',default=''); b.add_argument('--dependency-impact',default=''); b.add_argument('--artifact-relations',default=''); b.add_argument('--output',default=''); b.add_argument('--json',action='store_true')
    for cmd in ('validate','neighbors','owners','consumers','path','impact'):
        p=sub.add_parser(cmd); p.add_argument('--graph',required=True); p.add_argument('--json',action='store_true')
        if cmd in {'neighbors','owners','consumers'}: p.add_argument('--ref',required=True)
        if cmd=='path': p.add_argument('--from-ref',required=True); p.add_argument('--to-ref',required=True)
        if cmd=='impact': p.add_argument('--changed-ref',action='append',required=True); p.add_argument('--max-depth',type=int,default=8)
    a=ap.parse_args(argv)
    try:
        if a.cmd=='build':
            g=build(load(Path(a.domain_map)) if a.domain_map else None,load(Path(a.business_model)) if a.business_model else None,load(Path(a.dependency_impact)) if a.dependency_impact else None,load(Path(a.artifact_relations)) if a.artifact_relations else None)
            if a.output: Path(a.output).write_text(json.dumps(g,ensure_ascii=False,indent=2)+'\n')
            out=g
        else:
            g=json.loads(Path(a.graph).read_text())
            out=validate(g) if a.cmd=='validate' else neighbors(g,a.ref) if a.cmd=='neighbors' else owners(g,a.ref) if a.cmd=='owners' else consumers(g,a.ref) if a.cmd=='consumers' else path(g,a.from_ref,a.to_ref) if a.cmd=='path' else impact(g,a.changed_ref,a.max_depth)
        print(json.dumps(out,ensure_ascii=False,indent=2) if getattr(a,'json',False) or isinstance(out,(dict,list)) else out); return 0 if not isinstance(out,dict) or out.get('status')!='FAILED' else 2
    except Exception as exc: print(f'ERROR: {exc}',file=sys.stderr); return 1
if __name__=='__main__': raise SystemExit(main())
