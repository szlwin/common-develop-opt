from pathlib import Path
import unittest
from documentation_audit import audit


class WorkflowComponentTests(unittest.TestCase):
    def test_documentation_authority_audit(self):
        package=Path(__file__).resolve().parents[1]
        self.assertEqual('PASSED', audit(package)['status'])


if __name__ == '__main__': unittest.main()
