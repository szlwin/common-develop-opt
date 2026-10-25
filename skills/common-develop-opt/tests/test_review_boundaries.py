"""Regression tests for output protection, identity binding and seal recovery."""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
from application_services import submit_review
from candidate_fixture import candidate
from publish_candidate import publish, recover_publish


class ReviewBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.current = candidate(self.root, 'MODEL-A', 'R1')
        sealed = publish(self.root, self.current, self.root/'archive.json', self.root/'review.json')
        self.assertEqual('PASSED', sealed['status'])
        self.archive, self.review = sealed['archive'], sealed['review']

    def submit(self, output='result.json', **kwargs):
        return submit_review(self.archive, self.review, self.root/output, 'APPROVED',
                             current=kwargs.get('current', self.current), root=kwargs.get('root', self.root))

    def test_source_dependency_evidence_outputs_and_links_are_rejected(self):
        for filename in ('source.json', 'dependency.json', 'evidence.json'):
            with self.subTest(filename=filename):
                path = self.root / filename
                before = path.read_bytes()
                self.assertEqual('REVIEW_OUTPUT_REJECTED', self.submit(filename)['code'])
                self.assertEqual(before, path.read_bytes())
        for kind in ('symlink', 'hardlink'):
            alias = self.root / kind
            if kind == 'symlink':
                alias.symlink_to(self.root/'source.json')
            else:
                os.link(self.root/'source.json', alias)
            self.assertEqual('REVIEW_OUTPUT_REJECTED', self.submit(kind)['code'])

    def test_current_identity_and_revision_cannot_drift_with_same_bytes(self):
        for field, value in (('artifactId', 'MODEL-B'), ('revision', 'R2')):
            current = copy.deepcopy(self.current)
            current[field] = value
            self.assertEqual('CURRENT_INPUT_DRIFT', self.submit(current=current)['code'])
        self.assertFalse((self.root/'result.json').exists())

    def test_current_root_and_descriptor_are_required(self):
        for kwargs in ({'root': None}, {'current': None}):
            self.assertEqual('CURRENT_INPUT_REQUIRED', self.submit(**kwargs)['code'])

    def test_current_file_is_remeasured(self):
        (self.root/'source.json').write_text('changed')
        self.assertEqual('CURRENT_INPUT_INVALID', self.submit()['code'])

    def test_unchanged_current_inputs_can_be_reviewed(self):
        self.assertEqual('PASSED', self.submit()['status'])
        self.assertEqual('MODEL-A', json.loads((self.root/'result.json').read_text())['artifactId'])

    def test_cli_cannot_overwrite_its_json_inputs(self):
        path = self.root/'current.json'
        path.write_text(json.dumps(self.current))
        for output in ('current.json', 'archive.json', 'review.json', 'source.json'):
            before = (self.root/output).read_bytes()
            run = subprocess.run([sys.executable, '-B', str(SCRIPTS/'service_cli.py'), 'review',
                                  '--root', str(self.root), '--current', str(path),
                                  '--archive', str(self.root/'archive.json'), '--input', str(self.root/'review.json'),
                                  '--output', str(self.root/output), '--decision', 'APPROVED'],
                                 text=True, capture_output=True)
            self.assertNotEqual(0, run.returncode)
            self.assertEqual('REVIEW_OUTPUT_REJECTED', json.loads(run.stdout)['code'])
            self.assertEqual(before, (self.root/output).read_bytes())

    def test_recovery_rejects_corruption_and_mismatched_pair(self):
        for a, r in (('{}', '!'), ('{}', json.dumps(self.review)),
                     (json.dumps(self.archive), json.dumps(dict(self.review, revision='R2')))):
            (self.root/'archive.json').write_text(a)
            (self.root/'review.json').write_text(r)
            outcome = recover_publish(self.root/'archive.json', self.root/'review.json')
            self.assertEqual('CORRUPT', outcome['commitState'])
            self.assertEqual('REPAIR_REQUIRED', outcome['action'])

    def test_recovery_accepts_valid_pair(self):
        self.assertEqual('COMMITTED', recover_publish(self.root/'archive.json', self.root/'review.json')['commitState'])

    def test_recovery_rejects_null_even_when_other_side_is_absent(self):
        for a, r in (('null', json.dumps(self.review)), (json.dumps(self.archive), 'null'),
                     ('null', 'null'), ('null', None), (None, 'null')):
            with self.subTest(archive=a, review=r):
                for name, text in (('archive.json', a), ('review.json', r)):
                    path = self.root/name
                    if text is None:
                        path.unlink(missing_ok=True)
                    else:
                        path.write_text(text)
                value = recover_publish(self.root/'archive.json', self.root/'review.json')
                self.assertEqual('FAILED', value['status'])
                self.assertEqual('CORRUPT', value['commitState'])
                self.assertEqual('REPAIR_REQUIRED', value['action'])

    def test_relative_spellings_round_trip_through_publish_review_and_recovery(self):
        (self.root/'nested').mkdir()
        for prefix in ('./', 'nested/../', '././'):
            for records in (False, True):
                with self.subTest(prefix=prefix, records=records):
                    current = copy.deepcopy(self.current)
                    for group in ('sourceFiles', 'dependencyFiles', 'evidenceFiles'):
                        row = current[group][0]
                        current[group] = [dict(row, path=prefix + row['path']) if records else prefix + row['path']]
                    sealed = publish(self.root, current, self.root/'archive.json', self.root/'review.json')
                    self.assertEqual('PASSED', sealed['status'])
                    outcome = submit_review(sealed['archive'], sealed['review'], self.root/'result.json',
                                            'APPROVED', current=current, root=self.root)
                    self.assertEqual('PASSED', outcome['status'], outcome)
                    self.assertEqual('COMMITTED', recover_publish(self.root/'archive.json', self.root/'review.json')['commitState'])

    def test_duplicate_normalized_paths_are_rejected(self):
        current = copy.deepcopy(self.current)
        current['sourceFiles'] = ['source.json', './source.json']
        value = publish(self.root, current, self.root/'new-archive.json', self.root/'new-review.json')
        self.assertEqual('CANDIDATE_DUPLICATE_INPUT', value['code'])
        self.assertFalse((self.root/'new-archive.json').exists())

    def test_cli_relative_and_symlink_inputs_round_trip(self):
        for alias in ('./source.json', 'source-link.json'):
            with self.subTest(alias=alias):
                if alias == 'source-link.json':
                    (self.root/alias).symlink_to(self.root/'source.json')
                current = copy.deepcopy(self.current)
                current['sourceFiles'] = [alias]
                path = self.root/'current.json'
                path.write_text(json.dumps(current))
                commands = [
                    ['seal', '--root', str(self.root), '--candidate', str(path),
                     '--archive', str(self.root/'archive.json'), '--review', str(self.root/'review.json')],
                    ['review', '--root', str(self.root), '--current', str(path),
                     '--archive', str(self.root/'archive.json'), '--input', str(self.root/'review.json'),
                     '--output', str(self.root/'result.json'), '--decision', 'APPROVED'],
                ]
                for args in commands:
                    run = subprocess.run([sys.executable, '-B', str(SCRIPTS/'service_cli.py'), *args],
                                         text=True, capture_output=True, cwd=self.root)
                    self.assertEqual(0, run.returncode, run.stdout + run.stderr)
                    self.assertEqual('PASSED', json.loads(run.stdout)['status'])
                self.assertEqual('COMMITTED', recover_publish(self.root/'archive.json', self.root/'review.json')['commitState'])
