"""Review input and reopen history checks for the P2 read/write protocol."""
from __future__ import annotations
from document_core.evidence_validity import digest_snapshot
from diagnostic_io import is_digest, digest_manifest
from candidate_inputs import GROUPS, descriptor


def validate_review_inputs(archive, review, current=None, *, root=None):
    if not isinstance(archive, dict) or not isinstance(review, dict):
        return {'status': 'UNKNOWN', 'code': 'REVIEW_INPUTS_INVALID'}
    if (archive.get('state') != 'SEALED' or archive.get('kind') != 'CANDIDATE_ARCHIVE'
            or review.get('kind') != 'REVIEW_INPUT_SET'):
        return {'status': 'FAILED', 'code': 'REVIEW_INPUTS_NOT_SEALED'}
    for key in ('artifactId', 'revision'):
        if not isinstance(archive.get(key), str) or not archive[key].strip() or review.get(key) != archive[key]:
            return {'status': 'FAILED', 'code': 'REVIEW_IDENTITY_MISMATCH'}
    try:
        candidate = review['candidate']
        canonical_candidate = descriptor(candidate)
        if any(candidate[key] != review[key] for key in ('artifactId', 'revision', *GROUPS.values())):
            raise ValueError('candidate binding mismatch')
        for group, field in GROUPS.items():
            entries = review['inputs'][group]
            if not isinstance(entries, list) or (group != 'dependencyFiles' and not entries):
                raise ValueError('missing input records')
            if any(not isinstance(row.get('path'), str) or not row['path'] or not is_digest(row.get('sha256')) for row in entries):
                raise ValueError('invalid input record')
            if not is_digest(review.get(field)) or digest_manifest(entries) != review[field]:
                raise ValueError('input digest mismatch')
            if candidate['verification'][field] != review[field]:
                raise ValueError('verification mismatch')
            declared = canonical_candidate[group]
            if len(set(declared)) != len(declared) or sorted(declared) != sorted(row['path'] for row in entries):
                raise ValueError('candidate paths mismatch')
    except (KeyError, TypeError, ValueError, AttributeError):
        return {'status': 'FAILED', 'code': 'REVIEW_INPUTS_INVALID'}
    if current is not None:
        try:
            if descriptor(current, root=root) != canonical_candidate:
                return {'status': 'FAILED', 'code': 'CURRENT_INPUT_DRIFT'}
        except (KeyError, TypeError, ValueError, OSError):
            return {'status': 'FAILED', 'code': 'CURRENT_INPUT_DRIFT'}
    expected = archive.get('reviewInputDigest')
    actual = digest_snapshot(review)
    return {'status': 'PASSED' if expected == actual else 'FAILED',
            'code': None if expected == actual else 'REVIEW_INPUT_DRIFT'}


def reopen_relation(events):
    """Validate replacement order using event sequence, preserving history."""
    if not isinstance(events, list) or not events:
        return {'status': 'UNKNOWN', 'code': 'HISTORY_EMPTY'}
    sequences = [x.get('sequence') for x in events]
    if any(not isinstance(x, int) for x in sequences) or sequences != sorted(set(sequences)):
        return {'status': 'FAILED', 'code': 'HISTORY_ORDER_INVALID'}
    reviews = [x for x in events if x.get('kind') == 'REVIEW']
    reopens = [x for x in events if x.get('kind') == 'REOPEN']
    if reopens and not reviews:
        return {'status': 'FAILED', 'code': 'REOPEN_WITHOUT_REVIEW'}
    if reopens and reopens[-1]['sequence'] <= reviews[0]['sequence']:
        return {'status': 'FAILED', 'code': 'REOPEN_ORDER_INVALID'}
    return {'status': 'PASSED', 'historyPreserved': True}
