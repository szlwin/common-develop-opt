#!/usr/bin/env python3
"""Objective checks for human-readable project Markdown."""
from __future__ import annotations
import argparse, json, re, sys
from pathlib import Path

BOILERPLATE = (
    '综合以上分析可以发现', '通过上述设计，可以有效提升', '为后续业务发展提供有力支撑',
    '全方位提升', '全面赋能', '本 Agent 认为', '经过分析，本 Agent',
)
PLACEHOLDER_RE = re.compile(r'\{[A-Z][A-Z0-9_]+\}|\b(?:TODO|TBD)\b|待补充')
HEADING_RE = re.compile(r'^(#{1,6})\s+(.+?)\s*$')


def check_text(path: Path, text: str, *, ready: bool) -> tuple[list[str],list[str]]:
    errors=[]; warnings=[]; lines=text.splitlines(); seen=set(); fenced=False
    for i,line in enumerate(lines,1):
        if line.strip().startswith('```'): fenced=not fenced; continue
        if fenced: continue
        m=HEADING_RE.match(line)
        if m:
            key=(len(m.group(1)),m.group(2).strip())
            if key in seen: errors.append(f'{path}:{i}: duplicate heading: {key[1]}')
            seen.add(key)
        for phrase in BOILERPLATE:
            if phrase in line: errors.append(f'{path}:{i}: boilerplate phrase is not allowed: {phrase}')
        if ready and PLACEHOLDER_RE.search(line): errors.append(f'{path}:{i}: unresolved placeholder in ready document')
        if len(line)>220 and not line.lstrip().startswith('|'): warnings.append(f'{path}:{i}: paragraph line is difficult to read (>220 chars)')
    # Empty headings: a heading followed only by whitespace before the next heading.
    for idx,line in enumerate(lines):
        if not HEADING_RE.match(line): continue
        j=idx+1; content=False
        while j<len(lines) and not HEADING_RE.match(lines[j]):
            if lines[j].strip() and not lines[j].strip().startswith('<!--'): content=True; break
            j+=1
        if not content and idx+1<len(lines): warnings.append(f'{path}:{idx+1}: empty section')
    return errors,warnings


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--path',required=True); p.add_argument('--ready',action='store_true'); p.add_argument('--json',action='store_true'); args=p.parse_args(argv)
    root=Path(args.path); paths=[root] if root.is_file() else sorted(root.rglob('*.md'))
    errors=[]; warnings=[]
    for path in paths:
        e,w=check_text(path,path.read_text(encoding='utf-8'),ready=args.ready); errors.extend(e); warnings.extend(w)
    payload={'status':'PASSED' if not errors else 'FAILED','files':len(paths),'errors':errors,'warnings':warnings}
    print(json.dumps(payload,ensure_ascii=False,indent=2) if args.json else payload['status']+('\n'+'\n'.join(errors) if errors else ''))
    return 0 if not errors else 1
if __name__=='__main__': raise SystemExit(main())
