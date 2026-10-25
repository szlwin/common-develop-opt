"""Catalog contracts and append-only retirement checks before index writes."""
from __future__ import annotations

import json
from datetime import datetime

from resource_catalog import CATALOGS, safe_relative, safe_target

CATALOG_SCHEMA = 'assets/package/resource-catalog.schema.json'


def validate_catalog_schema(root, catalog):
    from jsonschema import Draft202012Validator, FormatChecker

    schema = json.loads(safe_target(root, CATALOG_SCHEMA, required=True).read_text(encoding='utf-8'))
    Draft202012Validator.check_schema(schema)
    errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(catalog))
    if errors:
        error = min(errors, key=lambda e: (len(e.path), str(list(e.path))))
        raise ValueError(f'CATALOG_SCHEMA_INVALID: {list(error.path)}: {error.message}')


def _history_record(root, reference):
    path, separator, pointer = reference.partition('#')
    if not separator or not pointer.startswith('/') or not path.endswith('.json'):
        raise ValueError(f'RETIREMENT_HISTORY_INVALID: {reference}')
    value = json.loads(safe_target(root, path, required=True).read_text(encoding='utf-8'))
    try:
        for raw in pointer[1:].split('/'):
            key = raw.replace('~1', '/').replace('~0', '~')
            if isinstance(value, list) and (not key.isdecimal() or (len(key) > 1 and key.startswith('0'))):
                raise ValueError('invalid array index')
            value = value[int(key)] if isinstance(value, list) else value[key]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ValueError(f'RETIREMENT_HISTORY_INVALID: {reference}') from exc
    if not isinstance(value, dict):
        raise ValueError(f'RETIREMENT_HISTORY_INVALID: {reference}')
    return value


def validate_retirements(root, kind, catalog, mappings):
    records, targets = {}, set(mappings.values())
    for i, record in enumerate(catalog.get('retirements', [])):
        logical, target = record['logicalPath'], record['target']
        for value in (logical, target):
            if safe_relative(value) != value or not value.startswith(kind + '/'):
                raise ValueError(f'RETIREMENT_PATH_INVALID: {value}')
        if logical in records or logical in mappings or logical in targets:
            raise ValueError(f'RETIREMENT_DUPLICATE_OR_ACTIVE: {logical}')
        replacement = record['replacement']
        if replacement is not None:
            replacement = safe_relative(replacement)
            # Replacement is a physical live resource, never another tombstone.
            if replacement == target or not replacement.startswith(kind + '/'):
                raise ValueError(f'RETIREMENT_REPLACEMENT_INVALID: {logical}')
            safe_target(root, replacement, required=True)
        if not record['reason'].strip():
            raise ValueError(f'RETIREMENT_REASON_MISSING: {logical}')
        try:
            date = datetime.fromisoformat(record['retiredAt'].replace('Z', '+00:00'))
        except ValueError as exc:
            raise ValueError(f'RETIREMENT_DATE_INVALID: {logical}') from exc
        if date.tzinfo is None:
            raise ValueError(f'RETIREMENT_DATE_INVALID: {logical}')
        reference = f'{CATALOGS[kind]}#/retirements/{i}'
        for history_ref in record['historyRefs']:
            history = _history_record(root, history_ref)
            if history.get('retirementRef') != reference or not (
                {str(history.get(k, '')) for k in ('target', 'logicalPath', 'source')} & {logical, target}
            ):
                raise ValueError(f'RETIREMENT_HISTORY_MISMATCH: {history_ref}')
        records[logical] = record
    retired_targets = {r['target'] for r in records.values()} - targets
    for record in records.values():
        if record['replacement'] in retired_targets:
            raise ValueError(f'RETIREMENT_REPLACEMENT_RETIRED: {record["logicalPath"]}')
    return records


def check_retirement_changes(root, current, previous=None):
    """Use the previous index/manifest as witnesses, never as metadata authority."""
    manifest = root / 'FILE_MANIFEST.sha256'
    old_paths = set()
    if manifest.is_file():
        from checks._check_skill.manifest import parse_manifest_entries
        entries, invalid = parse_manifest_entries(manifest.read_text(encoding='utf-8'))
        if invalid:
            raise ValueError(f'RETIREMENT_BASELINE_INVALID: {invalid}')
        old_paths = {p.removeprefix('./') for p in entries}
    for kind, catalog_path in CATALOGS.items():
        catalog = json.loads((root / catalog_path).read_text(encoding='utf-8'))
        if not catalog.get('catalogSchema'):
            continue
        section = current['catalogs'][kind]
        records = validate_retirements(root, kind, catalog, section['logicalPaths'])
        prior = (previous or {}).get('catalogs', {}).get(kind, {})
        removed_files = (set(prior.get('entities', {})) | {
            p for p in old_paths if p.startswith(kind + '/') and p != 'assets/resource-index.json'
        }) - set(section['entities'])
        removed_aliases = set(prior.get('logicalPaths', {})) - set(section['logicalPaths'])
        missing = sorted(removed_files - {r['target'] for r in records.values()})
        missing += sorted(removed_aliases - set(records))
        if missing:
            raise ValueError(f'RESOURCE_RETIREMENT_REQUIRED: {missing}')
        for record in prior.get('retirements', []):
            if records.get(record['logicalPath']) != record:
                raise ValueError(f'RETIREMENT_HISTORY_CHANGED: {record["logicalPath"]}')
        # Migration targets may be historical only when they link to a tombstone.
        history_path = root / 'references/migration-map.json'
        if history_path.is_file():
            history = json.loads(history_path.read_text(encoding='utf-8'))
            for i, item in enumerate(history.get(kind, [])):
                target = item.get('target')
                if not target or safe_target(root, target).is_file():
                    continue
                reference = f'references/migration-map.json#/{kind}/{i}'
                if not any(r['target'] == target and reference in r['historyRefs'] for r in records.values()):
                    raise ValueError(f'RESOURCE_HISTORY_UNDECLARED_RETIREMENT: {reference}')
