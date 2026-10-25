"""Observable finish/reopen behavior through the locked lifecycle dispatcher."""
import argparse
import contextlib
import copy
import io
import json
from pathlib import Path
import sys
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from attempt_fixture import AttemptFixture
from runtime import long_task, work_mode, work_record
from runtime._long_task import lifecycle_helpers, record_io, task_events
from runtime._long_task.commands import attempts, phases
from runtime.error_catalog import LifecycleError
from test_wk_functional import run_wk


class AttemptPhaseRefactorTests(AttemptFixture):
    def invoke(self, name, args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = getattr(long_task, name)(args)
        self.assertEqual(0, exit_code, output.getvalue())
        return json.loads(output.getvalue()) if output.getvalue() else None

    def save_state(self, state):
        document, _ = long_task.read_task_state_cache(self.task_dir)
        payloads, _, _ = long_task.append_task_events_payloads(
            self.task_dir, long_task.task_state_path(self.task_dir), document, state,
            [], synced_at='2026-09-14T00:00:00Z')
        long_task.transactional_text_write(payloads)

    def reopen_args(self, **changes):
        values = dict(task_dir=str(self.task_dir), agent='ProjectManagerAgent',
                      from_phase='development', changed_phase='', source_revision='',
                      confirmation_ref='', reason='upstream changed', round='')
        values.update(changes)
        return argparse.Namespace(**values)

    def assert_finish_status(self, status, task_status):
        attempt = self._start()
        args = self._finish_args(attempt, '')
        args.auto_evidence = False
        args.status = status
        args.output_revision = 'DEV-R01' if status in {'PASSED', 'PARTIAL'} else ''
        args.failure_reason = 'retry needed' if status in {'FAILED', 'BLOCKED'} else ''
        if status == 'PASSED':
            path = self.task_dir / 'evidence' / 'result.txt'
            path.write_text('verified output', encoding='utf-8')
            entry = long_task.evidence.register_evidence(
                self.task_dir, agent='DevelopAgent', evidence_type='document_ref',
                ref='evidence/result.txt', revision='DEV-R01', phase='development', scope_refs=['DEV-1'])
            args.evidence_ref = [entry['evidence_id'], entry['evidence_id']]
        result = self.invoke('command_finish_attempt', args)
        self.assertEqual((status, task_status), (result['status'], result['taskStatus']))
        history = long_task.load_task_history(self.task_dir)
        self.assertEqual(status, history['attempts'][-1]['status'])
        events = task_events.read_event_history(self.task_dir)
        self.assertIn('ATTEMPT_FINISHED', [event['type'] for event in events])
        state = long_task.runtime_state(self.task_dir)
        self.assertEqual([], state['active_task_ids'])
        self.assertEqual('', state['current_attempt_id'])

    def test_finish_passed(self):
        self.assert_finish_status('PASSED', 'PASSED')

    def test_finish_partial(self):
        self.assert_finish_status('PARTIAL', 'REWORK')

    def test_finish_failed(self):
        self.assert_finish_status('FAILED', 'REWORK')

    def test_finish_blocked(self):
        self.assert_finish_status('BLOCKED', 'BLOCKED')

    def test_finish_cancelled(self):
        self.assert_finish_status('CANCELLED', 'CANCELLED')

    def test_finish_invalid_options_preserve_ledger(self):
        attempt = self._start()
        for changes, code in [
            ({'status': 'FAILED'}, 'LIFECYCLE_AUTO_EVIDENCE_STATUS_INVALID'),
            ({'check': ''}, 'LIFECYCLE_AUTO_EVIDENCE_CHECK_REQUIRED'),
            ({'auto_evidence': False}, 'LIFECYCLE_AUTO_EVIDENCE_CHECK_WITHOUT_FLAG'),
        ]:
            args = self._finish_args(attempt, 'python3 -c "print(42)"')
            vars(args).update(changes)
            before = task_events.event_path(self.task_dir).read_bytes()
            with self.assertRaises(LifecycleError) as caught:
                self.invoke('command_finish_attempt', args)
            self.assertEqual(code, caught.exception.code)
            self.assertEqual(before, task_events.event_path(self.task_dir).read_bytes())

    def test_finish_auto_failure_audits_without_finishing(self):
        command = 'python3 -c "print(42)"'
        self._set_validation_command(command)
        attempt = self._start()
        for timed_out in (False, True):
            result = {'status': 'FAILED', 'evidenceId': 'EVIDENCE-AUDIT', 'exitCode': 1,
                      'timedOut': timed_out, 'durationMs': 10, 'timeoutSeconds': 1,
                      'commandResultRef': 'evidence/failed.json'}
            with mock.patch.object(attempts, 'run_and_register', return_value=result):
                with self.assertRaises(LifecycleError) as caught:
                    self.invoke('command_finish_attempt', self._finish_args(attempt, command))
            suffix = 'TIMEOUT' if timed_out else 'FAILED'
            self.assertEqual('LIFECYCLE_AUTO_EVIDENCE_COMMAND_' + suffix, caught.exception.code)
            recorded = long_task.load_task_history(self.task_dir)['attempts'][-1]
            self.assertEqual('RUNNING', recorded['status'])
            self.assertEqual(timed_out, recorded['last_command_audit']['timed_out'])

    def test_reopen_preserves_files_and_archives_review(self):
        _, state = long_task.read_task_state_cache(self.task_dir)
        review = state['collaboration_reviews']['development']
        review.update(artifact_revision='DEV-R01', independent_conclusions={'Reviewer': {'conclusion': 'PASSED'}})
        self.save_state(state)
        plan = (self.task_dir / 'task_plan.json').read_bytes()
        canonical = self.task_dir / 'canonical.txt'
        canonical.write_text('keep current document', encoding='utf-8')
        result = self.invoke('command_reopen_phase', self.reopen_args())
        self.assertEqual([], result['cancelledAttemptIds'])
        self.assertEqual('keep current document', canonical.read_text())
        self.assertEqual(plan, (self.task_dir / 'task_plan.json').read_bytes())
        state = long_task.runtime_state(self.task_dir)
        self.assertEqual('REWORK', state['task_status'])
        self.assertEqual('DEV-R01', state['collaboration_reviews']['development']['review_history'][-1]['artifact_revision'])
        self.assertEqual([], long_task.acceptance.load_document(self.task_dir)['assertions'])

    @property
    def work_path(self):
        return self.doc_root / 'version' / 'V_TEST' / 'work_record.md'

    def snapshot(self):
        return {p.relative_to(self.doc_root).as_posix(): p.read_bytes()
                for p in self.doc_root.rglob('*') if p.is_file() and p.suffix != '.lock'}

    def validated_work_events(self):
        result = self.invoke('command_validate_work_record', argparse.Namespace(
            doc_root=str(self.doc_root), version='V_TEST', agent='ProjectManagerAgent'))
        self.assertEqual('PASSED', result['status'], result)
        return work_record.read_events(self.work_path)[1]

    def test_reopen_active_attempt_cancels_and_writes_current_work_record(self):
        attempt_id = self._start()
        old_iteration = long_task.runtime_state(self.task_dir)['artifact_revisions']['development']['iteration_id']
        history_before = self.validated_work_events()
        plan = (self.task_dir / 'task_plan.json').read_bytes()
        canonical = self.task_dir / 'canonical.txt'
        canonical.write_text('preserve source', encoding='utf-8')
        event_count = len(task_events.read_event_history(self.task_dir))
        result = self.invoke('command_reopen_phase', self.reopen_args())
        self.assertEqual([attempt_id], result['cancelledAttemptIds'])
        state = long_task.runtime_state(self.task_dir)
        self.assertEqual('REWORK', state['task_status'])
        self.assertEqual([], state['active_task_ids'])
        self.assertEqual('', state['current_attempt_id'])
        self.assertNotEqual(old_iteration, result['newIterationId'])
        attempt = long_task.load_task_history(self.task_dir)['attempts'][-1]
        self.assertEqual(('CANCELLED', 'STALE_INVALIDATION'), (attempt['status'], attempt['failure_type']))
        history = self.validated_work_events()
        self.assertEqual(history_before, history[:-1])
        self.assertEqual((attempt_id, old_iteration, 'development', 'CANCELLED'),
                         tuple(history[-1][k] for k in ('attempt_id', 'iteration_id', 'phase', 'status')))
        self.assertEqual('TASK_ATTEMPT_CANCELLED', history[-1]['event_type'])
        types = [e['type'] for e in task_events.read_event_history(self.task_dir)[event_count:]]
        self.assertEqual(1, types.count('ATTEMPT_CANCELLED'))
        self.assertLess(types.index('ATTEMPT_CANCELLED'), types.index('PHASE_STALE'))
        self.assertEqual([], long_task.acceptance.load_document(self.task_dir)['assertions'])
        self.assertEqual(plan, (self.task_dir / 'task_plan.json').read_bytes())
        self.assertEqual('preserve source', canonical.read_text())
        self.assertEqual([], long_task.validate_task(self.task_dir)[0])
        projected = long_task.load_task_history(self.task_dir)['task_runtime']['DEV-1']
        self.assertEqual(('REWORK', result['newIterationId'], 0),
                         (projected['status'], projected['iteration_id'], projected['attempts']))

    def test_reopen_repeat_does_not_duplicate_cancellation(self):
        self._start()
        self.invoke('command_reopen_phase', self.reopen_args())
        before = self.work_path.read_bytes()
        result = self.invoke('command_reopen_phase', self.reopen_args())
        self.assertEqual([], result['cancelledAttemptIds'])
        self.assertEqual(before, self.work_path.read_bytes())
        self.assertEqual(1, sum(e['type'] == 'ATTEMPT_CANCELLED' for e in task_events.read_event_history(self.task_dir)))

    def test_reopen_recovers_from_cache_loss_and_starts_new_attempt(self):
        first = self._start()
        reopened = self.invoke('command_reopen_phase', self.reopen_args())
        ledger = task_events.event_path(self.task_dir).read_bytes()
        long_task.task_state_path(self.task_dir).unlink()
        self.assertEqual([], long_task.validate_task(self.task_dir)[0])
        self.assertEqual(ledger, task_events.event_path(self.task_dir).read_bytes())
        second = self._start()
        self.assertNotEqual(first, second)
        recorded = long_task.load_task_history(self.task_dir)['attempts']
        self.assertEqual(['CANCELLED', 'RUNNING'], [a['status'] for a in recorded])
        self.assertEqual(reopened['newIterationId'], recorded[-1]['iteration_id'])

    def test_cancellation_in_current_iteration_still_updates_runtime(self):
        self._start()
        events = task_events.read_event_history(self.task_dir)
        record = copy.deepcopy(long_task.load_task_history(self.task_dir)['attempts'][-1])
        record['status'] = 'CANCELLED'
        reduced = task_events.reduce_events([*events, {
            'seq': events[-1]['seq'] + 1, 'type': 'ATTEMPT_CANCELLED',
            'entity_id': record['attempt_id'], 'payload': {'attempt': record},
        }])
        self.assertEqual('CANCELLED', reduced['task_runtime']['DEV-1']['status'])
        self.assertEqual(record['iteration_id'], reduced['task_runtime']['DEV-1']['iteration_id'])

    def test_reopen_new_attempt_in_same_second_has_distinct_work_record(self):
        with mock.patch.object(phases, 'now', return_value='2026-09-14T10:00:00Z'):
            first = self._start()
            self.invoke('command_reopen_phase', self.reopen_args())
            second = self._start()
            self.assertNotEqual(first, second)
            self.invoke('command_reopen_phase', self.reopen_args())
        events = self.validated_work_events()
        cancelled = [e for e in events if e['event_type'] == 'TASK_ATTEMPT_CANCELLED']
        self.assertEqual([first, second], [e['attempt_id'] for e in cancelled])
        self.assertEqual(2, len({e['record_id'] for e in cancelled}))

    def test_cancelled_work_payload_batches_and_preserves_history_without_writing(self):
        first_id = self._start()
        self.invoke('command_reopen_phase', self.reopen_args())
        history = self.validated_work_events()
        before = self.snapshot()
        sample = long_task.load_task_history(self.task_dir)['attempts'][-1]
        cancelled = []
        for task_id in ('DEV-2', 'DEV-3'):
            entry = copy.deepcopy(sample)
            entry.update(task_id=task_id, attempt_id=f'ATTEMPT-{task_id}-I001-A001')
            cancelled.append(entry)
        payloads = {}
        lifecycle_helpers.add_cancelled_attempt_work_payload(
            payloads, self.task_dir, long_task.runtime_state(self.task_dir), cancelled,
            agent='ProjectManagerAgent', timestamp='2026-09-14T10:00:00Z')
        self.assertEqual(before, self.snapshot())
        self.assertEqual({self.work_path}, set(payloads))
        record_io.transactional_text_write(payloads)
        events = self.validated_work_events()
        self.assertEqual(history, events[:-2])
        self.assertEqual([first_id, *[e['attempt_id'] for e in cancelled]], [e['attempt_id'] for e in events])

    def test_reopen_creates_missing_work_record_in_transaction(self):
        attempt = self._start()
        self.work_path.unlink()
        result = self.invoke('command_reopen_phase', self.reopen_args())
        self.assertEqual([attempt], result['cancelledAttemptIds'])
        self.assertEqual(attempt, self.validated_work_events()[-1]['attempt_id'])

    def test_cancelled_work_record_ids_preserve_distinct_raw_identities(self):
        cancelled = [dict(task_id=task_id, attempt_id=f'ATTEMPT-{task_id}-I001-A001',
                          phase='development', iteration_id='ITER-DEV-001', iteration_no=1,
                          next_action='retry')
                     for task_id in ('DEV A', 'DEV?A')]
        self.assertEqual(work_record.safe_token(cancelled[0]['attempt_id']),
                         work_record.safe_token(cancelled[1]['attempt_id']))
        payloads = {}
        lifecycle_helpers.add_cancelled_attempt_work_payload(
            payloads, self.task_dir, long_task.runtime_state(self.task_dir), cancelled,
            agent='ProjectManagerAgent', timestamp='2026-09-14T10:00:00Z')
        record_io.transactional_text_write(payloads)
        events = self.validated_work_events()
        self.assertEqual(2, len({e['event_id'] for e in events}))
        self.assertEqual(2, len({e['record_id'] for e in events}))

    def test_reopen_work_record_write_failure_rolls_back_all_files(self):
        self._start()
        real_replace = record_io.os.replace

        def fail_work_write(source, destination):
            if Path(destination) == self.work_path:
                raise OSError('injected work record write failure')
            return real_replace(source, destination)

        for missing in (False, True):
            with self.subTest(missing=missing):
                if missing:
                    self.work_path.unlink()
                before = self.snapshot()
                with mock.patch.object(record_io.os, 'replace', side_effect=fail_work_write):
                    with self.assertRaisesRegex(OSError, 'injected work record write failure') as caught:
                        self.invoke('command_reopen_phase', self.reopen_args())
                self.assertEqual('COMPLETE', caught.exception.rollback_details['rollbackStatus'])
                self.assertEqual(before, self.snapshot())
                self.assertEqual('RUNNING', long_task.load_task_history(self.task_dir)['attempts'][-1]['status'])
        self.invoke('command_reopen_phase', self.reopen_args())
        self.assertEqual(1, len(self.validated_work_events()))

    def test_reopen_rejects_corrupt_work_record_without_writing(self):
        self._start()
        self.work_path.write_text('invalid work record', encoding='utf-8')
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'current format'):
            self.invoke('command_reopen_phase', self.reopen_args())
        self.assertEqual(before, self.snapshot())

    def test_reopen_minimal_supersedes_skipped_outcomes(self):
        work_mode.apply_mode(work_mode.work_path(self.doc_root, 'V_TEST'), work_mode.WorkMode(minimal=True))
        self.invoke('command_reopen_phase', self.reopen_args())
        self.invoke('command_reopen_phase', self.reopen_args())
        outcomes = long_task.load_task_history(self.task_dir)['outcomes']
        testing = [item for item in outcomes if item['phase'] == 'testing']
        self.assertEqual([False, True], [item['is_current'] for item in testing])
        self.assertEqual(['SKIPPED', 'SKIPPED'], [item['status'] for item in testing])

    def test_reopen_closed_lightweight_clears_waivers_and_overrides(self):
        _, state = long_task.read_task_state_cache(self.task_dir)
        state['work_mode'].update(skipped_phases=['testing'], lightweight_waived=True,
                                  minimal_skipped_phases=['testing'])
        state['work_policy'] = {'phase_overrides': {'testing': {'status': 'SKIPPED'}},
                                'explicit_reopen_required': True}
        self.save_state(state)
        self.invoke('command_reopen_phase', self.reopen_args(from_phase='testing'))
        state = long_task.runtime_state(self.task_dir)
        self.assertEqual([], state['work_mode']['skipped_phases'])
        self.assertEqual([], state['work_mode']['minimal_skipped_phases'])
        self.assertFalse(state['work_policy']['explicit_reopen_required'])

    def test_reopen_upstream_requires_prior_authorization(self):
        for values in ({}, {'confirmation_ref': 'unknown'},
                       {'confirmation_ref': 'unknown', 'source_revision': 'DEV-R01'}):
            before = task_events.event_path(self.task_dir).read_bytes()
            with self.assertRaises(LifecycleError):
                self.invoke('command_reopen_phase', self.reopen_args(from_phase='implementation_plan', **values))
            self.assertEqual(before, task_events.event_path(self.task_dir).read_bytes())

    def test_reopen_consumes_upstream_authorization_once(self):
        code, result, out, err = run_wk([
            'confirm', '-g', 'ProjectManagerAgent', '--task-dir', str(self.task_dir),
            '--reopen-from', 'implementation_plan', '--changed-phase', 'development',
            '--source-revision', 'DEV-R01', '--reason', 'upstream changed', '--apply',
        ])
        self.assertEqual(0, code, err or out)
        args = self.reopen_args(from_phase='implementation_plan', changed_phase='development',
                                source_revision='DEV-R01', confirmation_ref=result['requestFingerprint'])
        self.invoke('command_reopen_phase', args)
        events = task_events.read_event_history(self.task_dir)
        consumption = [e for e in events if e['type'] == 'AUTHORIZATION_BATCH_CONSUMED']
        self.assertEqual(1, len(consumption))
        self.assertEqual(result['requestFingerprint'], consumption[0]['payload']['requestFingerprint'])
        stale = [e for e in events if e['type'] == 'PHASE_STALE'][-1]
        self.assertLess(consumption[0]['seq'], stale['seq'])
        before = task_events.event_path(self.task_dir).read_bytes()
        with self.assertRaisesRegex(LifecycleError, 'valid prior reopen authorization'):
            self.invoke('command_reopen_phase', args)
        self.assertEqual(before, task_events.event_path(self.task_dir).read_bytes())

    def test_reopen_authorization_survives_project_doc_move(self):
        code, result, out, err = run_wk([
            'confirm', '-g', 'ProjectManagerAgent', '--task-dir', str(self.task_dir),
            '--reopen-from', 'implementation_plan', '--changed-phase', 'development',
            '--source-revision', 'DEV-R01', '--reason', 'upstream changed', '--apply',
        ])
        self.assertEqual(0, code, err or out)
        confirmation = [
            event for event in task_events.read_event_history(self.task_dir)
            if event['type'] == 'AUTHORIZATION_BATCH_CONFIRMED'
        ][-1]
        self.assertEqual('version/V_TEST/task/FEAT001', confirmation['payload']['taskPath'])
        self.assertNotIn('taskDir', confirmation['payload'])

        moved_root = Path(self.tmp.name) / 'moved_project_doc'
        self.doc_root.rename(moved_root)
        self.doc_root = moved_root
        self.task_dir = moved_root / 'version' / 'V_TEST' / 'task' / 'FEAT001'
        args = self.reopen_args(
            from_phase='implementation_plan', changed_phase='development',
            source_revision='DEV-R01', confirmation_ref=result['requestFingerprint'],
        )
        self.invoke('command_reopen_phase', args)
        errors, _warnings, _state, _tasks = long_task.validate_task(self.task_dir)
        self.assertFalse(
            [error for error in errors if 'boundary authorization' in error],
            errors,
        )

    def test_reopen_lightweight_rejects_testing(self):
        work_mode.apply_mode(work_mode.work_path(self.doc_root, 'V_TEST'), work_mode.WorkMode(lightweight=True))
        before = task_events.event_path(self.task_dir).read_bytes()
        with self.assertRaisesRegex(LifecycleError, 'wk -ws'):
            self.invoke('command_reopen_phase', self.reopen_args(from_phase='testing'))
        self.assertEqual(before, task_events.event_path(self.task_dir).read_bytes())

    def test_reopen_architecture_final_review_preserves_implementation(self):
        work_mode.apply_mode(work_mode.work_path(self.doc_root, 'V_TEST'), work_mode.WorkMode(architecture_review=True))
        _, state = long_task.read_task_state_cache(self.task_dir)
        state['architecture_review'] = long_task.new_architecture_review_state(True, 'ARCH-I1', '2026-09-14T00:00:00Z')
        state['architecture_review'].update(step='IMPLEMENTATION_REVIEW_PASSED',
                                           final_review_revision='DEV-R01', final_review_result_refs=['REVIEW-1'])
        self.save_state(state)
        self.invoke('command_reopen_phase', self.reopen_args(from_phase='code_review'))
        state = long_task.runtime_state(self.task_dir)
        self.assertEqual('IMPLEMENTATION_READY_FOR_REVIEW', state['architecture_review']['step'])
        self.assertEqual([], state['architecture_review']['final_review_result_refs'])

    def test_reopen_architecture_requires_implementation_ready(self):
        work_mode.apply_mode(work_mode.work_path(self.doc_root, 'V_TEST'), work_mode.WorkMode(architecture_review=True))
        before = task_events.event_path(self.task_dir).read_bytes()
        with self.assertRaisesRegex(LifecycleError, 'concrete implementation is ready'):
            self.invoke('command_reopen_phase', self.reopen_args(from_phase='code_review'))
        self.assertEqual(before, task_events.event_path(self.task_dir).read_bytes())

    def test_reopen_architecture_development_resets_skeleton(self):
        work_mode.apply_mode(work_mode.work_path(self.doc_root, 'V_TEST'), work_mode.WorkMode(architecture_review=True))
        self.invoke('command_reopen_phase', self.reopen_args())
        state = long_task.runtime_state(self.task_dir)
        self.assertEqual('SKELETON', state['architecture_review']['step'])
        self.assertEqual(state['artifact_revisions']['development']['iteration_id'],
                         state['architecture_review']['skeleton_iteration_id'])


if __name__ == '__main__':
    import unittest
    unittest.main()
