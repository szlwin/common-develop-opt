"""Validate formal resource indexes and snapshot-independent package paths."""
import json 
import re 
from pathlib import Path
from urllib.parse import unquote
from document_core.sections import parse_sections, slugify
from checks._check_skill.context import ROOT
from checks._check_skill.model import CheckResult
from resource_paths import formalize, validate_catalog, validate_index
from resource_governance import validate_catalog_schema
from schema_protocol import metadata_inventory

REQUIRED_AUTHORITY_KEYS = {
    'rules': {'parameters', 'machineShapes', 'identity', 'dependencies', 'candidateInputs',
              'reviewInputs', 'faultGuidance', 'migrationInventory'},
    'topics': {'lifecycle', 'requirementDesign', 'developmentTesting', 'reviewEvidence',
               'archiveCompatibility', 'gitCheckpoint'},
    'manuals': {'zh-CN', 'en'},
    'authoritativeSources': {'assetCatalog'},
}


def validate_link_fragment(path: Path, fragment: str):
    """Resolve fragments against the physical document, never a joined topic."""
    fragment = unquote(fragment)
    if not fragment:
        return
    if path.suffix == '.md':
        title, sections = parse_sections(path)
        anchors = {slugify(title)}
        anchors.update(section.section_id for section in sections)
        anchors.update(slugify(section.title) for section in sections)
        if fragment not in anchors:
            raise ValueError(f'MARKDOWN_FRAGMENT_MISSING: {fragment}')
    elif path.suffix == '.json':
        value = json.loads(path.read_text(encoding='utf-8'))
        # Existing top-level #field links and JSON Pointer links are supported.
        tokens = fragment[1:].split('/') if fragment.startswith('/') else [fragment]
        try:
            for token in tokens:
                key = token.replace('~1', '/').replace('~0', '~')
                if isinstance(value, list):
                    if not key.isdigit() or (len(key) > 1 and key.startswith('0')):
                        raise ValueError('invalid array index')
                    value = value[int(key)]
                else:
                    value = value[key]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ValueError(f'JSON_FRAGMENT_MISSING: {fragment}') from exc


def check_classified_resources(result: CheckResult):
    before = len(result.errors)
    try:
        result.details['assetMetadata'] = metadata_inventory(ROOT)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        result.errors.append(f'asset metadata validation: {exc}')
    try:
        result.details['resourceIndexDigest'] = validate_index(ROOT)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        result.errors.append(f'resource index validation: {exc}')
    def require(value, label):
        if not isinstance(value, str) or not value:
            result.errors.append(f'{label}: path required')
            return
        path = formalize(ROOT / value.split('#', 1)[0]).resolve()
        if not path.is_relative_to(ROOT.resolve()) or not path.is_file():
            result.errors.append(f'{label}: missing or external resource: {value}')

    for name in ('references/authority-map.json', 'assets/asset-catalog.json', 'references/reference-catalog.json'):
        try:
            value = json.loads((ROOT / name).read_text(encoding='utf-8'))
            if name.endswith('authority-map.json'):
                for group, required in REQUIRED_AUTHORITY_KEYS.items():
                    definitions = value.get(group)
                    if not isinstance(definitions, dict):
                        result.errors.append(f'{name}: {group} must be an object')
                        continue
                    missing = sorted(required - set(definitions))
                    if missing:
                        result.errors.append(f'{name}: missing required {group}: {missing}')
                    for key, target in definitions.items():
                        if group == 'rules' and key == 'parameters':
                            if target != 'scripts --help' or not (ROOT / 'scripts/cli_help.py').is_file():
                                result.errors.append(f'{name}:rules.parameters: invalid command discovery source')
                            continue
                        if group == 'rules' and key == 'machineShapes':
                            if target != 'assets/**/*.schema.json' or not list((ROOT / 'assets').rglob('*.schema.json')):
                                result.errors.append(f'{name}:rules.machineShapes: invalid schema source pattern')
                            continue
                        require(target, f'{name}:{group}.{key}')
            elif name.endswith('asset-catalog.json'):
                validate_catalog_schema(ROOT, value)
                validate_catalog(ROOT, "assets")
                for item in value["entries"]:
                    require(item["target"], f'{name}:{item["logicalPath"]}')
            else:
                validate_catalog_schema(ROOT, value)
                validate_catalog(ROOT, "references")
                for category, files in value["categories"].items():
                    for filename in files:
                        require(f'references/{category}/{filename}', name)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            result.errors.append(f'{name}: invalid catalog: {exc}')
    source_map = ROOT / 'ai/source-map.json'
    if source_map.is_file():
        try:
            source = json.loads(source_map.read_text())
            groups = {'package': [], 'projectTemplate': [], 'command': [], 'description': []}
            for key, target in source.get('authoritativeSources', {}).items():
                if not isinstance(target, str):
                    result.errors.append(f'ai/source-map.json:{key}: pointer must be a string')
                    continue
                if target.startswith(('scripts/', 'assets/', 'references/', 'ai/')) or target in ('SKILL.md', 'context-routing.json'):
                    value = target.split()[0]
                    if '*' in value:
                        matches = list(ROOT.glob(value))
                        if not matches:
                            result.errors.append(f'ai/source-map.json:{key}: pattern has no targets: {value}')
                        for match in matches:
                            require(match.relative_to(ROOT).as_posix(), f'ai/source-map.json:{key}')
                    else:
                        require(value, f'ai/source-map.json:{key}')
                    groups['command' if ' ' in target else 'package'].append(key)
                elif target.startswith(('version/', 'docs/', 'generated/')) or target == 'archive_manifest.yaml':
                    groups['projectTemplate'].append(key)
                else:
                    groups['description'].append(key)
            result.details['sourcePointerKinds'] = groups
        except (OSError, ValueError, TypeError) as exc:
            result.errors.append(f'ai/source-map.json: invalid index: {exc}')
    markdown = [ROOT / 'SKILL.md', *ROOT.joinpath('ai').rglob('*.md'), *ROOT.joinpath('references').rglob('*.md')]
    seen = set()
    for path in markdown:
        resolved = path.resolve()
        if not resolved.is_relative_to(ROOT.resolve()) or not path.is_file():
            result.errors.append(f'invalid Markdown alias: {path.relative_to(ROOT)}')
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        text = re.sub(r'```.*?```', '', resolved.read_text(), flags=re.S)
        text = re.sub(r'`+[^`\n]*`+', '', text)
        for target in re.findall(r'\]\(([^)]+)\)', text):
            target = target.strip('<>')
            if re.match(r'[a-z]+:', target) or any(c in target for c in '{}*'):
                continue
            name, _, fragment = target.partition('#')
            destination = (resolved.parent / unquote(name)).resolve() if name else resolved
            if not destination.is_relative_to(ROOT.resolve()) or not destination.exists():
                result.errors.append(f'broken internal link: {resolved.relative_to(ROOT)} -> {target}')
                continue
            if fragment:
                try:
                    validate_link_fragment(destination, fragment)
                except (OSError, ValueError) as exc:
                    result.errors.append(f'broken internal link: {resolved.relative_to(ROOT)} -> {target}: {exc}')
    if len(result.errors) == before:
        result.ok('Classified resources and authority indexes resolve inside the package')
