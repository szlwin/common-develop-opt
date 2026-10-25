"""Separate historical Evidence integrity from current validity and reuse."""
from __future__ import annotations
import hashlib
import json


def classify(evidence, *, current_revision, current_digest):
    if not isinstance(evidence, dict):
        return {'integrity': 'UNKNOWN', 'validity': 'UNKNOWN', 'reuse': 'UNKNOWN'}
    recorded = evidence.get('contentDigest')
    snapshot = evidence.get('snapshotDigest')
    integrity = 'INTACT' if recorded and snapshot and recorded == snapshot else 'TAMPERED'
    validity = 'CURRENT' if integrity == 'INTACT' and evidence.get('revision') == current_revision and recorded == current_digest else 'HISTORICAL_ONLY'
    reuse = 'ELIGIBLE' if validity == 'CURRENT' else 'INELIGIBLE'
    return {'integrity': integrity, 'validity': validity, 'reuse': reuse}


def commit_state(archive_exists, review_exists):
    if archive_exists and review_exists:
        return 'COMMITTED'
    if archive_exists or review_exists:
        return 'RECOVERABLE_PARTIAL'
    return 'NOT_COMMITTED'


from runtime._core.canonical_json import digest_json as digest_snapshot
