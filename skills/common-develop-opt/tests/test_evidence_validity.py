import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from document_core.evidence_validity import classify, commit_state, digest_snapshot


class EvidenceValidityTests(unittest.TestCase):
    def test_historical_integrity_is_separate_from_current_reuse(self):
        digest = digest_snapshot({'answer': 1})
        evidence = {'revision': 'R1', 'contentDigest': digest, 'snapshotDigest': digest}
        self.assertEqual({'integrity': 'INTACT', 'validity': 'CURRENT', 'reuse': 'ELIGIBLE'},
                         classify(evidence, current_revision='R1', current_digest=digest))
        self.assertEqual('HISTORICAL_ONLY', classify(evidence, current_revision='R2', current_digest=digest)['validity'])

    def test_tampered_snapshot_is_never_reusable(self):
        value = classify({'revision': 'R1', 'contentDigest': 'a', 'snapshotDigest': 'b'},
                         current_revision='R1', current_digest='a')
        self.assertEqual('TAMPERED', value['integrity'])
        self.assertEqual('INELIGIBLE', value['reuse'])

    def test_partial_seal_is_recoverable(self):
        self.assertEqual('NOT_COMMITTED', commit_state(False, False))
        self.assertEqual('RECOVERABLE_PARTIAL', commit_state(True, False))
        self.assertEqual('COMMITTED', commit_state(True, True))


if __name__ == '__main__':
    unittest.main()
