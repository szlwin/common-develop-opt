import sys
from pathlib import Path
import unittest
import sqlite3
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from internal.state_safety import (EventLog, MigrationJournal, preflight_snapshot,
                          recover_registration, reopen_order, verify_tool_identity)


class StateSafetyTests(unittest.TestCase):
    def test_same_second_reopen_uses_event_sequence(self):
        log = EventLog(); old = log.append('REVIEW', {'timestamp': 'same-second'})
        new = log.append('REOPEN', {'timestamp': 'same-second'})
        self.assertTrue(log.verify())
        self.assertEqual('REOPEN_AFTER_REVIEW', reopen_order(log, old['sequence'], new['sequence']))
        log.events[0]['payload']['timestamp'] = 'tampered'
        self.assertEqual('UNKNOWN', reopen_order(log, old['sequence'], new['sequence']))

    def test_publish_preflight_rechecks_snapshot(self):
        self.assertEqual('READY', preflight_snapshot('a', 'a'))
        self.assertEqual('INPUT_CHANGED', preflight_snapshot('a', 'b'))
        self.assertEqual('UNKNOWN', preflight_snapshot('', 'b'))

    def test_interrupted_registration_is_recoverable(self):
        self.assertEqual('RECORD_REQUIRED', recover_registration({'state': 'COMMAND_SUCCEEDED'}))
        self.assertEqual('ALREADY_RECORDED', recover_registration({'state': 'RECORDED'}))
        self.assertEqual('SAFE_TO_RETRY', recover_registration({'state': 'COMMAND_FAILED'}))

    def test_tool_upgrade_fails_identity_closed(self):
        a = {'name': 'tool', 'version': '1', 'digest': 'a'}
        self.assertEqual('MATCH', verify_tool_identity(a, dict(a)))
        self.assertEqual('IDENTITY_CHANGED', verify_tool_identity(a, dict(a, version='2')))

    def test_migration_retry_and_rollback_preserve_history(self):
        journal = MigrationJournal()
        self.assertEqual('APPLIED', journal.apply('M1'))
        self.assertEqual('ALREADY_APPLIED', journal.apply('M1'))
        self.assertEqual('ROLLED_BACK', journal.rollback('M1'))
        self.assertEqual([('APPLY', 'M1'), ('ROLLBACK', 'M1')], journal.history)

    def test_recovery_sequence_is_consistent_end_to_end(self):
        log = EventLog(); published = log.append('PUBLISH_PRECHECK', {'digest': 'a'})
        self.assertEqual('READY', preflight_snapshot('a', 'a'))
        command = log.append('COMMAND_SUCCEEDED', {'result': 'R1'})
        self.assertEqual('RECORD_REQUIRED', recover_registration({'state': 'COMMAND_SUCCEEDED'}))
        recorded = log.append('RESULT_RECORDED', {'result': 'R1'})
        self.assertTrue(log.verify())
        self.assertEqual('REOPEN_AFTER_REVIEW', reopen_order(log, published['sequence'], recorded['sequence']))

    def test_storage_transaction_rolls_back_and_retries_idempotently(self):
        db = sqlite3.connect(':memory:')
        db.execute('create table events (id text primary key, value text)')
        try:
            db.execute('begin')
            db.execute("insert into events values ('E1', 'published')")
            raise RuntimeError('injected registration interruption')
        except RuntimeError:
            db.rollback()
        self.assertEqual(0, db.execute('select count(*) from events').fetchone()[0])
        db.execute("insert into events values ('E1', 'published')")
        db.commit()
        db.execute("insert or ignore into events values ('E1', 'published')")
        db.commit()
        self.assertEqual(1, db.execute('select count(*) from events').fetchone()[0])


if __name__ == '__main__':
    unittest.main()
