import json
import sys
import tempfile
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from publish_candidate import publish
from candidate_fixture import candidate as make_candidate
from review_submit import submit


class ReviewSubmitTests(unittest.TestCase):
    def test_submit_uses_same_sealed_inputs(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); c=make_candidate(root, 'F', 'R1')
            publish(root,c,root/'a.json',root/'r.json')
            archive=json.loads((root/'a.json').read_text()); review=json.loads((root/'r.json').read_text())
            value=submit(archive,review,root/'result.json','NEEDS_CHANGES',[{'id':'I1'}], current=c, root=root)
            self.assertEqual('PASSED',value['status']); self.assertEqual('NEEDS_CHANGES',value['reviewResult']['decision'])

    def test_changed_review_input_cannot_be_submitted(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); c=make_candidate(root, 'F', 'R1')
            publish(root,c,root/'a.json',root/'r.json')
            archive=json.loads((root/'a.json').read_text()); review=json.loads((root/'r.json').read_text()); review['revision']='R2'
            value=submit(archive,review,root/'result.json','APPROVED', current=c, root=root)
            self.assertEqual('FAILED',value['status']); self.assertFalse((root/'result.json').exists())


if __name__ == '__main__': unittest.main()
