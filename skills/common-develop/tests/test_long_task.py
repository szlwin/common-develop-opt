"""Regression tests for the current common-develop long-task schema."""
from __future__ import annotations
import argparse
import copy
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import long_task
import manual_review
import wk
from _long_task import lifecycle_decision, task_events
from _long_task.record_io import read_block
from error_catalog import LifecycleError


def build_review_evidence(task_dir: Path, criterion_id: str, revision: str, phase: str, reviewer: str) -> list[dict]:
    criterion = long_task.REVIEW_CRITERIA[criterion_id]
    policy = criterion['evidencePolicy']
    types: list[str] = []
    for evidence_type in policy.get('allOfTypes', []):
        if evidence_type not in types:
            types.append(evidence_type)
    any_types = policy.get('anyOfTypes', [])
    if any_types and not any(value in types for value in any_types):
        types.append(any_types[0])
    while len(types) < policy.get('minimumEvidenceCount', 1):
        types.append(any_types[0] if any_types else 'document_ref')
    result = []
    for index, evidence_type in enumerate(types, start=1):
        path = task_dir / 'evidence' / 'reviews' / phase / f'{criterion_id}-{index}.txt'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'{criterion_id} current revision evidence', encoding='utf-8')
        entry = long_task.evidence.register_evidence(
            task_dir, agent=reviewer, evidence_type=evidence_type,
            ref=str(path.relative_to(task_dir)), revision=revision, phase=phase,
            scope_refs=[criterion_id],
        )
        result.append({'evidence_id': entry['evidence_id'], 'claim': f'{criterion_id} is satisfied.'})
    return result


def build_conclusion(task_dir: Path, phase: str, reviewer: str, revision: str) -> dict:
    profile = long_task.review_profile(phase, reviewer)
    return {
        'profile_id': f'{phase}:{reviewer}',
        'revision': revision,
        'conclusion': 'PASSED',
        'checked_scope': [f'{phase}/{reviewer}/current-scope'],
        'criteria_results': [
            {
                'criterion_id': criterion_id,
                'status': 'PASSED',
                'evidence': build_review_evidence(task_dir, criterion_id, revision, phase, reviewer),
                'finding_ids': [],
                'reason': '',
            }
            for criterion_id in profile['criterionIds']
        ],
        'finding_ids': [],
        'limitations': [],
        'reviewed_at': '2026-07-19T10:00:00-05:00',
    }

