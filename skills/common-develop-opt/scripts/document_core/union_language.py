"""Project ubiquitous-language glossary, version deltas, reference validation and merge."""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from runtime import doc_layout

TERM_HEADING_RE = re.compile(r"^##\s+(UL-[A-Z0-9_-]+)\s+(.+?)\s*$")
TERM_ID_RE = re.compile(r"^UL-[A-Z0-9_-]+$")
TERM_STATUSES = {"ACTIVE", "DEPRECATED"}
CHANGE_TYPES = {"ADD", "MODIFY", "DEPRECATE"}
META_HEADERS = ["属性", "内容"]
CJK_RE = re.compile(r"[\u3400-\u9fff]")
BARE_TERM_RE = re.compile(r"(?<!!)\[([^\]\n]+)\](?!\()")


class UnionLanguageError(ValueError):
    pass


@dataclass
class Term:
    stable_id: str
    heading_name: str
    properties: dict[str, str]
    definition: str
    boundary: str
    avoid: list[str]
    related: list[str]
    examples: list[str]

    @property
    def name(self) -> str:
        return self.properties.get("统一名称", self.heading_name).strip()

    @property
    def aliases(self) -> list[str]:
        raw = self.properties.get("别名", "")
        return [item.strip() for item in re.split(r"[;,；，]", raw) if item.strip() and item.strip() not in {"-", "无"}]


def _split_row(line: str) -> list[str]:
    text = line.strip()
    if not (text.startswith("|") and text.endswith("|")):
        raise UnionLanguageError(f"invalid Markdown table row: {line}")
    return [item.strip().replace("<br>", "\n") for item in text[1:-1].split("|")]


def _metadata(block: list[str]) -> dict[str, str]:
    for i in range(1, len(block) - 1):
        if block[i].strip() == "| 属性 | 内容 |":
            rows: dict[str, str] = {}
            j = i + 2
            while j < len(block) and block[j].strip().startswith("|"):
                row = _split_row(block[j])
                if len(row) == 2 and row[0]: rows[row[0]] = row[1]
                j += 1
            return rows
    raise UnionLanguageError("term is missing 属性/内容 table")


def _subsection(block: list[str], heading: str) -> list[str]:
    marker = f"### {heading}"
    try: start = next(i for i,line in enumerate(block) if line.strip() == marker) + 1
    except StopIteration: return []
    end = len(block)
    for i in range(start, len(block)):
        if block[i].startswith("### ") or block[i].startswith("## "):
            end = i; break
    return block[start:end]


def _paragraph(block: list[str], heading: str) -> str:
    return "\n".join(line.strip() for line in _subsection(block, heading) if line.strip()).strip()


def _bullets(block: list[str], heading: str) -> list[str]:
    result=[]
    for line in _subsection(block, heading):
        s=line.strip()
        if s.startswith("- "): result.append(s[2:].strip())
    return result


def parse_terms(text: str, *, changes: bool = False) -> list[Term]:
    lines=text.splitlines(); starts=[]
    for i,line in enumerate(lines):
        if TERM_HEADING_RE.match(line.strip()): starts.append(i)
    terms=[]
    for pos,start in enumerate(starts):
        end=starts[pos+1] if pos+1<len(starts) else len(lines)
        block=lines[start:end]
        m=TERM_HEADING_RE.match(block[0].strip()); assert m
        sid,heading=m.groups(); props=_metadata(block)
        term=Term(sid,heading.strip(),props,_paragraph(block,"定义"),_paragraph(block,"业务边界"),_bullets(block,"不要混用"),_bullets(block,"相关术语"),_bullets(block,"使用示例"))
        validate_term(term, changes=changes)
        terms.append(term)
    ids=[t.stable_id for t in terms]
    if len(ids)!=len(set(ids)): raise UnionLanguageError("duplicate unified-language term ID")
    names=[t.name for t in terms]
    if len(names)!=len(set(names)): raise UnionLanguageError("UNION_LANGUAGE_CONFLICT duplicate 统一名称")
    return terms


