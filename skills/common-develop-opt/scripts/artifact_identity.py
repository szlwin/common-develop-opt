"""Content identities are derived observations, never task-state authority."""
from __future__ import annotations
import argparse
import copy
import hashlib
from pathlib import Path, PurePosixPath
from diagnostic_io import (DIGEST_ALGORITHM, EDGE_KINDS, digest_file, digest_manifest,
                       finish_cli, is_digest, load_json, project_path, result)

from schema_protocol import ProtocolError, schema, validate

_FIELDS = schema('artifact-identity')['$defs']['record']['properties']
CATEGORIES = frozenset(_FIELDS['category']['enum'])
TYPES = frozenset(_FIELDS['artifactType']['enum'])
STATES = frozenset(_FIELDS['artifactStatus']['enum'])


def identity(spec, root, read_bytes=None):
    root = Path(root).resolve()
    if not isinstance(spec, dict):
        return result('FAILED', code='ARTIFACT_OBJECT_REQUIRED')
    if any(not isinstance(spec.get(k), str) or not spec[k].strip() for k in ('artifactId', 'artifactType', 'revision')):
        return result('FAILED', code='MISSING_IDENTITY_FIELDS')
    if spec['artifactType'] not in TYPES or not isinstance(spec.get('category'), str) or spec['category'] not in CATEGORIES:
        return result('UNKNOWN', code='ARTIFACT_TYPE_OR_CATEGORY_UNKNOWN')
    if type(spec.get('schemaVersion', 1)) is not int or spec.get('schemaVersion', 1) != 1:
        return result('UNKNOWN', code='ARTIFACT_SCHEMA_UNKNOWN')
    if not isinstance(spec.get('artifactStatus', 'CANDIDATE'), str) or spec.get('artifactStatus', 'CANDIDATE') not in STATES:
        return result('FAILED', code='ARTIFACT_STATUS_INVALID')
    lifecycle = spec.get('lifecycleBinding')
    if not isinstance(lifecycle, dict) or any(not isinstance(lifecycle.get(k), str) or not lifecycle[k].strip() for k in ('task', 'phase', 'iteration')):
        return result('UNKNOWN', code='LIFECYCLE_BINDING_REQUIRED')
    supersedes = spec.get('supersedes', [])
    if not isinstance(supersedes, list) or any(not isinstance(x, str) or not x for x in supersedes) or len(supersedes) != len(set(supersedes)):
        return result('FAILED', code='SUPERSEDES_INVALID')
    inputs = spec.get('inputs', [{'path': spec['sourceLocator']}] if spec.get('sourceLocator') else [])
    if not isinstance(inputs, list) or not inputs:
        return result('UNKNOWN', code='INPUTS_REQUIRED')
    try:
        validate('artifact-identity', spec, 'spec')
    except ProtocolError as exc:
        return result('UNKNOWN', code='ARTIFACT_PROTOCOL_INVALID', diagnostic=str(exc))
    records = []
    try:
        for item in inputs:
            if not isinstance(item, dict):
                return result('FAILED', code='INVALID_INPUT_RECORD')
            path = project_path(item.get('path'), root)
            if not path.is_file():
                return result('UNKNOWN', code='INPUT_NOT_FOUND', path=item['path'])
            records.append({'path': path.relative_to(root).as_posix(), 'sha256': hashlib.sha256(read_bytes(path)).hexdigest() if read_bytes else digest_file(path)})
        if len({x['path'] for x in records}) != len(records):
            return result('FAILED', code='DUPLICATE_INPUT')
        locator = spec.get('sourceLocator', min(x['path'] for x in records))
        locator = project_path(locator, root).relative_to(root).as_posix()
        if locator not in {x['path'] for x in records}:
            return result('FAILED', code='SOURCE_LOCATOR_NOT_IN_INPUTS')
    except (ValueError, OSError) as exc:
        return result('FAILED', code='INPUT_PATH_INVALID', diagnostic=str(exc))
    deps = spec.get('dependencies', [])
    if not isinstance(deps, list):
        return result('FAILED', code='INVALID_DEPENDENCIES')
    for dep in deps:
        if not isinstance(dep, dict) or not isinstance(dep.get('kind'), str) or dep['kind'] not in EDGE_KINDS or any(not isinstance(dep.get(k), str) or not dep[k].strip() for k in ('artifactId', 'revision')) or not is_digest(dep.get('contentDigest')):
            return result('UNKNOWN', code='DEPENDENCY_IDENTITY_REQUIRED')
    if len({(d['artifactId'], d['kind']) for d in deps}) != len(deps):
        return result('FAILED', code='DUPLICATE_DEPENDENCY')
    payload = {k: spec[k] for k in ('artifactId', 'artifactType', 'revision', 'category')}
    payload.update(schemaVersion=1, artifactStatus=spec.get('artifactStatus', 'CANDIDATE'),
                   lifecycleBinding=dict(lifecycle), sourceLocator=locator, supersedes=sorted(supersedes),
                   inputs=sorted(records, key=lambda x: x['path']), dependencies=sorted(deps, key=lambda d: (d['artifactId'], d['kind'])),
                   contentDigest=digest_manifest(records), dependencyManifestDigest=digest_manifest(deps), digestAlgorithm=DIGEST_ALGORITHM)
    return result('PASSED', artifact=copy.deepcopy(payload), validationScope='DECLARED_INPUTS_ONLY')


