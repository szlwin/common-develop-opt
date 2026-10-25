import sys
from pathlib import Path
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from publish_candidate import publish
from candidate_fixture import candidate as make_candidate
from review_lifecycle import reopen_relation, validate_review_inputs


class ReviewLifecycleTests(unittest.TestCase):
    def test_review_inputs_bind_to_sealed_archive(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); candidate=make_candidate(root, 'F', 'R1')
            publish(root,candidate,root/'a.json',root/'r.json')
            import json
            archive=json.loads((root/'a.json').read_text()); review=json.loads((root/'r.json').read_text())
            self.assertEqual('PASSED', validate_review_inputs(archive,review)['status'])
            review['revision']='R2'
            self.assertEqual('FAILED', validate_review_inputs(archive,review)['status'])

    def test_reopen_preserves_sequence_and_rejects_same_second_reorder(self):
        events=[{'sequence':1,'kind':'REVIEW'},{'sequence':2,'kind':'REOPEN'},{'sequence':3,'kind':'REVIEW'}]
        self.assertEqual('PASSED', reopen_relation(events)['status'])
        self.assertEqual('FAILED', reopen_relation([events[1],events[0]])['status'])

    def test_candidate_review_reopen_candidate_sequence(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); import json
            candidate=make_candidate(root, 'F', 'R1')
            publish(root,candidate,root/'a1.json',root/'r1.json')
            archive=json.loads((root/'a1.json').read_text()); review=json.loads((root/'r1.json').read_text())
            self.assertEqual('PASSED', validate_review_inputs(archive,review)['status'])
            self.assertEqual('PASSED', reopen_relation([{'sequence':1,'kind':'REVIEW'},{'sequence':2,'kind':'REOPEN'}])['status'])
            candidate=make_candidate(root, 'F', 'R2', content='new-source')
            publish(root,candidate,root/'a2.json',root/'r2.json')
            self.assertTrue((root/'a2.json').is_file() and (root/'r2.json').is_file())


if __name__ == '__main__': unittest.main()