def validate_term(term: Term, *, changes: bool) -> None:
    if not TERM_ID_RE.fullmatch(term.stable_id): raise UnionLanguageError(f"invalid term ID: {term.stable_id}")
    required=["统一名称","英文/代码名称","领域","Owner","首次引入","状态"]
    missing=[x for x in required if not term.properties.get(x)]
    if missing: raise UnionLanguageError(f"{term.stable_id} missing properties: {missing}")
    if term.properties["状态"] not in TERM_STATUSES: raise UnionLanguageError(f"invalid term status: {term.stable_id}")
    if not term.definition: raise UnionLanguageError(f"{term.stable_id} has no 定义")
    if changes:
        kind=term.properties.get("变更类型","")
        reason=term.properties.get("变更原因","")
        if kind not in CHANGE_TYPES: raise UnionLanguageError(f"{term.stable_id} invalid 变更类型: {kind}")
        if not reason or reason in {"-","无"}: raise UnionLanguageError(f"{term.stable_id} change has no 变更原因")


def _anchor(term_id: str, name: str) -> str:
    # Stable ID is authoritative; the name is intentionally not part of the anchor.
    return term_id.lower().replace("_","-")


def _render_term(term: Term, *, changes: bool) -> list[str]:
    props=dict(term.properties)
    order=["统一名称","英文/代码名称","领域","Owner","别名","首次引入","状态"]
    if changes: order += ["变更类型","变更原因","替代术语"]
    else: props.pop("变更类型",None); props.pop("变更原因",None); props.pop("替代术语",None)
    rows=[[key,props.get(key,"-")] for key in order if key in props or key in {"统一名称","英文/代码名称","领域","Owner","首次引入","状态"}]
    lines=[f'<a id="{_anchor(term.stable_id, term.name)}"></a>',"",f"## {term.stable_id} {term.name}","","| 属性 | 内容 |","| --- | --- |",*[f"| {a} | {b} |" for a,b in rows],"","### 定义","",term.definition or "-","","### 业务边界","",term.boundary or "-","","### 不要混用","",*([f"- {x}" for x in term.avoid] or ["- 无"]),"","### 相关术语","",*([f"- {x}" for x in term.related] or ["- 无"]),"","### 使用示例","",*([f"- {x}" for x in term.examples] or ["- 无"]),""]
    return lines


def _normalize_project_term_links(term: Term, lookup: dict[str, Term]) -> Term:
    clone=Term(term.stable_id,term.heading_name,dict(term.properties),term.definition,term.boundary,list(term.avoid),list(term.related),list(term.examples))
    normalized=[]
    for raw in clone.related:
        name=raw.strip()
        match=re.fullmatch(r"\[([^\]]+)\]",name)
        if match: name=match.group(1).strip()
        target=lookup.get(name)
        normalized.append(f"[{name}](#{_anchor(target.stable_id,target.name)})" if target else raw)
    clone.related=normalized
    return clone


def render_project(terms: Sequence[Term]) -> str:
    lines=["<!-- managed-by: common-develop/wk-d -->","# 项目统一语言","","> 本文定义跨需求、模块和角色共同使用的业务术语。正文中的裸 `[术语]` 引用必须能在本文唯一解析；模块内枚举不在这里定义。","","## 术语索引","","| 编号 | 统一名称 | 英文/代码名称 | 领域 | 简要定义 | Owner | 状态 |","| --- | --- | --- | --- | --- | --- | --- |"]
    lookup={}
    for t in terms:
        for name in [t.name,*t.aliases]: lookup[name]=t
        summary=" ".join(t.definition.split())[:80]
        lines.append(f"| {t.stable_id} | [{t.name}](#{_anchor(t.stable_id,t.name)}) | {t.properties.get('英文/代码名称','-')} | {t.properties.get('领域','-')} | {summary} | {t.properties.get('Owner','-')} | {t.properties.get('状态','ACTIVE')} |")
    if not terms: lines.append("| - | - | - | - | - | - | - |")
    lines.append("")
    for term in terms: lines.extend(_render_term(_normalize_project_term_links(term,lookup),changes=False))
    return "\n".join(lines).rstrip()+"\n"


