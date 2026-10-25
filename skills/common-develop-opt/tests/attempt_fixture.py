from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from runtime import long_task
from runtime._long_task import task_events
from runtime.error_catalog import LifecycleError


class AttemptFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.doc_root = Path(self.tmp.name) / "project_doc"
        with contextlib.redirect_stdout(io.StringIO()):
            long_task.command_init(argparse.Namespace(
                doc_root=str(self.doc_root), version="V_TEST", target="FEAT001", reuse=False,
                agent="ProjectManagerAgent",
            ))
        self.task_dir = self.doc_root / "version" / "V_TEST" / "task" / "FEAT001"
        self._prepare_development_task()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _prepare_development_task(self) -> None:
        state_document, state = long_task.read_task_state_cache(self.task_dir)
        for phase in ("implementation_plan", "test_design"):
            artifact = state["artifact_revisions"][phase]
            revision = f"{phase}-R01"
            artifact.update({"status": "PASSED", "revision": revision})
            review = state.get("collaboration_reviews", {}).get(phase)
            if not isinstance(review, dict):
                continue
            review["status"] = "PASSED"
            review["artifact_revision"] = revision
            review["current_iteration_id"] = artifact["iteration_id"]
            conclusions = {}
            for reviewer in sorted(long_task.expected_required_reviewers(phase, state, minimal=False)):
                criteria = []
                profile = long_task.review_profile(phase, reviewer)
                for index, criterion_id in enumerate(profile.get("criterionIds") or [], 1):
                    criterion = long_task.REVIEW_CRITERIA[criterion_id]
                    policy = criterion.get("evidencePolicy") or {}
                    kinds = list(policy.get("allOfTypes") or [])
                    any_of = list(policy.get("anyOfTypes") or [])
                    if any_of and not any(kind in kinds for kind in any_of):
                        kinds.append(any_of[0])
                    while len(kinds) < int(policy.get("minimumEvidenceCount") or 1):
                        kinds.append(any_of[0] if any_of else "document_ref")
                    evidence_refs = []
                    for evidence_index, kind in enumerate(kinds, 1):
                        ref = self.task_dir / "evidence" / "setup" / f"{phase}-{reviewer}-{index}-{evidence_index}.txt"
                        ref.parent.mkdir(parents=True, exist_ok=True)
                        ref.write_text("setup evidence", encoding="utf-8")
                        command_result_ref = ""
                        register_ref = ref
                        if kind == "command_ref":
                            output = ref.with_suffix(".log")
                            output.write_text("setup command passed", encoding="utf-8")
                            result = ref.with_suffix(".json")
                            result.write_text(json.dumps({
                                "schema_version": 1, "kind": "command_result",
                                "command": "python -m unittest", "exit_code": 0,
                                "executed_at": "2026-08-29T00:00:00Z", "revision": revision,
                                "output_ref": str(output.relative_to(self.task_dir)),
                                "output_digest": long_task.sha256_file(output),
                            }), encoding="utf-8")
                            register_ref = result
                            command_result_ref = str(result.relative_to(self.task_dir))
                        entry = long_task.evidence.register_evidence(
                            self.task_dir, agent=reviewer, evidence_type=kind,
                            ref=str(register_ref.relative_to(self.task_dir)), revision=revision, phase=phase,
                            scope_refs=[criterion_id], command_result_ref=command_result_ref,
                        )
                        evidence_refs.append({"evidence_id": entry["evidence_id"], "claim": "setup evidence"})
                    criteria.append({
                        "criterion_id": criterion_id, "status": "PASSED",
                        "evidence": evidence_refs,
                        "finding_ids": [], "reason": "",
                    })
                conclusions[reviewer] = {
                    "profile_id": f"{phase}:{reviewer}", "revision": revision,
                    "conclusion": "PASSED", "checked_scope": [f"{phase}:{revision}"],
                    "criteria_results": criteria, "finding_ids": [], "limitations": [],
                    "reviewed_at": "2026-08-29T00:00:00Z",
                }
                review["independent_conclusions"] = conclusions
        state["current_phase"] = "development"
        state["next_agent"] = "DevelopAgent"
        state["next_action"] = "implement"
        payloads, _, _ = long_task.append_task_events_payloads(
            self.task_dir, long_task.task_state_path(self.task_dir), state_document, state,
            [], synced_at="2026-08-29T00:00:00Z",
        )
        long_task.transactional_text_write(payloads)
        _, state = long_task.read_task_state_cache(self.task_dir)
        task = {
            "id": "DEV-1", "logical_task_id": "LOGICAL-DEV-1", "feature_id": "F01",
            "iteration_id": state["artifact_revisions"]["development"]["iteration_id"],
            "iteration_no": state["artifact_revisions"]["development"]["iteration_no"],
            "supersedes_iteration_id": "", "revision_reason": "initial iteration",
            "title": "DEV-1", "objective": "observable result", "phase": "development",
            "status": "READY", "depends_on": [], "owner_agent": "DevelopAgent",
            "reviewer_agents": [], "input_revisions": {
                phase: state["artifact_revisions"][phase]["revision"]
                for phase in long_task.phase_predecessors("development", False)
            },
            "allowed_files": [], "acceptance_trace_ids": ["TRACE-1"], "flow_refs": [], "flow_step_refs": [],
            "validation_commands": [], "expected_results": ["pass"],
            "stop_conditions": ["scope drift"], "risk_triggers": [], "attempts": 0,
            "max_attempts": 2, "output_revision": "", "validation_evidence_ids": [],
        }
        path = self.task_dir / "task_plan.json"
        plan = long_task.storage.read_json(path, default={})
        plan["tasks"] = [task]
        path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        traceability = self.task_dir / "traceability.json"
        traceability.write_text(json.dumps({
            "schema_version": 1,
            "items": [{"id": "TRACE-1", "description": "development outcome", "status": "COVERED",
                       "plan_task_ids": ["DEV-1"], "acceptance_criteria": ["AC-1"],
                       "test_case_ids": ["TC-1"]}],
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (self.task_dir / "acceptance.json").write_text(json.dumps({
            "schema_version": 1, "target_id": "FEAT001", "version": "V_TEST",
            "assertions": [{"assertion_id": "ASSERT-1", "acceptance_id": "AC-1",
                "statement": "observable result", "type": "COMMAND_EXIT",
                "phase": "development", "revision": "DEV-R01",
                "blocking": True, "status": "PENDING", "parameters": {}, "source_refs": ["TRACE-1"],
                "evidence_ids": [], "verified_by_agent": "", "verified_at": "", "reason": ""}],
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.task = task

    def _set_validation_command(self, command: str) -> None:
        plan_path = self.task_dir / "task_plan.json"
        plan = long_task.storage.read_json(plan_path, default={})
        plan["tasks"][0]["validation_commands"] = [command]
        plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _start(self) -> str:
        with contextlib.redirect_stdout(io.StringIO()):
            long_task.command_start_attempt(argparse.Namespace(
                task_dir=str(self.task_dir), task_id="DEV-1", input_revision="",
                summary="run", next_action="", agent="DevelopAgent",
            ))
        return long_task.load_task_history(self.task_dir)["attempts"][-1]["attempt_id"]

    def _finish_args(self, attempt_id: str, command: str) -> argparse.Namespace:
        return argparse.Namespace(
            task_dir=str(self.task_dir), attempt_id=attempt_id, status="PASSED",
            output_revision="DEV-R01", failure_type="", failure_reason="", modified_file=[],
            command_ref=[], evidence_ref=[], auto_evidence=True, check=command,
            summary="done", next_action="", next_agent="ProjectManagerAgent", agent="DevelopAgent",
        )
