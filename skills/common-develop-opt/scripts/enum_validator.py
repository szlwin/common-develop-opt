#!/usr/bin/env python3
"""Internal enum validator used by archive/check tooling."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
from document_core.enum_contract import EnumContractError, parse_enums
from document_core.enum_validation import validate_version

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest="command", required=True)
    f=sub.add_parser("file"); f.add_argument("--file", required=True); f.add_argument("--module", required=True); f.add_argument("--json", action="store_true")
    scan=sub.add_parser("scan"); scan.add_argument("--doc-root", required=True); scan.add_argument("--version", required=True); scan.add_argument("--json", action="store_true")
    args=parser.parse_args(argv)
    try:
        if args.command=="file":
            items=parse_enums(Path(args.file).read_text(encoding="utf-8"), args.module)
            payload={"status":"PASSED","enumCount":len(items),"enums":[x.code for x in items]}
        else:
            payload=validate_version(Path(args.doc_root), args.version)
        print(json.dumps(payload,ensure_ascii=False,indent=2) if args.json else payload["status"])
        return 0 if payload["status"]=="PASSED" else 1
    except (OSError, ValueError, EnumContractError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr); return 1

if __name__=="__main__": raise SystemExit(main())
