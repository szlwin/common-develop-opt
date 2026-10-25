"""Exercise migrated resources through the real public CLI in another cwd."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

PACKAGE = Path(__file__).resolve().parents[1]


class ReferenceReaderRegressionTests(unittest.TestCase):
    def command(self, script, *args):
        with tempfile.TemporaryDirectory() as temporary:
            run = subprocess.run([sys.executable, '-B', str(PACKAGE/'scripts'/script), *args],
                                 cwd=temporary, text=True, capture_output=True)
        self.assertEqual(0, run.returncode, run.stdout + run.stderr)
        return json.loads(run.stdout)

    def test_all_section_aliases_read_the_original_sections(self):
        routing = json.loads((PACKAGE/'context-routing.json').read_text())
        aliases = [(key, value) for key, value in routing['resourceCatalog'].items() if value.get('sectionIds')]
        self.assertEqual(16, len(aliases))
        for resource, definition in aliases:
            with self.subTest(resource=resource):
                result = self.command('reference_reader.py', 'read', '--resource', resource,
                                      '-g', 'ProjectManagerAgent', '--json')
                self.assertEqual(definition['sectionIds'], result['selectedSectionIds'])
                self.assertTrue(result['content'].strip())

    def test_model_context_resolves_classified_implementation_paths(self):
        value = self.command('ai_context.py', 'resolve', '--task-type', 'business_model',
                             '--phase', 'business_model', '--json')
        self.assertEqual('RESOLVED', value['contextStatus'])

    def test_detail_part_two_is_indexed_and_readable(self):
        value = self.command('reference_reader.py', 'index', '--resource', 'business-model',
                             '-g', 'ProjectManagerAgent', '--json')
        section = next(item for item in value['sections'] if '.part-2.' in item['path'])
        result = self.command('reference_reader.py', 'read', '--resource', 'business-model',
                              '--section', section['id'], '-g', 'ProjectManagerAgent', '--json')
        self.assertIn(section['title'], result['content'])
