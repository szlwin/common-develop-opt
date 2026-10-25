"""Executable gate and iteration regression tests for current common-develop."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import long_task
import wk
from _long_task import task_events
import work_mode
from internal.test_markers import slow


class ProcessGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.doc_root = Path(self.tmp.name) / "project_doc"
        self.call(
            long_task.command_init,
            argparse.Namespace(
                doc_root=str(self.doc_root), version="V_TEST", target="FEAT001", reuse=False,
                agent="ProjectManagerAgent",
            ),
        )
        self.task_dir = self.doc_root / "version" / "V_TEST" / "task" / "FEAT001"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    @staticmethod
    def call(function, args):
        with contextlib.redirect_stdout(io.StringIO()):
            return function(args)

    def read_record(self, record_name: str, block_name: str):
        if record_name == "runtime_state":
            return long_task.read_task_state_cache(self.task_dir)
        if record_name == "task_plan":
            return "", long_task.runtime_tasks(self.task_dir)
        if record_name == "task_attempts":
            return "", long_task.load_task_history(self.task_dir)["attempts"]
        if record_name == "stage_outcomes":
            return "", long_task.load_task_history(self.task_dir)["outcomes"]
        return long_task.read_block(self.task_dir / record_name, block_name)

    def write_record(self, record_name: str, block_name: str, value) -> None:
        if record_name == "runtime_state":
            state_path = long_task.task_state_path(self.task_dir)
            state_document, _ = long_task.read_task_state_cache(self.task_dir)
            payloads, _, _ = long_task.append_task_events_payloads(
                self.task_dir, state_path, state_document, value, [], synced_at="2026-08-15T00:00:00Z"
            )
            long_task.transactional_text_write(payloads)
            return
        if record_name == "review_issues":
            issues = value if isinstance(value, list) else []
            state_path = long_task.task_state_path(self.task_dir)
            state_document, state = long_task.read_task_state_cache(self.task_dir)
            specs = []
            for issue in issues:
                if not isinstance(issue, dict):
                    continue
                status = str(issue.get("status") or "OPEN")
                event_type = {
                    "OPEN": "ISSUE_OPENED",
                    "ASSIGNED": "ISSUE_ASSIGNED",
                    "FIXED": "ISSUE_REMEDIATED",
                    "REMEDIATED": "ISSUE_REMEDIATED",
                    "VERIFIED": "ISSUE_VERIFIED",
                    "CLOSED": "ISSUE_CLOSED",
                    "DEFERRED": "ISSUE_DEFERRED",
                    "REJECTED": "ISSUE_REJECTED",
                }.get(status, "ISSUE_OPENED")
                specs.append(long_task.task_events.issue_event_spec(
                    event_type, issue, timestamp="2026-08-15T00:00:00Z", source="test-setup"
                ))
            payloads, _, _ = long_task.append_task_events_payloads(
                self.task_dir, state_path, state_document, state, specs, synced_at="2026-08-15T00:00:00Z"
            )
            long_task.transactional_text_write(payloads)
            return
        if record_name == "task_plan":
            path = self.task_dir / "task_plan.json"
            tasks = value if isinstance(value, list) else []
            static_tasks = long_task.task_events.strip_plan_runtime_fields(tasks)
            specs = []
            for task in tasks:
                if not isinstance(task, dict):
                    continue
                runtime = {
                    key: task[key]
                    for key in long_task.task_events.TASK_PLAN_RUNTIME_FIELDS
                    if key in task
                }
                if runtime:
                    specs.append({
                        "type": "TASK_RUNTIME_SET",
                        "entity_id": str(task.get("id") or ""),
                        "timestamp": "2026-08-15T00:00:00Z",
                        "source": "test-setup",
                        "payload": {"runtime": runtime},
                    })
            state_path = long_task.task_state_path(self.task_dir)
            state_document, state = long_task.read_task_state_cache(self.task_dir)
            payloads, _, _ = long_task.append_task_events_payloads(
                self.task_dir, state_path, state_document, state, specs, synced_at="2026-08-15T00:00:00Z"
            )
            plan = long_task.storage.read_json(path, default={})
            plan["tasks"] = static_tasks
            payloads[path] = json.dumps(plan, ensure_ascii=False, indent=2) + "\n"
            long_task.transactional_text_write(payloads)
            return
        if record_name in {"task_attempts", "stage_outcomes"}:
            records = value if isinstance(value, list) else []
            specs = []
            for record in records:
                if not isinstance(record, dict):
                    continue
                if record_name == "task_attempts":
                    status = str(record.get("status") or "")
                    event_type = "ATTEMPT_STARTED" if status == "RUNNING" else (
                        "ATTEMPT_CANCELLED" if status == "CANCELLED" else "ATTEMPT_FINISHED"
                    )
                    payload = {"attempt": record}
                    entity_id = str(record.get("attempt_id") or "")
                    timestamp = str(record.get("completed_at") or record.get("started_at") or "2026-08-15T00:00:00Z")
                else:
                    status = str(record.get("status") or "")
                    if not bool(record.get("is_current", True)):
                        event_type = "STAGE_OUTCOME_SUPERSEDED"
                    elif status in {"PASSED", "SKIPPED"}:
                        event_type = "STAGE_OUTCOME_FINALIZED"
                    else:
                        event_type = "STAGE_OUTCOME_RECORDED"
                    payload = {"outcome": record}
                    entity_id = str(record.get("id") or "")
                    timestamp = str(record.get("completed_at") or record.get("started_at") or "2026-08-15T00:00:00Z")
                specs.append({
                    "type": event_type, "entity_id": entity_id, "payload": payload,
                    "timestamp": timestamp, "source": "test-setup",
                })
            state_path = long_task.task_state_path(self.task_dir)
            state_document, state = long_task.read_task_state_cache(self.task_dir)
            payloads, _, _ = long_task.append_task_events_payloads(
                self.task_dir, state_path, state_document, state, specs, synced_at="2026-08-15T00:00:00Z"
            )
            long_task.transactional_text_write(payloads)
            return
        path = self.task_dir / record_name
        document, _ = long_task.read_block(path, block_name)
        long_task.write_block(path, block_name, document, value)

    def criterion_evidence(self, criterion_id: str, revision: str, phase: str, reviewer: str) -> list[dict]:
        criterion = long_task.REVIEW_CRITERIA[criterion_id]
        policy = criterion["evidencePolicy"]
        kinds = list(policy.get("allOfTypes", []))
        any_of = list(policy.get("anyOfTypes", []))
        if any_of and not any(kind in kinds for kind in any_of):
            kinds.append(any_of[0])
        while len(kinds) < policy.get("minimumEvidenceCount", 1):
            kinds.append(any_of[0] if any_of else "document_ref")
        result = []
        for index, kind in enumerate(kinds, 1):
            path = self.task_dir / "evidence" / "reviews" / phase / f"{criterion_id}-{index}.txt"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("current revision verified", encoding="utf-8")
            entry = long_task.evidence.register_evidence(
                self.task_dir, agent=reviewer, evidence_type=kind,
                ref=str(path.relative_to(self.task_dir)), revision=revision, phase=phase,
                scope_refs=[criterion_id],
            )
            result.append({"evidence_id": entry["evidence_id"], "claim": "current revision verified"})
        return result

    def conclusion(self, phase: str, reviewer: str, revision: str) -> dict:
        profile = long_task.review_profile(phase, reviewer)
        results = []
        for criterion_id in profile["criterionIds"]:
            results.append(
                {
                    "criterion_id": criterion_id,
                    "status": "PASSED",
                    "evidence": self.criterion_evidence(criterion_id, revision, phase, reviewer),
                    "finding_ids": [],
                    "reason": "",
                }
            )
        return {
            "profile_id": f"{phase}:{reviewer}",
            "revision": revision,
            "conclusion": "PASSED",
            "checked_scope": [f"{phase}:{revision}"],
            "criteria_results": results,
            "finding_ids": [],
            "limitations": [],
            "reviewed_at": "2026-07-22T10:00:00-05:00",
        }

    def required_reviewers(self, state: dict, phase: str) -> list[str]:
        return sorted(
            long_task.expected_required_reviewers(
                phase,
                state,
                minimal=bool((state.get("work_mode") or {}).get("minimal")),
            )
        )

    def pass_phase(self, state: dict, phase: str, revision: str) -> None:
        artifact = state["artifact_revisions"][phase]
        artifact.update({"revision": revision, "status": "PASSED"})
        review = state.get("collaboration_reviews", {}).get(phase)
        if isinstance(review, dict):
            review["current_iteration_id"] = artifact["iteration_id"]
            review["artifact_revision"] = revision
            review["status"] = "PASSED"
            review["independent_conclusions"] = {
                reviewer: self.conclusion(phase, reviewer, revision)
                for reviewer in self.required_reviewers(state, phase)
            }

    def set_current_phase(self, phase: str) -> dict:
        document, state = self.read_record("runtime_state", "task-state")
        state["current_phase"] = phase
        state["current_round"] = f"{phase.upper()}-R01"
        state["next_agent"] = long_task.PHASE_DEFINITIONS[phase]["ownerAgent"]
        state["next_action"] = f"execute {phase}"
        self.write_record("runtime_state", "task-state", state)
        return state

    def plan_task(
        self,
        task_id: str,
        phase: str,
        *,
        status: str = "READY",
        depends_on: list[str] | None = None,
        validation_commands: list[str] | None = None,
        risk_triggers: list[str] | None = None,
        reviewer_agents: list[str] | None = None,
    ) -> dict:
        _, state = self.read_record("runtime_state", "task-state")
        artifact = state["artifact_revisions"][phase]
        return {
            "id": task_id,
            "logical_task_id": f"LOGICAL-{task_id}",
            "feature_id": "MOD0001-F01",
            "iteration_id": artifact["iteration_id"],
            "iteration_no": artifact["iteration_no"],
            "supersedes_iteration_id": "",
            "revision_reason": "initial iteration",
            "title": task_id,
            "objective": "observable result",
            "phase": phase,
            "status": status,
            "depends_on": depends_on or [],
            "owner_agent": long_task.PHASE_DEFINITIONS[phase]["ownerAgent"],
            "reviewer_agents": reviewer_agents or [],
            "input_revisions": {
                predecessor: state["artifact_revisions"][predecessor]["revision"]
                for predecessor in long_task.phase_predecessors(
                    phase, bool(state.get("work_mode", {}).get("minimal"))
                )
            },
            "allowed_files": [f"src/{task_id}.py"],
            "acceptance_trace_ids": [],
            "validation_commands": validation_commands or [],
            "expected_results": ["pass"],
            "stop_conditions": ["scope drift"],
            "risk_triggers": risk_triggers or [],
            "attempts": 0,
            "max_attempts": 2,
            "output_revision": "",
            "validation_evidence_ids": [],
        }

    def evidence(self, name: str, text: str = "evidence") -> str:
        path = self.task_dir / "evidence" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return str(path.relative_to(self.task_dir))

    def evidence_id(self, name: str, revision: str, phase: str, *, evidence_type: str = "runtime_ref", text: str = "evidence", agent: str | None = None, scope_refs: list[str] | None = None, command_result_ref: str = "") -> str:
        ref = self.evidence(name, text)
        entry = long_task.evidence.register_evidence(
            self.task_dir, agent=agent or long_task.PHASE_DEFINITIONS[phase]["ownerAgent"],
            evidence_type=evidence_type, ref=ref, revision=revision, phase=phase,
            scope_refs=scope_refs or [phase], command_result_ref=command_result_ref,
        )
        return entry["evidence_id"]

    def command_result(self, revision: str, *, exit_code: int = 0) -> str:
        output = self.task_dir / "evidence" / "command.log"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("command output", encoding="utf-8")
        result = self.task_dir / "evidence" / f"command-{revision.replace('@', '-')}.json"
        result.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "kind": "command_result",
                    "command": "python -m unittest",
                    "exit_code": exit_code,
                    "executed_at": "2026-07-22T10:00:00-05:00",
                    "revision": revision,
                    "output_ref": str(output.relative_to(self.task_dir)),
                    "output_digest": long_task.sha256_file(output),
                }
            ),
            encoding="utf-8",
        )
        result_ref = str(result.relative_to(self.task_dir))
        return self.evidence_id(
            f"command-evidence-{revision.replace('@', '-')}.json", revision, "development",
            evidence_type="command_ref", text=result.read_text(encoding="utf-8"),
            agent="DevelopAgent", scope_refs=["python -m unittest"], command_result_ref=result_ref,
        )

    def start(self, task_id: str, agent: str):
        return long_task.command_start_attempt(
            argparse.Namespace(
                task_dir=str(self.task_dir), task_id=task_id, input_revision="",
                summary="run", next_action="", agent=agent,
            )
        )

    def finish_pass(self, attempt_id: str, agent: str, revision: str, *, modified: str, command_ref: str | None = None):
        return long_task.command_finish_attempt(
            argparse.Namespace(
                task_dir=str(self.task_dir), attempt_id=attempt_id, status="PASSED",
                output_revision=revision, failure_type="", failure_reason="",
                modified_file=[modified], command_ref=[command_ref] if command_ref else [],
                evidence_ref=[self.evidence_id(f"{revision}.txt", revision, "development", agent=agent)], summary="done",
                next_action="", next_agent="ProjectManagerAgent", agent=agent,
            )
        )

    def enable_architecture_review_mode(self) -> dict:
        work_path = self.doc_root / "version" / "V_TEST" / "work.md"
        work_mode.apply_mode(work_path, work_mode.WorkMode(architecture_review=True))
        self.call(
            long_task.command_sync_work_mode,
            argparse.Namespace(task_dir=str(self.task_dir), agent="ProjectManagerAgent"),
        )
        _, state = self.read_record("runtime_state", "task-state")
        return state

    def test_wk_ar_initializes_skeleton_and_mode_specific_reviewers(self) -> None:
        state = self.enable_architecture_review_mode()
        self.assertTrue(state["work_mode"]["architecture_review"])
        self.assertEqual("SKELETON", state["architecture_review"]["step"])
        self.assertEqual(
            {"ArchitectureReviewAgent", "SpecComplianceReviewAgent"},
            set(self.required_reviewers(state, "development")),
        )
        self.assertEqual(
            {"SpecComplianceReviewAgent", "EngineeringStandardsReviewAgent", "ArchitectureReviewAgent"},
            set(self.required_reviewers(state, "code_review")),
        )

    def test_mode_reviewer_sync_keeps_passed_when_required_reviewers_are_subset_of_conclusions(self) -> None:
        state = self.enable_architecture_review_mode()
        review = state["collaboration_reviews"]["code_review"]
        review["status"] = "PASSED"
        review["independent_conclusions"] = {
            reviewer: {"conclusion": "PASSED"}
            for reviewer in [
                *self.required_reviewers(state, "code_review"),
                "SecurityReviewAgent",
            ]
        }
        long_task.sync_mode_review_requirements(state, minimal=False)
        self.assertEqual("PASSED", review["status"])

    def test_wk_ar_cannot_enter_concrete_implementation_before_first_review(self) -> None:
        state = self.enable_architecture_review_mode()
        state["current_phase"] = "development"
        with mock.patch.object(long_task, "validate_task", return_value=([], [], state, [])):
            with self.assertRaisesRegex(ValueError, "passed skeleton Review"):
                long_task.command_advance_development_step(
                    argparse.Namespace(task_dir=str(self.task_dir), agent="ProjectManagerAgent")
                )

    @slow
    def test_wk_ar_transition_preserves_skeleton_review_and_opens_implementation_iteration(self) -> None:
        state = self.enable_architecture_review_mode()
        state_document, state = self.read_record("runtime_state", "task-state")
        state["current_phase"] = "development"
        development = state["artifact_revisions"]["development"]
        development.update({"status": "PASSED", "revision": "DEV-SKELETON-R01@abc"})
        state["architecture_review"].update({
            "step": "SKELETON_REVIEW_PASSED",
            "skeleton_iteration_id": development["iteration_id"],
            "skeleton_revision": "DEV-SKELETON-R01@abc",
            "skeleton_review_result_refs": ["REV-AR-001", "REV-SPEC-001"],
        })
        self.write_record("runtime_state", "task-state", state)

        def fake_reopen(_args) -> int:
            document, reopened = self.read_record("runtime_state", "task-state")
            artifact = reopened["artifact_revisions"]["development"]
            artifact["iteration_no"] = int(artifact["iteration_no"]) + 1
            artifact["iteration_id"] = "ITER-FEAT001-DEVELOPMENT-002"
            artifact["status"] = "IN_PROGRESS"
            artifact["revision"] = ""
            review = reopened["collaboration_reviews"]["development"]
            review["current_iteration_id"] = artifact["iteration_id"]
            review["artifact_revision"] = ""
            review["status"] = "PENDING"
            review["independent_conclusions"] = {}
            self.write_record("runtime_state", "task-state", reopened)
            return 0

        with mock.patch.object(long_task, "validate_task", return_value=([], [], state, [])), \
             mock.patch.object(long_task, "command_reopen_phase", side_effect=fake_reopen):
            self.call(
                long_task.command_advance_development_step,
                argparse.Namespace(task_dir=str(self.task_dir), agent="ProjectManagerAgent"),
            )
        _, updated = self.read_record("runtime_state", "task-state")
        ar = updated["architecture_review"]
        self.assertEqual("IMPLEMENTATION", ar["step"])
        self.assertEqual("DEV-SKELETON-R01@abc", ar["skeleton_revision"])
        self.assertEqual(["REV-AR-001", "REV-SPEC-001"], ar["skeleton_review_result_refs"])
        self.assertEqual("ITER-FEAT001-DEVELOPMENT-002", ar["implementation_iteration_id"])
        self.assertEqual(["SpecComplianceReviewAgent", "TDDReviewAgent"], self.required_reviewers(updated, "development"))
        self.assertEqual("DevelopAgent", updated["next_agent"])

    def test_wk_ar_cannot_leave_development_after_only_skeleton_review(self) -> None:
        state = self.enable_architecture_review_mode()
        state["current_phase"] = "development"
        state["artifact_revisions"]["development"].update({
            "status": "PASSED", "revision": "DEV-SKELETON-R01@abc",
        })
        state["architecture_review"]["step"] = "SKELETON_REVIEW_PASSED"
        with mock.patch.object(long_task, "validate_task", return_value=([], [], state, [])):
            with self.assertRaisesRegex(ValueError, "cannot leave development"):
                long_task.command_advance_phase(
                    argparse.Namespace(task_dir=str(self.task_dir), agent="ProjectManagerAgent")
                )

    @slow
    def test_wk_ar_finalize_transition_distinguishes_development_gate_and_second_review(self) -> None:
        state = self.enable_architecture_review_mode()
        state["architecture_review"]["step"] = "IMPLEMENTATION"
        development_review = {
            "required_reviewers": ["TDDReviewAgent"],
            "independent_conclusions": {},
        }
        transition, refs = long_task.resolve_architecture_finalize_transition(
            "development", state, development_review, minimal=False
        )
        self.assertEqual("IMPLEMENTATION_READY_FOR_REVIEW", transition)
        self.assertEqual([], refs)

        state["architecture_review"]["step"] = "IMPLEMENTATION_READY_FOR_REVIEW"
        final_review = {
            "required_reviewers": [
                "SpecComplianceReviewAgent",
                "EngineeringStandardsReviewAgent",
                "ArchitectureReviewAgent",
            ],
            "independent_conclusions": {
                reviewer: {"conclusion": "PASSED", "review_result_ref": f"REV-{index}"}
                for index, reviewer in enumerate([
                    "SpecComplianceReviewAgent",
                    "EngineeringStandardsReviewAgent",
                    "ArchitectureReviewAgent",
                ], 1)
            },
        }
        transition, refs = long_task.resolve_architecture_finalize_transition(
            "code_review", state, final_review, minimal=False
        )
        self.assertEqual("IMPLEMENTATION_REVIEW_PASSED", transition)
        self.assertEqual(["REV-1", "REV-2", "REV-3"], refs)

    def test_wk_ar_final_review_state_requires_all_final_review_refs(self) -> None:
        state = self.enable_architecture_review_mode()
        development = state["artifact_revisions"]["development"]
        development.update({"status": "PASSED", "revision": "DEV-IMPL-R02@abc"})
        code_review = state["artifact_revisions"]["code_review"]
        code_review.update({"status": "PASSED", "revision": "CR-R01@def"})
        state["architecture_review"].update({
            "step": "IMPLEMENTATION_REVIEW_PASSED",
            "skeleton_iteration_id": development["iteration_id"],
            "skeleton_revision": "DEV-SKELETON-R01@aaa",
            "skeleton_review_result_refs": ["REV-SK-ARCH", "REV-SK-SPEC"],
            "implementation_iteration_id": development["iteration_id"],
            "implementation_revision": "DEV-IMPL-R02@abc",
            "final_review_revision": "CR-R01@def",
            "final_review_result_refs": ["REV-FINAL-SPEC", "REV-FINAL-ENG", "REV-FINAL-ARCH"],
        })
        records = {
            "REV-SK-ARCH": {"review_phase": "development", "review_revision": "DEV-SKELETON-R01@aaa", "result": "PASSED", "reviewer": "ArchitectureReviewAgent"},
            "REV-SK-SPEC": {"review_phase": "development", "review_revision": "DEV-SKELETON-R01@aaa", "result": "PASSED", "reviewer": "SpecComplianceReviewAgent"},
            "REV-FINAL-SPEC": {"review_phase": "code_review", "review_revision": "CR-R01@def", "result": "PASSED", "reviewer": "SpecComplianceReviewAgent"},
            "REV-FINAL-ENG": {"review_phase": "code_review", "review_revision": "CR-R01@def", "result": "PASSED", "reviewer": "EngineeringStandardsReviewAgent"},
            "REV-FINAL-ARCH": {"review_phase": "code_review", "review_revision": "CR-R01@def", "result": "PASSED", "reviewer": "ArchitectureReviewAgent"},
        }
        errors = []
        with mock.patch.object(long_task.review_store, "require_record", side_effect=lambda _task, ref: records[ref]):
            long_task.validate_architecture_review_state(
                self.task_dir, state, state["artifact_revisions"],
                {"architecture_review": True}, errors,
            )
        self.assertEqual([], errors)
        state["architecture_review"]["final_review_result_refs"].pop()
        errors = []
        with mock.patch.object(long_task.review_store, "require_record", side_effect=lambda _task, ref: records[ref]):
            long_task.validate_architecture_review_state(
                self.task_dir, state, state["artifact_revisions"],
                {"architecture_review": True}, errors,
            )
        self.assertTrue(any("missing passed final Reviewers" in item for item in errors), errors)

    def test_wk_ar_reopen_code_review_rolls_back_only_final_review_state(self) -> None:
        state = self.enable_architecture_review_mode()
        document, state = self.read_record("runtime_state", "task-state")
        development = state["artifact_revisions"]["development"]
        development.update({"status": "PASSED", "revision": "DEV-IMPL-R02@abc"})
        code_review = state["artifact_revisions"]["code_review"]
        code_review.update({"status": "PASSED", "revision": "CR-R01@def"})
        state["architecture_review"].update({
            "step": "IMPLEMENTATION_REVIEW_PASSED",
            "skeleton_iteration_id": development["iteration_id"],
            "skeleton_revision": "DEV-SKELETON-R01@aaa",
            "skeleton_review_result_refs": ["REV-SK-ARCH", "REV-SK-SPEC"],
            "implementation_iteration_id": development["iteration_id"],
            "implementation_revision": "DEV-IMPL-R02@abc",
            "final_review_revision": "CR-R01@def",
            "final_review_result_refs": ["REV-FINAL-SPEC", "REV-FINAL-ENG", "REV-FINAL-ARCH"],
        })
        self.write_record("runtime_state", "task-state", state)

        self.call(
            long_task.command_reopen_phase,
            argparse.Namespace(
                task_dir=str(self.task_dir), from_phase="code_review",
                source_revision="CR-R01@def", reason="recheck final code review",
                round="", agent="ProjectManagerAgent",
            ),
        )

        _, updated = self.read_record("runtime_state", "task-state")
        architecture = updated["architecture_review"]
        self.assertEqual("IMPLEMENTATION_READY_FOR_REVIEW", architecture["step"])
        self.assertEqual("DEV-SKELETON-R01@aaa", architecture["skeleton_revision"])
        self.assertEqual(
            ["REV-SK-ARCH", "REV-SK-SPEC"],
            architecture["skeleton_review_result_refs"],
        )
        self.assertEqual("DEV-IMPL-R02@abc", architecture["implementation_revision"])
        self.assertEqual("", architecture["final_review_revision"])
        self.assertEqual([], architecture["final_review_result_refs"])

    @slow
    def test_reopen_and_advance_rebind_downstream_task_inputs_to_current_predecessor(self) -> None:
        document, state = self.read_record("runtime_state", "task-state")
        development = state["artifact_revisions"]["development"]
        development.update({"status": "PASSED", "revision": "DEV-R01@abc"})
        code_review = state["artifact_revisions"]["code_review"]
        code_review.update({"status": "PASSED", "revision": "CR-OLD@def"})
        testing = state["artifact_revisions"]["testing"]
        testing.update({"status": "PASSED", "revision": "TEST-OLD@ghi"})
        completion = state["artifact_revisions"]["completion_verification"]
        completion.update({"status": "PASSED", "revision": "COMP-OLD@jkl"})
        state["current_phase"] = "completion_verification"
        self.write_record("runtime_state", "task-state", state)

        code_review_task = self.plan_task("CR-1", "code_review", status="PASSED")
        code_review_task["input_revisions"] = {"development": "DEV-R01@abc"}
        code_review_task["output_revision"] = "CR-OLD@def"
        code_review_task["validation_evidence_ids"] = ["OLD-CR-EVIDENCE"]
        testing_task = self.plan_task("TEST-1", "testing", status="PASSED")
        testing_task["input_revisions"] = {
            "tdd": "TDD-PRIOR@history",
            "development": "DEV-PRIOR@history",
            "code_review": "CR-OLD@def",
        }
        testing_task["output_revision"] = "TEST-OLD@ghi"
        testing_task["validation_evidence_ids"] = ["OLD-TEST-EVIDENCE"]
        completion_task = self.plan_task("COMP-1", "completion_verification", status="PASSED")
        completion_task["input_revisions"] = {
            "development": "DEV-PRIOR@history",
            "code_review": "CR-OLD@def",
            "testing": "TEST-OLD@ghi",
        }
        completion_task["output_revision"] = "COMP-OLD@jkl"
        completion_task["validation_evidence_ids"] = ["OLD-COMP-EVIDENCE"]
        self.write_record("task_plan", "task-plan", [code_review_task, testing_task, completion_task])

        self.call(
            wk.command_confirm,
            argparse.Namespace(
                task_dir=str(self.task_dir), until='', reopen_from='code_review',
                changed_phase='completion_verification', source_revision='CR-OLD@def',
                reason='new final review revision', agent='ProjectManagerAgent',
                preview=False, apply=True, json=True,
            ),
        )
        confirmation_ref = task_events.read_events(self.task_dir)[-1]['payload']['requestFingerprint']

        self.call(
            long_task.command_reopen_phase,
            argparse.Namespace(
                task_dir=str(self.task_dir), from_phase="code_review",
                source_revision="CR-OLD@def", reason="new final review revision",
                changed_phase="completion_verification", user_confirmed=True,
                confirmation_ref=confirmation_ref,
                round="", agent="ProjectManagerAgent",
            ),
        )
        _, reopened_tasks = self.read_record("task_plan", "task-plan")
        reopened_by_phase = {item["phase"]: item for item in reopened_tasks}
        self.assertEqual(
            {"development": "DEV-R01@abc"},
            reopened_by_phase["code_review"]["input_revisions"],
        )
        self.assertEqual({"code_review": ""}, reopened_by_phase["testing"]["input_revisions"])
        self.assertEqual(
            {"testing": ""},
            reopened_by_phase["completion_verification"]["input_revisions"],
        )

        state_document, reopened_state = self.read_record("runtime_state", "task-state")
        reopened_state["artifact_revisions"]["code_review"].update({
            "status": "PASSED", "revision": "CR-NEW@mno",
        })
        self.write_record("runtime_state", "task-state", reopened_state)
        _, current_tasks = self.read_record("task_plan", "task-plan")
        for task in current_tasks:
            if task["phase"] == "code_review":
                task.update({
                    "status": "PASSED",
                    "output_revision": "CR-NEW@mno",
                    "validation_evidence_ids": ["NEW-CR-EVIDENCE"],
                })
        self.write_record("task_plan", "task-plan", current_tasks)
        code_review_artifact = reopened_state["artifact_revisions"]["code_review"]
        self.write_record(
            "stage_outcomes",
            "stage-outcomes",
            [{
                "id": "SO-CR-NEW", "phase": "code_review", "round": "CODE_REVIEW-I002",
                "iteration_id": code_review_artifact["iteration_id"],
                "iteration_no": code_review_artifact["iteration_no"],
                "is_current": True, "superseded_by": "", "status": "PASSED",
                "produced_by_agent": "ProjectManagerAgent",
                "input_revisions": {"development": "DEV-R01@abc"},
                "output_revision": "CR-NEW@mno", "evidence_ids": ["NEW-CR-EVIDENCE"],
                "open_risks": [], "started_at": "2026-08-07T08:00:00+00:00",
                "completed_at": "2026-08-07T08:05:00+00:00",
            }],
        )
        with mock.patch.object(
            long_task, "validate_task", return_value=([], [], reopened_state, [])
        ):
            self.call(
                long_task.command_advance_phase,
                argparse.Namespace(task_dir=str(self.task_dir), agent="ProjectManagerAgent"),
            )
        _, advanced_tasks = self.read_record("task_plan", "task-plan")
        testing_task = next(item for item in advanced_tasks if item["phase"] == "testing")
        self.assertEqual({"code_review": "CR-NEW@mno"}, testing_task["input_revisions"])

        testing_state_document, testing_state = self.read_record("runtime_state", "task-state")
        testing_state["artifact_revisions"]["testing"].update({
            "status": "PASSED", "revision": "TEST-NEW@pqr",
        })
        self.write_record("runtime_state", "task-state", testing_state)
        _, testing_tasks = self.read_record("task_plan", "task-plan")
        for task in testing_tasks:
            if task["phase"] == "testing":
                task.update({
                    "status": "PASSED",
                    "output_revision": "TEST-NEW@pqr",
                    "validation_evidence_ids": ["NEW-TEST-EVIDENCE"],
                })
        self.write_record("task_plan", "task-plan", testing_tasks)
        _, outcomes = self.read_record("stage_outcomes", "stage-outcomes")
        testing_artifact = testing_state["artifact_revisions"]["testing"]
        outcomes.append({
            "id": "SO-TEST-NEW", "phase": "testing", "round": "TESTING-I002",
            "iteration_id": testing_artifact["iteration_id"],
            "iteration_no": testing_artifact["iteration_no"],
            "is_current": True, "superseded_by": "", "status": "PASSED",
            "produced_by_agent": long_task.PHASE_DEFINITIONS["testing"]["ownerAgent"],
            "input_revisions": {"code_review": "CR-NEW@mno"},
            "output_revision": "TEST-NEW@pqr", "evidence_ids": ["NEW-TEST-EVIDENCE"],
            "open_risks": [], "started_at": "2026-08-07T08:10:00+00:00",
            "completed_at": "2026-08-07T08:15:00+00:00",
        })
        self.write_record("stage_outcomes", "stage-outcomes", outcomes)
        with mock.patch.object(
            long_task, "validate_task", return_value=([], [], testing_state, [])
        ):
            self.call(
                long_task.command_advance_phase,
                argparse.Namespace(task_dir=str(self.task_dir), agent="ProjectManagerAgent"),
            )
        _, completion_tasks = self.read_record("task_plan", "task-plan")
        completion_task = next(
            item for item in completion_tasks if item["phase"] == "completion_verification"
        )
        self.assertEqual({"testing": "TEST-NEW@pqr"}, completion_task["input_revisions"])

    def test_development_cannot_start_without_tdd_and_plan(self) -> None:
        self.set_current_phase("development")
        self.write_record("task_plan", "task-plan", [self.plan_task("DEV-1", "development")])
        with self.assertRaisesRegex(ValueError, "predecessor implementation_plan is PENDING"):
            self.start("DEV-1", "DevelopAgent")

    def test_dependency_must_be_completed_before_start(self) -> None:
        document, state = self.read_record("runtime_state", "task-state")
        self.pass_phase(state, "implementation_plan", "PLAN-R01")
        self.pass_phase(state, "test_design", "TESTDESIGN-R01")
        state["current_phase"] = "development"
        state["next_agent"] = "DevelopAgent"
        self.write_record("runtime_state", "task-state", state)
        first = self.plan_task("DEV-A", "development", status="PENDING")
        second = self.plan_task("DEV-B", "development", depends_on=["DEV-A"])
        self.write_record("task_plan", "task-plan", [first, second])
        with self.assertRaisesRegex(ValueError, "task dependencies are not complete"):
            self.start("DEV-B", "DevelopAgent")

    def test_open_p1_blocks_downstream_execution(self) -> None:
        document, state = self.read_record("runtime_state", "task-state")
        self.pass_phase(state, "implementation_plan", "PLAN-R01")
        self.pass_phase(state, "test_design", "TESTDESIGN-R01")
        state["current_phase"] = "development"
        state["next_agent"] = "DevelopAgent"
        state["open_issue_ids"] = ["ISSUE-P1"]
        self.write_record("runtime_state", "task-state", state)
        issue = {
            "id": "ISSUE-P1", "issue_type": "RISK", "axis": "ENGINEERING", "severity": "P1",
            "confidence": 9, "status": "OPEN", "phase": "development", "round": "DEV-R01",
            "artifact_revision": "DEV-R00", "raised_by_agent": "EngineeringStandardsReviewAgent",
            "owner_agent": "DevelopAgent", "title": "blocking risk", "description": "risk",
            "impact": "unsafe", "motivating_evidence": ["logical:risk"], "question": "resolve?",
            "question_to": ["DevelopAgent"], "responses": [], "recommendation": "fix",
            "affected_artifacts": ["development"], "affected_trace_ids": [], "decision": "",
            "resolution_revision": "", "resolution_evidence": "", "verified_by_agent": "",
            "verified_at": "", "defer_reason": "",
        }
        self.write_record("review_issues", "review-issues", [issue])
        self.write_record("task_plan", "task-plan", [self.plan_task("DEV-1", "development")])
        with self.assertRaisesRegex(ValueError, "open P0/P1"):
            self.start("DEV-1", "DevelopAgent")

    def test_sync_risk_reviewers_populates_code_review_task_and_state(self) -> None:
        state_document, state = self.read_record("runtime_state", "task-state")
        state["artifact_revisions"]["development"].update({"revision": "DEV-R01", "status": "PASSED"})
        self.write_record("runtime_state", "task-state", state)
        task = self.plan_task("CR-1", "code_review")
        self.write_record("task_plan", "task-plan", [task])
        source = self.task_dir / "src" / "auth" / "PermissionService.py"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("authorize permission token", encoding="utf-8")
        long_task.risk_detect.scan(
            self.task_dir, agent="ProjectManagerAgent", revision="DEV-R01",
            refs=[str(source.relative_to(self.task_dir))],
        )
        self.call(
            long_task.command_sync_risk_reviewers,
            argparse.Namespace(task_dir=str(self.task_dir), agent="ProjectManagerAgent"),
        )
        _, updated_state = self.read_record("runtime_state", "task-state")
        _, updated_tasks = self.read_record("task_plan", "task-plan")
        trigger = updated_state["collaboration_reviews"]["code_review"]["additional_reviewers"]["SecurityReviewAgent"]
        self.assertEqual("security", trigger["trigger"])
        self.assertTrue(trigger["evidence_ids"])
        self.assertIn("security", updated_tasks[0]["risk_triggers"])
        self.assertIn("SecurityReviewAgent", updated_tasks[0]["reviewer_agents"])

    @slow
    def test_sync_risk_reviewers_registers_required_architecture_risk_without_double_execution(self) -> None:
        state = self.enable_architecture_review_mode()
        state["artifact_revisions"]["development"].update({"revision": "DEV-R01", "status": "PASSED"})
        self.write_record("runtime_state", "task-state", state)
        required = self.required_reviewers(state, "code_review")
        task = self.plan_task("CR-AR-1", "code_review", reviewer_agents=required)
        self.write_record("task_plan", "task-plan", [task])
        source = self.task_dir / "src" / "architecture" / "ModuleBoundary.py"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("module dependency adapter port", encoding="utf-8")
        long_task.risk_detect.scan(
            self.task_dir, agent="ProjectManagerAgent", revision="DEV-R01",
            refs=[str(source.relative_to(self.task_dir))],
        )

        self.call(
            long_task.command_sync_risk_reviewers,
            argparse.Namespace(task_dir=str(self.task_dir), agent="ProjectManagerAgent"),
        )

        _, updated_state = self.read_record("runtime_state", "task-state")
        _, updated_tasks = self.read_record("task_plan", "task-plan")
        additional = updated_state["collaboration_reviews"]["code_review"]["additional_reviewers"]
        self.assertIn("architecture_change", updated_tasks[0]["risk_triggers"])
        self.assertIn("ArchitectureReviewAgent", additional)
        self.assertEqual(
            "architecture_change",
            additional["ArchitectureReviewAgent"]["trigger"],
        )
        self.assertTrue(additional["ArchitectureReviewAgent"]["evidence_ids"])
        self.assertIn("ArchitectureReviewAgent", updated_tasks[0]["reviewer_agents"])
        self.assertEqual(1, updated_tasks[0]["reviewer_agents"].count("ArchitectureReviewAgent"))
        self.assertEqual(
            set(required),
            long_task.expected_required_reviewers("code_review", updated_state, minimal=False),
        )
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertFalse(
            any("ArchitectureReviewAgent duplicates a required or conditional reviewer" in error for error in errors),
            errors,
        )

        updated_state["artifact_revisions"]["code_review"].update({
            "revision": "CODE-REVIEW-R01",
            "status": "PASSED",
        })
        code_review = updated_state["collaboration_reviews"]["code_review"]
        code_review["artifact_revision"] = "CODE-REVIEW-R01"
        code_review.setdefault("independent_conclusions", {})["ArchitectureReviewAgent"] = {
            "conclusion": "PASSED",
        }
        _, report = long_task.risk_detect.validate_report(self.task_dir)
        final_gate_errors: list[str] = []
        long_task.validate_risk_gate(
            report,
            updated_state["artifact_revisions"],
            updated_state["collaboration_reviews"],
            updated_tasks,
            final_gate_errors,
        )
        self.assertEqual([], final_gate_errors)

        additional["ArchitectureReviewAgent"]["evidence_ids"] = ["EVD-NOT-CURRENT"]
        self.write_record("runtime_state", "task-state", updated_state)
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertTrue(
            any("ArchitectureReviewAgent duplicates a required or conditional reviewer" in error for error in errors),
            errors,
        )

    @slow
    def test_sync_risk_reviewers_preserves_required_architecture_reviewer_when_other_risk_is_triggered(self) -> None:
        state = self.enable_architecture_review_mode()
        state["artifact_revisions"]["development"].update({"revision": "DEV-R01", "status": "PASSED"})
        self.write_record("runtime_state", "task-state", state)
        required = self.required_reviewers(state, "code_review")
        task = self.plan_task("CR-AR-2", "code_review", reviewer_agents=required)
        self.write_record("task_plan", "task-plan", [task])
        source = self.task_dir / "src" / "auth" / "PermissionService.py"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("authorize permission token", encoding="utf-8")
        long_task.risk_detect.scan(
            self.task_dir, agent="ProjectManagerAgent", revision="DEV-R01",
            refs=[str(source.relative_to(self.task_dir))],
        )

        self.call(
            long_task.command_sync_risk_reviewers,
            argparse.Namespace(task_dir=str(self.task_dir), agent="ProjectManagerAgent"),
        )

        _, updated_state = self.read_record("runtime_state", "task-state")
        _, updated_tasks = self.read_record("task_plan", "task-plan")
        additional = updated_state["collaboration_reviews"]["code_review"]["additional_reviewers"]
        self.assertIn("SecurityReviewAgent", additional)
        self.assertIn("ArchitectureReviewAgent", updated_tasks[0]["reviewer_agents"])
        self.assertTrue(set(required).issubset(set(updated_tasks[0]["reviewer_agents"])))

    def test_stale_input_revision_blocks_start(self) -> None:
        document, state = self.read_record("runtime_state", "task-state")
        self.pass_phase(state, "implementation_plan", "PLAN-R02")
        self.pass_phase(state, "test_design", "TESTDESIGN-R02")
        state["current_phase"] = "development"
        state["next_agent"] = "DevelopAgent"
        self.write_record("runtime_state", "task-state", state)
        task = self.plan_task("DEV-1", "development")
        task["input_revisions"]["implementation_plan"] = "PLAN-R01"
        self.write_record("task_plan", "task-plan", [task])
        with self.assertRaisesRegex(ValueError, "input revision is stale"):
            self.start("DEV-1", "DevelopAgent")

    def test_standard_mode_rejects_not_applicable_tdd(self) -> None:
        document, state = self.read_record("runtime_state", "task-state")
        state["artifact_revisions"]["test_design"]["status"] = "NOT_APPLICABLE"
        self.write_record("runtime_state", "task-state", state)
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertIn(
            "artifact_revisions.test_design cannot be NOT_APPLICABLE without an active mode or standard user skip policy",
            errors,
        )

    def test_minimal_mode_skips_test_phases_and_blocks_start(self) -> None:
        work_path = self.doc_root / "version" / "V_TEST" / "work.md"
        long_task.work_mode.apply_mode(work_path, long_task.work_mode.WorkMode(minimal=True))
        self.call(
            long_task.command_sync_work_mode,
            argparse.Namespace(task_dir=str(self.task_dir), agent="ProjectManagerAgent"),
        )
        _, state = self.read_record("runtime_state", "task-state")
        self.assertEqual("NOT_APPLICABLE", state["artifact_revisions"]["test_design"]["status"])
        state_document, state = self.read_record("runtime_state", "task-state")
        state["current_phase"] = "test_design"
        state["next_agent"] = "TestDesignAgent"
        self.write_record("runtime_state", "task-state", state)
        self.write_record("task_plan", "task-plan", [self.plan_task("TD-1", "test_design")])
        with self.assertRaisesRegex(ValueError, "skipped by wk -min|cannot be active"):
            self.start("TD-1", "TestDesignAgent")

    @slow
    def test_finish_passed_requires_real_evidence_file(self) -> None:
        document, state = self.read_record("runtime_state", "task-state")
        self.pass_phase(state, "implementation_plan", "PLAN-R01")
        self.pass_phase(state, "test_design", "TESTDESIGN-R01")
        state["current_phase"] = "development"
        state["next_agent"] = "DevelopAgent"
        self.write_record("runtime_state", "task-state", state)
        self.write_record("task_plan", "task-plan", [self.plan_task("DEV-1", "development")])
        with contextlib.redirect_stdout(io.StringIO()):
            self.start("DEV-1", "DevelopAgent")
        _, attempts = self.read_record("task_attempts", "task-attempts")
        attempt_id = attempts[-1]["attempt_id"]
        with self.assertRaisesRegex(ValueError, "unknown evidence_id"):
            long_task.command_finish_attempt(
                argparse.Namespace(
                    task_dir=str(self.task_dir), attempt_id=attempt_id, status="PASSED",
                    output_revision="DEV-R01", failure_type="", failure_reason="",
                    modified_file=["src/DEV-1.py"], command_ref=[], evidence_ref=["missing.log"],
                    summary="", next_action="", next_agent="ProjectManagerAgent", agent="DevelopAgent",
                )
            )

    def test_finish_passed_rejects_files_outside_allowed_scope(self) -> None:
        document, state = self.read_record("runtime_state", "task-state")
        self.pass_phase(state, "implementation_plan", "PLAN-R01")
        self.pass_phase(state, "test_design", "TESTDESIGN-R01")
        state["current_phase"] = "development"
        state["next_agent"] = "DevelopAgent"
        self.write_record("runtime_state", "task-state", state)
        self.write_record("task_plan", "task-plan", [self.plan_task("DEV-1", "development")])
        with contextlib.redirect_stdout(io.StringIO()):
            self.start("DEV-1", "DevelopAgent")
        _, attempts = self.read_record("task_attempts", "task-attempts")
        with self.assertRaisesRegex(ValueError, "outside allowed_files"):
            self.finish_pass(attempts[-1]["attempt_id"], "DevelopAgent", "DEV-R01", modified="src/other.py")

    @slow
    def test_validation_command_must_pass_and_match_revision(self) -> None:
        document, state = self.read_record("runtime_state", "task-state")
        self.pass_phase(state, "implementation_plan", "PLAN-R01")
        self.pass_phase(state, "test_design", "TESTDESIGN-R01")
        state["current_phase"] = "development"
        state["next_agent"] = "DevelopAgent"
        self.write_record("runtime_state", "task-state", state)
        task = self.plan_task("DEV-1", "development", validation_commands=["python -m unittest"])
        self.write_record("task_plan", "task-plan", [task])
        with contextlib.redirect_stdout(io.StringIO()):
            self.start("DEV-1", "DevelopAgent")
        _, attempts = self.read_record("task_attempts", "task-attempts")
        with self.assertRaisesRegex(ValueError, "validation command failed"):
            self.finish_pass(
                attempts[-1]["attempt_id"], "DevelopAgent", "DEV-R01",
                modified="src/DEV-1.py", command_ref=self.command_result("DEV-R01", exit_code=1),
            )

    def test_code_review_risk_trigger_requires_specialist(self) -> None:
        document, state = self.read_record("runtime_state", "task-state")
        self.pass_phase(state, "development", "DEV-R01")
        source = self.task_dir / "src" / "auth" / "PermissionService.py"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("authorize permission token", encoding="utf-8")
        long_task.risk_detect.scan(
            self.task_dir, agent="ProjectManagerAgent", revision="DEV-R01",
            refs=[str(source.relative_to(self.task_dir))],
        )
        state["current_phase"] = "code_review"
        state["next_agent"] = "ProjectManagerAgent"
        self.write_record("runtime_state", "task-state", state)
        task = self.plan_task("REVIEW-1", "code_review", risk_triggers=["security"], reviewer_agents=[])
        self.write_record("task_plan", "task-plan", [task])
        with self.assertRaisesRegex(ValueError, "missing triggered risk reviewers"):
            self.start("REVIEW-1", "ProjectManagerAgent")

    def test_advance_phase_requires_current_stage_outcome(self) -> None:
        document, state = self.read_record("runtime_state", "task-state")
        self.pass_phase(state, "requirement_confirmation", "REQCONF-R01")
        self.write_record("runtime_state", "task-state", state)
        with self.assertRaisesRegex(ValueError, "no current PASSED StageOutcome"):
            long_task.command_advance_phase(
                argparse.Namespace(task_dir=str(self.task_dir), agent="ProjectManagerAgent")
            )

    def test_advance_phase_succeeds_with_current_outcome(self) -> None:
        document, state = self.read_record("runtime_state", "task-state")
        self.pass_phase(state, "requirement_confirmation", "REQCONF-R01")
        self.write_record("runtime_state", "task-state", state)
        artifact = state["artifact_revisions"]["requirement_confirmation"]
        evidence = self.evidence("reqconf.log")
        outcome = {
            "id": "SO-REQCONF-1", "phase": "requirement_confirmation", "round": "REQCONF-R01",
            "iteration_id": artifact["iteration_id"], "iteration_no": artifact["iteration_no"],
            "is_current": True, "superseded_by": "", "status": "PASSED",
            "produced_by_agent": "RequirementConfirmationAgent", "input_revisions": {},
            "output_revision": "REQCONF-R01", "evidence_ids": [self.evidence_id("reqconf.log", "REQCONF-R01", "requirement_confirmation", agent="RequirementConfirmationAgent")], "open_risks": [],
            "started_at": "2026-07-22T10:00:00-05:00", "completed_at": "2026-07-22T10:05:00-05:00",
        }
        self.write_record("stage_outcomes", "stage-outcomes", [outcome])
        self.call(
            long_task.command_advance_phase,
            argparse.Namespace(task_dir=str(self.task_dir), agent="ProjectManagerAgent"),
        )
        _, updated = self.read_record("runtime_state", "task-state")
        self.assertEqual("requirement_analysis", updated["current_phase"])

    def enable_git_checkpoint_mode(self) -> None:
        work_file = self.doc_root / "version" / "V_TEST" / "work.md"
        work_mode.apply_mode(work_file, work_mode.WorkMode(git_checkpoint=True))
        self.call(
            long_task.command_sync_work_mode,
            argparse.Namespace(task_dir=str(self.task_dir), agent="ProjectManagerAgent"),
        )

    def enable_git_push_mode(self) -> None:
        work_file = self.doc_root / "version" / "V_TEST" / "work.md"
        work_mode.apply_mode(work_file, work_mode.WorkMode(git_checkpoint=True, git_push=True))
        self.call(
            long_task.command_sync_work_mode,
            argparse.Namespace(task_dir=str(self.task_dir), agent="ProjectManagerAgent"),
        )

    def prepare_requirement_confirmation_outcome(self) -> None:
        document, state = self.read_record("runtime_state", "task-state")
        self.pass_phase(state, "requirement_confirmation", "REQCONF-R01")
        self.write_record("runtime_state", "task-state", state)
        artifact = state["artifact_revisions"]["requirement_confirmation"]
        self.evidence("reqconf-git.log")
        outcome = {
            "id": "SO-REQCONF-GIT-1", "phase": "requirement_confirmation", "round": "REQCONF-R01",
            "iteration_id": artifact["iteration_id"], "iteration_no": artifact["iteration_no"],
            "is_current": True, "superseded_by": "", "status": "PASSED",
            "produced_by_agent": "RequirementConfirmationAgent", "input_revisions": {},
            "output_revision": "REQCONF-R01",
            "evidence_ids": [self.evidence_id("reqconf-git.log", "REQCONF-R01", "requirement_confirmation", agent="RequirementConfirmationAgent")],
            "open_risks": [], "started_at": "2026-07-22T10:00:00-05:00",
            "completed_at": "2026-07-22T10:05:00-05:00",
        }
        self.write_record("stage_outcomes", "stage-outcomes", [outcome])

    @slow
    def test_advance_phase_creates_git_checkpoint_before_finalizing_transition(self) -> None:
        self.enable_git_checkpoint_mode()
        self.prepare_requirement_confirmation_outcome()
        with mock.patch("git_checkpoint.commit_checkpoint", return_value={"status": "COMMITTED", "commitSha": "abc"}) as checkpoint:
            self.call(
                long_task.command_advance_phase,
                argparse.Namespace(task_dir=str(self.task_dir), agent="ProjectManagerAgent"),
            )
        checkpoint.assert_called_once_with(
            self.task_dir, agent="ProjectManagerAgent", phase="requirement_confirmation"
        )
        _, updated = self.read_record("runtime_state", "task-state")
        self.assertEqual("requirement_analysis", updated["current_phase"])

    @slow
    def test_advance_phase_with_git_push_verifies_remote_before_phase_transition(self) -> None:
        self.enable_git_push_mode()
        self.prepare_requirement_confirmation_outcome()
        seen_phase = []

        def verify_before_advance(*args, **kwargs):
            seen_phase.append(long_task.runtime_state(self.task_dir)["current_phase"])
            return {"status": "PUSHED", "remoteVerified": True, "remoteHeadSha": "abc"}

        with mock.patch("git_checkpoint.commit_checkpoint", return_value={
            "status": "COMMITTED", "commitSha": "abc", "checkpointId": "CP-1"
        }) as checkpoint, mock.patch("git_checkpoint.push_checkpoint", side_effect=verify_before_advance) as push:
            self.call(
                long_task.command_advance_phase,
                argparse.Namespace(task_dir=str(self.task_dir), agent="ProjectManagerAgent"),
            )
        checkpoint.assert_called_once_with(
            self.task_dir, agent="ProjectManagerAgent", phase="requirement_confirmation"
        )
        push.assert_called_once_with(self.task_dir, agent="ProjectManagerAgent", checkpoint_id="CP-1")
        self.assertEqual(["requirement_confirmation"], seen_phase)
        self.assertEqual("requirement_analysis", long_task.runtime_state(self.task_dir)["current_phase"])

    def test_advance_phase_with_git_connector_handoff_does_not_advance_phase(self) -> None:
        self.enable_git_push_mode()
        self.prepare_requirement_confirmation_outcome()
        before = (self.task_dir / "task_events.jsonl").read_bytes()
        connector_result = {
            "status": "CONNECTOR_REQUIRED",
            "remoteVerified": False,
            "connectorRequestPath": "/tmp/request.json",
            "checkpointId": "CP-1",
        }
        with mock.patch("git_checkpoint.commit_checkpoint", return_value={
            "status": "COMMITTED", "commitSha": "abc", "checkpointId": "CP-1"
        }), mock.patch("git_checkpoint.push_checkpoint", return_value=connector_result):
            with self.assertRaisesRegex(ValueError, "remote_connector_required"):
                long_task.command_advance_phase(
                    argparse.Namespace(task_dir=str(self.task_dir), agent="ProjectManagerAgent")
                )
        self.assertEqual(before, (self.task_dir / "task_events.jsonl").read_bytes())
        self.assertEqual("requirement_confirmation", long_task.runtime_state(self.task_dir)["current_phase"])

    @slow
    def test_advance_phase_with_git_push_failure_does_not_advance_phase(self) -> None:
        self.enable_git_push_mode()
        self.prepare_requirement_confirmation_outcome()
        before = (self.task_dir / "task_events.jsonl").read_bytes()
        with mock.patch("git_checkpoint.commit_checkpoint", return_value={
            "status": "COMMITTED", "commitSha": "abc", "checkpointId": "CP-1"
        }), mock.patch("git_checkpoint.push_checkpoint", side_effect=ValueError("remote unavailable")):
            with self.assertRaisesRegex(ValueError, "remote Git checkpoint synchronization failed"):
                long_task.command_advance_phase(
                    argparse.Namespace(task_dir=str(self.task_dir), agent="ProjectManagerAgent")
                )
        self.assertEqual(before, (self.task_dir / "task_events.jsonl").read_bytes())
        self.assertEqual("requirement_confirmation", long_task.runtime_state(self.task_dir)["current_phase"])

    @slow
    def test_advance_phase_restores_task_state_when_git_checkpoint_fails(self) -> None:
        self.enable_git_checkpoint_mode()
        self.prepare_requirement_confirmation_outcome()
        analysis_task = self.plan_task("REQ-AN-1", "requirement_analysis")
        analysis_task["input_revisions"] = {"requirement_confirmation": "REQ-STALE@old"}
        self.write_record("task_plan", "task-plan", [analysis_task])
        before = long_task.task_state_path(self.task_dir).read_bytes()
        plan_before = (self.task_dir / "task_plan.json").read_bytes()
        with mock.patch("git_checkpoint.commit_checkpoint", side_effect=ValueError("commit rejected")):
            with self.assertRaisesRegex(ValueError, "Git checkpoint failed"):
                long_task.command_advance_phase(
                    argparse.Namespace(task_dir=str(self.task_dir), agent="ProjectManagerAgent")
                )
        self.assertEqual(before, long_task.task_state_path(self.task_dir).read_bytes())
        self.assertEqual(plan_before, (self.task_dir / "task_plan.json").read_bytes())
        _, updated = self.read_record("runtime_state", "task-state")
        self.assertEqual("requirement_confirmation", updated["current_phase"])

    @slow
    def test_reopen_phase_creates_new_iteration_and_preserves_attempt_history(self) -> None:
        document, state = self.read_record("runtime_state", "task-state")
        self.pass_phase(state, "business_model", "BM-R01")
        state["current_phase"] = "design"
        state["next_agent"] = "DesignAgent"
        self.write_record("runtime_state", "task-state", state)
        task = self.plan_task("DESIGN-1", "design")
        self.write_record("task_plan", "task-plan", [task])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.start("DESIGN-1", "DesignAgent")
        attempt_id = json.loads(output.getvalue())["attemptId"]
        with contextlib.redirect_stdout(io.StringIO()):
            self.finish_pass(attempt_id, "DesignAgent", "DES-R01", modified="src/DESIGN-1.py")
        self.call(
            long_task.command_reopen_phase,
            argparse.Namespace(
                task_dir=str(self.task_dir), from_phase="design", source_revision="",
                reason="review changes design", round="", agent="ProjectManagerAgent",
            ),
        )
        _, tasks = self.read_record("task_plan", "task-plan")
        self.assertEqual(2, tasks[0]["iteration_no"])
        self.assertEqual(0, tasks[0]["attempts"])
        self.assertEqual("REWORK", tasks[0]["status"])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.start("DESIGN-1", "DesignAgent")
        second = json.loads(output.getvalue())
        self.assertEqual("ATTEMPT-DESIGN-1-I002-A001", second["attemptId"])
        _, attempts = self.read_record("task_attempts", "task-attempts")
        self.assertEqual([1, 2], [item["iteration_no"] for item in attempts])
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertEqual([], errors)

    @slow
    def test_reopen_design_creates_new_iterations_for_design_development_and_testing(self) -> None:
        document, state = self.read_record("runtime_state", "task-state")
        state["current_phase"] = "design"
        state["next_agent"] = "DesignAgent"
        self.write_record("runtime_state", "task-state", state)
        tasks = [
            self.plan_task("DESIGN-1", "design", status="PASSED"),
            self.plan_task("DEV-1", "development", status="PASSED"),
            self.plan_task("TEST-1", "testing", status="PASSED"),
        ]
        for task in tasks:
            task["attempts"] = 1
            task["output_revision"] = f"{task['phase'].upper()}-R01"
            task["validation_evidence_ids"] = [self.evidence(f"{task['id']}-r1.log")]
        self.write_record("task_plan", "task-plan", tasks)
        self.call(
            long_task.command_reopen_phase,
            argparse.Namespace(
                task_dir=str(self.task_dir), from_phase="design", source_revision="DES-R02",
                reason="same feature requires another design/development/test cycle", round="",
                agent="ProjectManagerAgent",
            ),
        )
        _, updated = self.read_record("task_plan", "task-plan")
        by_phase = {task["phase"]: task for task in updated}
        for phase in ("design", "development", "testing"):
            self.assertEqual(2, by_phase[phase]["iteration_no"])
            self.assertEqual(0, by_phase[phase]["attempts"])
            self.assertEqual("", by_phase[phase]["output_revision"])
            self.assertEqual([], by_phase[phase]["validation_evidence_ids"])
            self.assertEqual("REWORK", by_phase[phase]["status"])

    def test_reopen_minimal_mode_recreates_current_skipped_outcomes(self) -> None:
        work_path = self.doc_root / "version" / "V_TEST" / "work.md"
        long_task.work_mode.apply_mode(work_path, long_task.work_mode.WorkMode(minimal=True))
        self.call(
            long_task.command_sync_work_mode,
            argparse.Namespace(task_dir=str(self.task_dir), agent="ProjectManagerAgent"),
        )
        self.call(
            long_task.command_reopen_phase,
            argparse.Namespace(
                task_dir=str(self.task_dir), from_phase="design", source_revision="DES-R02",
                reason="design changed in minimal mode", round="", agent="ProjectManagerAgent",
            ),
        )
        _, state = self.read_record("runtime_state", "task-state")
        _, outcomes = self.read_record("stage_outcomes", "stage-outcomes")
        for phase in ("test_design", "testing"):
            artifact = state["artifact_revisions"][phase]
            current = [
                item for item in outcomes
                if item.get("phase") == phase and item.get("is_current") is True
            ]
            self.assertEqual("NOT_APPLICABLE", artifact["status"])
            self.assertEqual(1, len(current))
            self.assertEqual("SKIPPED", current[0]["status"])
            self.assertEqual(artifact["iteration_id"], current[0]["iteration_id"])

    @slow
    def test_stage_outcome_history_survives_new_revision(self) -> None:
        document, state = self.read_record("runtime_state", "task-state")
        self.pass_phase(state, "business_model", "BM-R01")
        self.pass_phase(state, "design", "DES-R01")
        self.write_record("runtime_state", "task-state", state)
        artifact = state["artifact_revisions"]["design"]
        old = {
            "id": "SO-DES-I001", "phase": "design", "round": "DES-R01",
            "iteration_id": artifact["iteration_id"], "iteration_no": 1, "is_current": True,
            "superseded_by": "", "status": "PASSED", "produced_by_agent": "DesignAgent",
            "input_revisions": {"business_model": "BM-R01"}, "output_revision": "DES-R01", "evidence_ids": [self.evidence_id("des-r1.log", "DES-R01", "design", agent="DesignAgent")],
            "open_risks": [], "started_at": "2026-07-22T10:00:00-05:00",
            "completed_at": "2026-07-22T10:05:00-05:00",
        }
        self.write_record("stage_outcomes", "stage-outcomes", [old])
        self.call(
            long_task.command_reopen_phase,
            argparse.Namespace(
                task_dir=str(self.task_dir), from_phase="design", source_revision="",
                reason="new requirement", round="", agent="ProjectManagerAgent",
            ),
        )
        state_document, state = self.read_record("runtime_state", "task-state")
        self.pass_phase(state, "design", "DES-R02")
        self.write_record("runtime_state", "task-state", state)
        _, outcomes = self.read_record("stage_outcomes", "stage-outcomes")
        artifact = state["artifact_revisions"]["design"]
        outcomes.append(
            {
                "id": "SO-DES-I002", "phase": "design", "round": "DES-R02",
                "iteration_id": artifact["iteration_id"], "iteration_no": 2, "is_current": True,
                "superseded_by": "", "status": "PASSED", "produced_by_agent": "DesignAgent",
                "input_revisions": {"business_model": "BM-R01"}, "output_revision": "DES-R02", "evidence_ids": [self.evidence_id("des-r2.log", "DES-R02", "design", agent="DesignAgent")],
                "open_risks": [], "started_at": "2026-07-22T11:00:00-05:00",
                "completed_at": "2026-07-22T11:05:00-05:00",
            }
        )
        self.write_record("stage_outcomes", "stage-outcomes", outcomes)
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertEqual([], errors)
        self.assertFalse(outcomes[0]["is_current"])
        self.assertEqual(artifact["iteration_id"], outcomes[0]["superseded_by"])

    @slow
    def test_current_stage_outcome_requires_current_predecessor_revision(self) -> None:
        document, state = self.read_record("runtime_state", "task-state")
        self.pass_phase(state, "business_model", "BM-R01")
        self.pass_phase(state, "design", "DES-R01")
        state["current_phase"] = "design"
        state["next_agent"] = "DesignAgent"
        self.write_record("runtime_state", "task-state", state)
        artifact = state["artifact_revisions"]["design"]
        outcome = {
            "id": "SO-DES-STALE-INPUT", "phase": "design", "round": "DES-R01",
            "iteration_id": artifact["iteration_id"], "iteration_no": artifact["iteration_no"],
            "is_current": True, "superseded_by": "", "status": "PASSED",
            "produced_by_agent": "DesignAgent",
            "input_revisions": {"business_model": "BM-STALE"},
            "output_revision": "DES-R01", "evidence_ids": [self.evidence_id("design-current.log", "DES-R01", "design", agent="DesignAgent")],
            "open_risks": [], "started_at": "2026-07-22T10:00:00-05:00",
            "completed_at": "2026-07-22T10:05:00-05:00",
        }
        self.write_record("stage_outcomes", "stage-outcomes", [outcome])
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertTrue(any(
            "input_revisions.business_model must match current predecessor revision BM-R01" in error
            for error in errors
        ))

    def test_disabling_minimal_mode_preserves_skipped_history(self) -> None:
        work_path = self.doc_root / "version" / "V_TEST" / "work.md"
        long_task.work_mode.apply_mode(work_path, long_task.work_mode.WorkMode(minimal=True))
        self.call(
            long_task.command_sync_work_mode,
            argparse.Namespace(task_dir=str(self.task_dir), agent="ProjectManagerAgent"),
        )
        state_before = long_task.task_state_path(self.task_dir).read_text(encoding="utf-8")
        outcomes_before = (self.task_dir / "task_events.jsonl").read_text(encoding="utf-8")
        plan_before = (self.task_dir / "task_plan.json").read_text(encoding="utf-8") if (self.task_dir / "task_plan.json").exists() else ""
        long_task.work_mode.apply_mode(work_path, long_task.work_mode.WorkMode(minimal=False))
        with self.assertRaisesRegex(ValueError, "cannot disable wk -min by deleting skipped history"):
            long_task.command_sync_work_mode(
                argparse.Namespace(task_dir=str(self.task_dir), agent="ProjectManagerAgent")
            )
        self.assertEqual(state_before, long_task.task_state_path(self.task_dir).read_text(encoding="utf-8"))
        self.assertEqual(outcomes_before, (self.task_dir / "task_events.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(plan_before, (self.task_dir / "task_plan.json").read_text(encoding="utf-8") if (self.task_dir / "task_plan.json").exists() else "")
        _, outcomes = self.read_record("stage_outcomes", "stage-outcomes")
        current_skips = {
            item["phase"] for item in outcomes
            if item.get("is_current") is True and item.get("status") == "SKIPPED"
        }
        self.assertEqual({"test_design", "testing"}, current_skips)

    def test_work_mode_digest_change_requires_sync(self) -> None:
        work_path = self.doc_root / "version" / "V_TEST" / "work.md"
        long_task.work_mode.apply_mode(work_path, long_task.work_mode.WorkMode(auto=True))
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertTrue(any("differs from work.md" in error for error in errors))

    def test_enable_lightweight_accepts_reopened_stale_test_phases(self) -> None:
        document, state = self.read_record("runtime_state", "task-state")
        state["collaboration_reviews"]["requirement_confirmation"]["required_reviewers"] = (
            self.required_reviewers(state, "requirement_confirmation")
        )
        self.pass_phase(state, "requirement_confirmation", "REQ-WS-R01")
        upstream_reviewers = list(
            state["collaboration_reviews"]["requirement_confirmation"]["required_reviewers"]
        )
        state["collaboration_reviews"]["requirement_confirmation"]["required_reviewers"] = []
        state["work_mode"]["architecture_review"] = True
        state["architecture_review"].update({
            "enabled": True,
            "step": "SKELETON",
            "skeleton_iteration_id": state["artifact_revisions"]["development"]["iteration_id"],
        })
        for phase in ("test_design", "development", "code_review", "testing"):
            state["artifact_revisions"][phase]["status"] = "STALE"
        self.write_record("runtime_state", "task-state", state)
        tasks = [
            self.plan_task("DEV-WS", "development", status="REWORK"),
            self.plan_task("TEST-WS", "testing", status="REWORK"),
        ]
        self.write_record("task_plan", "task-plan", tasks)

        work_path = self.doc_root / "version" / "V_TEST" / "work.md"
        long_task.work_mode.apply_mode(
            work_path, long_task.work_mode.WorkMode(lightweight=True)
        )
        self.call(
            long_task.command_sync_work_mode,
            argparse.Namespace(task_dir=str(self.task_dir), agent="ProjectManagerAgent"),
        )

        _, updated = self.read_record("runtime_state", "task-state")
        self.assertTrue(updated["work_mode"]["lightweight"])
        self.assertFalse(updated["work_mode"]["auto"])
        self.assertFalse(updated["work_mode"]["architecture_review"])
        self.assertFalse(updated["work_mode"]["git_checkpoint"])
        self.assertFalse(updated["work_mode"]["git_push"])
        self.assertEqual("DISABLED", updated["architecture_review"]["step"])
        self.assertEqual(
            upstream_reviewers,
            updated["collaboration_reviews"]["requirement_confirmation"]["required_reviewers"],
        )
        self.assertEqual(
            "PASSED",
            updated["collaboration_reviews"]["requirement_confirmation"]["status"],
        )
        self.assertEqual("NOT_APPLICABLE", updated["artifact_revisions"]["test_design"]["status"])
        self.assertEqual("NOT_APPLICABLE", updated["artifact_revisions"]["code_review"]["status"])
        self.assertEqual("NOT_APPLICABLE", updated["artifact_revisions"]["testing"]["status"])
        by_id = {task["id"]: task for task in long_task.runtime_tasks(self.task_dir)}
        self.assertEqual("NOT_APPLICABLE", by_id["TEST-WS"]["status"])
        self.assertEqual(
            {"implementation_plan": updated["artifact_revisions"]["implementation_plan"]["revision"]},
            by_id["DEV-WS"]["input_revisions"],
        )


if __name__ == "__main__":
    unittest.main()
