"""Catalog structure and deterministic full-package index construction."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PureWindowsPath

INDEX_PATH = 'assets/resource-index.json'
CATALOGS = {'assets': 'assets/asset-catalog.json', 'references': 'references/reference-catalog.json'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def safe_relative(value):
    if not isinstance(value, str) or not value or '\\' in value:
        raise ValueError(f'invalid package resource path: {value!r}')
    path = Path(value.split('#', 1)[0])
    if not path.parts or path.is_absolute() or PureWindowsPath(str(path)).drive or '..' in path.parts:
        raise ValueError(f'invalid package resource path: {value!r}')
    return path.as_posix()


def safe_target(root, relative, *, required=False, code='UNDECLARED_RESOURCE_ALIAS'):
    relative = safe_relative(relative)
    path = root / relative
    # Check every component without caching mutable filesystem safety state.
    for component in (path, *path.parents):
        if component == root:
            break
        if component.is_symlink():
            raise ValueError(f'{code}: {relative}')
    if required and not path.is_file():
        raise ValueError(f'CATALOG_TARGET_MISSING: {relative}')
    return path


def source_paths(kind):
    if kind not in CATALOGS:
        raise ValueError(f'unknown resource kind: {kind}')
    names = [CATALOGS[kind], 'assets/package.json', 'assets/package/release-contract.json',
             'assets/package/resource-catalog.schema.json']
    if kind == 'references':
        names.append('context-routing.json')
    return names


def read_sources(root, kind):
    objects, hashes = {}, {}
    for name in source_paths(kind):
        path = safe_target(root, name)
        if not path.exists() and name != CATALOGS[kind]:
            hashes[name] = None
            continue
        raw = path.read_bytes()
        objects[name] = json.loads(raw)
        hashes[name] = hashlib.sha256(raw).hexdigest()
    return objects, hashes


def catalog_structure(kind, objects):
    data = objects[CATALOGS[kind]]
    mappings, metadata = {}, {}
    for item in data['entries']:
        source = item.get('logicalPath') or item['source'].replace('assets/legacy/', 'assets/', 1)
        source, target = safe_relative(source), safe_relative(item['target'])
        if not source.startswith(kind + '/') or not target.startswith(kind + '/'):
            raise ValueError(f'CATALOG_WRONG_KIND: {source} -> {target}')
        if source in mappings or target in metadata:
            raise ValueError(f'CATALOG_DUPLICATE: {source} -> {target}')
        mappings[source] = target
        metadata[target] = {'category': item.get('category'), 'kind': item.get('kind'),
                            'schemaAliases': item.get('schemaAliases', [])}
    for source, target in mappings.items():
        if target in mappings and mappings[target] != target:
            raise ValueError(f'CATALOG_CHAIN_OR_CYCLE: {source} -> {target}')
    ids = {}
    if kind == 'references':
        declared = [f'references/{category}/{name}' for category, names in data['categories'].items() for name in names]
        if len(set(declared)) != len(declared) or set(declared) != set(metadata):
            raise ValueError('CATALOG_CATEGORIES_MISMATCH')
        routing = objects.get('context-routing.json', {})
        template = routing.get('resourceDefaults', {}).get('pathTemplate', 'references/{id}.md')
        for resource_id, definition in routing.get('resourceCatalog', {}).items():
            raw = safe_relative(definition.get('path', template.format(id=resource_id)))
            if raw.startswith('references/'):
                ids[resource_id] = mappings.get(raw, raw)
    return mappings, metadata, ids


def entity_paths(root, kind):
    for path in sorted((root / kind).rglob('*')):
        if any(part in ('__pycache__', '.pytest_cache') for part in path.parts):
            continue
        if path.is_file() and not path.is_symlink() and path.relative_to(root).as_posix() != INDEX_PATH:
            yield path


def build_section(root, kind, *, sources=None):
    objects, hashes = sources or read_sources(root, kind)
    catalog = objects[CATALOGS[kind]]
    if catalog.get('catalogSchema'):
        from resource_governance import validate_catalog_schema
        validate_catalog_schema(root, catalog)
    if kind == 'references' and objects[CATALOGS[kind]].get('schemaVersion') == 2:
        from reference_policy import validate_reading_policy
        validate_reading_policy(root, objects[CATALOGS[kind]])
    mappings, metadata, ids = catalog_structure(kind, objects)
    if catalog.get('catalogSchema'):
        from resource_governance import validate_retirements
        validate_retirements(root, kind, catalog, mappings)
    for target in metadata:
        safe_target(root, target, required=True, code='CATALOG_NONFORMAL_TARGET')
    entities = {}
    for path in entity_paths(root, kind):
        relative = path.relative_to(root).as_posix()
        safe_target(root, relative, required=True, code='CATALOG_NONFORMAL_TARGET')
        raw = path.read_bytes()
        entities[relative] = {'sha256': hashlib.sha256(raw).hexdigest(), 'size': len(raw)}
    result = {'sources': hashes, 'logicalPaths': mappings, 'metadata': metadata,
            'resourceIds': ids, 'entities': entities,
            'catalogVersion': objects[CATALOGS[kind]].get('schemaVersion')}
    if catalog.get('catalogSchema'):
        result['retirements'] = catalog['retirements']
    return result


def build_index(root):
    root = Path(root).resolve()
    value = {'schemaVersion': 2, 'kind': 'RESOURCE_INDEX', 'authority': 'DERIVED',
             'catalogs': {kind: build_section(root, kind) for kind in CATALOGS}}
    return {**value, 'digest': digest(value)}


def read_index(root):
    path = safe_target(root, INDEX_PATH)
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_bytes())
        if not isinstance(value, dict):
            raise ValueError('object required')
        payload = {k: v for k, v in value.items() if k != 'digest'}
        if value.get('schemaVersion') != 2 or value.get('kind') != 'RESOURCE_INDEX' or value.get('authority') != 'DERIVED':
            raise ValueError('unsupported index version or identity')
        if value.get('digest') != digest(payload):
            raise ValueError('digest mismatch')
        if set(value['catalogs']) != set(CATALOGS):
            raise ValueError('catalog kinds mismatch')
        return value
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError(f'RESOURCE_INDEX_CORRUPT: {exc}') from exc


def validate_index(root):
    actual = read_index(root)
    if actual is None:
        raise ValueError('RESOURCE_INDEX_MISSING: run scripts/checks/generate_resource_index.py')
    expected = build_index(root)
    from resource_governance import check_retirement_changes
    check_retirement_changes(Path(root), expected, actual)
    if actual != expected:
        raise ValueError('RESOURCE_INDEX_STALE: run scripts/checks/generate_resource_index.py before manifest update')
    return expected['digest']
