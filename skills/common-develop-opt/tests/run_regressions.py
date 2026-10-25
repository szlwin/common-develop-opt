"""Run the package test suite in a fresh interpreter."""
import argparse
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--module', action='append')
    args = parser.parse_args()
    directory = ROOT / 'tests'
    sys_path = str(ROOT / 'scripts')
    sys.path.insert(0, sys_path)
    sys.path.insert(0, str(directory))
    os.environ['PYTHONPATH'] = os.pathsep.join([str(directory), sys_path])
    suite = unittest.TestSuite()
    for path in sorted(directory.glob('test_*.py')):
        if args.module and path.stem not in args.module:
            continue
        suite.addTests(unittest.defaultTestLoader.loadTestsFromName(path.stem))
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