def _verified_record(record):
    """Validate external observations; PASSED alone is not an identity proof."""
    try:
        validate('artifact-identity', record)
    except ProtocolError:
        return False
    # Shape and enums are owned by the schema; filesystem/path and manifest
    # invariants remain semantic checks.
    inputs, dependencies = record['inputs'], record['dependencies']
    paths = [item['path'] for item in inputs]
    for raw in paths:
        path = PurePosixPath(raw)
        if path.is_absolute() or '..' in path.parts or not path.parts or path.as_posix() != raw:
            return False
    if len(set(paths)) != len(paths) or record['sourceLocator'] not in paths:
        return False
    if len({(dep['artifactId'], dep['kind']) for dep in dependencies}) != len(dependencies):
        return False
    return (record['contentDigest'] == digest_manifest(inputs)
            and record['dependencyManifestDigest'] == digest_manifest(dependencies))


def compare(current, expected):
    if any(not isinstance(x, dict) or x.get('status') != 'PASSED' for x in (current, expected)):
        return result('UNKNOWN', code='IDENTITY_NOT_VERIFIED')
    actual, old = current.get('artifact'), expected.get('artifact')
    if not all(_verified_record(x) for x in (actual, old)):
        return result('UNKNOWN', code='EXPECTED_IDENTITY_MISSING')
    if actual['artifactId'] != old['artifactId']:
        return result('FAILED', code='ARTIFACT_ID_CHANGED')
    changed = [k for k in ('artifactType', 'category', 'schemaVersion', 'lifecycleBinding', 'sourceLocator', 'supersedes', 'artifactStatus', 'contentDigest', 'dependencyManifestDigest') if actual.get(k) != old.get(k)]
    if actual['revision'] != old['revision']:
        return result('CHANGED', code='NEW_REVISION_REQUIRES_VALIDATION', changedFields=changed)
    return result('STALE' if changed else 'CURRENT', code='SAME_REVISION_CONTENT_DRIFT' if changed else 'IDENTITY_MATCH', changedFields=changed, artifactId=actual['artifactId'])


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--root', required=True); ap.add_argument('--spec', required=True)
    ap.add_argument('--expected'); ap.add_argument('--output', required=True)
    args = ap.parse_args()
    def operation():
        value = identity(load_json(args.spec), args.root)
        if args.expected and value['status'] == 'PASSED':
            comparison = compare(value, load_json(args.expected))
            value.update(status=comparison['status'], comparison=comparison)
        return value
    return finish_cli(operation, args.output, [args.root, args.spec] + ([args.expected] if args.expected else []))

if __name__ == '__main__':
    raise SystemExit(main())
