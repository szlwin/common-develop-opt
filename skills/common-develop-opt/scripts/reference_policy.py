"""Validate Reference reading metadata and select bounded navigation plans."""
from __future__ import annotations

import json
from pathlib import Path

CATALOG_PATH = 'references/reference-catalog.json'
FIELDS = ('role', 'requires', 'nextOn', 'maxReadBytes', 'machineAuthority', 'deprecatedBy')
CONDITIONS = {'DETAIL_REQUIRED', 'REVIEW_OR_VALIDATION_REQUIRED', 'TOPIC_SELECTED', 'EXAMPLE_REQUESTED'}


def definitions(catalog):
    return {item['target']: item for item in catalog['entries'] + catalog.get('documents', [])}


def load_catalog(root):
    return json.loads((Path(root) / CATALOG_PATH).read_text(encoding='utf-8'))


def validate_reading_policy(root, catalog):
    from resource_catalog import safe_target

    entries = catalog['entries'] + catalog.get('documents', [])
    documents = definitions(catalog)
    if len(documents) != len(entries):
        raise ValueError('REFERENCE_DUPLICATE_AUTHORITY')
    policy = catalog.get('readingPolicy', {})
    if policy.get('maxUpgradeSteps') != 3 or policy.get('defaultPrimary') not in documents:
        raise ValueError('REFERENCE_READING_POLICY_INVALID')
    declared = {p.relative_to(root).as_posix() for p in (root / 'references').rglob('*.md')}
    if declared != set(documents):
        raise ValueError('REFERENCE_DOCUMENT_COVERAGE_MISMATCH')
    for path, item in documents.items():
        safe_target(root, path, required=True)
        if not path.startswith('references/') or any(field not in item for field in FIELDS):
            raise ValueError(f'REFERENCE_METADATA_MISSING: {path}')
        if item['role'] not in {'quick', 'core', 'detail', 'example'}:
            raise ValueError(f'REFERENCE_ROLE_INVALID: {path}')
        if type(item['maxReadBytes']) is not int or not 1 <= item['maxReadBytes'] <= 32768:
            raise ValueError(f'REFERENCE_BUDGET_INVALID: {path}')
        for field in ('requires', 'nextOn', 'machineAuthority'):
            if not isinstance(item[field], list):
                raise ValueError(f'REFERENCE_METADATA_INVALID: {path}:{field}')
        if not item['machineAuthority']:
            raise ValueError(f'REFERENCE_AUTHORITY_MISSING: {path}')
        for authority in item['machineAuthority']:
            safe_target(root, authority, required=True)
        edges = item['nextOn']
        if len(edges) > policy['maxUpgradeSteps']:
            raise ValueError(f'REFERENCE_CHAIN_TOO_LONG: {path}')
        for edge in edges:
            if not isinstance(edge, dict) or edge.get('when') not in CONDITIONS or edge.get('target') not in documents:
                raise ValueError(f'REFERENCE_UPGRADE_INVALID: {path}')
        for required in item['requires']:
            if required not in documents:
                raise ValueError(f'REFERENCE_DEPENDENCY_MISSING: {path}')
        replacement = item['deprecatedBy']
        if replacement is not None and replacement not in documents:
            raise ValueError(f'REFERENCE_REPLACEMENT_MISSING: {path}')
        if str(item.get('status', '')).upper() in {'RETIRED', 'DEPRECATED'} and replacement is None:
            raise ValueError(f'REFERENCE_REPLACEMENT_MISSING: {path}')
    # Dependencies point back to prerequisites; upgrades point forward.
    # Validate each graph separately rather than treating the pair as a cycle.
    for relation in ('requires', 'nextOn', 'deprecatedBy'):
        def visit(path, ancestors):
            if path in ancestors:
                raise ValueError(f'REFERENCE_CYCLE: {relation}:{path}')
            item = documents[path]
            if relation == 'nextOn':
                targets = [edge['target'] for edge in item[relation]]
            elif relation == 'deprecatedBy':
                targets = [item[relation]] if item[relation] else []
            else:
                targets = item[relation]
            for target in targets:
                visit(target, ancestors | {path})
        for path in documents:
            visit(path, set())


def reading_plan(root, routed):
    catalog = load_catalog(root)
    documents = definitions(catalog)
    resources = routed.get('resources') or []
    candidates = [r for r in resources if r.get('path') in documents]
    selected = next((r for r in candidates if str(r.get('selectedBy', '')).startswith('route:')),
                    next(iter(candidates), None))
    path = selected['path'] if selected else catalog['readingPolicy']['defaultPrimary']
    seen = set()
    while documents[path]['deprecatedBy']:
        if path in seen:
            raise ValueError('REFERENCE_CYCLE: deprecatedBy')
        seen.add(path)
        path = documents[path]['deprecatedBy']
    item = documents[path]
    primary = {'path': path, **{key: item[key] for key in FIELDS if key != 'nextOn'}}
    if selected and path == selected['path']:
        primary['resourceId'] = selected['id']
        # Section aliases already select authoritative quick rules in details.
        selector = '' if selected.get('sectionIds') else ' --entry'
        primary['readCommand'] = f"python3 scripts/reference_reader.py read -g {{CURRENT_AGENT}} --resource {selected['id']}{selector} --json"
    upgrades, queue, seen = [], [(path, edge) for edge in item['nextOn']], {path}
    while queue and len(upgrades) < catalog['readingPolicy']['maxUpgradeSteps']:
        source, edge = queue.pop(0)
        target = edge['target']
        if target in seen:
            continue
        seen.add(target)
        destination = documents[target]
        upgrades.append({'from': source, **edge, 'role': destination['role'],
                         'maxReadBytes': destination['maxReadBytes'],
                         'omissionReason': 'CONDITION_NOT_REQUESTED'})
        queue.extend((target, child) for child in destination['nextOn'])
    return {'primary': primary, 'upgradeChain': upgrades,
            'omittedUpgradeCount': len(queue),
            'omittedUpgradeReason': 'MAX_UPGRADE_STEPS' if queue else None,
            'supportingResourceIds': [r['id'] for r in resources if r is not selected],
            'supportingReason': 'ROUTED_GATES_AND_MODE_CONSTRAINTS',
            'budgetSemantics': catalog['readingPolicy']['budgetSemantics']}
