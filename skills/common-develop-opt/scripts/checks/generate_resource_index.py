#!/usr/bin/env python3
"""Generate the derived resource index used by fast runtime lookups."""
from __future__ import annotations
import argparse, json, sys, tempfile
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[2]
if str(SKILL_ROOT / 'scripts') not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT / 'scripts'))

from resource_catalog import build_index, read_index
from resource_governance import check_retirement_changes

PERMISSION_REQUIRED = False


def write_index(root, output=None):
    root = Path(root).resolve()
    output = Path(output) if output is not None else root / 'assets/resource-index.json'
    value = build_index(root)
    check_retirement_changes(root, value, read_index(root))
    content = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n'
    if output.exists() and output.read_text(encoding='utf-8') == content:
        return value
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=output.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        temporary.replace(output)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return value

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=SKILL_ROOT)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    output = args.output or (root / 'assets' / 'resource-index.json')
    value = write_index(root, output)
    print(json.dumps({'status': 'PASSED', 'output': str(output), 'digest': value['digest'],
                      'entries': sum(len(section['logicalPaths']) for section in value['catalogs'].values())}))
    return 0
if __name__ == '__main__': raise SystemExit(main())
