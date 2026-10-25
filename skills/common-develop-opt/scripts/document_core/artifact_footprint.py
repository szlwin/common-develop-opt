#!/usr/bin/env python3
"""Classify changed files and report common-develop artifact churn warnings."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Iterable
_SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(_SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_ROOT))

from resource_paths import asset_path

ROOT = Path(__file__).resolve().parents[2]
# This is a repository utility, not an Agent-authorized workflow command.
PERMISSION_REQUIRED = False
POLICY = json.loads(asset_path('assets/development/artifact-footprint-policy.json').read_text(encoding="utf-8"))
def normalize(path: str) -> str:
    return path.strip().replace("\\", "/").lstrip("./")


def category(path: str) -> str:
    value = normalize(path)
    if not value:
        return "empty"
    if "/evidence/reviews/" in value:
        return "review_object"
    if "/handoff/" in value:
        return "historical_handoff"
    if value.endswith("/current_manifest.json") or value.endswith("/current_impact.json"):
        return "derived_projection"
    if "/task/" in value:
        return "canonical_task"
    if value.startswith("project_doc/") or "/project_doc/" in value:
        return "canonical_document"
    return "product_or_test"


def analyze(paths: Iterable[str]) -> dict:
    normalized = sorted({normalize(path) for path in paths if normalize(path)})
    counts = Counter(category(path) for path in normalized)
    budgets = POLICY["budgets"]
    warnings: list[dict[str, str]] = []

    derived = [path for path in normalized if category(path) == "derived_projection"]
    if len(derived) > int(budgets["physicalDerivedProjectionFiles"]):
        warnings.append({
            "code": "DERIVED_PROJECTION_BUDGET",
            "message": f"Derived projection files={len(derived)} exceed budget={budgets['physicalDerivedProjectionFiles']}.",
        })

    handoffs = counts["historical_handoff"]
    if handoffs > int(budgets["historicalHandoffFilesPerSamePhaseIteration"]):
        warnings.append({
            "code": "HISTORICAL_HANDOFF_REVIEW",
            "message": "Dated handoff files were added; keep them only for phase/external ownership boundaries.",
        })

    governance_count = len(normalized) - counts["product_or_test"]
    if governance_count > int(budgets["governanceFilesWarning"]):
        warnings.append({
            "code": "GOVERNANCE_FILE_BUDGET",
            "message": f"Governance files={governance_count} exceed advisory budget={budgets['governanceFilesWarning']}; review derived and handoff categories.",
        })

    return {
        "schemaVersion": 1,
        "status": "WARNING" if warnings else "PASSED",
        "totalFiles": len(normalized),
        "governanceFiles": governance_count,
        "counts": dict(sorted(counts.items())),
        "warnings": warnings,
        "paths": normalized,
    }


def _git_paths(repo: Path, diff_ref: str) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), "diff", "--name-only", diff_ref],
        check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return result.stdout.splitlines()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", action="append", default=[])
    parser.add_argument("--file-list", default="", help="UTF-8 file containing one changed path per line")
    parser.add_argument("--repo", default="")
    parser.add_argument("--diff-ref", default="", help="git diff ref, for example HEAD~1..HEAD or --cached")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    paths = list(args.path)
    if args.file_list:
        paths.extend(Path(args.file_list).read_text(encoding="utf-8").splitlines())
    if args.repo and args.diff_ref:
        paths.extend(_git_paths(Path(args.repo), args.diff_ref))
    if not paths and not sys.stdin.isatty():
        paths.extend(sys.stdin.read().splitlines())
    if not paths:
        print("ERROR: provide --path, --file-list, --repo/--diff-ref, or stdin", file=sys.stderr)
        return 2
    report = analyze(paths)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(report["status"])
        print(f"files={report['totalFiles']} governance={report['governanceFiles']}")
        for name, count in report["counts"].items():
            print(f"{name}: {count}")
        for warning in report["warnings"]:
            print(f"WARNING {warning['code']}: {warning['message']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
