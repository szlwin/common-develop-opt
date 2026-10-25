"""Verify candidate file inputs and write archive and Review manifests."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
from diagnostic_io import finish_cli, load_json, project_path, result, is_digest
from document_core.evidence_validity import commit_state
from candidate_inputs import measure, descriptor
from read_session import ReadSession
from diagnostic_io import output_path, write_json


from runtime._core.canonical_json import digest_json as _digest


def publish(root, candidate, archive_output, review_output):
    root = Path(root).resolve()
    archive_output = Path(archive_output).resolve(); review_output = Path(review_output).resolve()
    if archive_output == review_output or archive_output == root or review_output == root:
        return result('UNKNOWN', code='OUTPUT_PATH_INVALID')
    session = ReadSession()
    checked = measure(root, candidate, session)
    if checked['status'] != 'PASSED':
        return checked
    if candidate.get('status') not in (None, 'READY'):
        return result('FAILED', code='CANDIDATE_NOT_READY')
    protected = [root / row['path'] for rows in checked['inputs'].values() for row in rows]
    try:
        output_path(archive_output, protected)
        output_path(review_output, protected)
        session.verify()
    except (OSError, ValueError) as exc:
        return result('FAILED', code='CANDIDATE_OUTPUT_REJECTED', diagnostic=str(exc))
    review = {'schemaVersion': 1, 'kind': 'REVIEW_INPUT_SET', 'authority': 'DERIVED',
              'artifactId': candidate['artifactId'], 'revision': candidate['revision'],
              'contentDigest': candidate['contentDigest'],
              'dependencyManifestDigest': candidate['dependencyManifestDigest'],
              'evidenceDigest': checked['evidenceDigest'], 'inputs': checked['inputs'],
              'candidate': descriptor(candidate, root=root)}
    archive = {'schemaVersion': 1, 'kind': 'CANDIDATE_ARCHIVE', 'authority': 'DERIVED',
               'artifactId': candidate['artifactId'], 'revision': candidate['revision'],
               'candidateDigest': _digest(candidate), 'reviewInputDigest': _digest(review),
               'state': 'SEALED'}
    try:
        session.verify()
        for target, payload in ((archive_output, archive), (review_output, review)):
            write_json(payload, target, protected)
    except OSError as exc:
        return result('UNKNOWN', code='PUBLISH_WRITE_FAILED', diagnostic=str(exc), archiveWritten=archive_output.is_file(), reviewWritten=review_output.is_file())
    return result('PASSED', archive=archive, review=review, archiveWritten=True, reviewWritten=True)


def recover_publish(archive_output, review_output):
    """Classify an interrupted seal without blindly regenerating either file."""
    archive = Path(archive_output).is_file()
    review = Path(review_output).is_file()
    state = commit_state(archive, review)
    if archive or review:
        try:
            a = load_json(archive_output) if archive else None
            r = load_json(review_output) if review else None
            if archive and (not isinstance(a, dict) or a.get('kind') != 'CANDIDATE_ARCHIVE' or a.get('state') != 'SEALED'
                                  or any(not isinstance(a.get(k), str) or not a[k].strip() for k in ('artifactId', 'revision'))
                                  or any(not is_digest(a.get(k)) for k in ('candidateDigest', 'reviewInputDigest'))):
                raise ValueError('invalid archive')
            if review:
                if not isinstance(r, dict):
                    raise ValueError('invalid review')
                from review_lifecycle import validate_review_inputs
                probe = a or {'kind': 'CANDIDATE_ARCHIVE', 'state': 'SEALED',
                              'artifactId': r.get('artifactId'), 'revision': r.get('revision'),
                              'reviewInputDigest': _digest(r)}
                if validate_review_inputs(probe, r)['status'] != 'PASSED':
                    raise ValueError('invalid or mismatched review inputs')
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            return result('FAILED', code='PUBLISH_STATE_CORRUPT', commitState='CORRUPT',
                          action='REPAIR_REQUIRED', diagnostic=str(exc))
    return {'status': 'PASSED', 'commitState': state,
            'action': 'NONE' if state == 'COMMITTED' else ('RETRY_MISSING_SIDE' if state == 'RECOVERABLE_PARTIAL' else 'START')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True); parser.add_argument('--candidate', required=True)
    parser.add_argument('--archive-output', required=True); parser.add_argument('--review-output', required=True)
    args = parser.parse_args()
    return finish_cli(lambda: __import__("application_services").seal_candidate(args.root, load_json(args.candidate), args.archive_output, args.review_output),
                      args.archive_output + '.report.json', [args.root, args.candidate])


if __name__ == '__main__':
    raise SystemExit(main())
