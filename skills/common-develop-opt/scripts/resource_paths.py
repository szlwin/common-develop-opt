"""Cached catalog lookup with live target checks and explicit full validation."""
from __future__ import annotations

from collections import OrderedDict
from functools import lru_cache
from pathlib import Path
from threading import RLock
from types import MappingProxyType
from typing import NamedTuple, Mapping

from resource_catalog import (INDEX_PATH, CATALOGS, safe_relative, safe_target,
                              source_paths, read_sources, catalog_structure,
                              build_section, build_index as _build_index,
                              read_index, validate_index, entity_paths)

SKILL_ROOT = Path(__file__).resolve().parents[1]
INDEX_NAME = Path(INDEX_PATH).name
_CACHE = OrderedDict()
_LOCK = RLock()


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


class CatalogIndex(NamedTuple):
    root: Path
    kind: str
    logical_paths: Mapping
    metadata: Mapping
    resource_ids: Mapping
    entities: Mapping
    origin: str
    fingerprint: tuple
    retirements: Mapping


def _stamp(path):
    try:
        stat = path.lstat()
        return stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size, stat.st_ino, stat.st_mode
    except FileNotFoundError:
        return None


def _fingerprint(root, kind):
    # Fixed-size stat work, independent of the number of resources. ctime and
    # inode catch same-size edits and atomic replacement in this process.
    return tuple(_stamp(root / name) for name in [*source_paths(kind), INDEX_PATH, 'FILE_MANIFEST.sha256'])


def clear_cache(root=None):
    with _LOCK:
        if root is None:
            _CACHE.clear()
        else:
            root = Path(root).resolve()
            for key in list(_CACHE):
                if key[0] == root:
                    del _CACHE[key]


def _load(root, kind, fingerprint):
    sources = read_sources(root, kind)
    mappings, metadata, ids = catalog_structure(kind, sources[0])
    persisted = read_index(root)
    section = persisted['catalogs'][kind] if persisted is not None else None
    if persisted is not None and not isinstance(section, dict):
        raise ValueError('RESOURCE_INDEX_CORRUPT: section must be an object')
    origin = 'GENERATED'
    if section is None or section.get('sources') != sources[1]:
        origin = 'REBUILT_MISSING' if section is None else 'REBUILT_STALE'
        section = build_section(root, kind, sources=sources)
    else:
        # A matching self-digest is not proof that mappings match the catalog.
        if any(section.get(key) != expected for key, expected in
               [('logicalPaths', mappings), ('metadata', metadata), ('resourceIds', ids)]):
            raise ValueError('RESOURCE_INDEX_CORRUPT: catalog mapping mismatch')
        if section.get('retirements', []) != sources[0][CATALOGS[kind]].get('retirements', []):
            raise ValueError('RESOURCE_INDEX_CORRUPT: retirement mapping mismatch')
        if not isinstance(section.get('entities'), dict) or not set(metadata).issubset(section['entities']):
            raise ValueError('RESOURCE_INDEX_CORRUPT: incomplete entity metadata')
        for relative, record in section['entities'].items():
            if safe_relative(relative) != relative or not relative.startswith(kind + '/') or not isinstance(record, dict):
                raise ValueError('RESOURCE_INDEX_CORRUPT: invalid entity metadata')
    return CatalogIndex(root, kind, _freeze(section['logicalPaths']), _freeze(section['metadata']),
                        _freeze(section['resourceIds']), _freeze(section['entities']), origin, fingerprint,
                        _freeze({r['logicalPath']: r for r in section.get('retirements', [])}))


def get_index(root=None, kind='assets'):
    root = SKILL_ROOT if root is None else Path(root).resolve()
    key = root, kind
    with _LOCK:
        stamp = _fingerprint(root, kind)
        current = _CACHE.get(key)
        if current is not None and current.fingerprint == stamp:
            _CACHE.move_to_end(key)
            return current
        current = _load(root, kind, stamp)
        if stamp != _fingerprint(root, kind):
            raise ValueError('RESOURCE_INPUTS_CHANGED: retry after package writes finish')
        _CACHE[key] = current
        _CACHE.move_to_end(key)
        while len(_CACHE) > 8:
            _CACHE.popitem(last=False)
        return current


def validate_catalog(root=None, kind=None):
    root = SKILL_ROOT if root is None else Path(root).resolve()
    kinds = (kind,) if kind is not None else tuple(CATALOGS)
    return {name: len(build_section(root, name)['logicalPaths']) for name in kinds}


def catalog_map(root, kind):
    """Compatibility full-validation API. Runtime callers use get_index()."""
    validate_catalog(root, kind)
    return get_index(root, kind).logical_paths


catalog_map.cache_clear = clear_cache


def build_index(root=None):
    return _build_index(SKILL_ROOT if root is None else root)


@lru_cache(maxsize=512)
def _path(root, relative):
    return root / relative


def resource_path(relative, *, root=None):
    value = safe_relative(relative)
    kind = value.split('/')[0]
    index = get_index(root, kind)
    retired = index.retirements.get(value)
    if retired is None and value not in index.entities:
        retired = next((r for r in index.retirements.values() if r['target'] == value), None)
    if retired is not None:
        raise ValueError(f'RESOURCE_RETIRED: {value}; replacement={retired["replacement"]}')
    target = index.logical_paths.get(value, value)
    safe_target(index.root, target, required=target in index.metadata)
    return _path(index.root, target)


resolve_resource = resource_path


def asset_path(relative, *, root=None):
    relative = safe_relative(relative)
    value = relative if relative.startswith('assets/') else 'assets/' + relative
    return resource_path(value, root=root)


def reference_path(relative, *, root=None):
    relative = safe_relative(relative)
    value = relative if relative.startswith('references/') else 'references/' + relative
    return resource_path(value, root=root)


def formalize(path, *, root=None):
    root = SKILL_ROOT if root is None else Path(root).resolve()
    candidate = Path(path)
    try:
        relative = candidate.absolute().relative_to(root).as_posix()
    except ValueError:
        return candidate
    if relative.startswith(('assets/', 'references/')):
        return resource_path(relative, root=root)
    return candidate


def formal_relative(path):
    return formalize(path).relative_to(SKILL_ROOT).as_posix()


def formal_entities(kind, root=None):
    root = SKILL_ROOT if root is None else Path(root).resolve()
    get_index(root, kind)
    # Enumeration is for schema loading and audit, not individual lookups.
    yield from entity_paths(root, kind)
