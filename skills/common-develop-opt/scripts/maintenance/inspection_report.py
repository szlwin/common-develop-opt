"""Summarize scoped source observations without granting lifecycle authority."""
from pathlib import Path
import argparse
if __package__:
    from .inspection_io import result
else:
    from inspection_io import result
from diagnostic_io import finish_cli, load_json


def summarize(values):
    statuses = {key: value.get('status', 'UNKNOWN') if isinstance(value, dict) else 'UNKNOWN'
                for key, value in values.items()}
    unknown = [key for key, status in statuses.items() if status not in {'PASSED', 'FAILED'}]
    failed = [key for key, status in statuses.items() if status == 'FAILED']
    return result('UNKNOWN' if unknown else ('FAILED' if failed else 'PASSED'),
                  inputs=statuses, failedInputs=failed, unknownInputs=unknown,
                  authority='OBSERVATION_ONLY', scope='SOURCE_INSPECTION',
                  limitation='Local observation completion is not workflow acceptance.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('baseline', 'inventory', 'scenarios', 'behavior', 'output'):
        parser.add_argument('--'+name, required=True)
    args = parser.parse_args()
    inputs = {key: getattr(args, key) for key in ('baseline', 'inventory', 'scenarios', 'behavior')}
    return finish_cli(lambda: summarize({k: load_json(p) for k,p in inputs.items()}), args.output, list(inputs.values()))

if __name__ == '__main__':
    raise SystemExit(main())