class LongTaskValidationTest(unittest.TestCase):

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.doc_root = Path(self.temp_dir.name) / 'project_doc'
        args = argparse.Namespace(doc_root=str(self.doc_root), version='V_TEST', target='MOD0001', reuse=False)
        self.assertEqual(self.call(long_task.command_init, args), 0)
        self.task_dir = self.doc_root / 'version' / 'V_TEST' / 'task' / 'MOD0001'

    def test_evidence_policy_accepts_registry_used_as_types(self) -> None:
        criterion = {
            'evidencePolicy': {
                'minimumEvidenceCount': 1,
                'allOfTypes': ['design_ref'],
                'anyOfTypes': ['diagram_ref'],
            },
        }
        evidence = [{
            'type': 'config_ref',
            'used_as_types': ['design_ref', 'diagram_ref'],
        }]

        self.assertEqual((True, ''), long_task.evidence_satisfies_policy(evidence, criterion))

    def test_standard_user_skip_is_audited_and_validates_without_review_or_test_pass(self) -> None:
        for phase in ('code_review', 'testing'):
            reason = f'user explicitly waived {phase}'
            confirmation_ref = self.confirm_standard_skip(phase, reason)
            result = self.call(long_task.command_skip_phase, argparse.Namespace(
                task_dir=str(self.task_dir), phase=phase,
                reason=reason, confirmation_ref=confirmation_ref,
                agent='ProjectManagerAgent', json=True,
            ))
            self.assertEqual(0, result)
        state = long_task.runtime_state(self.task_dir)
        self.assertTrue(long_task.phase_skip_allowed('code_review', False, state))
        self.assertTrue(long_task.phase_skip_allowed('testing', False, state))
        self.assertEqual('NOT_APPLICABLE', state['artifact_revisions']['code_review']['status'])
        outcomes = long_task.load_task_history(self.task_dir)['outcomes']
        self.assertEqual('SKIPPED', next(item for item in outcomes if item.get('phase') == 'code_review' and item.get('is_current'))['status'])
        consumptions = long_task.load_task_history(self.task_dir)['authorization_consumptions']
        self.assertEqual(2, len([item for item in consumptions if item['payload'].get('purpose') == 'STANDARD_PHASE_SKIP']))
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertEqual([], errors)

    def test_standard_skip_override_is_fail_closed_when_tampered(self) -> None:
        reason = 'user explicitly waived testing'
        confirmation_ref = self.confirm_standard_skip('testing', reason)
        self.call(long_task.command_skip_phase, argparse.Namespace(
            task_dir=str(self.task_dir), phase='testing',
            reason=reason, confirmation_ref=confirmation_ref,
            agent='ProjectManagerAgent', json=True,
        ))
        document, state = self.read_state()
        original = copy.deepcopy(state)
        cases = {
            'requested_by': 'agent',
            'recorded_at': 'not-a-timestamp',
            'iteration_id': 'ITER-BOGUS',
            'revision': 'SKIP-BOGUS',
            'confirmation_event_id': 'TE-BOGUS',
        }
        for field, value in cases.items():
            with self.subTest(field=field):
                tampered = copy.deepcopy(original)
                tampered['work_policy']['phase_overrides']['testing'][field] = value
                self.write_state(document, tampered)
                errors, _, _, _ = long_task.validate_task(self.task_dir)
                self.assertTrue(errors)
                self.write_state(document, original)
        tampered = copy.deepcopy(original)
        tampered['work_policy']['explicit_reopen_required'] = False
        self.write_state(document, tampered)
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertIn(
            'work_policy.explicit_reopen_required must match active standard or closed-lightweight waivers',
            errors,
        )

    def test_standard_skip_rejects_forged_or_replayed_confirmation(self) -> None:
        args = argparse.Namespace(
            task_dir=str(self.task_dir), phase='testing', reason='skip testing by user request',
            confirmation_ref='FAKE-REF', agent='ProjectManagerAgent', json=True,
        )
        before = task_events.event_path(self.task_dir).read_bytes()
        with self.assertRaisesRegex(LifecycleError, 'confirmation-ref does not resolve'):
            self.call(long_task.command_skip_phase, args)
        self.assertEqual(before, task_events.event_path(self.task_dir).read_bytes())
        confirmation_ref = self.skip_standard_phase('testing', args.reason)
        state = long_task.runtime_state(self.task_dir)
        self.assertIsNone(long_task.authorization.find_valid_standard_skip(
            self.task_dir, state, phase='testing', reason=args.reason,
            confirmation_ref=confirmation_ref,
        ))

    def test_standard_skip_rejects_started_iteration_and_mode_sync_preserves_waiver(self) -> None:
        document, state = self.read_state()
        testing = state['artifact_revisions']['testing']
        self.write_record('task_attempts', 'task-attempts', [{
            'attempt_id': 'ATTEMPT-TESTING-001', 'task_id': 'TASK-TESTING',
            'iteration_id': testing['iteration_id'], 'status': 'RUNNING',
        }])
        reason = 'skip after work began'
        confirmation_ref = self.confirm_standard_skip('testing', reason)
        with self.assertRaisesRegex(LifecycleError, 'current iteration has started'):
            self.call(long_task.command_skip_phase, argparse.Namespace(
                task_dir=str(self.task_dir), phase='testing', reason=reason,
                confirmation_ref=confirmation_ref, agent='ProjectManagerAgent', json=True,
            ))

        # Use the untouched Review phase to verify a regular mode sync does not erase its waiver.
        self.skip_standard_phase('code_review', 'skip independent Review')
        self.call(long_task.command_sync_work_mode, argparse.Namespace(
            task_dir=str(self.task_dir), agent='ProjectManagerAgent',
        ))
        synced = long_task.runtime_state(self.task_dir)
        self.assertIn('code_review', synced['work_policy']['phase_overrides'])
        self.assertTrue(synced['work_policy']['explicit_reopen_required'])

    def test_standard_skip_closes_blocked_task_after_retry_exhaustion(self) -> None:
        self.skip_standard_phase('code_review', 'user also accepts independent Review risk')
        testing_task = self.plan_task('TASK-TESTING', phase='testing')
        document, state = self.read_state()
        testing = state['artifact_revisions']['testing']
        testing_task['iteration_id'] = testing['iteration_id']
        testing_task['iteration_no'] = testing['iteration_no']
        testing_task['input_revisions'] = {
            predecessor: state['artifact_revisions'][predecessor]['revision']
            for predecessor in long_task.phase_predecessors('testing', False)
        }
        self.write_record('task_plan', 'task-plan', [testing_task])
        _plan_document, static_tasks = long_task.read_static_task_plan(self.task_dir)
        document, state = self.read_state()
        testing = state['artifact_revisions']['testing']
        testing['status'] = 'BLOCKED'
        state['current_phase'] = 'testing'
        state['task_status'] = 'BLOCKED'
        self.write_state(document, state)
        self._append_specs([{
            'type': 'TASK_RUNTIME_SET',
            'entity_id': testing_task['id'],
            'timestamp': '2026-07-19T10:00:00-05:00',
            'source': 'test retry exhaustion',
            'payload': {
                'runtime': {
                    'status': 'BLOCKED',
                    'iteration_id': testing['iteration_id'],
                    'iteration_no': testing['iteration_no'],
                },
            },
        }])
        materialized = task_events.materialize_tasks(
            static_tasks, long_task.load_task_history(self.task_dir)
        )
        self.assertEqual(
            'BLOCKED', next(task for task in materialized if task['id'] == testing_task['id'])['status']
        )

        reason = 'user accepts unverified testing risk after retries were exhausted'
        confirmation_ref = self.confirm_standard_skip('testing', reason)
        self.assertEqual(0, self.call(long_task.command_skip_phase, argparse.Namespace(
            task_dir=str(self.task_dir), phase='testing', reason=reason,
            confirmation_ref=confirmation_ref, agent='ProjectManagerAgent', json=True,
        )))

        current = long_task.runtime_state(self.task_dir)
        materialized = task_events.materialize_tasks(
            static_tasks, long_task.load_task_history(self.task_dir)
        )
        self.assertEqual('NOT_APPLICABLE', current['artifact_revisions']['testing']['status'])
        self.assertEqual(
            'NOT_APPLICABLE',
            next(task for task in materialized if task['id'] == testing_task['id'])['status'],
        )
        outcome = next(
            item for item in long_task.load_task_history(self.task_dir)['outcomes']
            if item.get('phase') == 'testing' and item.get('is_current') is True
        )
        self.assertEqual('SKIPPED', outcome['status'])
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertEqual([], errors)
        self.assertEqual(0, self.call(long_task.command_advance_phase, argparse.Namespace(
            task_dir=str(self.task_dir), agent='ProjectManagerAgent',
        )))
        self.assertEqual(
            'completion_verification', long_task.runtime_state(self.task_dir)['current_phase']
        )

    def test_reopen_clears_only_standard_skips_at_or_after_boundary(self) -> None:
        self.skip_standard_phase('code_review', 'skip independent Review')
        self.skip_standard_phase('testing', 'skip test execution')
        self.call(long_task.command_reopen_phase, argparse.Namespace(
            task_dir=str(self.task_dir), from_phase='testing', source_revision='',
            reason='restore test execution', round='', agent='ProjectManagerAgent',
        ))
        state = long_task.runtime_state(self.task_dir)
        self.assertIn('code_review', state['work_policy']['phase_overrides'])
        self.assertNotIn('testing', state['work_policy']['phase_overrides'])
        self.assertTrue(state['work_policy']['explicit_reopen_required'])
        self.assertEqual('IN_PROGRESS', state['artifact_revisions']['testing']['status'])

    def test_pending_reopened_phase_ignores_issue_from_invalidated_downstream_revision(self) -> None:
        _, state = self.read_state()
        state['artifact_revisions']['requirement_confirmation'].update({
            'status': 'PASSED',
            'revision': 'REQCONF-R01',
        })
        state['artifact_revisions']['requirement_analysis'].update({
            'status': 'IN_PROGRESS',
            'revision': '',
            'iteration_no': 2,
        })
        task = {
            'id': 'TASK-REQAN',
            'phase': 'requirement_analysis',
            'depends_on': [],
            'iteration_id': state['artifact_revisions']['requirement_analysis']['iteration_id'],
            'iteration_no': 2,
            'input_revisions': {
                'requirement_confirmation': 'REQCONF-R01',
            },
        }
        issues = [{
            'id': 'ISSUE-DESIGN-OLD',
            'status': 'OPEN',
            'severity': 'P1',
            'phase': 'design',
            'artifact_revision': 'DESIGN-R01',
        }]

        long_task.validate_start_gate(self.task_dir, state, [task], task, issues)

    def test_phase_revision_gate_blocks_current_and_upstream_but_not_superseded_or_downstream(self) -> None:
        issues = [
            {
                'id': 'ISSUE-GLOBAL', 'status': 'OPEN', 'severity': 'P1',
                'phase': 'design', 'artifact_revision': '',
            },
            {
                'id': 'ISSUE-UPSTREAM', 'status': 'OPEN', 'severity': 'P1',
                'phase': 'requirement_confirmation', 'artifact_revision': 'REQCONF-R01',
            },
            {
                'id': 'ISSUE-CURRENT', 'status': 'OPEN', 'severity': 'P1',
                'phase': 'requirement_analysis', 'artifact_revision': 'REQAN-R05',
            },
            {
                'id': 'ISSUE-SUPERSEDED', 'status': 'OPEN', 'severity': 'P1',
                'phase': 'requirement_analysis', 'artifact_revision': 'REQAN-R04',
            },
            {
                'id': 'ISSUE-DOWNSTREAM', 'status': 'OPEN', 'severity': 'P1',
                'phase': 'design', 'artifact_revision': 'DESIGN-R04',
            },
        ]

        self.assertEqual(
            ['ISSUE-GLOBAL', 'ISSUE-UPSTREAM', 'ISSUE-CURRENT'],
            long_task.blocking_issue_ids_for_phase_revision(
                issues, 'requirement_analysis', 'REQAN-R05'
            ),
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @staticmethod
    def call(function, args):
        with contextlib.redirect_stdout(io.StringIO()):
            return function(args)

    def confirm_standard_skip(self, phase: str, reason: str) -> str:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = wk.command_confirm(argparse.Namespace(
                task_dir=str(self.task_dir), until='', reopen_from='', skip_phase=phase,
                changed_phase='', source_revision='', reason=reason,
                agent='ProjectManagerAgent', preview=False, apply=True, json=True,
            ))
        self.assertEqual(0, result)
        return str(json.loads(output.getvalue())['requestFingerprint'])

    def skip_standard_phase(self, phase: str, reason: str) -> str:
        confirmation_ref = self.confirm_standard_skip(phase, reason)
        self.call(long_task.command_skip_phase, argparse.Namespace(
            task_dir=str(self.task_dir), phase=phase, reason=reason,
            confirmation_ref=confirmation_ref, agent='ProjectManagerAgent', json=True,
        ))
        return confirmation_ref

    def _record_path(self, record_name: str) -> Path:
        if record_name == 'runtime_state':
            return long_task.task_state_path(self.task_dir)
        if record_name == 'task_plan':
            return self.task_dir / 'task_plan.json'
        if record_name == 'traceability':
            return self.task_dir / 'traceability.json'
        return self.task_dir / record_name

    def read_record(self, record_name: str, block_name: str):
        if record_name == 'runtime_state':
            return long_task.read_task_state_cache(self.task_dir)
        if record_name == 'task_plan':
            path = self._record_path(record_name)
            value = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'tasks': []}
            return json.dumps(value, ensure_ascii=False, separators=(',', ':')), list(value.get('tasks') or [])
        if record_name == 'traceability':
            path = self._record_path(record_name)
            value = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'items': []}
            items = value.get('items') if isinstance(value, dict) else []
            return json.dumps(value, ensure_ascii=False, separators=(',', ':')), items
        reduced = task_events.reduce_events(task_events.read_events(self.task_dir))
        if record_name == 'task_attempts':
            return '', list(reduced.get('attempts') or [])
        if record_name == 'stage_outcomes':
            return '', list(reduced.get('outcomes') or [])
        if record_name == 'review_issues':
            return '', list(reduced.get('issues') or [])
        return long_task.read_block(self._record_path(record_name), block_name)

    def _append_specs(self, specs: list[dict]) -> None:
        state_document, state = long_task.read_task_state_cache(self.task_dir)
        payloads, _projected, _reduced = long_task.append_task_events_payloads(
            self.task_dir, long_task.task_state_path(self.task_dir), state_document, state, specs,
            synced_at='2026-07-19T10:00:00-05:00',
        )
        for path, text in payloads.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding='utf-8')

    def write_record(self, record_name: str, block_name: str, value) -> None:
        if record_name == 'runtime_state':
            document, state = long_task.read_task_state_cache(self.task_dir)
            payloads, _, _ = long_task.append_task_events_payloads(
                self.task_dir, long_task.task_state_path(self.task_dir), document, value, [],
                synced_at='2026-07-19T10:00:00-05:00',
            )
            long_task.transactional_text_write(payloads)
            return
        if record_name == 'task_plan':
            path = self._record_path(record_name)
            static_tasks = task_events.strip_plan_runtime_fields(value if isinstance(value, list) else [])
            specs = []
            for task in value if isinstance(value, list) else []:
                runtime = {
                    key: task[key] for key in task_events.TASK_PLAN_RUNTIME_FIELDS if key in task
                }
                if runtime:
                    specs.append({
                        'type': 'TASK_RUNTIME_SET', 'entity_id': str(task.get('id') or ''),
                        'payload': {'runtime': runtime},
                    })
            if specs:
                self._append_specs(specs)
            existing = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
            existing['tasks'] = static_tasks
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            return
        if record_name == 'traceability':
            path = self._record_path(record_name)
            existing = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
            existing['items'] = value
            path.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            return
        if record_name == 'task_attempts':
            specs=[]
            for item in value:
                status=str(item.get('status') or '')
                event_type='ATTEMPT_STARTED' if status == 'RUNNING' else ('ATTEMPT_CANCELLED' if status == 'CANCELLED' else 'ATTEMPT_FINISHED')
                specs.append({'type':event_type,'entity_id':str(item.get('attempt_id') or ''),'payload':{'attempt':item}})
            self._append_specs(specs); return
        if record_name == 'stage_outcomes':
            self._append_specs([{'type':'STAGE_OUTCOME_RECORDED','entity_id':str(item.get('id') or ''),'payload':{'outcome':item}} for item in value]); return
        if record_name == 'review_issues':
            self._append_specs([task_events.issue_event_spec('ISSUE_OPENED', item, timestamp='2026-07-19T10:00:00-05:00', source='test fixture') for item in value]); return
        path=self._record_path(record_name); document,_=long_task.read_block(path,block_name); long_task.write_block(path,block_name,document,value)

    def read_state(self) -> tuple[str, dict]:
        return long_task.read_task_state_cache(self.task_dir)

    def write_state(self, document: str, state: dict) -> None:
        payloads, _projected, _reduced = long_task.append_task_events_payloads(
            self.task_dir, long_task.task_state_path(self.task_dir), document, state, [],
            synced_at='2026-07-19T10:00:00-05:00',
        )
        for path, text in payloads.items():
            path.parent.mkdir(parents=True, exist_ok=True); path.write_text(text,encoding='utf-8')

    def evidence_file(self, name: str, content: str='evidence') -> str:
        path = self.task_dir / 'evidence' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
        return str(path.relative_to(self.task_dir))

    def stage_outcome(self, state: dict, phase: str, revision: str, *, status: str='PASSED') -> dict:
        artifact = state['artifact_revisions'][phase]
        evidence_id = ''
        if status == 'PASSED':
            evidence_ref = self.evidence_file(f'{phase}-{artifact["iteration_no"]}.log')
            evidence_id = long_task.evidence.register_evidence(
                self.task_dir, agent=long_task.PHASE_DEFINITIONS[phase]['ownerAgent'],
                evidence_type='runtime_ref', ref=evidence_ref, revision=revision, phase=phase,
                scope_refs=[phase],
            )['evidence_id']
        return {
            'id': f'SO-MOD0001-{phase.upper()}-I{artifact["iteration_no"]:03d}',
            'phase': phase, 'round': f'{phase.upper()}-R{artifact["iteration_no"]:02d}',
            'iteration_id': artifact['iteration_id'], 'iteration_no': artifact['iteration_no'],
            'is_current': True, 'superseded_by': '', 'status': status,
            'produced_by_agent': long_task.PHASE_DEFINITIONS[phase]['ownerAgent'],
            'input_revisions': {
                predecessor: state['artifact_revisions'][predecessor]['revision']
                for predecessor in long_task.phase_predecessors(
                    phase, bool(state.get('work_mode', {}).get('minimal'))
                )
            }, 'output_revision': revision,
            'evidence_ids': [evidence_id] if evidence_id else [],
            'open_risks': [] if status == 'PASSED' else ['skipped by work mode'],
            'started_at': '2026-07-19T10:00:00-05:00', 'completed_at': '2026-07-19T10:05:00-05:00',
        }

    def prepare_completed_minimal(self) -> tuple[str, dict, list[dict]]:
        path = self.doc_root / 'version' / 'V_TEST' / 'work.md'
        long_task.work_mode.apply_mode(path, long_task.work_mode.WorkMode(minimal=True))
        self.call(long_task.command_sync_work_mode, argparse.Namespace(task_dir=str(self.task_dir), agent='ProjectManagerAgent'))
        trace = self.trace()
        trace.update({
            'status': 'COVERED', 'business_model_refs': ['business_model.yaml#BM-001'],
            'design_refs': ['design.md#DES-001'], 'plan_task_ids': ['PLAN-001'],
            'implementation_refs': ['src/implementation.py'],
        })
        self.write_record('traceability', 'traceability', [trace])
        document, state = self.read_state()
        outcomes_document, outcomes = self.read_record('stage_outcomes', 'stage-outcomes')
        for phase in long_task.PHASES:
            if phase in long_task.work_mode.MINIMAL_PHASES:
                continue
            revision = f'{phase.upper()}-R01@ok'
            if phase in state['collaboration_reviews']:
                self.pass_collaboration_phase(state, phase, revision)
            else:
                state['artifact_revisions'][phase].update({'revision': revision, 'status': 'PASSED'})
            outcomes.append(self.stage_outcome(state, phase, revision))
        source = self.task_dir / 'src' / 'minimal_fixture.py'
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text('def minimal_fixture():\n    return True\n', encoding='utf-8')
        long_task.risk_detect.scan(
            self.task_dir, agent='ProjectManagerAgent',
            revision=state['artifact_revisions']['development']['revision'],
            refs=[str(source.relative_to(self.task_dir))],
        )
        completion_revision = state['artifact_revisions']['completion_verification']['revision']
        acceptance_result = self.task_dir / 'evidence' / 'acceptance-result.json'
        acceptance_result.write_text('{"status": "PASSED"}', encoding='utf-8')
        acceptance_evidence = long_task.evidence.register_evidence(
            self.task_dir, agent='CompletionVerificationAgent', evidence_type='test_ref',
            ref=str(acceptance_result.relative_to(self.task_dir)), revision=completion_revision,
            phase='completion_verification', scope_refs=['AC-ORDER-001'],
        )
        acceptance_document = long_task.acceptance.load_document(self.task_dir)
        acceptance_document['assertions'] = [{
            'assertion_id': 'ASRT-ORDER-001', 'acceptance_id': 'AC-ORDER-001',
            'statement': 'cancelled order cannot be paid', 'type': 'TEST_CASE',
            'phase': 'completion_verification', 'revision': completion_revision, 'blocking': True,
            'status': 'VERIFIED', 'parameters': {'test_case_id': 'AC-ORDER-001', 'expected_status': 'PASSED'},
            'source_refs': ['acceptance:AC-ORDER-001'],
            'evidence_ids': [acceptance_evidence['evidence_id']],
            'verified_by_agent': 'CompletionVerificationAgent',
            'verified_at': '2026-07-19T10:05:00-05:00', 'reason': '',
        }]
        long_task.acceptance.save_document(self.task_dir, acceptance_document)
        state['task_status'] = 'COMPLETED'
        state['current_phase'] = 'completion_verification'
        state['next_action'] = ''
        state['active_task_ids'] = []
        state['current_attempt_id'] = ''
        self.write_state(document, state)
        self.write_record('stage_outcomes', 'stage-outcomes', outcomes)
        return document, state, outcomes

    def conclusion(self, phase: str, reviewer: str, revision: str) -> dict:
        return build_conclusion(self.task_dir, phase, reviewer, revision)

    @staticmethod
    def trace() -> dict:
        return {'id': 'TR-MOD0001-001', 'description': 'cancelled order cannot be paid', 'status': 'PENDING', 'acceptance_criteria': ['AC-ORDER-001'], 'requirement_refs': ['requirement.md#rule-001'], 'impact_required': False, 'dependency_impact_refs': [], 'cross_module_implementation_required': False, 'cross_module_implementation_refs': [], 'business_model_refs': [], 'design_refs': [], 'test_case_ids': [], 'plan_task_ids': [], 'contract_refs': [], 'implementation_refs': [], 'verification_evidence_ids': [], 'verified_by_agents': [], 'notes': ''}

    @staticmethod
    def plan_task(task_id: str, *, status: str='PENDING', phase: str='development') -> dict:
        token = phase.upper().replace('_', '-')
        return {'id': task_id, 'logical_task_id': f'LOGICAL-{task_id}', 'feature_id': 'MOD0001-F01', 'iteration_id': f'ITER-MOD0001-{token}-001', 'iteration_no': 1, 'supersedes_iteration_id': '', 'revision_reason': 'initial iteration', 'title': f'Task {task_id}', 'objective': 'produce an observable change', 'phase': phase, 'status': status, 'depends_on': [], 'owner_agent': 'DevelopAgent', 'reviewer_agents': ['SpecComplianceReviewAgent', 'EngineeringStandardsReviewAgent'], 'input_revisions': {predecessor: '' for predecessor in long_task.phase_predecessors(phase, False)}, 'allowed_files': [f'src/{task_id}.py'], 'acceptance_trace_ids': [], 'flow_refs': [], 'flow_step_refs': [], 'validation_commands': ['python -m unittest'], 'expected_results': ['tests pass'], 'stop_conditions': ['unexpected scope expansion'], 'risk_triggers': [], 'attempts': 0, 'max_attempts': 3, 'output_revision': '', 'validation_evidence_ids': []}

    def pass_collaboration_phase(self, state: dict, phase: str, revision: str) -> None:
        artifact = state['artifact_revisions'][phase]
        artifact.update({'revision': revision, 'status': 'PASSED'})
        review = state['collaboration_reviews'][phase]
        review['current_iteration_id'] = artifact['iteration_id']
        review['artifact_revision'] = revision
        review['status'] = 'PASSED'
        review['independent_conclusions'] = {reviewer: self.conclusion(phase, reviewer, revision) for reviewer in sorted(long_task.expected_required_reviewers(phase, state, minimal=bool((state.get('work_mode') or {}).get('minimal'))))}

    def test_validate_cli_json_is_machine_readable(self) -> None:
        output = io.StringIO()
        args = argparse.Namespace(task_dir=str(self.task_dir), json=True)
        with contextlib.redirect_stdout(output):
            result = long_task.command_validate(args)
        self.assertEqual(result, 0)
        import json
        payload = json.loads(output.getvalue())
        self.assertEqual(payload['status'], 'PASSED')
        self.assertEqual(payload['schemaVersion'], long_task.LONG_TASK_SCHEMA_VERSION)
        self.assertEqual(payload['errors'], [])

    def test_validate_cli_json_reports_errors_without_mixed_logs(self) -> None:
        document, state = self.read_state()
        state['current_phase'] = 'unknown_phase'
        self.write_state(document, state)
        output = io.StringIO()
        args = argparse.Namespace(task_dir=str(self.task_dir), json=True)
        with contextlib.redirect_stdout(output):
            result = long_task.command_validate(args)
        self.assertEqual(result, 1)
        import json
        payload = json.loads(output.getvalue())
        self.assertEqual(payload['status'], 'FAILED')
        self.assertTrue(payload['errors'])

    def test_initialized_current_schema_is_valid(self) -> None:
        errors, warnings, _, _ = long_task.validate_task(self.task_dir)
        self.assertEqual(errors, [])
        self.assertEqual([], warnings)

    def test_render_block_preserves_literal_json_escape_sequences(self) -> None:
        document, state = self.read_state()
        state['next_action'] = 'line-one\\nline-two'
        path = self.task_dir / 'scratch.md'
        document = '```json task-state\n{}\n```\n'
        rendered = long_task.render_block(path, 'task-state', document, state)
        path.write_text(rendered, encoding='utf-8')
        _, loaded = read_block(path, 'task-state')
        self.assertEqual('line-one\\nline-two', loaded['next_action'])


    def test_initialized_task_uses_exact_current_identity_fields(self) -> None:
        _, state = self.read_state()
        self.assertEqual(long_task.LONG_TASK_SCHEMA_VERSION, state['schema_version'])
        self.assertEqual('ProjectManagerAgent', state['project_manager_agent'])
        self.assertEqual('ProjectManagerAgent', state['current_agent'])
        self.assertTrue(set(state).issubset(long_task.TASK_STATE_FIELDS))
        self.assertNotIn('resume_from', state)
        self.assertNotIn('max_auto_review_rounds', state)
        self.assertNotIn('review_rounds', state)
        self.assertNotIn('failed_attempts', state)
        self.assertNotIn('stale_events', state)

    def test_schema_version_must_match_runtime_contract(self) -> None:
        document, state = self.read_state()
        state['schema_version'] = 0
        self.write_state(document, state)
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertEqual(
            [f'unsupported schema_version: 0; expected {long_task.LONG_TASK_SCHEMA_VERSION}'],
            errors,
        )

    def test_task_plan_input_revisions_accept_exact_direct_predecessors(self) -> None:
        task = self.plan_task('DESIGN-INPUT-OK', phase='design')
        task['input_revisions'] = {'business_model': 'BM-R01'}
        errors: list[str] = []
        long_task.validate_task_plan([task], long_task.PHASES, {}, errors)
        self.assertFalse(any('input_revisions' in error for error in errors), errors)

    def test_task_plan_input_revisions_reject_unexpected_phase_key(self) -> None:
        task = self.plan_task('DESIGN-INPUT-EXTRA', phase='design')
        task['input_revisions'] = {
            'business_model': 'BM-R01',
            'requirement': 'REQAN-P2-R01@demo + Overlay-R04',
        }
        errors: list[str] = []
        long_task.validate_task_plan([task], long_task.PHASES, {}, errors)
        self.assertIn(
            "task-plan[0] input_revisions contains unexpected predecessors: ['requirement']; expected: ['business_model']",
            errors,
        )

    def test_task_plan_input_revisions_reject_missing_direct_predecessor(self) -> None:
        task = self.plan_task('DESIGN-INPUT-MISSING', phase='design')
        task['input_revisions'] = {}
        errors: list[str] = []
        long_task.validate_task_plan([task], long_task.PHASES, {}, errors)
        self.assertIn(
            "task-plan[0] input_revisions missing required predecessors: ['business_model']",
            errors,
        )

    def test_start_gate_rejects_unexpected_input_before_revision_lookup(self) -> None:
        _, state = self.read_state()
        state['artifact_revisions']['business_model'].update({'status': 'PASSED', 'revision': 'BM-R01'})
        state['current_phase'] = 'design'
        task = self.plan_task('DESIGN-START-EXTRA', phase='design')
        task['iteration_id'] = state['artifact_revisions']['design']['iteration_id']
        task['iteration_no'] = state['artifact_revisions']['design']['iteration_no']
        task['input_revisions'] = {
            'business_model': 'BM-R01',
            'requirement': 'REQAN-P2-R01@demo + Overlay-R04',
        }
        with self.assertRaisesRegex(
            ValueError,
            r"unexpected predecessors: \['requirement'\]; expected: \['business_model'\]",
        ):
            long_task.validate_start_gate(self.task_dir, state, [task], task, [])

    def test_rebind_phase_task_inputs_removes_historical_extra_keys(self) -> None:
        _, state = self.read_state()
        state['artifact_revisions']['business_model'].update({'status': 'PASSED', 'revision': 'BM-R01'})
        task = self.plan_task('DESIGN-REBIND', phase='design')
        task['iteration_id'] = state['artifact_revisions']['design']['iteration_id']
        task['iteration_no'] = state['artifact_revisions']['design']['iteration_no']
        task['input_revisions'] = {
            'business_model': 'BM-OLD',
            'requirement': 'REQAN-P2-R01@demo + Overlay-R04',
        }
        updated = long_task.rebind_phase_task_inputs([task], state, 'design', minimal=False)
        self.assertEqual(['DESIGN-REBIND'], updated)
        self.assertEqual({'business_model': 'BM-R01'}, task['input_revisions'])

    def test_unknown_fields_are_rejected_by_current_record_contracts(self) -> None:
        document, state = self.read_state()
        state['unexpected_field'] = 'value'
        self.write_state(document, state)
        task = self.plan_task('TASK-001')
        task['unexpected_field'] = 'value'
        self.write_record('task_plan', 'task-plan', [task])
        trace = self.trace()
        trace['unexpected_field'] = 'value'
        self.write_record('traceability', 'traceability', [trace])
        issue = {
            'id': 'ISSUE-001', 'issue_type': 'RISK', 'axis': 'ENGINEERING',
            'severity': 'P2', 'confidence': 5, 'status': 'OPEN', 'phase': 'development',
            'round': 'DEV-R01', 'artifact_revision': 'CODE-R01',
            'raised_by_agent': 'EngineeringStandardsReviewAgent', 'owner_agent': 'DevelopAgent',
            'title': 'Unexpected field', 'description': 'The record contains an unknown field.',
            'impact': 'The record contract is ambiguous.', 'motivating_evidence': ['task metadata'],
            'question': 'Why is this field present?', 'question_to': ['DevelopAgent'],
            'responses': [], 'recommendation': 'Use only fields defined by the current contract.',
            'affected_artifacts': [], 'affected_trace_ids': ['TR-MOD0001-001'],
            'decision': '', 'resolution_revision': '', 'resolution_evidence': '',
            'verified_by_agent': '', 'verified_at': '', 'defer_reason': '',
            'unexpected_field': 'value',
        }
        self.write_record('review_issues', 'review-issues', [issue])
        outcome = {
            'id': 'SO-001', 'phase': 'development', 'round': 'DEV-R01',
            'status': 'RUNNING', 'produced_by_agent': 'DevelopAgent',
            'input_revisions': {}, 'output_revision': '', 'evidence_ids': [], 'open_risks': [],
            'started_at': '2026-07-19T10:00:00-05:00', 'completed_at': '',
            'unexpected_field': 'value',
        }
        self.write_record('stage_outcomes', 'stage-outcomes', [outcome])
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertIn("task-state contains unknown fields: ['unexpected_field']", errors)
        self.assertIn("task-plan[0] contains unknown fields: ['unexpected_field']", errors)
        self.assertIn("traceability[0] contains unknown fields: ['unexpected_field']", errors)
        self.assertIn("review-issues[0] contains unknown fields: ['unexpected_field']", errors)
        self.assertIn("stage-outcomes[0] contains unknown fields: ['unexpected_field']", errors)

    def test_project_manager_agent_must_be_allowed_for_management(self) -> None:
        document, state = self.read_state()
        state['project_manager_agent'] = 'DesignAgent'
        self.write_state(document, state)
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertIn(
            "project_manager_agent is not authorized for project management: DesignAgent; allowed=['ProjectManagerAgent']",
            errors,
        )

    def test_handoff_uses_exact_current_fields(self) -> None:
        handoff = {'schema_version': long_task.LONG_TASK_SCHEMA_VERSION, 'target_id': 'MOD0001', 'version': 'V_TEST', 'task_id': 'TASK-001', 'phase': 'requirement_confirmation', 'round': 'REQCONF-R01', 'from_agent': 'RequirementConfirmationAgent', 'to_agent': 'RequirementAnalysisAgent', 'input_revisions': {}, 'output_revision': 'REQCONF-R01@demo', 'read_files': [], 'modified_files': [], 'new_files': [], 'decision_ids': [], 'review_conclusion_refs': [], 'discussion_issue_ids': [], 'traceability_updates': [], 'validation': [], 'open_issues': [], 'blockers': [], 'next_action': 'review requirement confirmation', 'stop_conditions': [], 'created_at': '2026-07-19T10:00:00-05:00'}
        path = self.task_dir / 'handoff' / 'REQCONF-R01.md'
        path.parent.mkdir(parents=True, exist_ok=True)
        document = '# 显式交接记录\n\n```json handoff\n{}\n```\n'
        path.write_text(document, encoding='utf-8')
        document, _ = long_task.read_block(path, 'handoff')
        long_task.write_block(path, 'handoff', document, handoff)
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertEqual(errors, [])
        handoff['unexpected_field'] = 'value'
        long_task.write_block(path, 'handoff', document, handoff)
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertIn("handoff REQCONF-R01.md contains unknown fields: ['unexpected_field']", errors)

    def test_requirement_analysis_requires_traceability_and_acceptance(self) -> None:
        document, state = self.read_state()
        revision = 'REQ-R01@demo'
        self.pass_collaboration_phase(state, 'requirement_analysis', revision)
        self.write_state(document, state)
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertIn('requirement_analysis cannot pass without traceability entries', errors)
        trace = self.trace()
        trace['acceptance_criteria'] = []
        self.write_record('traceability', 'traceability', [trace])
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertIn("requirement_analysis traces missing refs/acceptance: ['TR-MOD0001-001']", errors)
        trace['acceptance_criteria'] = ['AC-ORDER-001']
        self.write_record('traceability', 'traceability', [trace])
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertEqual(errors, [])

    def test_requirement_analysis_requires_declared_impact_and_flow_refs(self) -> None:
        document, state = self.read_state()
        revision = 'REQ-R02@impact'
        self.pass_collaboration_phase(state, 'requirement_analysis', revision)
        self.write_state(document, state)
        trace = self.trace()
        trace['impact_required'] = True
        trace['cross_module_implementation_required'] = True
        self.write_record('traceability', 'traceability', [trace])
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertIn("requirement_analysis impact-required traces missing dependency refs: ['TR-MOD0001-001']", errors)
        self.assertIn("requirement_analysis cross-module traces missing implementation refs: ['TR-MOD0001-001']", errors)
        trace['dependency_impact_refs'] = ['docs/_relations/dependency_impact.yaml#IMP-ORDER-CANCEL']
        trace['cross_module_implementation_refs'] = ['docs/_relations/dependency_impact.yaml#CMI-ORDER-CREATE']
        self.write_record('traceability', 'traceability', [trace])
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertIn('collaboration_reviews.requirement_analysis.ImpactAnalysisReviewAgent missing independent conclusion', errors)
        self.assertIn('collaboration_reviews.requirement_analysis.CrossModuleIntegrationReviewAgent missing independent conclusion', errors)
        document, state = self.read_state()
        review = state['collaboration_reviews']['requirement_analysis']
        review['independent_conclusions']['ImpactAnalysisReviewAgent'] = self.conclusion('requirement_analysis', 'ImpactAnalysisReviewAgent', revision)
        review['independent_conclusions']['CrossModuleIntegrationReviewAgent'] = self.conclusion('requirement_analysis', 'CrossModuleIntegrationReviewAgent', revision)
        self.write_state(document, state)
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertEqual(errors, [])

    def test_flow_required_plan_task_requires_flow_and_step_refs(self) -> None:
        trace = self.trace()
        trace['business_flow_required'] = True
        trace['business_flow_refs'] = ['FLOW-ORDER-E2E']
        self.write_record('traceability', 'traceability', [trace])
        task = self.plan_task('TASK-FLOW-001', phase='development')
        task['acceptance_trace_ids'] = [trace['id']]
        self.write_record('task_plan', 'task-plan', [task])
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertIn("task-plan[0] flow-required traces need matching flow_refs: ['FLOW-ORDER-E2E']", errors)
        self.assertIn('task-plan[0] flow-required task missing flow_step_refs', errors)
        task['flow_refs'] = ['FLOW-ORDER-E2E']
        task['flow_step_refs'] = ['STEP-ORDER-E2E-01']
        self.write_record('task_plan', 'task-plan', [task])
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertNotIn("task-plan[0] flow-required traces need matching flow_refs: ['FLOW-ORDER-E2E']", errors)
        self.assertNotIn('task-plan[0] flow-required task missing flow_step_refs', errors)

    def test_traceability_requires_boolean_impact_flags(self) -> None:
        trace = self.trace()
        trace['impact_required'] = 'yes'
        trace['cross_module_implementation_required'] = None
        self.write_record('traceability', 'traceability', [trace])
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertIn('traceability[0] impact_required must be a boolean', errors)
        self.assertIn('traceability[0] cross_module_implementation_required must be a boolean', errors)

    def test_design_requires_architecture_reviewer_on_same_revision(self) -> None:
        document, state = self.read_state()
        revision = 'DESIGN-R01@demo'
        self.pass_collaboration_phase(state, 'design', revision)
        del state['collaboration_reviews']['design']['independent_conclusions']['ArchitectureReviewAgent']
        self.write_state(document, state)
        trace = self.trace()
        trace['design_refs'] = ['design.md#rule-001']
        self.write_record('traceability', 'traceability', [trace])
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertIn('collaboration_reviews.design.ArchitectureReviewAgent missing independent conclusion', errors)
        document, state = self.read_state()
        state['collaboration_reviews']['design']['independent_conclusions']['ArchitectureReviewAgent'] = self.conclusion('design', 'ArchitectureReviewAgent', revision)
        self.write_state(document, state)
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertEqual(errors, [])

    def test_risk_triggered_additional_reviewer_is_a_hard_gate(self) -> None:
        document, state = self.read_state()
        revision = 'REVIEW-R01@demo'
        self.pass_collaboration_phase(state, 'development', 'DEV-R01@demo')
        source = self.task_dir / 'src' / 'admin' / 'UserController.java'
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text('authorize permission token', encoding='utf-8')
        report = long_task.risk_detect.scan(
            self.task_dir, agent='ProjectManagerAgent', revision='DEV-R01@demo',
            refs=[str(source.relative_to(self.task_dir))],
        )
        risk_evidence = report['detections'][0]['evidence_ids'][0]
        self.pass_collaboration_phase(state, 'code_review', revision)
        review = state['collaboration_reviews']['code_review']
        review['additional_reviewers'] = {'SecurityReviewAgent': {'trigger': 'security', 'reason': 'authorization logic changed', 'evidence_ids': [risk_evidence]}}
        self.write_state(document, state)
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertIn('collaboration_reviews.code_review.SecurityReviewAgent missing independent conclusion', errors)
        document, state = self.read_state()
        state['collaboration_reviews']['code_review']['independent_conclusions']['SecurityReviewAgent'] = self.conclusion('code_review', 'SecurityReviewAgent', revision)
        self.write_state(document, state)
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertEqual(errors, [])

    def test_additional_reviewer_requires_valid_trigger_reason_and_evidence(self) -> None:
        document, state = self.read_state()
        review = state['collaboration_reviews']['design']
        review['additional_reviewers'] = {'SecurityReviewAgent': {'trigger': 'performance', 'reason': '', 'evidence_ids': []}}
        self.write_state(document, state)
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertIn('collaboration_reviews.design.additional_reviewers.SecurityReviewAgent.trigger must be security', errors)
        self.assertIn('collaboration_reviews.design.additional_reviewers.SecurityReviewAgent missing reason', errors)
        self.assertIn('collaboration_reviews.design.additional_reviewers.SecurityReviewAgent.evidence_ids must contain current trigger evidence IDs', errors)

    def test_current_schema_rejects_task_level_passed_status(self) -> None:
        document, state = self.read_state()
        state['task_status'] = 'PASSED'
        self.write_state(document, state)
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertIn('invalid task_status: PASSED', errors)

    def test_task_dependency_cycle_is_rejected(self) -> None:
        first = self.plan_task('TASK-001')
        second = self.plan_task('TASK-002')
        first['depends_on'] = ['TASK-002']
        second['depends_on'] = ['TASK-001']
        self.write_record('task_plan', 'task-plan', [first, second])
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertTrue(any((error.startswith('task dependency cycle:') for error in errors)))

    def test_passed_plan_task_requires_revision_and_fresh_evidence(self) -> None:
        task = self.plan_task('TASK-001')
        for field in ('status','attempts','output_revision','validation_evidence_ids'):
            task.pop(field, None)
        self.write_record('task_plan', 'task-plan', [task])
        self._append_specs([{'type':'TASK_RUNTIME_SET','entity_id':'TASK-001','payload':{'runtime':{'status':'PASSED','attempts':0,'output_revision':'','validation_evidence_ids':[]}}}])
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertIn('task TASK-001 PASSED without a current-iteration PASSED attempt', errors)
        self.assertIn('task TASK-001 status PASSED requires current-iteration attempt history', errors)
        self._append_specs([{'type':'TASK_RUNTIME_SET','entity_id':'TASK-001','payload':{'runtime':{'status':'PASSED','attempts':0,'output_revision':'CODE-R01@abc','validation_evidence_ids':['EVD-MISSING']}}}])
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertIn('task TASK-001 PASSED without a current-iteration PASSED attempt', errors)

    def test_review_issue_requires_real_recipients_and_evidence(self) -> None:
        issue = {'id': 'ISSUE-001', 'status': 'OPEN', 'severity': 'P1', 'phase': 'design', 'round': 'DESIGN-R01', 'axis': 'ARCHITECTURE', 'issue_type': 'RISK', 'confidence': 9, 'artifact_revision': 'DESIGN-R01@abc', 'raised_by_agent': 'ArchitectureReviewAgent', 'question_to': [], 'owner_agent': 'DesignAgent', 'title': 'transaction boundary unclear', 'description': 'write ordering can leave partial state', 'motivating_evidence': [], 'impact': 'inconsistent order', 'question': 'What is the atomicity rule?', 'recommendation': 'define one transaction or compensation', 'affected_artifacts': ['design', 'test_design'], 'affected_trace_ids': [], 'responses': [], 'decision': '', 'resolution_revision': '', 'resolution_evidence': '', 'verified_by_agent': 'ArchitectureReviewAgent'}
        self.write_record('review_issues', 'review-issues', [issue])
        document, state = self.read_state()
        state['open_issue_ids'] = ['ISSUE-001']
        self.write_state(document, state)
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertIn('review-issues[0] question_to must be a non-empty string array', errors)
        self.assertIn('review-issues[0] motivating_evidence must be a non-empty string array', errors)
        issue['question_to'] = ['DesignAgent']
        issue['motivating_evidence'] = ['design.md#transaction-boundary']
        self.write_record('review_issues', 'review-issues', [issue])
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertEqual(errors, [])

    def test_completed_task_requires_completion_verification_outcome(self) -> None:
        _, _, outcomes = self.prepare_completed_minimal()
        failed = [dict(item, status='FAILED') if item.get('phase') == 'completion_verification' else item for item in outcomes]
        self.write_record('stage_outcomes', 'stage-outcomes', failed)
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertIn('COMPLETED task requires a PASSED completion_verification outcome', errors)
        self.write_record('stage_outcomes', 'stage-outcomes', outcomes)
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertEqual(errors, [])

    def test_reopen_phase_reworks_downstream_tasks_and_clears_evidence(self) -> None:
        test_case = self.doc_root / 'version' / 'V_TEST' / 'doc' / 'MOD0001' / 'test_case.md'
        test_case.parent.mkdir(parents=True, exist_ok=True)
        test_case.write_text('# canonical test design\n', encoding='utf-8')
        document, state = self.read_state()
        state['artifact_revisions']['design'].update({'revision': 'DESIGN-R02', 'status': 'PASSED'})
        state['artifact_revisions']['development'].update({'revision': 'CODE-R01', 'status': 'PASSED'})
        state['artifact_revisions']['code_review'].update({'revision': 'REVIEW-R01', 'status': 'PASSED'})
        review_artifact = state['artifact_revisions']['code_review']
        state['work_policy']['phase_overrides'] = {
            'code_review': {
                'status': 'SKIPPED_BY_USER', 'phase': 'code_review',
                'reason': 'temporary waiver',
                'requested_by': 'user', 'recorded_by': 'ProjectManagerAgent',
                'recorded_at': '2026-07-28T00:00:00+00:00',
                'requires_explicit_reopen': True,
                'iteration_id': review_artifact['iteration_id'],
                'iteration_no': review_artifact['iteration_no'],
                'revision': review_artifact['revision'],
            }
        }
        self.write_state(document, state)
        task = self.plan_task('TASK-001', status='PASSED', phase='development')
        task['output_revision'] = 'CODE-R01'
        task['validation_evidence_ids'] = ['stale evidence']
        self.write_record('task_plan', 'task-plan', [task])
        acceptance_document = long_task.acceptance.load_document(self.task_dir)
        acceptance_document['assertions'] = [
            {
                'assertion_id': 'ASRT-REQ-001', 'acceptance_id': 'AC-ORDER-001',
                'statement': 'requirement remains current', 'type': 'MANUAL_REVIEW',
                'phase': 'requirement_confirmation', 'revision': 'REQ-R01',
                'blocking': True, 'status': 'PENDING',
                'parameters': {'reviewer_agent': 'RequirementReviewAgent'},
                'source_refs': ['acceptance:AC-ORDER-001'], 'evidence_ids': [],
                'verified_by_agent': '', 'verified_at': '', 'reason': '',
            },
            {
                'assertion_id': 'ASRT-DES-001', 'acceptance_id': 'AC-ORDER-001',
                'statement': 'design review is stale', 'type': 'MANUAL_REVIEW',
                'phase': 'design', 'revision': 'DESIGN-R02',
                'blocking': True, 'status': 'FAILED',
                'parameters': {'reviewer_agent': 'ArchitectureReviewAgent'},
                'source_refs': ['acceptance:AC-ORDER-001'], 'evidence_ids': [],
                'verified_by_agent': 'ArchitectureReviewAgent',
                'verified_at': '2026-07-28T00:00:00+00:00', 'reason': 'needs changes',
            },
        ]
        long_task.acceptance.save_document(self.task_dir, acceptance_document)
        args = argparse.Namespace(task_dir=str(self.task_dir), from_phase='design', source_revision='DESIGN-R03', reason='business rule changed', round='DESIGN-R03')
        self.assertEqual(self.call(long_task.command_reopen_phase, args), 0)
        tasks = long_task.runtime_tasks(self.task_dir)
        self.assertEqual(tasks[0]['status'], 'REWORK')
        self.assertEqual(tasks[0]['output_revision'], '')
        self.assertEqual(tasks[0]['validation_evidence_ids'], [])
        _, state = self.read_state()
        self.assertEqual(state['artifact_revisions']['design']['status'], 'IN_PROGRESS')
        self.assertEqual(state['artifact_revisions']['development']['status'], 'STALE')
        self.assertEqual(state['task_status'], 'REWORK')
        self.assertEqual({}, state['work_policy'].get('phase_overrides'))
        self.assertFalse(state['work_policy']['explicit_reopen_required'])
        assertions = long_task.acceptance.load_document(self.task_dir)['assertions']
        self.assertEqual(['ASRT-REQ-001'], [item['assertion_id'] for item in assertions])
        self.assertEqual('# canonical test design\n', test_case.read_text(encoding='utf-8'))

    def test_reopen_uses_selected_earliest_boundary_without_forcing_upstream_phases(self) -> None:
        _, before = self.read_state()
        base = {
            'task_dir': str(self.task_dir), 'source_revision': 'BM-R02@changed',
            'reason': 'business model change confirmed at business_model boundary', 'round': '',
        }
        self.assertEqual(
            self.call(long_task.command_reopen_phase, argparse.Namespace(**base, from_phase='business_model')),
            0,
        )
        _, after = self.read_state()
        self.assertEqual('IN_PROGRESS', after['artifact_revisions']['business_model']['status'])
        self.assertEqual(
            before['artifact_revisions']['requirement_analysis']['iteration_no'],
            after['artifact_revisions']['requirement_analysis']['iteration_no'],
        )
        self.assertEqual(
            before['artifact_revisions']['requirement_confirmation']['iteration_no'],
            after['artifact_revisions']['requirement_confirmation']['iteration_no'],
        )

    def test_reopen_upstream_boundary_requires_and_records_user_confirmation(self) -> None:
        document, state = self.read_state()
        state['current_phase'] = 'business_model'
        state['artifact_revisions']['requirement_confirmation'].update({
            'status': 'PASSED', 'revision': 'REQCONF-R01@current',
        })
        state['collaboration_reviews']['requirement_confirmation'].update({
            'status': 'PASSED', 'artifact_revision': 'REQCONF-R01@current',
        })
        self.write_state(document, state)
        base = {
            'task_dir': str(self.task_dir),
            'from_phase': 'requirement_analysis',
            'changed_phase': 'business_model',
            'source_revision': 'BM-R02@changed',
            'reason': '业务模型边界变化',
            'round': '',
            'agent': 'ProjectManagerAgent',
        }
        with self.assertRaisesRegex(ValueError, 'user-confirmed'):
            self.call(long_task.command_reopen_phase, argparse.Namespace(**base))
        self.call(wk.command_confirm, argparse.Namespace(
            task_dir=str(self.task_dir), until='', reopen_from='requirement_analysis',
            changed_phase='business_model', source_revision='BM-R02@changed',
            reason='业务模型边界变化', agent='ProjectManagerAgent', preview=False,
            apply=True, json=True,
        ))
        confirmation = task_events.read_events(self.task_dir)[-1]['payload']['requestFingerprint']
        confirmed = argparse.Namespace(
            **base, user_confirmed=True, confirmation_ref=confirmation
        )
        self.assertEqual(self.call(long_task.command_reopen_phase, confirmed), 0)
        history = long_task.load_task_history(self.task_dir)
        stale = history['stale_events'][-1]
        self.assertEqual('business_model', stale['changed_phase'])
        self.assertEqual(confirmation, stale['confirmation_ref'])
        self.assertTrue(stale['user_confirmed'])
        self.assertRegex(stale['confirmation_fingerprint'], r'^[a-f0-9]{64}$')

        pristine_events = task_events.read_events(self.task_dir)
        consumption_index = next(
            index for index, event in enumerate(pristine_events)
            if event['type'] == 'AUTHORIZATION_BATCH_CONSUMED'
            and event.get('payload', {}).get('purpose') == 'REOPEN_BOUNDARY'
        )
        for field in (
            'confirmationEventId', 'confirmationEventDigest', 'taskId',
            'changedPhase', 'fromPhase', 'sourceRevision',
        ):
            with self.subTest(field=field):
                events = copy.deepcopy(pristine_events)
                events[consumption_index]['payload'][field] = f'BOGUS-{field}'
                for index in range(consumption_index, len(events)):
                    events[index]['prev_digest'] = events[index - 1]['digest'] if index else ''
                    events[index]['digest'] = task_events._event_digest(events[index])
                task_events.event_path(self.task_dir).write_text(task_events.render_events(events), encoding='utf-8')
                errors, _, _, _ = long_task.validate_task(self.task_dir)
                self.assertTrue(any('consumption fields do not match' in error for error in errors), errors)

        confirmation_index = next(
            index for index, event in enumerate(pristine_events)
            if event['type'] == 'AUTHORIZATION_BATCH_CONFIRMED'
            and event.get('payload', {}).get('requestFingerprint') == confirmation
        )
        source_cases = (
            (confirmation_index, 'source', 'wrong.confirm', 'confirmation source must be wk.confirm'),
            (consumption_index, 'source', 'wrong.reopen', 'consumption source must be long_task.py reopen-phase'),
            (confirmation_index, 'agent', 'DevelopAgent', 'confirmation agent must be ProjectManagerAgent'),
            (confirmation_index, 'atomicActions', [], 'atomicActions do not match'),
        )
        for event_index, field, value, expected_error in source_cases:
            with self.subTest(audit_field=field):
                events = copy.deepcopy(pristine_events)
                if field == 'source':
                    events[event_index]['source'] = value
                else:
                    events[event_index]['payload'][field] = value
                if event_index == confirmation_index:
                    events[event_index]['digest'] = task_events._event_digest(events[event_index])
                    changed_digest = events[event_index]['digest']
                    events[consumption_index]['payload']['confirmationEventDigest'] = changed_digest
                    stale_record = next(event for event in events if event['type'] == 'PHASE_STALE')
                    stale_record['payload']['stale_event']['confirmation_event_digest'] = changed_digest
                for index in range(event_index, len(events)):
                    events[index]['prev_digest'] = events[index - 1]['digest'] if index else ''
                    events[index]['digest'] = task_events._event_digest(events[index])
                task_events.event_path(self.task_dir).write_text(task_events.render_events(events), encoding='utf-8')
                errors, _, _, _ = long_task.validate_task(self.task_dir)
                self.assertTrue(any(expected_error in error for error in errors), errors)

        events = copy.deepcopy(pristine_events)
        stale_index = next(index for index, event in enumerate(events) if event['type'] == 'PHASE_STALE')
        events.insert(stale_index, {
            'schema_version': task_events.EVENT_SCHEMA_VERSION,
            'seq': 0, 'event_id': '', 'timestamp': stale['created_at'],
            'type': 'TASK_STATE_PATCHED', 'entity_id': self.task_dir.name,
            'source': 'test', 'prev_digest': '', 'payload': {'set': {}}, 'digest': '',
        })
        for index, event in enumerate(events):
            event['seq'] = index + 1
            event['event_id'] = f'TE-{index + 1:06d}'
            event['prev_digest'] = events[index - 1]['digest'] if index else ''
            event['digest'] = task_events._event_digest(event)
        task_events.event_path(self.task_dir).write_text(task_events.render_events(events), encoding='utf-8')
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertTrue(any('must immediately precede PHASE_STALE' in error for error in errors), errors)

    def test_prevalidated_acceptance_does_not_rescan_document(self) -> None:
        with mock.patch('acceptance.validate_document', side_effect=AssertionError('unexpected acceptance scan')):
            long_task.validate_task(self.task_dir, prevalidated={'acceptance': ([], {})})

    def test_latest_handoff_prefers_current_revision_when_mtime_is_tied(self) -> None:
        import os
        document, state = self.read_state()
        state["artifact_revisions"]["requirement_confirmation"].update({
            "status": "PASSED", "revision": "REQCONF-R04@current",
            "iteration_id": "ITER-MOD0001-REQUIREMENT-CONFIRMATION-004", "iteration_no": 4,
        })
        self.write_state(document, state)
        handoff_dir = self.task_dir / "handoff"
        handoff_dir.mkdir(exist_ok=True)
        for revision, round_id in (("REQCONF-R03@old", "REQCONF-R03"), ("REQCONF-R04@current", "REQCONF-R04")):
            path = handoff_dir / f"{round_id}.md"
            payload = {
                "schema_version": 2, "target_id": "MOD0001", "version": "V_TEST",
                "phase": "requirement_confirmation", "round": round_id,
                "from_agent": "RequirementConfirmationAgent", "to_agent": "ProjectManagerAgent",
                "status": "READY", "input_revisions": {}, "output_revision": revision,
                "completed_work": [], "modified_files": [], "validation_commands": [],
                "validation_evidence_ids": [], "review_result_refs": [], "open_issues": [],
                "unverified_items": [], "next_action": "continue", "context_refs": [],
                "created_at": "2026-07-26T09:40:00+00:00",
            }
            path.write_text(
                "# handoff\n\n```json handoff\n" +
                __import__('json').dumps(payload, ensure_ascii=False, indent=2) +
                "\n```\n", encoding="utf-8"
            )
        tied = 1_774_000_000
        for path in handoff_dir.glob("REQCONF-R*.md"):
            os.utime(path, (tied, tied))
        latest = long_task.latest_handoff(self.task_dir, state)
        self.assertEqual("REQCONF-R04.md", latest.name)

    def test_resume_context_includes_ready_tasks_and_active_decisions(self) -> None:
        task = self.plan_task('TASK-001', status='READY')
        for field in ('status','attempts','output_revision','validation_evidence_ids'):
            task.pop(field, None)
        self.write_record('task_plan', 'task-plan', [task])
        self._append_specs([{'type':'TASK_RUNTIME_SET','entity_id':'TASK-001','payload':{'runtime':{'status':'READY','attempts':0}}}])
        args = argparse.Namespace(task_dir=str(self.task_dir), allow_invalid=False)
        self.assertEqual(self.call(long_task.command_resume, args), 0)
        content = (self.task_dir / 'cache' / 'resume_context.md').read_text(encoding='utf-8')
        self.assertIn('TASK-001: Task TASK-001', content)

    def test_review_conclusion_requires_every_profile_criterion(self) -> None:
        document, state = self.read_state()
        revision = 'REVIEW-R02@criteria'
        self.pass_collaboration_phase(state, 'code_review', revision)
        conclusion = state['collaboration_reviews']['code_review']['independent_conclusions']['SpecComplianceReviewAgent']
        removed = conclusion['criteria_results'].pop()
        self.write_state(document, state)
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertIn(
            f"collaboration_reviews.code_review.SpecComplianceReviewAgent missing criterion results: ['{removed['criterion_id']}']",
            errors,
        )

    def test_failed_criterion_requires_matching_finding_and_drives_conclusion(self) -> None:
        document, state = self.read_state()
        revision = 'REVIEW-R03@finding'
        self.pass_collaboration_phase(state, 'code_review', revision)
        review = state['collaboration_reviews']['code_review']
        review['status'] = 'IN_REVIEW'
        state['artifact_revisions']['code_review']['status'] = 'IN_PROGRESS'
        conclusion = review['independent_conclusions']['SpecComplianceReviewAgent']
        criterion = conclusion['criteria_results'][0]
        criterion['status'] = 'FAILED'
        criterion['finding_ids'] = ['RI-CODE-SPEC-001']
        criterion['reason'] = 'The implementation misses one required behavior.'
        conclusion['finding_ids'] = ['RI-CODE-SPEC-001']
        conclusion['conclusion'] = 'NEEDS_CHANGES'
        issue = {
            'id': 'RI-CODE-SPEC-001', 'issue_type': 'OMISSION', 'axis': 'SPEC',
            'severity': 'P1', 'confidence': 9, 'status': 'OPEN', 'phase': 'code_review',
            'round': 'REVIEW-R03', 'artifact_revision': revision,
            'raised_by_agent': 'SpecComplianceReviewAgent', 'owner_agent': 'DevelopAgent',
            'title': 'Required cancellation branch is missing',
            'description': 'The implementation does not handle the captured-payment branch.',
            'impact': 'The accepted requirement is only partially implemented.',
            'motivating_evidence': ['src/order/CancelOrderService.java:72'],
            'question': 'Where is the captured-payment refund path?',
            'question_to': ['DevelopAgent'], 'responses': [],
            'recommendation': 'Implement the branch and add a regression test.',
            'affected_artifacts': ['implementation', 'testing'],
            'affected_trace_ids': ['TR-MOD0001-001'], 'decision': '',
            'resolution_revision': '', 'resolution_evidence': '',
            'verified_by_agent': '', 'verified_at': '', 'defer_reason': '',
        }
        self.write_record('review_issues', 'review-issues', [issue])
        self.write_record('traceability', 'traceability', [self.trace()])
        state['open_issue_ids'] = ['RI-CODE-SPEC-001']
        self.write_state(document, state)
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertEqual(errors, [])
        criterion['finding_ids'] = []
        conclusion['finding_ids'] = []
        self.write_state(document, state)
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertIn('collaboration_reviews.code_review.SpecComplianceReviewAgent.criteria_results[0] FAILED requires finding_ids', errors)

    def test_not_applicable_requires_contract_permission_and_reason(self) -> None:
        document, state = self.read_state()
        revision = 'DESIGN-R04@na'
        self.pass_collaboration_phase(state, 'design', revision)
        conclusion = state['collaboration_reviews']['design']['independent_conclusions']['ArchitectureReviewAgent']
        allowed_result = next(item for item in conclusion['criteria_results'] if long_task.REVIEW_CRITERIA[item['criterion_id']]['allowNotApplicable'])
        allowed_result['status'] = 'NOT_APPLICABLE'
        allowed_result['evidence'] = []
        allowed_result['reason'] = 'This design introduces no API, data, concurrency, or retry boundary for this criterion.'
        self.write_state(document, state)
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertEqual(errors, [])
        disallowed_result = next(item for item in conclusion['criteria_results'] if not long_task.REVIEW_CRITERIA[item['criterion_id']]['allowNotApplicable'])
        disallowed_result['status'] = 'NOT_APPLICABLE'
        disallowed_result['evidence'] = []
        disallowed_result['reason'] = 'Not applicable.'
        self.write_state(document, state)
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertTrue(any('criterion does not allow NOT_APPLICABLE' in error for error in errors))

    def test_passed_criterion_requires_structured_evidence(self) -> None:
        document, state = self.read_state()
        revision = 'TDD-R05@evidence'
        self.pass_collaboration_phase(state, 'development', revision)
        conclusion = state['collaboration_reviews']['development']['independent_conclusions']['TDDReviewAgent']
        conclusion['criteria_results'][0]['evidence'] = ['reviewed']
        self.write_state(document, state)
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertIn('collaboration_reviews.development.TDDReviewAgent.criteria_results[0].evidence[0] must be an object', errors)

    def test_conclusion_cannot_claim_passed_with_unresolved_limitations(self) -> None:
        document, state = self.read_state()
        revision = 'TEST-R06@limited'
        self.pass_collaboration_phase(state, 'testing', revision)
        conclusion = state['collaboration_reviews']['testing']['independent_conclusions']['TestEvidenceReviewAgent']
        conclusion['limitations'] = ['The integration environment was unavailable.']
        self.write_state(document, state)
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertIn('collaboration_reviews.testing.TestEvidenceReviewAgent PASSED must not contain unresolved limitations', errors)

    def test_current_schema_rejects_unknown_artifact_and_review_phases(self) -> None:
        document, state = self.read_state()
        unsupported_phase = 'unsupported_phase'
        state['artifact_revisions'][unsupported_phase] = {'revision': 'ARCH-R01', 'status': 'PASSED', 'iteration_id': 'ITER-X', 'iteration_no': 1}
        state['collaboration_reviews'][unsupported_phase] = {'artifact_revision': 'ARCH-R01', 'required_reviewers': ['ArchitectureReviewAgent'], 'independent_conclusions': {'ArchitectureReviewAgent': self.conclusion('design', 'ArchitectureReviewAgent', 'ARCH-R01')}, 'status': 'PASSED'}
        self.write_state(document, state)
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertIn(f"artifact_revisions contains unknown phases: ['{unsupported_phase}']", errors)
        self.assertIn(f"collaboration_reviews contains unknown phases: ['{unsupported_phase}']", errors)

    def test_current_schema_rejects_unknown_agents(self) -> None:
        document, state = self.read_state()
        state['project_manager_agent'] = 'InventedAgent'
        state['next_agent'] = 'InventedAgent'
        self.write_state(document, state)
        task = self.plan_task('TASK-001')
        task['owner_agent'] = 'InventedAgent'
        self.write_record('task_plan', 'task-plan', [task])
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertIn('unknown project_manager_agent: InventedAgent', errors)
        self.assertIn('unknown next_agent: InventedAgent', errors)
        self.assertIn('task-plan[0] unknown owner_agent: InventedAgent', errors)

    def test_passed_stage_outcome_must_match_current_artifact_revision(self) -> None:
        document, state = self.read_state()
        self.pass_collaboration_phase(state, 'development', 'CODE-R02')
        self.write_state(document, state)
        outcome = self.stage_outcome(state, 'development', 'CODE-R01')
        self.write_record('stage_outcomes', 'stage-outcomes', [outcome])
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertIn('stage-outcomes[0] current output_revision does not match current phase artifact revision', errors)

    def test_completed_task_rejects_pending_artifacts(self) -> None:
        document, state, _ = self.prepare_completed_minimal()
        state['artifact_revisions']['design'].update({'revision': '', 'status': 'PENDING'})
        self.write_state(document, state)
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertTrue(any(error.startswith('COMPLETED task has incomplete or illegally skipped artifacts') for error in errors))
        self.assertTrue(any('design' in error for error in errors))
