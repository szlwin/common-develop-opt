"""Published pointers must be directly readable without compatibility aliases."""
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
import reference_reader
from checks._check_skill.classified_resources import validate_link_fragment, check_classified_resources
from checks._check_skill.model import CheckResult


def strings(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)
    elif isinstance(value, str):
        yield value


class FormalResourceTests(unittest.TestCase):
    def test_published_internal_links_include_valid_fragments(self):
        result = CheckResult()
        check_classified_resources(result)
        self.assertEqual([], result.errors)

    def test_split_topic_anchor_requires_physical_part(self):
        topic = ROOT / 'references/implementation/web-code-reference.detail.md'
        with self.assertRaisesRegex(ValueError, 'MARKDOWN_FRAGMENT_MISSING'):
            validate_link_fragment(topic, '3-full-模板')
        validate_link_fragment(topic.with_name('web-code-reference.detail.part-2.md'), '3-full-模板')
        validate_link_fragment(ROOT / 'references/specialist/page-design.md', 'page-design-quick-rules')

    def test_contract_fragments_reject_missing_keys(self):
        contract = ROOT / 'assets/runtime/runtime-contract.json'
        validate_link_fragment(contract, 'commandInterface')
        validate_link_fragment(contract, '/commandInterface/workModes')
        with self.assertRaisesRegex(ValueError, 'JSON_FRAGMENT_MISSING'):
            validate_link_fragment(contract, '/commandInterface/missing')

    def test_current_source_map_pointers_read_physical_sections(self):
        source = json.loads((ROOT/'ai/source-map.json').read_text())
        for value in strings(source):
            if not value.startswith('references/') or '#' not in value:
                continue
            with self.subTest(pointer=value):
                name, fragment = value.split('#', 1)
                path = ROOT/name
                self.assertFalse(path.is_symlink())
                _, sections = reference_reader.parse_sections(path)
                self.assertIn(fragment, {section.section_id for section in sections})

    def test_routes_publish_explicit_formal_paths_and_preserve_sections(self):
        for filename in ('context-routing.json',):
            raw = json.loads((ROOT/filename).read_text())
            for resource_id, definition in raw['resourceCatalog'].items():
                with self.subTest(routing=filename, resource=resource_id):
                    path = ROOT/definition['path']
                    self.assertTrue(path.is_file())
                    self.assertFalse(path.is_symlink())
                    if path.suffix == '.md':
                        _, sections, _ = reference_reader.resource_sections(path)
                        self.assertTrue(set(definition.get('sectionIds', ())).issubset({s.section_id for s in sections}))

    def test_all_catalog_targets_exist_and_have_no_symlink_components(self):
        for filename in ('assets/asset-catalog.json', 'references/reference-catalog.json'):
            for entry in json.loads((ROOT/filename).read_text())['entries']:
                with self.subTest(target=entry['target']):
                    path = ROOT/entry['target']
                    self.assertTrue(path.is_file())
                    self.assertTrue(path.resolve().is_relative_to(ROOT.resolve()))
                    while path != ROOT:
                        self.assertFalse(path.is_symlink())
                        path = path.parent


if __name__ == '__main__':
    unittest.main()