def render_changes(version: str, terms: Sequence[Term]) -> str:
    lines=["<!-- template: common-develop/union-language-changes-v1 -->",f"# {version} 统一语言变更","","> 这里只记录本版本新增、修改或废弃的术语；项目当前全量定义由 `wk -d` 合并到 `docs/union/union_language.md`。","",]
    for term in terms: lines.extend(_render_term(term,changes=True))
    return "\n".join(lines).rstrip()+"\n"


def merge_terms(source: Sequence[Term], target: Sequence[Term]) -> list[Term]:
    merged={t.stable_id:Term(t.stable_id,t.heading_name,dict(t.properties),t.definition,t.boundary,list(t.avoid),list(t.related),list(t.examples)) for t in target}; order=[t.stable_id for t in target]
    def name_owner(name: str, exclude: str="") -> str|None:
        for sid,t in merged.items():
            if sid!=exclude and (t.name==name or name in t.aliases): return sid
        return None
    for src in source:
        kind=src.properties.get("变更类型")
        cur=merged.get(src.stable_id)
        if kind=="ADD":
            if cur is not None: raise UnionLanguageError(f"UNION_LANGUAGE_CONFLICT {src.stable_id} already exists")
            owner=name_owner(src.name)
            if owner: raise UnionLanguageError(f"UNION_LANGUAGE_CONFLICT term '{src.name}' already belongs to {owner}")
            props=dict(src.properties); props.pop("变更类型",None); props.pop("变更原因",None); props.pop("替代术语",None)
            merged[src.stable_id]=Term(src.stable_id,src.heading_name,props,src.definition,src.boundary,list(src.avoid),list(src.related),list(src.examples)); order.append(src.stable_id)
        elif kind in {"MODIFY","DEPRECATE"}:
            if cur is None: raise UnionLanguageError(f"UNION_LANGUAGE_CONFLICT {src.stable_id} does not exist")
            owner=name_owner(src.name, src.stable_id)
            if owner: raise UnionLanguageError(f"UNION_LANGUAGE_CONFLICT term '{src.name}' already belongs to {owner}")
            for key,val in src.properties.items():
                if key not in {"变更类型","变更原因","替代术语"} and val not in {"","-"}: cur.properties[key]=val
            cur.heading_name=src.heading_name or cur.heading_name
            if src.definition: cur.definition=src.definition
            if src.boundary and src.boundary!="-": cur.boundary=src.boundary
            if src.avoid and src.avoid!=["无"]: cur.avoid=list(src.avoid)
            if src.related and src.related!=["无"]: cur.related=list(src.related)
            if src.examples and src.examples!=["无"]: cur.examples=list(src.examples)
            if kind=="DEPRECATE":
                cur.properties["状态"]="DEPRECATED"
                replacement=src.properties.get("替代术语","")
                if replacement and replacement not in {"-","无"}: cur.properties["替代术语"]=replacement
        else: raise UnionLanguageError(f"invalid change type: {kind}")
    result=[merged[sid] for sid in order]
    # Global name/alias ambiguity is forbidden; use qualified terms instead.
    owners={}
    for t in result:
        for name in [t.name,*t.aliases]:
            if name in owners and owners[name]!=t.stable_id: raise UnionLanguageError(f"UNION_LANGUAGE_CONFLICT ambiguous term '{name}'")
            owners[name]=t.stable_id
    return result


def project_path(doc_root: Path) -> Path:
    return doc_layout.project_union_root(doc_root)/"union_language.md"


def changes_path(doc_root: Path, version: str) -> Path:
    return doc_layout.version_union_root(doc_root,version)/"union_language_changes.md"