if __name__ == '__main__':
    unittest.main()

    def test_unknown_risk_trigger_error_lists_allowed_values(self) -> None:
        task = self.plan_task("DES-1", "design", risk_triggers=["architecture"])
        self.write_record("task_plan", "task-plan", [task])
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        message = "\n".join(errors)
        self.assertIn("unknown risk_triggers", message)
        self.assertIn("architecture_change", message)

    def test_risk_trigger_must_support_task_phase(self) -> None:
        task = self.plan_task("DES-1", "design", risk_triggers=["architecture_change"])
        self.write_record("task_plan", "task-plan", [task])
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertIn(
            "task-plan[0] risk trigger architecture_change is not allowed in phase design; allowed phases: ['code_review']",
            errors,
        )

    def test_list_risk_triggers_filters_by_phase(self) -> None:
        output = io.StringIO()
        args = argparse.Namespace(phase="design", json=True, agent="ProjectManagerAgent")
        with contextlib.redirect_stdout(output):
            self.assertEqual(0, long_task.command_list_risk_triggers(args))
        import json
        payload = json.loads(output.getvalue())
        triggers = {item["trigger"] for item in payload["riskTriggers"]}
        self.assertIn("api_contract", triggers)
        self.assertNotIn("architecture_change", triggers)


class LongTaskAttemptHistoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.doc_root = Path(self.temp_dir.name) / 'project_doc'
        args = argparse.Namespace(doc_root=str(self.doc_root), version='V_TEST', target='MOD0001', reuse=False, agent='ProjectManagerAgent')
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(long_task.command_init(args), 0)
        self.task_dir = self.doc_root / 'version' / 'V_TEST' / 'task' / 'MOD0001'
        plan_path = self.task_dir / 'task_plan.json'
        document = plan_path.read_text(encoding='utf-8') if plan_path.exists() else '{"tasks": []}\n'
        tasks = json.loads(document).get('tasks', [])
        task = LongTaskValidationTest.plan_task('TASK-001', status='READY')
        for field in ('status','attempts','output_revision','validation_evidence_ids'):
            task.pop(field, None)
        task['acceptance_trace_ids'] = ['TR-MOD0001-001']
        tasks.append(task)
        plan_path.write_text(json.dumps({'tasks': tasks}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        trace = LongTaskValidationTest.trace()
        trace.update({
            'status': 'COVERED',
            'business_model_refs': ['business-model.md#aggregate'],
            'design_refs': ['design.md#implementation'],
            'test_case_ids': ['CASE-001'],
            'plan_task_ids': ['TASK-001'],
        })
        (self.task_dir / 'traceability.json').write_text(json.dumps({
            'schema_version': 1,
            'items': [trace],
        }, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        state_path = long_task.task_state_path(self.task_dir)
        state_document, state = long_task.read_task_state_cache(self.task_dir)
        for phase, revision in [('implementation_plan', 'PLAN-R01@demo'), ('test_design', 'TESTDESIGN-R01@demo')]:
            artifact = state['artifact_revisions'][phase]
            artifact.update({'revision': revision, 'status': 'PASSED'})
            review = state['collaboration_reviews'][phase]
            review['current_iteration_id'] = artifact['iteration_id']
            review['artifact_revision'] = revision
            review['status'] = 'PASSED'
            review['independent_conclusions'] = {
                reviewer: build_conclusion(self.task_dir, phase, reviewer, revision)
                for reviewer in sorted(long_task.expected_required_reviewers(phase, state, minimal=bool((state.get('work_mode') or {}).get('minimal'))))
            }
        plan_document, tasks = document, json.loads(plan_path.read_text(encoding='utf-8')).get('tasks', [])
        tasks[0]['input_revisions'] = {
            'implementation_plan': 'PLAN-R01@demo',
            'test_design': 'TESTDESIGN-R01@demo',
        }
        plan_path.write_text(json.dumps({'tasks': tasks}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        state['current_phase'] = 'development'
        state['current_round'] = 'DEV-R01'
        state['next_agent'] = 'DevelopAgent'
        state['next_action'] = '执行 TASK-001'
        payloads, _, _ = long_task.append_task_events_payloads(
            self.task_dir, state_path, state_document, state, [{'type':'TASK_RUNTIME_SET','entity_id':'TASK-001','payload':{'runtime':{'status':'READY','attempts':0}}}], synced_at='2026-08-15T00:00:00Z'
        )
        long_task.transactional_text_write(payloads)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write_state(self, _document: str, state: dict) -> None:
        document, _ = long_task.read_task_state_cache(self.task_dir)
        payloads, _, _ = long_task.append_task_events_payloads(
            self.task_dir, long_task.task_state_path(self.task_dir), document, state, [],
            synced_at='2026-08-15T00:00:00Z',
        )
        long_task.transactional_text_write(payloads)

    def call_json(self, function, args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = function(args)
        return result, __import__('json').loads(output.getvalue())

    def start(self):
        return self.call_json(long_task.command_start_attempt, argparse.Namespace(
            task_dir=str(self.task_dir), task_id='TASK-001', input_revision='',
            summary='实现任务', next_action='', agent='DevelopAgent'))

    def passed_refs(self, revision: str) -> tuple[str, str]:
        evidence = self.task_dir / 'evidence' / 'TASK-001' / 'summary.json'
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text('{"status": "passed"}', encoding='utf-8')
        output = self.task_dir / 'evidence' / 'TASK-001' / 'unittest.log'
        output.write_text('tests pass', encoding='utf-8')
        result = self.task_dir / 'evidence' / 'TASK-001' / 'command.json'
        result.write_text(__import__('json').dumps({
            'schema_version': 1, 'kind': 'command_result', 'command': 'python -m unittest',
            'exit_code': 0, 'executed_at': '2026-07-22T10:00:00-05:00', 'revision': revision,
            'output_ref': str(output.relative_to(self.task_dir)),
            'output_digest': long_task.sha256_file(output),
        }), encoding='utf-8')
        command_ref = str(result.relative_to(self.task_dir))
        command_entry = long_task.evidence.register_evidence(
            self.task_dir, agent='DevelopAgent', evidence_type='command_ref',
            ref=command_ref, revision=revision, phase='development',
            scope_refs=['python -m unittest'], command_result_ref=command_ref,
        )
        summary_entry = long_task.evidence.register_evidence(
            self.task_dir, agent='DevelopAgent', evidence_type='runtime_ref',
            ref=str(evidence.relative_to(self.task_dir)), revision=revision, phase='development',
            scope_refs=['TASK-001'],
        )
        return command_entry['evidence_id'], summary_entry['evidence_id']

    def finish_passed_attempt(self, revision: str='DEV-R01@hash') -> dict:
        _, started = self.start()
        command_ref, evidence_ref = self.passed_refs(revision)
        finish_args = argparse.Namespace(
            task_dir=str(self.task_dir), attempt_id=started['attemptId'], status='PASSED',
            output_revision=revision, failure_type='', failure_reason='',
            modified_file=['src/TASK-001.py'], command_ref=[command_ref],
            evidence_ref=[evidence_ref], summary='实现完成',
            next_action='发布候选制品', next_agent='DevelopAgent', agent='DevelopAgent')
        result, finished = self.call_json(long_task.command_finish_attempt, finish_args)
        self.assertEqual(0, result)
        self.assertEqual('PASSED', finished['status'])
        return started

    def publish_artifact(self, attempt_id: str, *, agent: str='DevelopAgent', ensure_review: bool=True):
        if ensure_review:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(0, long_task.acceptance.command_ensure_review(argparse.Namespace(
                    task_dir=str(self.task_dir), phase='development', revision='',
                    agent='ProjectManagerAgent',
                )))
        return self.call_json(long_task.command_publish_artifact, argparse.Namespace(
            task_dir=str(self.task_dir), attempt_id=attempt_id,
            evidence_id=[], open_risk=[], agent=agent,
        ))

    def test_init_creates_event_ledger_and_version_work_record_files(self) -> None:
        self.assertTrue((self.task_dir / 'task_events.jsonl').is_file())
        self.assertFalse((self.task_dir / 'task_attempts.md').exists())
        self.assertFalse((self.task_dir / 'stage_outcomes.md').exists())
        self.assertTrue((self.doc_root / 'version' / 'V_TEST' / 'work_record.md').is_file())
        history = long_task.load_task_history(self.task_dir)
        _, events = long_task.read_work_events(self.doc_root / 'version' / 'V_TEST' / 'work_record.md')
        self.assertEqual([], history['attempts'])
        self.assertEqual([], history['outcomes'])
        self.assertEqual([], events)

    def test_start_and_finish_attempt_update_all_records(self) -> None:
        result, started = self.start()
        self.assertEqual(0, result)
        self.assertEqual('ATTEMPT-TASK-001-I001-A001', started['attemptId'])
        command_ref, evidence_ref = self.passed_refs('DEV-R01@hash')
        finish_args = argparse.Namespace(
            task_dir=str(self.task_dir), attempt_id=started['attemptId'], status='PASSED',
            output_revision='DEV-R01@hash', failure_type='', failure_reason='',
            modified_file=['src/TASK-001.py'], command_ref=[command_ref],
            evidence_ref=[evidence_ref], summary='实现完成',
            next_action='进入 Review', next_agent='ProjectManagerAgent', agent='DevelopAgent')
        result, finished = self.call_json(long_task.command_finish_attempt, finish_args)
        self.assertEqual(0, result)
        self.assertEqual('PASSED', finished['status'])
        errors, _, state, _ = long_task.validate_task(self.task_dir)
        self.assertEqual([], errors)
        self.assertEqual('', state['current_attempt_id'])
        tasks = long_task.runtime_tasks(self.task_dir)
        self.assertEqual(1, tasks[0]['attempts'])
        self.assertEqual('PASSED', tasks[0]['status'])
        _, static_tasks = long_task.read_static_task_plan(self.task_dir)
        self.assertNotIn('attempts', static_tasks[0])
        self.assertNotIn('status', static_tasks[0])
        attempts = long_task.load_task_history(self.task_dir)['attempts']
        self.assertEqual('PASSED', attempts[0]['status'])
        _, events = long_task.read_work_events(self.doc_root / 'version' / 'V_TEST' / 'work_record.md')
        self.assertEqual('TASK_ATTEMPT_COMPLETED', events[0]['event_type'])
        self.assertEqual(started['attemptId'], events[0]['attempt_id'])

    def test_failed_attempt_can_retry_with_continuous_attempt_number(self) -> None:
        _, started = self.start()
        fail_args = argparse.Namespace(
            task_dir=str(self.task_dir), attempt_id=started['attemptId'], status='FAILED',
            output_revision='', failure_type='VALIDATION_FAILED', failure_reason='构建失败',
            modified_file=[], command_ref=['CMD-FAIL'], evidence_ref=['evidence/fail.json'],
            summary='首次失败', next_action='修复后重试', next_agent='DevelopAgent', agent='DevelopAgent')
        self.assertEqual(0, self.call_json(long_task.command_finish_attempt, fail_args)[0])
        result, second = self.start()
        self.assertEqual(0, result)
        self.assertEqual(2, second['attemptNo'])
        _, state = long_task.read_task_state_cache(self.task_dir)
        self.assertEqual(1, state['failed_attempts']['TASK-001'])

    def test_publish_artifact_freezes_passed_attempt_revision_and_opens_review(self) -> None:
        started = self.finish_passed_attempt()
        result, published = self.publish_artifact(started['attemptId'])
        self.assertEqual(0, result)
        self.assertEqual('PUBLISHED', published['status'])
        self.assertEqual('DEV-R01@hash', published['revision'])
        _, state = long_task.read_task_state_cache(self.task_dir)
        artifact = state['artifact_revisions']['development']
        review = state['collaboration_reviews']['development']
        self.assertEqual({'revision': 'DEV-R01@hash', 'status': 'IN_PROGRESS'},
                         {key: artifact[key] for key in ('revision', 'status')})
        self.assertEqual('DEV-R01@hash', review['artifact_revision'])
        self.assertEqual('IN_REVIEW', review['status'])
        self.assertEqual('SpecComplianceReviewAgent', state['next_agent'])
        outcomes = long_task.load_task_history(self.task_dir)['outcomes']
        self.assertEqual(1, len(outcomes))
        self.assertEqual('RUNNING', outcomes[0]['status'])
        self.assertEqual('DevelopAgent', outcomes[0]['produced_by_agent'])
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertEqual([], errors)

    def test_publish_artifact_rejects_missing_review_assertions_without_writing_events(self) -> None:
        started = self.finish_passed_attempt()
        before = (self.task_dir / 'task_events.jsonl').read_bytes()
        with self.assertRaisesRegex(long_task.LifecycleError, 'requires current MANUAL_REVIEW assertions'):
            self.publish_artifact(started['attemptId'], ensure_review=False)
        self.assertEqual(before, (self.task_dir / 'task_events.jsonl').read_bytes())

    def test_lifecycle_routes_missing_review_assertions_to_project_manager(self) -> None:
        self.finish_passed_attempt()
        decision = lifecycle_decision.evaluate(self.task_dir, long_task)
        self.assertEqual('ACCEPTANCE_REGISTRATION_REQUIRED', decision['classification'])
        self.assertEqual('REVIEW_ASSERTION_MISSING', decision['currentState'])
        self.assertEqual('ProjectManagerAgent', decision['requiredAgent'])
        self.assertEqual(['acceptance.ensure-review'], decision['legalActions'])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, long_task.acceptance.command_ensure_review(argparse.Namespace(
                task_dir=str(self.task_dir), phase='development', revision='DEV-R01@hash',
                agent='ProjectManagerAgent',
            )))
        ready = lifecycle_decision.evaluate(self.task_dir, long_task)
        self.assertEqual('PUBLISH_READY', ready['classification'])

    def test_published_assertion_gap_routes_back_to_project_manager(self) -> None:
        started = self.finish_passed_attempt()
        self.publish_artifact(started['attemptId'])
        document = long_task.acceptance.load_document(self.task_dir)
        document['assertions'] = []
        long_task.acceptance.save_document(self.task_dir, document)
        decision = lifecycle_decision.evaluate(self.task_dir, long_task)
        self.assertEqual('ACCEPTANCE_REGISTRATION_REQUIRED', decision['classification'])
        self.assertEqual('ProjectManagerAgent', decision['requiredAgent'])
        with self.assertRaisesRegex(ValueError, 'ProjectManagerAgent must run: .*ensure-review'):
            manual_review.build_context(self.task_dir, 'ASRT-MISSING-CURRENT-REVISION')

    def test_publish_artifact_rejects_non_phase_owner(self) -> None:
        started = self.finish_passed_attempt()
        with self.assertRaisesRegex(
            long_task.agent_context.AgentAuthorizationError,
            'cannot publish development artifact',
        ):
            self.publish_artifact(started['attemptId'], agent='ProjectManagerAgent')

    def test_finalize_phase_requires_review_then_passes_artifact_and_outcome(self) -> None:
        started = self.finish_passed_attempt()
        self.publish_artifact(started['attemptId'])
        finalize_args = argparse.Namespace(
            task_dir=str(self.task_dir), agent='ProjectManagerAgent',
        )
        with self.assertRaisesRegex(ValueError, 'before collaboration Review passes'):
            long_task.command_finalize_phase(finalize_args)

        state_path = long_task.task_state_path(self.task_dir)
        state_document, state = long_task.read_task_state_cache(self.task_dir)
        review = state['collaboration_reviews']['development']
        review['status'] = 'PASSED'
        review['independent_conclusions'] = {
            reviewer: build_conclusion(self.task_dir, 'development', reviewer, 'DEV-R01@hash')
            for reviewer in ('TDDReviewAgent', 'SpecComplianceReviewAgent')
        }
        self.write_state(state_document, state)

        result, finalized = self.call_json(long_task.command_finalize_phase, finalize_args)
        self.assertEqual(0, result)
        self.assertEqual('PASSED', finalized['status'])
        _, state = long_task.read_task_state_cache(self.task_dir)
        self.assertEqual('PASSED', state['artifact_revisions']['development']['status'])
        outcomes = long_task.load_task_history(self.task_dir)['outcomes']
        self.assertEqual('PASSED', outcomes[0]['status'])
        self.assertTrue(outcomes[0]['completed_at'])
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertEqual([], errors)

    def test_reopen_then_rerun_development_to_advance(self) -> None:
        started = self.finish_passed_attempt()
        self.publish_artifact(started['attemptId'])
        document, state = long_task.read_task_state_cache(self.task_dir)
        review = state['collaboration_reviews']['development']
        review['status'] = 'PASSED'
        review['independent_conclusions'] = {
            reviewer: build_conclusion(self.task_dir, 'development', reviewer, 'DEV-R01@hash')
            for reviewer in ('TDDReviewAgent', 'SpecComplianceReviewAgent')
        }
        self.write_state(document, state)
        self.call_json(long_task.command_finalize_phase, argparse.Namespace(
            task_dir=str(self.task_dir), agent='ProjectManagerAgent',
        ))
        self.assertEqual(0, self.call_json(long_task.command_advance_phase, argparse.Namespace(
            task_dir=str(self.task_dir), agent='ProjectManagerAgent',
        ))[0])

        reopen_reason = 'restore development after explicit Review waiver'
        _, confirmation = self.call_json(wk.command_confirm, argparse.Namespace(
            task_dir=str(self.task_dir), until='', reopen_from='development',
            changed_phase='code_review', source_revision='DEV-R02@reopened',
            reason=reopen_reason, agent='ProjectManagerAgent', preview=False,
            apply=True, json=True, skip_phase='',
        ))
        self.assertEqual(0, self.call_json(long_task.command_reopen_phase, argparse.Namespace(
            task_dir=str(self.task_dir), from_phase='development', changed_phase='code_review',
            source_revision='DEV-R02@reopened', reason=reopen_reason, round='',
            user_confirmed=True, confirmation_ref=confirmation['requestFingerprint'],
            agent='ProjectManagerAgent',
        ))[0])
        reopened = long_task.runtime_state(self.task_dir)
        self.assertEqual('IN_PROGRESS', reopened['artifact_revisions']['development']['status'])
        self.assertFalse(reopened['work_policy'].get('explicit_reopen_required'))

        with mock.patch(
            '_long_task.commands.attempts.now',
            return_value='2026-09-05T14:22:00+08:00',
        ):
            rerun = self.finish_passed_attempt('DEV-R02@reopened')
        self.publish_artifact(rerun['attemptId'])
        document, state = long_task.read_task_state_cache(self.task_dir)
        review = state['collaboration_reviews']['development']
        review['status'] = 'PASSED'
        review['independent_conclusions'] = {
            reviewer: build_conclusion(self.task_dir, 'development', reviewer, 'DEV-R02@reopened')
            for reviewer in ('TDDReviewAgent', 'SpecComplianceReviewAgent')
        }
        self.write_state(document, state)
        self.assertEqual(0, self.call_json(long_task.command_finalize_phase, argparse.Namespace(
            task_dir=str(self.task_dir), agent='ProjectManagerAgent',
        ))[0])
        self.assertEqual(0, self.call_json(long_task.command_advance_phase, argparse.Namespace(
            task_dir=str(self.task_dir), agent='ProjectManagerAgent',
        ))[0])
        self.assertEqual('code_review', long_task.runtime_state(self.task_dir)['current_phase'])

    def test_published_artifact_completes_manual_review_and_advances_phase(self) -> None:
        started = self.finish_passed_attempt()
        self.publish_artifact(started['attemptId'])
        test_path = self.task_dir / 'evidence' / 'TASK-001' / 'tdd-green.json'
        test_path.write_text('{"status": "PASSED"}', encoding='utf-8')
        test_entry = long_task.evidence.register_evidence(
            self.task_dir,
            agent='DevelopAgent',
            evidence_type='test_ref',
            ref=str(test_path.relative_to(self.task_dir)),
            revision='DEV-R01@hash',
            phase='development',
            scope_refs=['TASK-001', 'TDD-GREEN'],
        )
        assertions = long_task.acceptance.load_document(self.task_dir)
        tdd_assertion = long_task.acceptance.matching_manual_review_assertions(
            assertions, phase='development', revision='DEV-R01@hash',
            reviewer='TDDReviewAgent', profile_id='development:TDDReviewAgent',
        )[0]
        tdd_assertion_id = tdd_assertion['assertion_id']
        long_task.acceptance.ensure_review_assertions(
            self.task_dir,
            phase='development',
            revision='DEV-R01@hash',
            reviewers=['TDDReviewAgent'],
            acceptance_ids=['AC-ORDER-001', 'AC-ORDER-SECONDARY'],
            source_refs=['TR-MOD0001-001', 'TASK-001#expected_results/0'],
        )
        attempts = long_task.load_task_history(self.task_dir)['attempts']
        attempt = next(item for item in attempts if item['attempt_id'] == started['attemptId'])
        selected_evidence = [
            *attempt['evidence_ids'],
            *attempt['command_evidence_ids'],
            test_entry['evidence_id'],
        ]
        profile = manual_review.PROFILES['development:TDDReviewAgent']
        review_file = Path(self.temp_dir.name) / 'development-tdd-review.md'
        draft_args = argparse.Namespace(
            agent='DevelopAgent',
            task_dir=str(self.task_dir),
            assertion_id=tdd_assertion_id,
            answer=[
                f"{question['question_id']}=YES"
                for question in manual_review.build_questions('TDDReviewAgent', profile)
            ],
            detail=[],
            evidence_id=selected_evidence,
            summary='开发 revision、测试和命令证据满足 TDD Review。',
            note=[],
            output=str(review_file),
        )
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, manual_review.command_draft(draft_args))
            self.assertEqual(0, manual_review.command_submit(argparse.Namespace(
                agent='TDDReviewAgent',
                task_dir=str(self.task_dir),
                review_file=str(review_file),
            )))
        assertions = long_task.acceptance.matching_manual_review_assertions(
            long_task.acceptance.load_document(self.task_dir),
            phase='development', revision='DEV-R01@hash',
            reviewer='TDDReviewAgent', profile_id='development:TDDReviewAgent',
        )
        self.assertEqual({'VERIFIED'}, {item['status'] for item in assertions})
        spec_profile = manual_review.PROFILES['development:SpecComplianceReviewAgent']
        spec_path = self.task_dir / 'evidence' / 'TASK-001' / 'spec-diff.txt'
        spec_path.write_text('implementation matches current requirement and design', encoding='utf-8')
        spec_evidence = long_task.evidence.register_evidence(
            self.task_dir, agent='DevelopAgent', evidence_type='diff_ref',
            ref=str(spec_path.relative_to(self.task_dir)), revision='DEV-R01@hash',
            phase='development', scope_refs=['TASK-001', *spec_profile['criterionIds']],
        )
        spec_code_evidence = long_task.evidence.register_evidence(
            self.task_dir, agent='DevelopAgent', evidence_type='code_ref',
            ref=str(spec_path.relative_to(self.task_dir)), revision='DEV-R01@hash',
            phase='development', scope_refs=['TASK-001', *spec_profile['criterionIds']],
        )
        assertions = long_task.acceptance.load_document(self.task_dir)
        spec_assertion = long_task.acceptance.matching_manual_review_assertions(
            assertions, phase='development', revision='DEV-R01@hash',
            reviewer='SpecComplianceReviewAgent',
            profile_id='development:SpecComplianceReviewAgent',
        )[0]
        spec_assertion_id = spec_assertion['assertion_id']
        spec_review_file = Path(self.temp_dir.name) / 'development-spec-review.md'
        spec_draft_args = argparse.Namespace(
            agent='DevelopAgent', task_dir=str(self.task_dir), assertion_id=spec_assertion_id,
            answer=[
                f"{question['question_id']}=YES"
                for question in manual_review.build_questions('SpecComplianceReviewAgent', spec_profile)
            ],
            detail=[], evidence_id=[spec_evidence['evidence_id'], spec_code_evidence['evidence_id']],
            summary='开发 revision 与当前规格和允许范围一致。', note=[], output=str(spec_review_file),
        )
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, manual_review.command_draft(spec_draft_args))
            self.assertEqual(0, manual_review.command_submit(argparse.Namespace(
                agent='SpecComplianceReviewAgent', task_dir=str(self.task_dir),
                review_file=str(spec_review_file),
            )))
        _, state = long_task.read_task_state_cache(self.task_dir)
        self.assertEqual('PASSED', state['collaboration_reviews']['development']['status'])
        assertion = next(
            item for item in long_task.acceptance.load_document(self.task_dir)['assertions']
            if item['assertion_id'] == tdd_assertion_id
        )
        self.assertEqual('VERIFIED', assertion['status'])
        self.assertTrue(set(selected_evidence).issubset(assertion['evidence_ids']))

        decision = lifecycle_decision.evaluate(self.task_dir, long_task)
        self.assertEqual('CLOSURE_ONLY', decision['classification'])
        self.assertEqual('TASK_FACTS_ONLY', decision['mutationRequirement'])
        close_output = io.StringIO()
        with contextlib.redirect_stdout(close_output):
            self.assertEqual(0, wk.command_close(argparse.Namespace(
                task_dir=str(self.task_dir), agent='ProjectManagerAgent', apply=True,
            )))
        close_payload = __import__('json').loads(close_output.getvalue())
        self.assertEqual('APPLIED', close_payload['status'])
        self.assertEqual('ADVANCE_READY', close_payload['decision']['classification'])
        advance_args = argparse.Namespace(
            task_dir=str(self.task_dir), agent='ProjectManagerAgent',
        )
        result, advanced = self.call_json(long_task.command_advance_phase, advance_args)
        self.assertEqual(0, result)
        self.assertEqual('code_review', advanced['currentPhase'])

    def test_task_context_returns_compact_attempt_context(self) -> None:
        _, started = self.start()
        args = argparse.Namespace(task_dir=str(self.task_dir), task_id='TASK-001', agent='DevelopAgent')
        result, payload = self.call_json(long_task.command_task_context, args)
        self.assertEqual(0, result)
        self.assertEqual(started['attemptId'], payload['currentAttempt']['attempt_id'])
        self.assertNotIn('artifact_revisions', payload)

    def test_static_plan_runtime_fields_do_not_override_event_reducer(self) -> None:
        self.start()
        plan_path = self.task_dir / 'task_plan.json'
        document = json.loads(plan_path.read_text(encoding='utf-8')) if plan_path.exists() else {'tasks': []}
        tasks = document.get('tasks', [])
        tasks[0]['attempts'] = 0
        tasks[0]['status'] = 'PENDING'
        plan_path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        runtime = long_task.runtime_tasks(self.task_dir)
        self.assertEqual(1, runtime[0]['attempts'])
        self.assertEqual('RUNNING', runtime[0]['status'])
    def test_wk_tp_plan_blocks_downstream_until_current_revision_passes(self) -> None:
        state_path = long_task.task_state_path(self.task_dir)
        document, state = long_task.read_task_state_cache(self.task_dir)
        state['current_phase'] = 'development'
        payloads, _, _ = long_task.append_task_events_payloads(
            self.task_dir, state_path, document, state, [], synced_at='2026-08-15T00:00:00Z'
        )
        long_task.transactional_text_write(payloads)
        init_args = argparse.Namespace(
            task_dir=str(self.task_dir), version='V_TEST', target_id='MOD0001',
            requirement_analysis_revision='REQAN-R01@demo', business_model_revision='BM-R01@demo',
            design_revision='DESIGN-R01@demo', test_design_revision='TESTDESIGN-R01@demo',
            minimal=False, force=False, agent='ImplementationPlanAgent',
        )
        self.call_json(long_task.task_plan.command_init, init_args)
        plan = long_task.task_plan.load_plan(self.task_dir)
        plan['tasks'] = [{
            'task_id': 'TASK-MOD0001-DEV-TP-001',
            'title': '实现确定性编译入口',
            'goal': '将已冻结设计转换为一个可独立验证且不依赖全局可变状态的编译入口。',
            'implementation': {
                'owner_module': 'compiler', 'owner_component': 'pipeline',
                'steps': ['定义编译入口和输入输出契约', '实现最小编译流程并保留失败诊断'],
                'affected_files': ['src/compiler/**', 'tests/compiler/**'],
                'forbidden_changes': ['不得改变已冻结设计语义'],
                'implementation_decision_ref': 'IMPL-DEC-MOD0001-COMPILER-ENTRY',
                'strategy': 'MODIFY',
                'existing_code_refs': ['src/compiler/pipeline.py#compile'],
                'shared_logic_disposition': 'EXTRACT_EXISTING',
                'shared_logic_owner': 'compiler.pipeline',
                'compatibility_verifications': ['旧编译调用回归与新确定性入口验证'],
                'source_doc_refs': ['project_doc/version/current/doc/COMPILER/COMPILER_design.md#implementation-strategy-decisions'],
            },
            'acceptance_criteria': [{'id': 'AC-TP-001', 'assertion': '合法输入必须生成确定性结果，非法输入必须返回稳定错误。'}],
            'depends_on': [], 'owner_agent': 'DevelopAgent',
            'reviewer_agents': list(long_task.task_plan.STANDARD_REVIEWERS),
            'validation_commands': ['python -m unittest'],
            'expected_results': ['验证命令成功且输出稳定'],
            'deliverables': ['编译入口', '验证证据'],
            'stop_conditions': ['需要改变 DESIGN-R01 语义'],
            'trace_ids': ['TR-MOD0001-001'], 'flow_refs': [], 'flow_step_refs': [],
            'risk_triggers': [], 'git_checkpoint_required': True,
        }]
        self.task_dir.joinpath(long_task.task_plan.PLAN_FILENAME).write_text(
            json.dumps(plan, ensure_ascii=False, indent=2) + '\n', encoding='utf-8'
        )
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertTrue(any(error.startswith('development-task-plan:') for error in errors))
        self.assertIn('development-task-plan must be PASSED before leaving implementation_plan or starting downstream work', errors)

        self.call_json(long_task.task_plan.command_seal, argparse.Namespace(task_dir=str(self.task_dir), agent='ImplementationPlanAgent'))
        for reviewer in long_task.task_plan.STANDARD_REVIEWERS:
            self.call_json(long_task.task_plan.command_submit_review, argparse.Namespace(
                task_dir=str(self.task_dir), result='PASSED', findings_file='',
                summary=f'{reviewer} passed', agent=reviewer,
            ))
        self.call_json(long_task.task_plan.command_finalize, argparse.Namespace(task_dir=str(self.task_dir), agent='ProjectManagerAgent'))
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertFalse(any(error.startswith('development-task-plan:') for error in errors))
        self.assertNotIn('development-task-plan must be PASSED before leaving implementation_plan or starting downstream work', errors)
