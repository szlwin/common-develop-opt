#!/usr/bin/env python3
"""Regression tests for bounded in-place requirement/design amendments."""
from __future__ import annotations

import argparse
import copy
import contextlib
import fcntl
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import long_task
import impact_analysis
import review_contract
import work_record
from error_catalog import LifecycleError
from _long_task import task_events
from _long_task import amendment_validation
from _long_task.amendment_validation import (
    canonical_file_path,
    prepare_canonical_append,
    render_amendment_block,
    task_writer_lock,
    validate_amendment_history,
    validate_amendment_record,
    validate_bounded_impact_plan,
)


class AmendmentTests(unittest.TestCase):
    phase = "requirement_confirmation"
    base_revision = "REQCONF-R01@base"
    revision = "REQCONF-R01-AM01@" + "0" * 64

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.doc_root = Path(self.temp.name) / "project_doc"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, long_task.command_init(argparse.Namespace(
                doc_root=str(self.doc_root), version="V_TEST", target="AMEND-01",
                reuse=False, request_intake="", agent="ProjectManagerAgent",
            )))
        self.task_dir = self.doc_root / "version" / "V_TEST" / "task" / "AMEND-01"
        self._prepare_current_phase()
        self.canonical_path = self.doc_root / "version" / "V_TEST" / "doc" / "AMEND-01" / "requirement.md"
        self.canonical_path.parent.mkdir(parents=True, exist_ok=True)
        self.canonical_path.write_text("# AMEND-01 requirement\n\nInitial confirmed rule.\n", encoding="utf-8")
        self.revision = self._canonical_revision(self.base_revision, 1)
        self.plan_path = Path(self.temp.name) / "impact-plan.json"
        self._write_impact_plan()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _task(self, task_id: str, *, depends_on: list[str] | None = None) -> dict:
        artifact = long_task.runtime_state(self.task_dir)["artifact_revisions"][self.phase]
        return {
            "id": task_id,
            "logical_task_id": f"LOGICAL-{task_id}",
            "feature_id": "AMEND-01-F01",
            "iteration_id": artifact["iteration_id"],
            "iteration_no": artifact["iteration_no"],
            "supersedes_iteration_id": "",
            "revision_reason": "initial iteration",
            "title": task_id,
            "objective": "confirm one bounded requirement",
            "phase": self.phase,
            "depends_on": list(depends_on or []),
            "owner_agent": "RequirementConfirmationAgent",
            "reviewer_agents": ["RequirementAnalysisAgent"],
            "input_revisions": {},
            "allowed_files": [f"docs/{task_id}.md"],
            "acceptance_trace_ids": [],
            "flow_refs": [],
            "flow_step_refs": [],
            "validation_commands": [],
            "expected_results": ["confirmed"],
            "stop_conditions": ["scope expands"],
            "max_attempts": 3,
            "risk_triggers": [],
        }

    def _prepare_current_phase(self, *, dependent: bool = False) -> None:
        self.canonical_path = self.doc_root / "version" / "V_TEST" / "doc" / "AMEND-01" / "requirement.md"
        self.canonical_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.canonical_path.exists():
            self.canonical_path.write_text("# AMEND-01 requirement\n\nInitial confirmed rule.\n", encoding="utf-8")
        state_document, state = long_task.read_task_state_cache(self.task_dir)
        artifact = state["artifact_revisions"][self.phase]
        artifact.update({"revision": self.base_revision, "status": "IN_PROGRESS"})
        review = state["collaboration_reviews"][self.phase]
        review.update({"artifact_revision": self.base_revision, "status": "IN_REVIEW"})
        state["next_action"] = "review current requirement"
        payloads, _, _ = long_task.append_task_events_payloads(
            self.task_dir, long_task.task_state_path(self.task_dir), state_document, state, [],
            synced_at="2026-09-03T00:00:00+00:00",
        )
        long_task.transactional_text_write(payloads)

        evidences = []
        for task_id in ("TASK-A", "TASK-B"):
            source = self.task_dir / "evidence" / f"{task_id}.txt"
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text(f"evidence for {task_id}\n", encoding="utf-8")
            evidences.append(long_task.evidence.register_evidence(
                self.task_dir,
                agent="RequirementConfirmationAgent",
                evidence_type="document_ref",
                ref=str(source.relative_to(self.task_dir)),
                revision=self.base_revision,
                phase=self.phase,
                scope_refs=[task_id],
            )["evidence_id"])
        self.evidence_ids = evidences

        tasks = [
            self._task("TASK-A"),
            self._task("TASK-B", depends_on=["TASK-A"] if dependent else []),
        ]
        plan_path = self.task_dir / "task_plan.json"
        plan = json.loads(plan_path.read_text(encoding="utf-8")) if plan_path.exists() else {"tasks": []}
        plan["tasks"] = tasks
        plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        state_document, state = long_task.read_task_state_cache(self.task_dir)
        artifact = state["artifact_revisions"][self.phase]
        outcome = {
            "id": "SO-AMEND-01-REQCONF-I001",
            "phase": self.phase,
            "round": "REQCONF-I001",
            "iteration_id": artifact["iteration_id"],
            "iteration_no": artifact["iteration_no"],
            "is_current": True,
            "superseded_by": "",
            "status": "RUNNING",
            "produced_by_agent": "RequirementConfirmationAgent",
            "input_revisions": {},
            "output_revision": self.base_revision,
            "evidence_ids": [evidences[0]],
            "open_risks": [],
            "started_at": "2026-09-03T00:00:00+00:00",
            "completed_at": "",
        }
        specs = [
            {
                "type": "TASK_RUNTIME_SET",
                "entity_id": task["id"],
                "payload": {"runtime": {
                    "status": "PASSED",
                    "attempts": 1,
                    "output_revision": self.base_revision,
                    "validation_evidence_ids": [evidence_id],
                    "iteration_id": artifact["iteration_id"],
                    "iteration_no": artifact["iteration_no"],
                }},
            }
            for task, evidence_id in zip(tasks, evidences)
        ]
        for task, evidence_id in zip(tasks, evidences):
            attempt = {
                "attempt_id": f"ATT-{task['id']}-I001-A001",
                "task_id": task["id"],
                "attempt_no": 1,
                "agent": "RequirementConfirmationAgent",
                "phase": self.phase,
                "status": "PASSED",
                "input_revision": self.base_revision,
                "output_revision": self.base_revision,
                "started_at": "2026-09-03T00:00:00+00:00",
                "completed_at": "2026-09-03T00:01:00+00:00",
                "failure_type": "",
                "failure_reason": "",
                "modified_files": [],
                "command_evidence_ids": [],
                "evidence_ids": [evidence_id],
                "summary": "passed",
                "next_action": "continue",
                "iteration_id": artifact["iteration_id"],
                "iteration_no": artifact["iteration_no"],
            }
            specs.append({
                "type": "ATTEMPT_FINISHED",
                "entity_id": attempt["attempt_id"],
                "payload": {"attempt": attempt},
            })
            work_path = self.doc_root / "version" / "V_TEST" / "work_record.md"
            work_text, _ = work_record.append_event(work_path, {
                "event_id": f"EVENT-{attempt['attempt_id']}-PASSED",
                "timestamp": "2026-09-03T00:01:00+00:00",
                "event_type": "TASK_ATTEMPT_COMPLETED",
                "status": "PASSED",
                "agent": "RequirementConfirmationAgent",
                "source": "test fixture",
                "version": "V_TEST",
                "target_id": "AMEND-01",
                "scope": task["objective"],
                "phase": self.phase,
                "task_type": self.phase,
                "execution_mode": "standard / sequential",
                "task_id": task["id"],
                "attempt_id": attempt["attempt_id"],
                "iteration_id": artifact["iteration_id"],
                "iteration_no": artifact["iteration_no"],
                "input_revision": self.base_revision,
                "output_revision": self.base_revision,
                "evidence_ids": [evidence_id],
                "summary": "passed",
                "state_change": f"{task['id']}: RUNNING -> PASSED",
                "sql_change_summary": "none",
                "validation_summary": "reviewed",
                "modified_files_summary": [],
                "next_action": "continue",
                "next_agent": "ProjectManagerAgent",
            }, version="V_TEST")
            work_path.write_text(work_text, encoding="utf-8")
        specs.append({
            "type": "STAGE_OUTCOME_RECORDED",
            "entity_id": outcome["id"],
            "payload": {"outcome": outcome},
        })
        payloads, _, _ = long_task.append_task_events_payloads(
            self.task_dir, long_task.task_state_path(self.task_dir), state_document, state, specs,
            synced_at="2026-09-03T00:01:00+00:00",
        )
        long_task.transactional_text_write(payloads)
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertEqual([], errors)

    def _write_impact_plan(
        self,
        *,
        fallback: str = "ADAPTER_SCOPED",
        status: str = "PASSED",
        base_revision: str | None = None,
        revision: str | None = None,
        task_dir: Path | None = None,
    ) -> None:
        profile_id = "requirement_confirmation:RequirementAnalysisAgent"
        profile = review_contract.load_contract()["profiles"][profile_id]
        criteria = list(profile["criterionIds"])
        payload = {
            "schemaVersion": 1,
            "authority": "DERIVED",
            "kind": "incremental_revalidation_plan",
            "taskDir": str(task_dir or self.task_dir),
            "phase": self.phase,
            "provenance": {
                "schemaVersion": 1,
                "authority": "DERIVED",
                "baseRevision": base_revision or self.base_revision,
                "revision": revision or self.revision,
                "changeClass": "SEMANTIC_AUTHORITY",
                "processOnly": False,
                "changedScopes": ["requirement-section-2"],
                "changedPaths": [],
                "semanticArtifactChanged": "TRUE",
                "productionChanged": "FALSE",
                "warnings": [],
            },
            "graphImpact": None,
            "requiredRiskTemplates": [],
            "adapter": {
                "used": True,
                "confidence": "HIGH",
                "affectedScopes": ["requirement-section-2"],
                "affectedArtifacts": [],
                "notes": [],
            },
            "fallbackPolicy": fallback,
            "minimumRevalidation": {
                "reviews": [{"profileId": profile_id, "criterionIds": criteria}],
                "validationCapabilities": [],
                "acceptanceDisposition": "RECHECK_AFFECTED_ASSERTIONS",
            },
            "unaffected": {"reviewCriteria": []},
            "reusePolicy": {
                "reviewResultReuseAcrossExactRevision": False,
                "criterionEvidenceIdReuseAcrossExactRevision": False,
                "unaffectedEvidenceSourceReuse": "CONDITIONAL_RECAPTURE_OR_REBIND",
                "conditions": [
                    "source content/digest and criterion scope remain unchanged",
                    "the source is recaptured or deterministically rebound as current-revision Evidence before it supports the criterion",
                    "current Evidence remains ACTIVE and satisfies the criterion Evidence Policy",
                    "Reviewer still emits a new independent Review Result when the exact review revision changes",
                ],
            },
            "revalidationRequired": True,
            "reasonCodes": ["CRITERION_REVALIDATION_REQUIRED"],
            "capabilityResolution": {"status": "NOT_EVALUATED", "required": []},
            "warnings": [],
            "status": status,
            "blockers": [],
            "affectedScope": {"taskIds": ["TASK-B"], "traceIds": [], "acceptanceIds": []},
        }
        self.plan_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _args(self, **overrides) -> argparse.Namespace:
        values = {
            "task_dir": str(self.task_dir),
            "phase": self.phase,
            "base_revision": self.base_revision,
            "revision": self.revision,
            "change_class": "SEMANTIC_AUTHORITY",
            "changed_section": ["requirement-section-2"],
            "affected_phase": [self.phase],
            "affected_task_id": ["TASK-B"],
            "affected_trace_id": [],
            "affected_acceptance_id": [],
            "preserve_evidence_id": [self.evidence_ids[0]],
            "invalidate_evidence_id": [self.evidence_ids[1]],
            "canonical_file": "doc/AMEND-01/requirement.md",
            "impact_plan": str(self.plan_path),
            "reason": "clarify the confirmed rule",
            "agent": "ProjectManagerAgent",
        }
        values.update(overrides)
        return argparse.Namespace(**values)

    def _canonical_revision(
        self,
        base_revision: str,
        amendment_no: int,
        *,
        affected_trace_ids: list[str] | None = None,
        affected_acceptance_ids: list[str] | None = None,
    ) -> str:
        amendment = {
            "amendment_id": f"AM-AMEND-01-REQUIREMENT-CONFIRMATION-{amendment_no:02d}",
            "phase": self.phase,
            "base_revision": base_revision,
            "revision": f"REQCONF-R01-AM{amendment_no:02d}@" + "0" * 64,
            "change_class": "SEMANTIC_AUTHORITY",
            "changed_sections": ["requirement-section-2"],
            "affected_phases": [self.phase],
            "affected_task_ids": ["TASK-B"],
            "affected_trace_ids": list(affected_trace_ids or []),
            "affected_acceptance_ids": list(affected_acceptance_ids or []),
            "preserved_evidence_ids": [self.evidence_ids[0]],
            "invalidated_evidence_ids": [self.evidence_ids[1]],
            "impact_plan_digest": "0" * 64,
            "impact_plan_path": "../../../../../impact-plan.json",
            "canonical_file": "doc/AMEND-01/requirement.md",
            "amendment_no": amendment_no,
            "canonical_document_digest": "",
            "reason": "clarify the confirmed rule",
            "created_at": "2026-09-03T00:00:00+00:00",
            "executed_by_agent": "ProjectManagerAgent",
        }
        _, digest = prepare_canonical_append(self.canonical_path, amendment)
        return f"REQCONF-R01-AM{amendment_no:02d}@{digest}"

    def _tracked_bytes(self) -> dict[str, bytes]:
        paths = [
            self.task_dir / "task_events.jsonl",
            self.task_dir / "cache" / "current_manifest.json",
            self.task_dir / "task_plan.json",
            long_task.evidence.registry_path(self.task_dir),
            long_task.acceptance.assertions_path(self.task_dir),
            self.task_dir / "traceability.json",
        ]
        return {str(path): path.read_bytes() for path in paths}

    def _call(self, args: argparse.Namespace) -> dict:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(0, long_task.command_amend_phase(args))
        return json.loads(output.getvalue())

    def test_amend_only_reworks_affected_task_and_preserves_fact_files(self) -> None:
        before = self._tracked_bytes()
        result = self._call(self._args())
        after = self._tracked_bytes()
        self.assertEqual("AMENDED", result["status"])
        for path in (
            self.task_dir / "task_plan.json",
            long_task.evidence.registry_path(self.task_dir),
            long_task.acceptance.assertions_path(self.task_dir),
            self.task_dir / "traceability.json",
        ):
            self.assertEqual(before[str(path)], after[str(path)])
        tasks = {item["id"]: item for item in long_task.runtime_tasks(self.task_dir)}
        self.assertEqual("REWORK", tasks["TASK-B"]["status"])
        self.assertEqual("", tasks["TASK-B"]["output_revision"])
        self.assertEqual([], tasks["TASK-B"]["validation_evidence_ids"])
        self.assertEqual("PASSED", tasks["TASK-A"]["status"])
        self.assertEqual(self.base_revision, tasks["TASK-A"]["output_revision"])
        self.assertEqual([self.evidence_ids[0]], tasks["TASK-A"]["validation_evidence_ids"])
        self.assertNotEqual(tasks["TASK-A"]["iteration_id"], tasks["TASK-B"]["iteration_id"])

        state = long_task.runtime_state(self.task_dir)
        artifact = state["artifact_revisions"][self.phase]
        self.assertEqual(self.revision, artifact["revision"])
        self.assertEqual("IN_PROGRESS", artifact["status"])
        self.assertEqual(result["amendmentId"], state["active_amendment"]["amendment_id"])
        review = state["collaboration_reviews"][self.phase]
        self.assertEqual("PENDING", review["status"])
        self.assertEqual(1, len(review["review_history"]))
        outcomes = long_task.load_task_history(self.task_dir)["outcomes"]
        self.assertFalse(outcomes[0]["is_current"])
        self.assertEqual(result["newIterationId"], outcomes[0]["superseded_by"])
        self.assertEqual([], long_task.validate_task(self.task_dir)[0])

    def test_rejection_for_mismatched_base_revision_writes_nothing(self) -> None:
        before = self._tracked_bytes()
        with self.assertRaisesRegex(LifecycleError, "base revision"):
            long_task.command_amend_phase(self._args(base_revision="REQCONF-R00@old"))
        self.assertEqual(before, self._tracked_bytes())

    def test_invalid_change_class_reason_and_phase_write_nothing(self) -> None:
        cases = (
            (self._args(change_class="NOT_A_CLASS"), "invalid amend-phase change class"),
            (self._args(reason=""), "non-empty reason"),
            (
                self._args(phase="design", affected_phase=["design"]),
                "only supports the current phase",
            ),
        )
        for args, message in cases:
            with self.subTest(message=message):
                before = self._tracked_bytes()
                with self.assertRaisesRegex(LifecycleError, message):
                    long_task.command_amend_phase(args)
                self.assertEqual(before, self._tracked_bytes())

    def test_conservative_impact_plan_writes_nothing(self) -> None:
        self._write_impact_plan(fallback="CONSERVATIVE_FULL_PROFILE")
        before = self._tracked_bytes()
        with self.assertRaisesRegex(LifecycleError, "requires reopen-phase"):
            long_task.command_amend_phase(self._args())
        self.assertEqual(before, self._tracked_bytes())

    def test_dependency_closure_must_include_dependent_tasks(self) -> None:
        self.temp.cleanup()
        self.temp = tempfile.TemporaryDirectory()
        self.doc_root = Path(self.temp.name) / "project_doc"
        with contextlib.redirect_stdout(io.StringIO()):
            long_task.command_init(argparse.Namespace(
                doc_root=str(self.doc_root), version="V_TEST", target="AMEND-01",
                reuse=False, request_intake="", agent="ProjectManagerAgent",
            ))
        self.task_dir = self.doc_root / "version" / "V_TEST" / "task" / "AMEND-01"
        self._prepare_current_phase(dependent=True)
        self.plan_path = Path(self.temp.name) / "impact-plan.json"
        self._write_impact_plan()
        before = self._tracked_bytes()
        with self.assertRaisesRegex(LifecycleError, "dependency-closed"):
            long_task.command_amend_phase(self._args(affected_task_id=["TASK-A"]))
        self.assertEqual(before, self._tracked_bytes())

    def test_running_attempt_and_downstream_passed_outcome_are_rejected(self) -> None:
        before = self._tracked_bytes()
        history = long_task.load_task_history(self.task_dir)
        history["attempts"].append({"status": "RUNNING"})
        with mock.patch.object(long_task, "validate_task", return_value=([], [], {}, [])), \
             mock.patch.object(long_task, "load_task_history", return_value=history):
            with self.assertRaisesRegex(LifecycleError, "RUNNING"):
                long_task.command_amend_phase(self._args())
        self.assertEqual(before, self._tracked_bytes())

        downstream = dict(history)
        downstream["attempts"] = []
        downstream["outcomes"] = [
            *history["outcomes"],
            {"phase": "requirement_analysis", "status": "PASSED", "is_current": True},
        ]
        with mock.patch.object(long_task, "validate_task", return_value=([], [], {}, [])), \
             mock.patch.object(long_task, "load_task_history", return_value=downstream):
            with self.assertRaisesRegex(LifecycleError, "downstream PASSED"):
                long_task.command_amend_phase(self._args())
        self.assertEqual(before, self._tracked_bytes())

    def test_compaction_preserves_amendment_history_and_active_projection(self) -> None:
        self._call(self._args())
        prepared = task_events.prepare_compaction(
            self.task_dir, force=True, timestamp="2026-09-03T01:00:00+00:00"
        )
        archive = self.task_dir / prepared["archiveRef"]
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_bytes(prepared["_archiveBytes"])
        (self.task_dir / "task_events.jsonl").write_text(prepared["_ledgerText"], encoding="utf-8")
        reduced = task_events.reduce_events(task_events.read_events(self.task_dir))
        self.assertEqual(1, len(reduced["amendment_events"]))
        state = task_events.project_state({}, reduced, synced_at="2026-09-03T01:00:00+00:00")
        self.assertEqual("AM-AMEND-01-REQUIREMENT-CONFIRMATION-01", state["active_amendment"]["amendment_id"])

    def test_multiple_amendments_preserve_unaffected_task_across_revision_chain(self) -> None:
        self._call(self._args())
        second_revision = self._canonical_revision(self.revision, 2)
        self._write_impact_plan(base_revision=self.revision, revision=second_revision)
        second = self._call(self._args(
            base_revision=self.revision,
            revision=second_revision,
        ))
        self.assertEqual("AMENDED", second["status"])
        tasks = {item["id"]: item for item in long_task.runtime_tasks(self.task_dir)}
        self.assertEqual("PASSED", tasks["TASK-A"]["status"])
        self.assertEqual(self.base_revision, tasks["TASK-A"]["output_revision"])
        self.assertEqual("REWORK", tasks["TASK-B"]["status"])
        self.assertEqual([], long_task.validate_task(self.task_dir)[0])

    def test_amendment_projection_expires_after_artifact_passes(self) -> None:
        self._call(self._args())
        events = task_events.read_events(self.task_dir)
        reduced = task_events.reduce_events(events)
        state = reduced["state_authority"]
        state["artifact_revisions"][self.phase]["status"] = "PASSED"
        projected = task_events.project_state(
            state, reduced, synced_at="2026-09-03T02:00:00+00:00"
        )
        self.assertNotIn("active_amendment", projected)

    def test_unknown_references_and_impact_binding_mismatch_write_nothing(self) -> None:
        cases = (
            (self._args(affected_trace_id=["TRACE-UNKNOWN"]), "unknown affected trace"),
            (self._args(affected_acceptance_id=["AC-UNKNOWN"]), "unknown affected acceptance"),
            (self._args(preserve_evidence_id=["EVD-UNKNOWN"]), "unknown Evidence"),
        )
        for args, message in cases:
            with self.subTest(message=message):
                before = self._tracked_bytes()
                with self.assertRaisesRegex(LifecycleError, message):
                    long_task.command_amend_phase(args)
                self.assertEqual(before, self._tracked_bytes())

        wrong_task_dir = Path(self.temp.name) / "other-task"
        self._write_impact_plan(task_dir=wrong_task_dir)
        before = self._tracked_bytes()
        with self.assertRaisesRegex(LifecycleError, "taskDir"):
            long_task.command_amend_phase(self._args())
        self.assertEqual(before, self._tracked_bytes())

    def test_canonical_append_and_digest_are_verified(self) -> None:
        self._call(self._args())
        text = self.canonical_path.read_text(encoding="utf-8")
        self.assertIn("common-develop:amendment", text)
        self.assertIn(self.revision, text)
        self.assertEqual([], long_task.validate_task(self.task_dir)[0])
        self.canonical_path.write_text(text.replace("clarify the confirmed rule", "tampered rule"), encoding="utf-8")
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertTrue(any("canonical document digest mismatch" in error for error in errors))

    def test_invalidated_evidence_cannot_remain_on_unaffected_task(self) -> None:
        before = self._tracked_bytes()
        with self.assertRaisesRegex(LifecycleError, "unaffected task"):
            self._call(self._args(preserve_evidence_id=[], invalidate_evidence_id=[self.evidence_ids[0]]))
        self.assertEqual(before, self._tracked_bytes())

    def test_impact_plan_scope_must_match_command_exactly(self) -> None:
        before = self._tracked_bytes()
        with self.assertRaisesRegex(LifecycleError, "exactly match Impact Plan"):
            self._call(self._args(affected_task_id=["TASK-A"], preserve_evidence_id=[], invalidate_evidence_id=[]))
        self.assertEqual(before, self._tracked_bytes())

    def test_process_only_is_not_a_canonical_amendment(self) -> None:
        before = self._tracked_bytes()
        with self.assertRaisesRegex(LifecycleError, "PROCESS_ONLY changes do not use amend-phase"):
            long_task.command_amend_phase(self._args(change_class="PROCESS_ONLY"))
        self.assertEqual(before, self._tracked_bytes())

    def test_trace_and_acceptance_closure_is_required_by_command(self) -> None:
        plan = json.loads(self.plan_path.read_text(encoding="utf-8"))
        plan["affectedScope"] = {
            "taskIds": ["TASK-B"],
            "traceIds": ["TR-AFFECTED"],
            "acceptanceIds": ["AC-AFFECTED"],
        }
        self.plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        task_plan = json.loads((self.task_dir / "task_plan.json").read_text(encoding="utf-8"))
        task_plan["tasks"][1]["acceptance_trace_ids"] = ["TR-AFFECTED"]
        (self.task_dir / "task_plan.json").write_text(
            json.dumps(task_plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (self.task_dir / "traceability.json").write_text(json.dumps({
            "schema_version": 1,
            "items": [{
                "id": "TR-AFFECTED",
                "description": "affected requirement trace",
                "status": "COVERED",
                "plan_task_ids": ["TASK-B"],
                "acceptance_criteria": ["AC-AFFECTED"],
            }],
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        before = self._tracked_bytes()
        with self.assertRaisesRegex(LifecycleError, "complete task Trace closure"):
            self._call(self._args())
        self.assertEqual(before, self._tracked_bytes())
        with self.assertRaisesRegex(LifecycleError, "complete Trace Acceptance closure"):
            self._call(self._args(affected_trace_id=["TR-AFFECTED"]))

    def test_history_validation_rechecks_trace_acceptance_closure(self) -> None:
        plan = json.loads(self.plan_path.read_text(encoding="utf-8"))
        plan["affectedScope"] = {
            "taskIds": ["TASK-B"],
            "traceIds": ["TR-AFFECTED"],
            "acceptanceIds": ["AC-AFFECTED"],
        }
        self.plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        task_plan = json.loads((self.task_dir / "task_plan.json").read_text(encoding="utf-8"))
        task_plan["tasks"][1]["acceptance_trace_ids"] = ["TR-AFFECTED"]
        (self.task_dir / "task_plan.json").write_text(
            json.dumps(task_plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (self.task_dir / "traceability.json").write_text(json.dumps({
            "schema_version": 1,
            "items": [{"id": "TR-AFFECTED", "description": "affected requirement trace", "status": "COVERED", "plan_task_ids": ["TASK-B"], "acceptance_criteria": ["AC-AFFECTED"]}],
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.revision = self._canonical_revision(
            self.base_revision, 1,
            affected_trace_ids=["TR-AFFECTED"],
            affected_acceptance_ids=["AC-AFFECTED"],
        )
        self._write_impact_plan(revision=self.revision)
        plan = json.loads(self.plan_path.read_text(encoding="utf-8"))
        plan["affectedScope"] = {
            "taskIds": ["TASK-B"],
            "traceIds": ["TR-AFFECTED"],
            "acceptanceIds": ["AC-AFFECTED"],
        }
        self.plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self._call(self._args(
            affected_trace_id=["TR-AFFECTED"], affected_acceptance_id=["AC-AFFECTED"]
        ))
        history = task_events.read_events(self.task_dir)
        amendment_event = next(item for item in history if item.get("type") == "ARTIFACT_AMENDED")
        amendment_event["payload"]["amendment"]["affected_trace_ids"] = []
        amendment_event["payload"]["amendment"]["affected_acceptance_ids"] = []
        # The tampered event is intentionally not reserialized into the hash chain;
        # direct history validation is still expected to report the closure gap.
        errors: list[str] = []
        from _long_task.amendment_validation import validate_amendment_history
        static_tasks = long_task.read_static_task_plan(self.task_dir)[1]
        validate_amendment_history(
            self.task_dir, long_task.runtime_state(self.task_dir),
            {"amendment_events": [amendment_event["payload"]["amendment"]]},
            static_tasks,
            json.loads((self.task_dir / "traceability.json").read_text(encoding="utf-8"))["items"],
            {}, {}, errors,
        )
        self.assertTrue(any("complete task Trace closure" in error for error in errors))

    def test_canonical_file_rejects_rogue_block_and_unknown_field(self) -> None:
        self._call(self._args())
        history = long_task.load_task_history(self.task_dir)["amendment_events"]
        amendment = history[0]
        rogue = dict(amendment)
        rogue["amendment_id"] = "AM-ROGUE"
        rogue["revision"] = amendment["revision"].replace("AM01", "AM99")
        rogue["amendment_no"] = 99
        with self.canonical_path.open("a", encoding="utf-8") as stream:
            stream.write("\n## Amendment AM-ROGUE\n" + render_amendment_block(rogue) + "\n")
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertTrue(any("no ARTIFACT_AMENDED event" in error for error in errors))

        text = self.canonical_path.read_text(encoding="utf-8")
        text = text.replace('"amendment_id":"AM-ROGUE"', '"unknown_field":"x","amendment_id":"AM-ROGUE"')
        self.canonical_path.write_text(text, encoding="utf-8")
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertTrue(any("unknown fields" in error for error in errors))

    def test_canonical_file_rejects_missing_and_duplicate_blocks(self) -> None:
        self._call(self._args())
        amendment = long_task.load_task_history(self.task_dir)["amendment_events"][0]
        original = self.canonical_path.read_text(encoding="utf-8")
        block = render_amendment_block(amendment)
        self.canonical_path.write_text(original.replace(block, ""), encoding="utf-8")
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertTrue(any("blocks missing for events" in error for error in errors))

        self.canonical_path.write_text(original + "\n" + block + "\n", encoding="utf-8")
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertTrue(any("duplicate IDs" in error for error in errors))
        self.assertTrue(any("duplicate revisions" in error for error in errors))

    def test_canonical_file_ownership_and_lock_contention_are_fail_closed(self) -> None:
        foreign = self.doc_root / "version" / "V_TEST" / "doc" / "FOREIGN" / "requirement.md"
        foreign.parent.mkdir(parents=True, exist_ok=True)
        foreign.write_text("# foreign\n", encoding="utf-8")
        with self.assertRaisesRegex(LifecycleError, "ownership cannot be proven"):
            self._call(self._args(canonical_file="doc/FOREIGN/requirement.md"))
        lock_path = self.task_dir / "cache" / "amend-phase.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            before = self._tracked_bytes()
            with self.assertRaisesRegex(LifecycleError, "another amend-phase writer"):
                self._call(self._args())
            self.assertEqual(before, self._tracked_bytes())
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def test_unreferenced_version_document_cannot_contain_amendment_block(self) -> None:
        self._call(self._args())
        history = long_task.load_task_history(self.task_dir)["amendment_events"]
        foreign = self.doc_root / "version" / "V_TEST" / "doc" / "UNREFERENCED" / "notes.md"
        foreign.parent.mkdir(parents=True, exist_ok=True)
        foreign.write_text("# Notes\n\n" + render_amendment_block(history[0]) + "\n", encoding="utf-8")
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertTrue(any("contains Amendment blocks with no ARTIFACT_AMENDED event" in error for error in errors))

    def test_history_record_semantics_reject_bad_timestamp_and_external_plan(self) -> None:
        self._call(self._args())
        amendment = long_task.load_task_history(self.task_dir)["amendment_events"][0]
        plan = json.loads(self.plan_path.read_text(encoding="utf-8"))
        common = {
            "tasks": long_task.task_events.materialize_tasks(
                long_task.read_static_task_plan(self.task_dir)[1],
                long_task.load_task_history(self.task_dir),
            ),
            "traceability": [],
            "impact_plan": plan,
            "require_impact_file": False,
        }
        bad_timestamp = copy.deepcopy(amendment)
        bad_timestamp["created_at"] = "not-a-time"
        errors = validate_amendment_record(self.task_dir, bad_timestamp, prefix="bad-timestamp", **common)
        self.assertTrue(any("created_at must be a valid" in error for error in errors))
        external_plan = copy.deepcopy(amendment)
        external_plan["impact_plan_path"] = "/etc/hosts"
        errors = validate_amendment_record(self.task_dir, external_plan, prefix="external-plan", **common)
        self.assertTrue(any("impact_plan_path must be inside" in error for error in errors))

    def test_task_writer_lock_protects_non_amend_lifecycle_command(self) -> None:
        lock_path = self.task_dir / "cache" / "amend-phase.lock"
        with lock_path.open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(LifecycleError, "another amend-phase writer"):
                long_task.command_start_attempt(argparse.Namespace(task_dir=str(self.task_dir)))
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def test_symlinked_doc_root_is_rejected_before_canonical_resolution(self) -> None:
        doc_root = self.doc_root / "version" / "V_TEST" / "doc"
        external = Path(self.temp.name) / "external-doc"
        external.mkdir()
        external_file = external / "AMEND-01" / "requirement.md"
        external_file.parent.mkdir()
        external_file.write_text("external\n", encoding="utf-8")
        real_doc = doc_root.with_name("doc-real")
        doc_root.rename(real_doc)
        doc_root.symlink_to(external, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "canonical doc tree.*symbolic link"):
            canonical_file_path(
                self.task_dir,
                "doc/AMEND-01/requirement.md",
                phase=self.phase,
                tasks=[],
                affected_task_ids=[],
            )
        self.assertEqual("external\n", external_file.read_text(encoding="utf-8"))

    def test_symlinked_doc_child_and_dangling_link_fail_closed(self) -> None:
        doc_root = self.doc_root / "version" / "V_TEST" / "doc"
        external = Path(self.temp.name) / "external-doc"
        external.mkdir()
        (doc_root / "UNREFERENCED").symlink_to(external, target_is_directory=True)
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertTrue(any("canonical doc tree contains unsupported symbolic link" in error for error in errors))

        (doc_root / "UNREFERENCED").unlink()
        (doc_root / "DANGLING").symlink_to(Path(self.temp.name) / "missing-doc", target_is_directory=True)
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertTrue(any("canonical doc tree contains unsupported symbolic link" in error for error in errors))

    def test_symlinked_impact_snapshot_root_is_rejected(self) -> None:
        self._call(self._args())
        amendment = long_task.load_task_history(self.task_dir)["amendment_events"][0]
        snapshot_root = self.task_dir / "history" / "amendments"
        real_root = snapshot_root.with_name("amendments-real")
        snapshot_root.rename(real_root)
        external = Path(self.temp.name) / "external-amendments"
        external.mkdir()
        snapshot_root.symlink_to(external, target_is_directory=True)
        errors = validate_amendment_record(
            self.task_dir,
            amendment,
            tasks=long_task.runtime_tasks(self.task_dir),
            impact_plan=json.loads(self.plan_path.read_text(encoding="utf-8")),
            require_impact_file=False,
            prefix="symlinked-history",
        )
        self.assertTrue(any("impact plan snapshot tree must not be a symbolic link" in error for error in errors))

    def test_symlinked_impact_snapshot_child_is_rejected(self) -> None:
        snapshot_root = self.task_dir / "history" / "amendments"
        snapshot_root.mkdir(parents=True, exist_ok=True)
        (snapshot_root / "EXTERNAL").symlink_to(
            Path(self.temp.name) / "missing-amendments", target_is_directory=True
        )
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertTrue(any(
            "impact plan snapshot tree contains unsupported symbolic link" in error
            for error in errors
        ))

    def test_history_replays_bounded_impact_gate(self) -> None:
        self._call(self._args())
        amendment = long_task.load_task_history(self.task_dir)["amendment_events"][0]
        plan = json.loads(self.plan_path.read_text(encoding="utf-8"))
        common = {
            "tasks": long_task.runtime_tasks(self.task_dir),
            "traceability": [],
            "require_impact_file": False,
        }
        cases = []
        unknown_amendment = copy.deepcopy(amendment)
        unknown_amendment["change_class"] = "UNKNOWN"
        unknown_plan = copy.deepcopy(plan)
        unknown_plan["provenance"]["changeClass"] = "UNKNOWN"
        cases.append((unknown_amendment, unknown_plan, "unknown or conservative"))
        conservative_plan = copy.deepcopy(plan)
        conservative_plan["fallbackPolicy"] = "CONSERVATIVE_FULL_PROFILE"
        cases.append((copy.deepcopy(amendment), conservative_plan, "unknown or conservative"))
        low_confidence_plan = copy.deepcopy(plan)
        low_confidence_plan["adapter"]["confidence"] = "LOW"
        cases.append((copy.deepcopy(amendment), low_confidence_plan, "cannot prove a bounded"))
        low_graph_plan = copy.deepcopy(plan)
        low_graph_plan["fallbackPolicy"] = "GRAPH_SCOPED"
        low_graph_plan["graphImpact"] = {"confidence": "MEDIUM", "crossContext": False}
        cases.append((copy.deepcopy(amendment), low_graph_plan, "cannot prove a bounded"))
        cross_context_plan = copy.deepcopy(plan)
        cross_context_plan["graphImpact"] = {"crossContext": True}
        cases.append((copy.deepcopy(amendment), cross_context_plan, "cross-context"))
        high_risk_plan = copy.deepcopy(plan)
        high_risk_plan["requiredRiskTemplates"] = ["security"]
        cases.append((copy.deepcopy(amendment), high_risk_plan, "high-risk"))
        high_risk_scope_plan = copy.deepcopy(plan)
        high_risk_scope_plan["adapter"]["affectedScopes"] = ["transaction-boundary"]
        cases.append((copy.deepcopy(amendment), high_risk_scope_plan, "high-risk"))
        for bad_amendment, bad_plan, message in cases:
            with self.subTest(message=message):
                errors = validate_amendment_record(
                    self.task_dir, bad_amendment, impact_plan=bad_plan, **common
                )
                self.assertTrue(any(message in error for error in errors), errors)

    def test_malformed_impact_plan_is_rejected_before_amendment_write(self) -> None:
        base_plan = json.loads(self.plan_path.read_text(encoding="utf-8"))

        def adapter_not_used(plan: dict) -> None:
            plan["adapter"]["used"] = False

        def string_cross_context(plan: dict) -> None:
            plan["graphImpact"] = {
                "graphPath": "graph.json",
                "changedRefs": ["REQ-1"],
                "impactedRefs": [{"ref": "REQ-1", "depth": 0, "reasonPath": []}],
                "impactedNodeTypes": ["REQUIREMENT"],
                "contexts": [],
                "crossContext": "true",
                "requiredRiskTemplates": [],
                "confidence": "HIGH",
            }

        def string_risk_templates(plan: dict) -> None:
            plan["requiredRiskTemplates"] = "security"

        def unknown_risk_template(plan: dict) -> None:
            plan["requiredRiskTemplates"] = ["NOT-A-RISK-TEMPLATE"]

        def string_adapter_scopes(plan: dict) -> None:
            plan["adapter"]["affectedScopes"] = "transaction-boundary"

        def incomplete_graph_scope(plan: dict) -> None:
            plan["fallbackPolicy"] = "GRAPH_SCOPED"
            plan["graphImpact"] = {"confidence": "HIGH", "crossContext": False}

        def inconsistent_graph_risks(plan: dict) -> None:
            plan["graphImpact"] = {
                "graphPath": "graph.json",
                "changedRefs": ["REQ-1"],
                "impactedRefs": [{"ref": "REQ-1", "depth": 0, "reasonPath": []}],
                "impactedNodeTypes": ["REQUIREMENT"],
                "contexts": [],
                "crossContext": False,
                "requiredRiskTemplates": ["security"],
                "confidence": "HIGH",
            }

        def string_affected_tasks(plan: dict) -> None:
            plan["affectedScope"]["taskIds"] = "TASK-B"

        def incomplete_plan_shape(plan: dict) -> None:
            del plan["reusePolicy"]

        def empty_revalidation(plan: dict) -> None:
            plan["minimumRevalidation"]["reviews"] = []
            plan["minimumRevalidation"]["validationCapabilities"] = []
            plan["unaffected"]["reviewCriteria"] = []
            plan["revalidationRequired"] = False
            plan["reasonCodes"] = []

        def all_criteria_unaffected(plan: dict) -> None:
            affected = plan["minimumRevalidation"]["reviews"][0]
            plan["unaffected"]["reviewCriteria"] = [copy.deepcopy(affected)]
            plan["minimumRevalidation"]["reviews"] = []
            plan["revalidationRequired"] = False
            plan["reasonCodes"] = []

        def unknown_profile_in_both_partitions(plan: dict) -> None:
            entry = {"profileId": "requirement_confirmation:UnknownAgent", "criterionIds": ["RC-REQ-001"]}
            plan["minimumRevalidation"]["reviews"].append(entry)
            plan["unaffected"]["reviewCriteria"].append(copy.deepcopy(entry))

        def overlapping_partitions(plan: dict) -> None:
            profile_id = plan["minimumRevalidation"]["reviews"][0]["profileId"]
            criterion_id = plan["minimumRevalidation"]["reviews"][0]["criterionIds"][0]
            plan["unaffected"]["reviewCriteria"] = [
                {"profileId": profile_id, "criterionIds": [criterion_id]}
            ]

        def object_criterion_ids(plan: dict) -> None:
            plan["minimumRevalidation"]["reviews"][0]["criterionIds"] = [{"id": "RC-REQ-001"}]

        def object_reason_codes(plan: dict) -> None:
            plan["reasonCodes"] = [{"code": "CRITERION_REVALIDATION_REQUIRED"}]

        cases = (
            (adapter_not_used, "cannot prove a bounded amendment"),
            (string_cross_context, "graphImpact.crossContext must be a boolean"),
            (string_risk_templates, "requiredRiskTemplates must be an array"),
            (unknown_risk_template, "unknown risk Review template"),
            (string_adapter_scopes, "adapter.affectedScopes must be an array"),
            (incomplete_graph_scope, "GRAPH_SCOPED requires non-empty Graph impact paths"),
            (inconsistent_graph_risks, "requiredRiskTemplates must equal"),
            (string_affected_tasks, "affectedScope.taskIds must be an array"),
            (incomplete_plan_shape, "snapshot is missing fields"),
            (empty_revalidation, "must declare Review criteria or validation capabilities"),
            (all_criteria_unaffected, "must affect at least one Review criterion"),
            (unknown_profile_in_both_partitions, "unknown Review profile"),
            (overlapping_partitions, "criteria overlap"),
            (object_criterion_ids, "must be an array of non-empty strings"),
            (object_reason_codes, "must be an array of non-empty strings"),
        )
        before = self._tracked_bytes()
        for mutate, message in cases:
            with self.subTest(message=message):
                plan = copy.deepcopy(base_plan)
                mutate(plan)
                self.plan_path.write_text(
                    json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(LifecycleError, message):
                    long_task.command_amend_phase(self._args())
                self.assertEqual(before, self._tracked_bytes())

    def test_bounded_gate_accepts_real_impact_plan_output(self) -> None:
        contract = review_contract.load_contract()
        profile_id = next(
            key for key, value in contract["profiles"].items()
            if value.get("phase") == self.phase
        )
        plan = impact_analysis.build_plan(
            task_dir=self.task_dir,
            phase=self.phase,
            base_revision=self.base_revision,
            revision=self.revision,
            change_class="SEMANTIC_AUTHORITY",
            changed_scopes=["requirement-section-2"],
            profile_ids=[profile_id],
            adapter_result={
                "schemaVersion": 1,
                "baseRevision": self.base_revision,
                "revision": self.revision,
                "confidence": "HIGH",
                "affectedProfiles": [profile_id],
                "affectedScopes": ["requirement-section-2"],
                "affectedTaskIds": ["TASK-B"],
            },
        )
        self.assertEqual("ADAPTER_SCOPED", plan["fallbackPolicy"])
        self.assertEqual([], validate_bounded_impact_plan(
            plan, change_class="SEMANTIC_AUTHORITY"
        ))

        graph_path = Path(self.temp.name) / "engineering-graph.json"
        graph_path.write_text(json.dumps({
            "schemaVersion": 1,
            "authority": "DERIVED",
            "kind": "engineering_knowledge_graph",
            "nodes": [{
                "id": "REQ-AMEND-01",
                "type": "REQUIREMENT",
                "name": "Amendment requirement",
                "sourceRefs": ["requirement.md"],
                "contextRef": "BC-AMEND",
            }],
            "relationships": [],
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        graph_plan = impact_analysis.build_plan(
            task_dir=self.task_dir,
            phase=self.phase,
            base_revision=self.base_revision,
            revision=self.revision,
            change_class="SEMANTIC_AUTHORITY",
            changed_scopes=["requirement-section-2"],
            profile_ids=[profile_id],
            graph_path=graph_path,
            changed_refs=["REQ-AMEND-01"],
            affected_task_ids=["TASK-B"],
        )
        self.assertEqual("GRAPH_SCOPED", graph_plan["fallbackPolicy"])
        self.assertEqual([], validate_bounded_impact_plan(
            graph_plan, change_class="SEMANTIC_AUTHORITY"
        ))

    def test_bounded_gate_accepts_dynamic_risk_profile_and_rejects_wrong_phase(self) -> None:
        dynamic_plan = impact_analysis.build_plan(
            task_dir=self.task_dir,
            phase="design",
            base_revision="DES-R01@base",
            revision="DES-R02@" + "1" * 64,
            change_class="PRODUCTION",
            profile_ids=["design:ConcurrencyReviewAgent"],
            adapter_result={
                "schemaVersion": 1,
                "confidence": "HIGH",
                "affectedProfiles": ["design:ConcurrencyReviewAgent"],
            },
            affected_task_ids=["TASK-B"],
        )
        self.assertEqual([], validate_bounded_impact_plan(dynamic_plan, change_class="PRODUCTION"))

        wrong_phase = copy.deepcopy(dynamic_plan)
        wrong_phase["minimumRevalidation"]["reviews"][0]["profileId"] = "development:TDDReviewAgent"
        errors = validate_bounded_impact_plan(wrong_phase, change_class="PRODUCTION")
        self.assertTrue(any("belongs to phase development" in error for error in errors), errors)

    def test_external_symlink_loops_return_structured_errors(self) -> None:
        loop_a = Path(self.temp.name) / "loop-a"
        loop_b = Path(self.temp.name) / "loop-b"
        loop_a.symlink_to(loop_b)
        loop_b.symlink_to(loop_a)
        try:
            self._write_impact_plan(task_dir=loop_a)
            before = self._tracked_bytes()
            with self.assertRaisesRegex(LifecycleError, "cannot be resolved"):
                long_task.command_amend_phase(self._args())
            self.assertEqual(before, self._tracked_bytes())

            self._write_impact_plan()
            self._call(self._args())
            amendment = copy.deepcopy(
                long_task.load_task_history(self.task_dir)["amendment_events"][0]
            )
            amendment["impact_plan_path"] = str(loop_a)
            errors = validate_amendment_record(
                self.task_dir,
                amendment,
                tasks=long_task.runtime_tasks(self.task_dir),
                impact_plan=json.loads(self.plan_path.read_text(encoding="utf-8")),
                require_impact_file=False,
                prefix="loop-plan",
            )
            self.assertTrue(any("impact_plan_path cannot be resolved" in error for error in errors), errors)
        finally:
            loop_a.unlink(missing_ok=True)
            loop_b.unlink(missing_ok=True)

    def test_impact_plan_symlink_loop_is_a_structured_lifecycle_error(self) -> None:
        loop_a = Path(self.temp.name) / "impact-plan-a"
        loop_b = Path(self.temp.name) / "impact-plan-b"
        loop_a.symlink_to(loop_b)
        loop_b.symlink_to(loop_a)
        try:
            with self.assertRaisesRegex(LifecycleError, "impact plan is invalid"):
                long_task.command_amend_phase(self._args(impact_plan=str(loop_a)))
        finally:
            loop_a.unlink(missing_ok=True)
            loop_b.unlink(missing_ok=True)

    def test_history_validation_scans_each_protected_tree_once(self) -> None:
        self._call(self._args())
        original = amendment_validation.assert_no_symlink_tree
        with mock.patch.object(
            amendment_validation,
            "assert_no_symlink_tree",
            wraps=original,
        ) as scan:
            errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertEqual([], errors)
        self.assertEqual(2, scan.call_count)
        roots = [Path(call.args[0]).resolve() for call in scan.call_args_list]
        self.assertCountEqual([
            (self.doc_root / "version" / "V_TEST" / "doc").resolve(),
            (self.task_dir / "history" / "amendments").resolve(),
        ], roots)

    def test_active_amendment_without_history_returns_structured_error(self) -> None:
        self._call(self._args())
        state = long_task.runtime_state(self.task_dir)
        static_tasks = long_task.read_static_task_plan(self.task_dir)[1]
        errors: list[str] = []
        validate_amendment_history(
            self.task_dir,
            state,
            {"amendment_events": []},
            static_tasks,
            [],
            {},
            {},
            errors,
        )
        self.assertTrue(any("active_amendment has no corresponding Amendment history" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
