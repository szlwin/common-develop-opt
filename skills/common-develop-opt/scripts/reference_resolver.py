"""Typed adapter over the pinned canonical parser; declaration paths only."""
from __future__ import annotations
import argparse
import re
from pathlib import Path
from urllib.parse import unquote
from diagnostic_io import finish_cli, load_structured, project_path, result
from document_core import reference_resolution as legacy


def pointer(value, fragment):
    current = value
    for token in fragment[1:].split('/'):
        if re.search(r'~(?![01])', token):
            raise ValueError('INVALID_POINTER_ESCAPE')
        token = token.replace('~1', '/').replace('~0', '~')
        if isinstance(current, dict) and token in current:
            current = current[token]
        elif isinstance(current, list) and re.fullmatch(r'0|[1-9][0-9]*', token) and int(token) < len(current):
            current = current[int(token)]
        else:
            raise ValueError('JSON_POINTER_NOT_FOUND')
    return current


def resolve(root, raw, expected_type='', expected_revision='', source=None, read_session=None):
    root = Path(root).resolve()
    if not isinstance(raw, str) or not raw.strip():
        return result('FAILED', code='EMPTY_REFERENCE')
    path_text, sep, fragment = raw.partition('#')
    fragment = unquote(fragment)
    try:
        declaration = project_path(source, root) if source else None
        path = project_path(path_text, root, declaration)
    except ValueError as exc:
        return result('FAILED', code=str(exc))
    if not path.is_file():
        return result('UNKNOWN', code='PATH_NOT_FOUND', path=path_text)
    target = {}
    try:
        if path.suffix.lower() in {'.json', '.yaml', '.yml'}:
            value = read_session.load(path) if read_session else load_structured(path)
            # Reuse canonical ID indexing; also reject duplicates on Pointer paths.
            locations = legacy._structured_id_locations(value, path, 'structured')
            if fragment.startswith('/'):
                target = pointer(value, fragment)
                kind, location = 'json-pointer', fragment
            elif sep and fragment:
                matches = [x for x in locations if x.artifact_id == fragment]
                if len(matches) != 1:
                    return result('FAILED', code='STABLE_ID_AMBIGUOUS' if matches else 'STABLE_ID_NOT_FOUND')
                location, kind = matches[0].pointer, 'stable-id'
                target = pointer(value, location) if location else value
            else:
                target, kind, location = value, 'document', ''
        elif path.suffix.lower() in {'.md', '.markdown'}:
            if sep and fragment:
                match = legacy._markdown_targets(path).get(fragment)
                if not match or match == ('document', ''):
                    return result('FAILED', code='MARKDOWN_ANCHOR_NOT_FOUND')
                kind, location = match
            else:
                kind, location = 'document', ''
        else:
            return result('UNKNOWN', code='UNSUPPORTED_REFERENCE_FORMAT')
    except (ValueError, OSError) as exc:
        message = str(exc)
        unsupported = 'unsupported' in message.lower()
        code = 'STRUCTURED_FORMAT_UNSUPPORTED' if unsupported else ('STABLE_ID_AMBIGUOUS' if 'duplicate stable' in message else ('JSON_POINTER_NOT_FOUND' if 'POINTER' in message else 'STRUCTURED_REFERENCE_INVALID'))
        return result('UNKNOWN' if unsupported else 'FAILED', code=code, diagnostic=message)
    metadata = target if isinstance(target, dict) else {}
    resolved_id = next((metadata.get(k) for k in ('artifactId','id','stableId','stable_id') if isinstance(metadata.get(k), str) and metadata.get(k)), '')
    for expected, key, label in ((expected_type, 'artifactType', 'TYPE'), (expected_revision, 'revision', 'REVISION')):
        actual = metadata.get(key)
        if expected and not isinstance(actual, str):
            return result('UNKNOWN', code=f'TARGET_{label}_UNDECLARED')
        if expected and not actual:
            return result('UNKNOWN', code=f'TARGET_{label}_UNDECLARED')
        if expected and expected != actual:
            return result('FAILED', code=f'TARGET_{label}_MISMATCH', expected=expected, actual=actual)
    if (expected_type or expected_revision) and not resolved_id and not (kind == 'json-pointer' and location == '/'):
        return result('UNKNOWN', code='TARGET_ID_UNDECLARED')
    return result('PASSED', kind=kind, path=path.relative_to(root).as_posix(), fragment=fragment,
                  pointer=location, artifactId=resolved_id,
                  artifactType=metadata.get('artifactType'), revision=metadata.get('revision'))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--root', required=True); ap.add_argument('--reference', required=True)
    ap.add_argument('--expected-type', default=''); ap.add_argument('--expected-revision', default='')
    ap.add_argument('--source'); ap.add_argument('--output', required=True)
    args = ap.parse_args()
    return finish_cli(lambda: resolve(args.root, args.reference, args.expected_type, args.expected_revision, args.source), args.output, [args.root])

if __name__ == '__main__':
    raise SystemExit(main())
