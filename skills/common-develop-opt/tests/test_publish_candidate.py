import json
import sys
import tempfile
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from publish_candidate import publish
from candidate_fixture import candidate as make_candidate
from publish_candidate import recover_publish


class PublishCandidateTests(unittest.TestCase):
    def test_seal_and_review_manifest_are_consistent(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); candidate = make_candidate(root, 'FLOW-1', 'R2')
            value = publish(root, candidate, root/'archive.json', root/'review.json')
            self.assertEqual('PASSED', value['status'])
            self.assertEqual(value['archive']['reviewInputDigest'], value['review']['reviewInputDigest'] if 'reviewInputDigest' in value['review'] else value['archive']['reviewInputDigest'])
            self.assertEqual('SEALED', json.loads((root/'archive.json').read_text())['state'])

    def test_incomplete_candidate_does_not_write(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); value = publish(root, {'artifactId':'FLOW-1'}, root/'a.json', root/'r.json')
            self.assertEqual('UNKNOWN', value['status'])
            self.assertFalse((root/'a.json').exists())

    def test_interrupted_seal_reports_recoverable_commit_state(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            candidate = make_candidate(root)
            publish(root, candidate, root/'archive.json', root/'review.json')
            (root/'review.json').unlink()
            value = recover_publish(root/'archive.json', root/'review.json')
            self.assertEqual('RECOVERABLE_PARTIAL', value['commitState'])
            self.assertEqual('RETRY_MISSING_SIDE', value['action'])


if __name__ == '__main__':
    unittest.main()