def combined_terms(doc_root: Path, version: str="") -> list[Term]:
    base=parse_terms(project_path(doc_root).read_text(encoding="utf-8"),changes=False) if project_path(doc_root).is_file() else []
    if version and changes_path(doc_root,version).is_file():
        delta=parse_terms(changes_path(doc_root,version).read_text(encoding="utf-8"),changes=True)
        return merge_terms(delta,base)
    return base


def _strip_code(text: str) -> str:
    out=[]; fenced=False
    for line in text.splitlines():
        if line.strip().startswith("```"): fenced=not fenced; continue
        if not fenced: out.append(re.sub(r"`[^`]*`","",line))
    return "\n".join(out)


def bare_term_refs(text: str) -> list[str]:
    result=[]
    for match in BARE_TERM_RE.finditer(_strip_code(text)):
        term=match.group(1).strip()
        if term and CJK_RE.search(term) and term not in {" ","x","X"}: result.append(term)
    return result


def validate_references(paths: Iterable[Path], terms: Sequence[Term]) -> list[str]:
    lookup={}
    for t in terms:
        # Deprecated terms remain in the glossary for history and explicit
        # resolution, but new/current documents must migrate to an active term.
        if t.properties.get("状态") != "ACTIVE":
            continue
        for name in [t.name,*t.aliases]:
            if name in lookup and lookup[name]!=t.stable_id: raise UnionLanguageError(f"UNION_LANGUAGE_CONFLICT ambiguous term '{name}'")
            lookup[name]=t.stable_id
    errors=[]
    for path in paths:
        if not path.is_file() or path.name in {"union_language.md","union_language_changes.md"}: continue
        for name in bare_term_refs(path.read_text(encoding="utf-8")):
            if name not in lookup: errors.append(f"{path}: undefined unified-language term [{name}]")
    return errors


def main(argv: list[str]|None=None)->int:
    parser=argparse.ArgumentParser(description=__doc__); sub=parser.add_subparsers(dest="command",required=True)
    v=sub.add_parser("validate"); v.add_argument("--doc-root",required=True); v.add_argument("--version",default=""); v.add_argument("--scan",action="store_true"); v.add_argument("--json",action="store_true")
    r=sub.add_parser("resolve"); r.add_argument("--doc-root",required=True); r.add_argument("--version",default=""); r.add_argument("--term",required=True); r.add_argument("--json",action="store_true")
    args=parser.parse_args(argv)
    try:
        root=doc_layout.validate_doc_root(args.doc_root,args.version); terms=combined_terms(root,args.version)
        if args.command=="validate":
            errors=[]
            if args.scan:
                paths=[]
                if args.version:
                    for base in (
                        doc_layout.version_requirement_root(root,args.version),
                        doc_layout.version_module_root(root,args.version),
                        root / "docs",
                    ):
                        if base.is_dir(): paths.extend(base.rglob("*.md"))
                errors=validate_references(paths,terms)
            payload={"status":"PASSED" if not errors else "FAILED","termCount":len(terms),"errors":errors}
            print(json.dumps(payload,ensure_ascii=False,indent=2) if args.json else payload["status"]+(("\n"+"\n".join(errors)) if errors else "")); return 0 if not errors else 1
        matches=[t for t in terms if args.term in [t.name,*t.aliases,t.stable_id]]
        if len(matches)!=1: raise UnionLanguageError(f"term must resolve uniquely: {args.term}")
        t=matches[0]; payload={"id":t.stable_id,"name":t.name,"definition":t.definition,"boundary":t.boundary,"owner":t.properties.get("Owner"),"status":t.properties.get("状态"),"aliases":t.aliases}
        print(json.dumps(payload,ensure_ascii=False,indent=2) if args.json else f"{t.stable_id} {t.name}: {t.definition}"); return 0
    except (OSError,UnionLanguageError,ValueError) as exc:
        print(f"ERROR: {exc}",file=sys.stderr); return 1

if __name__=="__main__": raise SystemExit(main())
