"""Local JSON Schema protocols shared by CLI and domain services."""
from functools import lru_cache
import json
from pathlib import Path
from resource_paths import asset_path, formal_entities, get_index

ASSETS = asset_path('assets/asset-catalog.json').parent

class ProtocolError(ValueError):
    pass

@lru_cache(maxsize=1)
def bundle():
    try:
        from jsonschema import Draft202012Validator
        from referencing import Registry, Resource
    except ImportError as exc:
        raise ProtocolError('DEPENDENCY_REQUIRED: install requirements.txt') from exc
    schemas = {}
    # Register only formal entities. Flat compatibility names are aliases and
    # must never create a second public schema name.
    for p in formal_entities('assets'):
        if not p.name.endswith('.schema.json'):
            continue
        name = p.stem.removesuffix('.schema')
        if name in schemas:
            raise ProtocolError(f'SCHEMA_NAME_CONFLICT: {name}')
        schemas[name] = json.loads(p.read_text(encoding='utf-8'))
    registry = Registry()
    identities = set()
    for name, schema in schemas.items():
        if not isinstance(schema.get('$id'), str) or not schema['$id'].strip():
            raise ProtocolError(f'SCHEMA_ID_MISSING: {name}')
        if schema['$id'] in identities:
            raise ProtocolError(f'SCHEMA_ID_CONFLICT: {schema["$id"]}')
        identities.add(schema['$id'])
        Draft202012Validator.check_schema(schema)
        registry = registry.with_resource(schema['$id'], Resource.from_contents(schema))
    # Preserve names previously exposed by alias traversal, but register each
    # physical schema only once. Explicit aliases live with its catalog entry.
    for target, item in get_index(kind='assets').metadata.items():
        for alias in item.get('schemaAliases', []):
            name = Path(target).name.removesuffix('.schema.json')
            if alias in schemas or name not in schemas:
                raise ProtocolError(f'SCHEMA_ALIAS_CONFLICT: {alias}')
            schemas[alias] = schemas[name]
    return schemas, registry, Draft202012Validator

def schema(name):
    return bundle()[0][name]

def validate(name, value, definition=None):
    schemas, registry, validator = bundle()
    source = schemas[name]
    if definition:
        source = {'$ref': source['$id'] + '#/$defs/' + definition}
    try:
        errors = list(validator(source, registry=registry).iter_errors(value))
    except Exception as exc:
        raise ProtocolError('SCHEMA_RESOLUTION_FAILED: ' + name) from exc
    if errors:
        error = min(errors, key=lambda e: (len(e.path), str(list(e.path))))
        location = '/' + '/'.join(str(x) for x in error.path)
        raise ProtocolError(f'SCHEMA_INVALID:{name}:{location}: {error.message}')
    return value

def package_metadata():
    return json.loads((ASSETS/'package.json').read_text(encoding='utf-8'))


def resource_metadata(relative, *, root=None):
    """Query target-owned metadata without copying it into catalog entries."""
    from resource_catalog import CATALOGS, read_sources, catalog_structure, safe_relative, safe_target

    root = ASSETS.parent if root is None else Path(root).resolve()
    relative = safe_relative(relative)
    if not relative.startswith('assets/'):
        raise ProtocolError('ASSET_METADATA_PATH_REQUIRED')
    objects, _ = read_sources(root, 'assets')
    mappings, _, _ = catalog_structure('assets', objects)
    for record in objects[CATALOGS['assets']].get('retirements', []):
        if relative in {record['logicalPath'], record['target']} and relative not in mappings.values():
            raise ProtocolError(f'RESOURCE_RETIRED: {relative}; replacement={record["replacement"]}')
    target = mappings.get(relative, relative)
    path = safe_target(root, target, required=True)
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ProtocolError(f'ASSET_METADATA_OBJECT_REQUIRED: {target}')
    fields = {}
    for name in ('schemaVersion', '$id', 'owner', 'sourceSemantics'):
        fields[name] = {'value': value.get(name), 'source': f'{target}#/{name}' if name in value else None,
                        'status': 'DECLARED' if name in value else 'NOT_DECLARED'}
    # A document's schema version and the version of instances it validates
    # have different meanings. Report the latter as a constraint, not a guess.
    versions = {name: {'constraint': definition, 'source': f'{target}#/properties/{name}'}
                for name, definition in value.get('properties', {}).items()
                if name in {'schemaVersion', 'schema_version'}}
    return {'path': target, 'fields': fields, 'instanceVersionConstraints': versions}


def metadata_inventory(root=None):
    """Validate declared metadata and report explicit gaps for every JSON asset."""
    from resource_catalog import entity_paths

    root = ASSETS.parent if root is None else Path(root).resolve()
    records, identities = [], {}
    for path in entity_paths(root, 'assets'):
        if path.suffix != '.json':
            continue
        record = resource_metadata(path.relative_to(root).as_posix(), root=root)
        fields = record['fields']
        for name in ('owner', 'sourceSemantics', '$id'):
            field = fields[name]
            if field['status'] == 'DECLARED' and (not isinstance(field['value'], str) or not field['value'].strip()):
                raise ProtocolError(f'ASSET_METADATA_INVALID: {record["path"]}:{name}')
        version = fields['schemaVersion']
        if version['status'] == 'DECLARED' and (type(version['value']) is not int or version['value'] < 1):
            raise ProtocolError(f'ASSET_METADATA_INVALID: {record["path"]}:schemaVersion')
        identity = fields['$id']['value']
        if path.name.endswith('.schema.json') and not identity:
            raise ProtocolError(f'SCHEMA_ID_MISSING: {record["path"]}')
        if identity:
            if identity in identities:
                raise ProtocolError(f'SCHEMA_ID_CONFLICT: {record["path"]} and {identities[identity]}')
            identities[identity] = record['path']
        records.append(record)
    return {'authority': 'READ_FROM_TARGET', 'resources': records,
            'notDeclared': {name: sum(r['fields'][name]['status'] == 'NOT_DECLARED' for r in records)
                            for name in ('schemaVersion', '$id', 'owner', 'sourceSemantics')}}
