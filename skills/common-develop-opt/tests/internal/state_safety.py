"""Test helpers modeling storage-neutral lifecycle safety observations.

These helpers deliberately model observations and recovery decisions only;
they do not write a project task ledger or advance a lifecycle stage.
"""
from __future__ import annotations
import hashlib
import json


from runtime._core.canonical_json import canonical_json as _canonical


class EventLog:
    def __init__(self):
        self.events = []
        self._head = '0' * 64

    def append(self, kind, payload):
        seq = len(self.events) + 1
        event = {'sequence': seq, 'kind': str(kind), 'payload': payload,
                 'previousHash': self._head}
        event['hash'] = hashlib.sha256(_canonical(event).encode()).hexdigest()
        self.events.append(event)
        self._head = event['hash']
        return dict(event)

    def verify(self):
        previous = '0' * 64
        for expected, event in enumerate(self.events, 1):
            if event.get('sequence') != expected or event.get('previousHash') != previous:
                return False
            copy = dict(event); digest = copy.pop('hash', None)
            if digest != hashlib.sha256(_canonical(copy).encode()).hexdigest():
                return False
            previous = digest
        return True


def reopen_order(log, old_review_sequence, new_review_sequence):
    """Use event order, never wall-clock timestamps, for reopen decisions."""
    if not log.verify() or not isinstance(old_review_sequence, int) or not isinstance(new_review_sequence, int):
        return 'UNKNOWN'
    if old_review_sequence >= new_review_sequence:
        return 'INVALID_ORDER'
    return 'REOPEN_AFTER_REVIEW'


def preflight_snapshot(expected_digest, current_digest):
    if not expected_digest or not current_digest:
        return 'UNKNOWN'
    return 'READY' if expected_digest == current_digest else 'INPUT_CHANGED'


def recover_registration(journal):
    """Resolve an interrupted result registration without blind rerun."""
    if not isinstance(journal, dict):
        return 'UNKNOWN'
    state = journal.get('state')
    if state == 'RECORDED':
        return 'ALREADY_RECORDED'
    if state == 'COMMAND_SUCCEEDED':
        return 'RECORD_REQUIRED'
    if state in {'NOT_STARTED', 'COMMAND_FAILED'}:
        return 'SAFE_TO_RETRY'
    return 'UNKNOWN'


def verify_tool_identity(expected, actual):
    if not isinstance(expected, dict) or not isinstance(actual, dict):
        return 'UNKNOWN'
    keys = ('name', 'version', 'digest')
    if any(not expected.get(key) or not actual.get(key) for key in keys):
        return 'UNKNOWN'
    return 'MATCH' if all(expected[key] == actual[key] for key in keys) else 'IDENTITY_CHANGED'


class MigrationJournal:
    def __init__(self):
        self.applied = set()
        self.history = []

    def apply(self, migration_id):
        if migration_id in self.applied:
            return 'ALREADY_APPLIED'
        self.applied.add(migration_id); self.history.append(('APPLY', migration_id))
        return 'APPLIED'

    def rollback(self, migration_id):
        if migration_id not in self.applied:
            return 'NOT_APPLIED'
        self.applied.remove(migration_id); self.history.append(('ROLLBACK', migration_id))
        return 'ROLLED_BACK'
