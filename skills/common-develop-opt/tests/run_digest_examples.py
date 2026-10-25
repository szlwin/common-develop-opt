"""Small digest examples; these do not test application or workflow behavior."""
from pathlib import Path
import argparse
import hashlib
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from diagnostic_io import finish_cli, result


def examples():
    digest = lambda value: hashlib.sha256(value.encode()).hexdigest()
    facts = {'sameBytes': digest('value') == digest('value'),
             'differentBytes': digest('before') != digest('after')}
    return result('PASSED' if all(facts.values()) else 'FAILED', examples=facts,
                  scope='EDUCATIONAL_ONLY', authority='NO_GATE')

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', help='Compatibility label only; never read')
    parser.add_argument('--output', required=True)
    args=parser.parse_args()
    raise SystemExit(finish_cli(examples, args.output, [args.root] if args.root else []))
