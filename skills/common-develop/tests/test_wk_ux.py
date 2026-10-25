#!/usr/bin/env python3
"""Regression tests for the UX-1..UX-3 unified wk experience."""
from __future__ import annotations

import argparse
import contextlib
import copy
import fcntl
import io
import json
import os
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import governance_profile
import long_task
import evidence
from _long_task import authorization, task_events
from error_catalog import LifecycleError
import wk


class WKUXTests(unittest.TestCase):
    def _start_task(self, root: Path, *, classification: str = "new-requirement", mode: str = "") -> tuple[Path, dict]:
        args = [
            "start", "-g", "ProjectManagerAgent", "--doc-root", str(root), "--version", "V_TEST",
            "-f", "调整订单流程", "--classification", classification, "--related-feature", "PRD0013-F01",
        ]
        for flag in (
            "business-logic", "data-model", "api-contract", "permission", "workflow",
            "persistence", "external-side-effect", "migration", "dependency",
        ):
            args.extend([f"--{flag}-impact", "NONE"])
        if mode:
            args.append(mode)
        args.append("--apply")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(0, wk.main(args))
        text = output.getvalue()
        payload = json.loads(text[text.index("{"):])
        return Path(payload["taskDir"]), payload

    def _json_command(self, args: list[str]) -> tuple[int, dict]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            rc = wk.main(args)
        return rc, json.loads(output.getvalue())

    def _build_review_conclusion(self, task_dir: Path, phase: str, reviewer: str, revision: str) -> dict:
        """Create a real, schema-valid independent conclusion for a fixture phase."""
        profile = long_task.review_profile(phase, reviewer)
        criteria_results = []
        for criterion_id in profile["criterionIds"]:
            policy = long_task.REVIEW_CRITERIA[criterion_id]["evidencePolicy"]
            evidence_types = list(policy.get("allOfTypes", []))
            any_types = policy.get("anyOfTypes", [])
            if any_types and not set(evidence_types).intersection(any_types):
                evidence_types.append(any_types[0])
            while len(evidence_types) < int(policy.get("minimumEvidenceCount", 1)):
                evidence_types.append(any_types[0] if any_types else "document_ref")
            refs = []
            for index, evidence_type in enumerate(evidence_types, start=1):
                source = task_dir / "evidence" / "ux-fixture" / f"{criterion_id}-{index}.txt"
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_text(f"fixture evidence for {criterion_id}\n", encoding="utf-8")
                entry = evidence.register_evidence(
                    task_dir,
                    agent=reviewer,
                    evidence_type=evidence_type,
                    ref=str(source.relative_to(task_dir)),
                    revision=revision,
                    phase=phase,
                    scope_refs=[criterion_id],
                    capture_mode="SNAPSHOT",
                )
                refs.append({"evidence_id": entry["evidence_id"], "claim": f"{criterion_id} is satisfied."})
            criteria_results.append({
                "criterion_id": criterion_id,
                "status": "PASSED",
                "evidence": refs,
                "finding_ids": [],
                "reason": "",
            })
        return {
            "profile_id": f"{phase}:{reviewer}",
            "revision": revision,
            "conclusion": "PASSED",
            "checked_scope": [f"{phase}/ux-fixture"],
            "criteria_results": criteria_results,
            "finding_ids": [],
            "limitations": [],
            "reviewed_at": "2026-08-29T00:00:00+08:00",
        }

    def _prepare_passed_phase_fixture(self, task_dir: Path, phase: str = "requirement_confirmation") -> None:
        """Prepare only the current phase facts through the event-backed write path."""
        state = long_task.runtime_state(task_dir)
        artifact = state["artifact_revisions"][phase]
        revision = f"{phase.upper()}-UX-R01"
        artifact.update({"status": "PASSED", "revision": revision})

        stage_source = task_dir / "evidence" / "ux-fixture" / "stage-outcome.txt"
        stage_source.parent.mkdir(parents=True, exist_ok=True)
        stage_source.write_text("current phase passed fixture\n", encoding="utf-8")
        stage_evidence = evidence.register_evidence(
            task_dir,
            agent="RequirementConfirmationAgent",
            evidence_type="requirement_ref",
            ref=str(stage_source.relative_to(task_dir)),
            revision=revision,
            phase=phase,
            scope_refs=["stage-outcome"],
            capture_mode="SNAPSHOT",
        )
        state["collaboration_reviews"][phase].update({
            "artifact_revision": revision,
            "status": "PASSED",
            "independent_conclusions": {
                "RequirementAnalysisAgent": self._build_review_conclusion(
                    task_dir, phase, "RequirementAnalysisAgent", revision
                ),
            },
        })
        outcome = {
            "id": f"SO-{task_dir.name}-{phase.upper()}-UX-I001",
            "phase": phase,
            "round": f"{phase.upper()}-UX-I001",
            "status": "PASSED",
            "produced_by_agent": "RequirementConfirmationAgent",
            "input_revisions": {},
            "output_revision": revision,
            "evidence_ids": [stage_evidence["evidence_id"]],
            "open_risks": [],
            "started_at": "2026-08-29T00:00:00+08:00",
            "completed_at": "2026-08-29T00:00:01+08:00",
            "iteration_id": artifact["iteration_id"],
            "iteration_no": artifact["iteration_no"],
            "is_current": True,
            "superseded_by": "",
        }
        state_document, _ = long_task.read_task_state_cache(task_dir)
        event_payloads, _projected, _history = long_task.append_task_events_payloads(
            task_dir,
            long_task.task_state_path(task_dir),
            state_document,
            state,
            [{
                "type": "STAGE_OUTCOME_RECORDED",
                "entity_id": outcome["id"],
                "timestamp": "2026-08-29T00:00:02+08:00",
                "source": "tests.test_wk_ux",
                "payload": {"outcome": outcome},
            }],
            synced_at="2026-08-29T00:00:02+08:00",
        )
        long_task.transactional_text_write(event_payloads)

    def test_next_exposes_canonical_and_legacy_command_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            task_dir, _ = self._start_task(Path(temp) / "project_doc")
            rc, payload = self._json_command([
                "next", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir),
            ])
        self.assertEqual(0, rc)
        self.assertIn("nextCommand", payload)
        self.assertIn("recommendedCommand", payload)
        self.assertIsInstance(payload["nextCommand"], dict)
        self.assertEqual(payload["nextCommand"]["command"], payload["recommendedCommand"])
        self.assertIn("reason", payload["nextCommand"])
        self.assertIn("requiredAgent", payload["nextCommand"])
        self.assertIn("requiresConfirmation", payload["nextCommand"])
        self.assertIn("readOnly", payload["nextCommand"])

    def test_status_health_is_read_only_and_reports_quick_checks(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            task_dir, _ = self._start_task(Path(temp) / "project_doc")
            tracked = [task_dir / "task_events.jsonl", task_dir / "cache" / "current_manifest.json"]
            before = {path: path.read_bytes() for path in tracked if path.exists()}
            rc, payload = self._json_command([
                "status", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir),
            ])
            after = {path: path.read_bytes() for path in tracked if path.exists()}
        self.assertEqual(0, rc)
        self.assertEqual(before, after)
        self.assertTrue(payload["health"]["readOnly"])
        self.assertIsInstance(payload["health"]["quickChecks"], list)
        self.assertTrue({item["id"] for item in payload["health"]["quickChecks"]} >= {
            "work-file", "task-events", "runtime-reducer", "request-intake", "authority-consistency",
        })

    def test_status_health_detects_missing_and_corrupt_ledger_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            task_dir, _ = self._start_task(Path(temp) / "project_doc")
            ledger = task_dir / "task_events.jsonl"
            original = ledger.read_bytes()
            ledger.unlink()
            _rc, missing = self._json_command([
                "status", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir),
            ])
            self.assertTrue(any(item["id"] == "task-events" and item["status"] == "INVALID" for item in missing["health"]["quickChecks"]))
            ledger.write_bytes(original + b"{broken\n")
            corrupted_before = ledger.read_bytes()
            _rc, corrupt = self._json_command([
                "status", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir),
            ])
            self.assertTrue(any(item["id"] == "task-events" and item["status"] == "INVALID" for item in corrupt["health"]["quickChecks"]))
            self.assertEqual(corrupted_before, ledger.read_bytes())

    def test_status_validation_failure_is_structured_and_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            task_dir, _ = self._start_task(Path(temp) / "project_doc")
            with mock.patch.object(wk.long_task, "validate_task", side_effect=RuntimeError("synthetic validation failure")):
                rc, payload = self._json_command([
                    "status", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir),
                ])
        self.assertEqual(1, rc)
        self.assertEqual("INVALID", payload["status"])
        self.assertTrue(payload["errors"])
        self.assertIn("nextCommand", payload)
        self.assertTrue(payload["health"]["readOnly"])

    def test_next_degrades_to_validation_command_when_projection_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            task_dir, _ = self._start_task(Path(temp) / "project_doc")
            with mock.patch.object(wk, "_next_payload", side_effect=RuntimeError("synthetic projection failure")):
                rc, payload = self._json_command([
                    "next", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir),
                ])
        self.assertEqual(0, rc)
        self.assertEqual("TASK_VALIDATION", payload["currentGate"])
        self.assertEqual(["NEXT_PROJECTION_UNAVAILABLE"], payload["reasonCodes"])
        self.assertIn("nextCommand", payload)
        self.assertIsInstance(payload["nextCommand"], dict)
        self.assertEqual(payload["nextCommand"]["command"], payload["recommendedCommand"])
        self.assertIn("reason", payload["nextCommand"])
        self.assertIn("requiredAgent", payload["nextCommand"])
        self.assertIn("requiresConfirmation", payload["nextCommand"])
        self.assertIn("readOnly", payload["nextCommand"])

    def test_status_manifest_failure_marks_projection_invalid_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            task_dir, _ = self._start_task(Path(temp) / "project_doc")
            with mock.patch.object(wk, "_current_manifest_ref", side_effect=RuntimeError("synthetic manifest failure")):
                rc, payload = self._json_command([
                    "status", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir),
                ])
        self.assertEqual(1, rc)
        self.assertEqual("INVALID", payload["status"])
        self.assertTrue(any("派生 manifest" in error for error in payload["errors"]))
        self.assertIn("nextCommand", payload)

    def test_governance_recommendation_uses_impact_but_remains_advisory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            payload = governance_profile.recommend(
                Path(temp), impact_assessment={"dataModel": "HIGH", "permission": "HIGH"},
            )
        self.assertEqual("HIGH_RISK", payload["profile"])
        self.assertEqual("-ar -gc", payload["recommendedMode"])
        self.assertNotIn("impactSignals", payload)
        self.assertTrue(payload["advisory"])
        self.assertTrue(payload["overridable"])
        self.assertTrue(payload["overrideAllowed"])

    def test_simple_optimization_is_not_reduced_to_minimal_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            payload = governance_profile.recommend(
                Path(temp), classification="SIMPLE_OPTIMIZATION",
            )
        self.assertEqual("SIMPLE_OPTIMIZATION_FLOW", payload["recommendedWorkflow"])
        self.assertEqual("standard", payload["recommendedMode"])
        self.assertNotEqual("-min", payload["recommendedMode"])

    def test_start_persists_full_governance_inputs_and_keeps_task_state_compact(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project_doc"
            constraints = Path(temp) / "constraints.json"
            constraints.write_text(json.dumps({"constraints": []}), encoding="utf-8")
            args = [
                "start", "-g", "ProjectManagerAgent", "--doc-root", str(root), "--version", "V_TEST",
                "-f", "调整订单流程", "--classification", "new-requirement", "--related-feature", "PRD0013-F01",
                "--changed-scope", "database/schema", "--changed-ref", "src/orders.py#Order",
                "--constraints", str(constraints), "--current-profile", "SMALL",
                "--override-reason", "数据库约束需要更高治理深度", "--exploration", "--apply",
            ]
            for flag in (
                "business-logic", "data-model", "api-contract", "permission", "workflow",
                "persistence", "external-side-effect", "migration", "dependency",
            ):
                args.extend([f"--{flag}-impact", "NONE"])
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(0, wk.main(args))
            payload = json.loads(output.getvalue()[output.getvalue().index("{"):])
            task_dir = Path(payload["taskDir"])
            recommendation = payload["governanceProfile"]
            self.assertNotIn("changedScopes", recommendation)

            intake = long_task.request_intake.load(task_dir / "request_intake.json")
            snapshot = intake["governanceRecommendation"]
            self.assertEqual(["database/schema"], snapshot["changedScopes"])
            self.assertEqual(["src/orders.py#Order"], snapshot["changedRefs"])
            self.assertEqual("SMALL", snapshot["currentGovernanceProfile"])
            self.assertTrue(snapshot["exploration"])
            self.assertTrue(snapshot["override"]["active"])
            self.assertTrue(snapshot["inputDigest"])
            self.assertNotEqual(recommendation, snapshot)
            self.assertEqual(recommendation["profile"], snapshot["profile"])
            state = long_task.runtime_state(task_dir)
            self.assertEqual(recommendation["profile"], state["request_intake"]["governance_profile"])
            self.assertEqual(snapshot["inputDigest"], state["request_intake"]["governance_recommendation_digest"])
            self.assertTrue(state["request_intake"]["governance_override_active"])

    def test_start_requires_reason_when_current_governance_profile_is_overridden(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project_doc"
            args = [
                "start", "-g", "ProjectManagerAgent", "--doc-root", str(root), "--version", "V_TEST",
                "-f", "调整订单流程", "--classification", "new-requirement", "--related-feature", "PRD0013-F01",
                "--changed-scope", "database/schema", "--current-profile", "SMALL", "--apply",
            ]
            for flag in (
                "business-logic", "data-model", "api-contract", "permission", "workflow",
                "persistence", "external-side-effect", "migration", "dependency",
            ):
                args.extend([f"--{flag}-impact", "NONE"])
            stderr = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(stderr):
                rc = wk.main(args)
        self.assertEqual(1, rc)
        self.assertIn("override_reason is required", stderr.getvalue())

    def test_explicit_mode_is_preserved_when_start_emits_advisory_recommendation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            task_dir, payload = self._start_task(Path(temp) / "project_doc", mode="-ws")
            self.assertTrue(task_dir.exists())
            mode_state = long_task.actual_work_mode_state(task_dir)
            self.assertTrue(mode_state["lightweight"])
            self.assertTrue(payload["decisionGuide"]["advisory"])
            self.assertTrue(payload["decisionGuide"]["overrideAllowed"])
            self.assertIn("不会覆盖用户显式选择", payload["decisionGuide"]["whatWillNotChange"])

    def test_confirm_parser_is_public_and_preview_is_non_mutating(self) -> None:
        parser = wk.build_parser()
        parsed = parser.parse_args([
            "confirm", "-g", "ProjectManagerAgent", "--task-dir", "task", "--until", "testing",
            "--reason", "集中处理本阶段门禁",
        ])
        self.assertEqual("confirm", parsed.command)
        with tempfile.TemporaryDirectory() as temp:
            task_dir, _ = self._start_task(Path(temp) / "project_doc")
            ledger = task_dir / "task_events.jsonl"
            before = ledger.read_bytes()
            rc, payload = self._json_command([
                "confirm", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir),
                "--until", "testing", "--reason", "集中处理本阶段门禁", "--preview", "--json",
            ])
            self.assertEqual(0, rc)
            self.assertEqual("PREVIEW", payload["status"])
            self.assertFalse(payload["written"])
            self.assertEqual(before, ledger.read_bytes())
            self.assertTrue(any("BATCH_AUTHORIZATION_DOES_NOT_BYPASS_GATE" in item["reasonCodes"] for item in payload["atomicActions"]))

    def test_confirm_marks_mode_skipped_phases_not_applicable(self) -> None:
        cases = {
            "-min": {"test_design", "testing"},
            "-ws": {"test_design", "code_review", "testing"},
        }
        for mode, skipped_phases in cases.items():
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp:
                task_dir, _ = self._start_task(Path(temp) / "project_doc", mode=mode)
                rc, payload = self._json_command([
                    "confirm", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir),
                    "--until", "testing", "--reason", "检查模式跳过阶段", "--preview", "--json",
                ])
                self.assertEqual(0, rc)
                actions = {item["phase"]: item for item in payload["atomicActions"]}
                for phase in skipped_phases:
                    self.assertIn(phase, actions)
                    self.assertEqual("NOT_APPLICABLE", actions[phase]["status"])
                    self.assertEqual("", actions[phase]["command"])
                    self.assertEqual(["PHASE_SKIPPED_BY_ACTIVE_WORKFLOW"], actions[phase]["reasonCodes"])
                executable = set(actions) - skipped_phases
                self.assertTrue(executable)
                for phase in executable:
                    self.assertNotEqual("NOT_APPLICABLE", actions[phase]["status"])
                    self.assertNotEqual("", actions[phase]["command"])

    def test_authorization_consumption_crosses_mode_skipped_phases(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            task_dir, _ = self._start_task(Path(temp) / "project_doc", mode="-min")
            rc, confirmed = self._json_command([
                "confirm", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir),
                "--until", "implementation_plan", "--reason", "跨越跳过阶段", "--apply", "--json",
            ])
            self.assertEqual(0, rc)
            state = long_task.runtime_state(task_dir)
            batch = authorization.find_valid_batch(
                task_dir, state, str(state["current_phase"]), long_task.PHASES
            )
            self.assertIsNotNone(batch)

            # Simulate the already-gated transitions up to design.  The skipped
            # test_design phase must not become a required predecessor in the
            # authorization consumption chain.
            transitions = [
                ("requirement_confirmation", "requirement_analysis"),
                ("requirement_analysis", "business_model"),
                ("business_model", "design"),
            ]
            for from_phase, to_phase in transitions:
                spec = authorization.consumption_spec(
                    batch, from_phase=from_phase, to_phase=to_phase,
                    state=state, timestamp="2026-08-29T00:00:00Z",
                )
                text, _event, _events = task_events.append_event_text(
                    task_dir, spec["type"], spec["entity_id"], spec["payload"],
                    timestamp=spec["timestamp"], source=spec["source"],
                )
                task_events.event_path(task_dir).write_text(text, encoding="utf-8")

            design_state = copy.deepcopy(state)
            design_state["current_phase"] = "design"
            self.assertIsNotNone(
                authorization.find_valid_batch(task_dir, design_state, "design", long_task.PHASES)
            )

            design_to_plan = authorization.consumption_spec(
                batch, from_phase="design", to_phase="implementation_plan",
                state=state, timestamp="2026-08-29T00:00:01Z",
            )
            text, _event, _events = task_events.append_event_text(
                task_dir, design_to_plan["type"], design_to_plan["entity_id"], design_to_plan["payload"],
                timestamp=design_to_plan["timestamp"], source=design_to_plan["source"],
            )
            task_events.event_path(task_dir).write_text(text, encoding="utf-8")

            plan_state = copy.deepcopy(state)
            plan_state["current_phase"] = "implementation_plan"
            next_batch = authorization.find_valid_batch(
                task_dir, plan_state, "implementation_plan", long_task.PHASES
            )
            self.assertIsNotNone(next_batch)
            self.assertEqual(confirmed["requestFingerprint"], next_batch["fingerprint"])
            consumed_from = {
                event["payload"].get("fromPhase")
                for event in task_events.reduce_events(task_events.read_events(task_dir))["authorization_consumptions"]
            }
            self.assertNotIn("test_design", consumed_from)
            self.assertNotIn("testing", consumed_from)

    def test_confirm_apply_is_audited_idempotent_and_does_not_advance(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            task_dir, _ = self._start_task(Path(temp) / "project_doc")
            before_state = long_task.runtime_state(task_dir)
            args = [
                "confirm", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir),
                "--until", "testing", "--reason", "用户授权本批次处理", "--apply", "--json",
            ]
            rc, applied = self._json_command(args)
            self.assertEqual(0, rc)
            self.assertEqual("CONFIRMED", applied["status"])
            self.assertTrue(applied["written"])
            events = task_events.read_events(task_dir)
            self.assertEqual("AUTHORIZATION_BATCH_CONFIRMED", events[-1]["type"])
            event_payload = events[-1]["payload"]
            self.assertEqual("用户授权本批次处理", event_payload["reason"])
            self.assertEqual("ProjectManagerAgent", event_payload["agent"])
            self.assertEqual("testing", event_payload["untilPhase"])
            self.assertEqual(str(long_task.runtime_state(task_dir)["target_id"]), event_payload["taskId"])
            self.assertEqual(str(task_dir.resolve()), event_payload["taskDir"])
            current_state = long_task.runtime_state(task_dir)
            current_artifact = current_state["artifact_revisions"][event_payload["fromPhase"]]
            self.assertEqual(current_artifact["iteration_id"], event_payload["iterationId"])
            self.assertEqual(current_artifact["revision"], event_payload["revision"])
            self.assertTrue(event_payload["atomicActions"])
            self.assertTrue(event_payload["requestFingerprint"])
            reduced = task_events.reduce_events(events)
            self.assertEqual(1, len(reduced["authorization_events"]))
            self.assertEqual([], reduced["authorization_consumptions"])
            after_state = long_task.runtime_state(task_dir)
            self.assertEqual(before_state["current_phase"], after_state["current_phase"])
            self.assertEqual(before_state["task_status"], after_state["task_status"])
            rc, duplicate = self._json_command(args)
            self.assertEqual(0, rc)
            self.assertEqual("NO_OP", duplicate["status"])
            self.assertEqual(len(events), len(task_events.read_events(task_dir)))

    def test_confirm_apply_uses_shared_task_writer_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            task_dir, _ = self._start_task(Path(temp) / "project_doc")
            ledger = task_events.event_path(task_dir)
            before = ledger.read_bytes()
            lock_path = task_dir / "cache" / "amend-phase.lock"
            with lock_path.open("a+b") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaisesRegex(ValueError, "task lifecycle writer"):
                    wk.command_confirm(argparse.Namespace(
                        task_dir=str(task_dir), until="testing", reopen_from="",
                        changed_phase="", source_revision="", reason="锁测试",
                        agent="ProjectManagerAgent", preview=False, apply=True, json=True,
                    ))
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            self.assertEqual(before, ledger.read_bytes())

    def test_legacy_authorization_without_runtime_binding_is_not_consumable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            task_dir, _ = self._start_task(Path(temp) / "project_doc")
            state = long_task.runtime_state(task_dir)
            current = str(state["current_phase"])
            text, _event, _events = task_events.append_event_text(
                task_dir,
                "AUTHORIZATION_BATCH_CONFIRMED",
                task_dir.name,
                {
                    "taskDir": str(task_dir.resolve()),
                    "fromPhase": current,
                    "untilPhase": "testing",
                    "reason": "legacy authorization",
                    "agent": "ProjectManagerAgent",
                    "mode": "apply",
                    "atomicActions": [],
                    "requestFingerprint": "legacy-fingerprint",
                },
                source="test",
            )
            task_events.event_path(task_dir).write_text(text, encoding="utf-8")
            self.assertIsNone(authorization.find_valid_batch(task_dir, state, current, long_task.PHASES))

    def test_authorization_consumption_is_bound_and_requires_continuous_phases(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            task_dir, _ = self._start_task(Path(temp) / "project_doc")
            rc, applied = self._json_command([
                "confirm", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir),
                "--until", "testing", "--reason", "连续处理", "--apply", "--json",
            ])
            self.assertEqual(0, rc)
            state = long_task.runtime_state(task_dir)
            current = str(state["current_phase"])
            batch = authorization.find_valid_batch(task_dir, state, current, long_task.PHASES)
            self.assertIsNotNone(batch)
            next_phase = long_task.PHASES[long_task.PHASES.index(current) + 1]
            spec = authorization.consumption_spec(
                batch, from_phase=current, to_phase=next_phase, state=state, timestamp="2026-08-29T00:00:00Z"
            )
            text, _event, _events = task_events.append_event_text(
                task_dir, spec["type"], spec["entity_id"], spec["payload"],
                timestamp=spec["timestamp"], source=spec["source"],
            )
            task_events.event_path(task_dir).write_text(text, encoding="utf-8")
            reduced = task_events.reduce_events(task_events.read_events(task_dir))
            self.assertEqual(1, len(reduced["authorization_consumptions"]))
            consumed = reduced["authorization_consumptions"][0]["payload"]
            self.assertEqual(str(state["target_id"]), consumed["taskId"])
            self.assertEqual(current, consumed["fromPhase"])
            self.assertEqual(next_phase, consumed["toPhase"])
            self.assertEqual(batch["fingerprint"], consumed["requestFingerprint"])
            self.assertIsNone(authorization.find_valid_batch(task_dir, state, current, long_task.PHASES))
            next_state = copy.deepcopy(state)
            next_state["current_phase"] = next_phase
            next_batch = authorization.find_valid_batch(task_dir, next_state, next_phase, long_task.PHASES)
            self.assertIsNotNone(next_batch)
            self.assertEqual(batch["fingerprint"], next_batch["fingerprint"])

    def test_real_advance_phase_consumes_confirmed_authorization(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            task_dir, _ = self._start_task(Path(temp) / "project_doc")
            self._prepare_passed_phase_fixture(task_dir)
            self.assertEqual([], long_task.validate_task(task_dir)[0])
            rc, confirmed = self._json_command([
                "confirm", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir),
                "--until", "requirement_analysis", "--reason", "推进当前已通过阶段", "--apply", "--json",
            ])
            self.assertEqual(0, rc)
            self.assertEqual("CONFIRMED", confirmed["status"])
            fingerprint = confirmed["requestFingerprint"]

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                rc = long_task.command_advance_phase(argparse.Namespace(
                    task_dir=str(task_dir), agent="ProjectManagerAgent",
                ))
            self.assertEqual(0, rc)
            advanced = json.loads(output.getvalue())
            self.assertEqual("ADVANCED", advanced["status"])
            self.assertEqual("CONSUMED", advanced["authorization"]["status"])
            self.assertEqual(fingerprint, advanced["authorization"]["requestFingerprint"])
            self.assertEqual("requirement_analysis", long_task.runtime_state(task_dir)["current_phase"])

            events = task_events.read_events(task_dir)
            consumed = [event for event in events if event["type"] == "AUTHORIZATION_BATCH_CONSUMED"]
            self.assertEqual(1, len(consumed))
            consumed_payload = consumed[0]["payload"]
            confirmation = next(event for event in events if event["type"] == "AUTHORIZATION_BATCH_CONFIRMED")
            confirmation_payload = confirmation["payload"]
            for key in ("taskId", "iterationId", "revision", "requestFingerprint"):
                self.assertEqual(confirmation_payload[key], consumed_payload[key])
            self.assertEqual("requirement_confirmation", consumed_payload["fromPhase"])
            self.assertEqual("requirement_analysis", consumed_payload["toPhase"])

    def test_real_advance_phase_does_not_consume_authorization_when_gate_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            task_dir, _ = self._start_task(Path(temp) / "project_doc")
            rc, _confirmed = self._json_command([
                "confirm", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir),
                "--until", "requirement_analysis", "--reason", "等待阶段门禁", "--apply", "--json",
            ])
            self.assertEqual(0, rc)
            with self.assertRaises(LifecycleError):
                long_task.command_advance_phase(argparse.Namespace(
                    task_dir=str(task_dir), agent="ProjectManagerAgent",
                ))
            events = task_events.read_events(task_dir)
            self.assertFalse(any(event["type"] == "AUTHORIZATION_BATCH_CONSUMED" for event in events))
            self.assertEqual("requirement_confirmation", long_task.runtime_state(task_dir)["current_phase"])

    def test_phase_stale_invalidates_confirmed_authorization(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            task_dir, _ = self._start_task(Path(temp) / "project_doc")
            self._json_command([
                "confirm", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir),
                "--until", "testing", "--reason", "待后续阶段", "--apply", "--json",
            ])
            state = long_task.runtime_state(task_dir)
            current = str(state["current_phase"])
            self.assertIsNotNone(authorization.find_valid_batch(task_dir, state, current, long_task.PHASES))
            stale_payload = {
                "invalidated_phases": long_task.PHASES[long_task.PHASES.index(current):],
                "reason": "reopen-phase",
            }
            text, _event, _events = task_events.append_event_text(
                task_dir, "PHASE_STALE", current, stale_payload, source="test"
            )
            task_events.event_path(task_dir).write_text(text, encoding="utf-8")
            self.assertIsNone(authorization.find_valid_batch(task_dir, state, current, long_task.PHASES))

    def test_relative_task_dir_matches_absolute_authorization_binding(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            task_dir, _ = self._start_task(Path(temp) / "project_doc")
            relative = os.path.relpath(task_dir, Path.cwd())
            rc, payload = self._json_command([
                "confirm", "-g", "ProjectManagerAgent", "--task-dir", relative,
                "--until", "requirement_confirmation", "--reason", "相对路径", "--apply", "--json",
            ])
            self.assertEqual(0, rc)
            self.assertEqual("CONFIRMED", payload["status"])
            event = task_events.read_events(task_dir)[-1]
            self.assertEqual(str(task_dir.resolve()), event["payload"]["taskDir"])

    def test_confirm_rejects_invalid_target_and_empty_reason(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            task_dir, _ = self._start_task(Path(temp) / "project_doc")
            for until, reason, expected in (("no-such-phase", "x", "CONFIRM_PHASE_INVALID"), ("testing", "", "CONFIRM_REASON_REQUIRED")):
                output = io.StringIO()
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(output):
                    rc = wk.main([
                        "confirm", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir),
                        "--until", until, "--reason", reason, "--json",
                    ])
                self.assertEqual(1, rc)
                self.assertIn(expected, output.getvalue())

    def test_confirm_allows_authorizing_through_current_phase(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            task_dir, _ = self._start_task(Path(temp) / "project_doc")
            rc, payload = self._json_command([
                "confirm", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir),
                "--until", "requirement_confirmation", "--reason", "确认当前门禁", "--preview", "--json",
            ])
        self.assertEqual(0, rc)
        self.assertEqual("PREVIEW", payload["status"])
        self.assertEqual("requirement_confirmation", payload["fromPhase"])
        self.assertEqual("requirement_confirmation", payload["untilPhase"])

    def test_confirm_requires_project_manager_authorization(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            task_dir, _ = self._start_task(Path(temp) / "project_doc")
            output = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(output):
                rc = wk.main([
                    "confirm", "-g", "DevelopAgent", "--task-dir", str(task_dir),
                    "--until", "testing", "--reason", "x", "--json",
                ])
        self.assertEqual(1, rc)
        self.assertIn("not authorized for wk.confirm", output.getvalue())


    def test_step_preview_is_exposed_and_returns_stable_atomic_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            task_dir, _ = self._start_task(Path(temp) / "project_doc")
            before = {
                path: path.read_bytes()
                for path in task_dir.rglob("*")
                if path.is_file()
            }
            rc, first = self._json_command([
                "step", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir),
                "--preview", "--json",
            ])
            rc2, second = self._json_command([
                "step", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir),
                "--preview", "--json",
            ])
            after = {
                path: path.read_bytes()
                for path in task_dir.rglob("*")
                if path.is_file()
            }
        self.assertEqual(0, rc)
        self.assertEqual(0, rc2)
        self.assertEqual("READY", first["status"])
        self.assertRegex(first["planDigest"], r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(first["planDigest"], second["planDigest"])
        self.assertEqual(before, after)
        self.assertTrue(first["readOnly"])
        self.assertFalse(first["mutationsApplied"])
        self.assertFalse(first["authorizationRecorded"])
        self.assertTrue(first["steps"])
        self.assertTrue(all("command" in step and "requiresConfirmation" in step for step in first["steps"]))

    def test_step_preview_digest_changes_when_event_ledger_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            task_dir, _ = self._start_task(Path(temp) / "project_doc")
            _rc, first = self._json_command([
                "step", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir),
                "--preview", "--json",
            ])
            ledger = task_dir / "task_events.jsonl"
            original = ledger.read_bytes()
            # A malformed append must still invalidate a previously generated
            # plan; preview remains fail-closed and must not repair the ledger.
            ledger.write_bytes(original + b"{malformed\\n")
            _rc2, second = self._json_command([
                "step", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir),
                "--preview", "--json",
            ])
        self.assertNotEqual(first["planDigest"], second["planDigest"])
        self.assertEqual("BLOCKED", second["status"])
        self.assertFalse(second["mutationsApplied"])

    def test_step_preview_never_records_authorization_or_accepts_apply(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            task_dir, _ = self._start_task(Path(temp) / "project_doc")
            before = (task_dir / "task_events.jsonl").read_bytes()
            output = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(output):
                rc = wk.main([
                    "step", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir),
                    "--apply", "--json",
                ])
            after = (task_dir / "task_events.jsonl").read_bytes()
        self.assertEqual(1, rc)
        self.assertIn("STEP_PREVIEW_REQUIRED", output.getvalue())
        self.assertEqual(before, after)

    def test_step_apply_requires_matching_preview_digest_and_reports_no_safe_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            task_dir, _ = self._start_task(Path(temp) / "project_doc")
            _rc, preview = self._json_command([
                "step", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir),
                "--preview", "--json",
            ])
            rc, payload = self._json_command([
                "step", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir),
                "--apply", "--plan-digest", preview["planDigest"], "--json",
            ])
        self.assertEqual(2, rc)
        self.assertEqual("NO_SAFE_MUTATION", payload["status"])
        self.assertFalse(payload["mutationsApplied"])

    def test_step_apply_rejects_stale_digest_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            task_dir, _ = self._start_task(Path(temp) / "project_doc")
            _rc, preview = self._json_command([
                "step", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir),
                "--preview", "--json",
            ])
            ledger = task_dir / "task_events.jsonl"
            before = ledger.read_bytes()
            ledger.write_bytes(before + b"\n")
            output = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(output):
                rc = wk.main([
                    "step", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir),
                    "--apply", "--plan-digest", preview["planDigest"], "--json",
                ])
            after = ledger.read_bytes()
        self.assertEqual(1, rc)
        self.assertIn("STEP_PLAN_STALE", output.getvalue())
        self.assertEqual(before + b"\n", after)


    def _step_apply_with_payload(self, task_dir: Path, payload: dict, *patchers, decision=None):
        """Run apply against a deterministic injected preview projection."""
        effective_decision = decision or {"classification": "CLOSURE_ONLY", "currentPhase": "requirement_confirmation"}
        with mock.patch.object(wk.step_plan, "build_preview", return_value=copy.deepcopy(payload)), \
             mock.patch.object(wk.lifecycle_decision, "evaluate", return_value=copy.deepcopy(effective_decision)):
            for patcher in patchers:
                patcher.start()
            try:
                return self._json_command([
                    "step", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir),
                    "--apply", "--plan-digest", payload["planDigest"], "--json",
                ])
            finally:
                for patcher in reversed(patchers):
                    patcher.stop()

    def test_step_apply_executes_only_declared_actions_in_declared_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            task_dir, _ = self._start_task(Path(temp) / "project_doc")
            payload = {
                "status": "READY", "taskDir": str(task_dir), "planVersion": 1,
                "planDigest": "sha256:" + "1" * 64,
                "steps": [
                    {"permissionCommandId": "long_task.finalize-phase", "autoApplyAllowed": True},
                    {"permissionCommandId": "long_task.advance-phase", "autoApplyAllowed": True},
                ],
                "applySteps": ["long_task.finalize-phase", "long_task.advance-phase"],
            }
            calls = []
            finalize = mock.patch.object(wk.long_task, "command_finalize_phase", side_effect=lambda args: calls.append("finalize") or 0)
            advance = mock.patch.object(wk.long_task, "command_advance_phase", side_effect=lambda args: calls.append("advance") or 0)
            rc, result = self._step_apply_with_payload(task_dir, payload, finalize, advance)
        self.assertEqual(0, rc)
        self.assertEqual(["finalize", "advance"], calls)
        self.assertEqual("APPLIED", result["status"])
        self.assertTrue(result["mutationsApplied"])
        self.assertEqual(2, len(result["completedSteps"]))

    def test_step_apply_uses_canonical_phase_owner_for_publish_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            task_dir, _ = self._start_task(Path(temp) / "project_doc")
            payload = {
                "status": "READY", "taskDir": str(task_dir), "planVersion": 1,
                "planDigest": "sha256:" + "5" * 64,
                "steps": [{
                    "permissionCommandId": "long_task.publish-artifact",
                    "autoApplyAllowed": True,
                }],
                "applySteps": ["long_task.publish-artifact"],
            }
            decision = {
                "classification": "PUBLISH_READY",
                "currentPhase": "development",
                "requiredAgent": "DevelopAgent",
                "candidate": {"attemptId": "ATTEMPT-001"},
            }
            calls = []
            publish = mock.patch.object(
                wk.long_task, "command_publish_artifact",
                side_effect=lambda args: calls.append(args) or 9,
            )
            rc, result = self._step_apply_with_payload(
                task_dir, payload, publish, decision=decision,
            )
        self.assertEqual(2, rc)
        self.assertEqual(1, len(calls))
        self.assertEqual("DevelopAgent", calls[0].agent)
        self.assertEqual("ATTEMPT-001", calls[0].attempt_id)
        self.assertEqual("PARTIAL", result["status"])
        self.assertIn("-g DevelopAgent", result["repairCommand"])

    def test_step_apply_registers_review_assertions_as_project_manager(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            task_dir, _ = self._start_task(Path(temp) / "project_doc")
            payload = {
                "status": "READY", "taskDir": str(task_dir), "planVersion": 1,
                "planDigest": "sha256:" + "6" * 64,
                "steps": [{
                    "permissionCommandId": "acceptance.ensure-review",
                    "autoApplyAllowed": True,
                }],
                "applySteps": ["acceptance.ensure-review"],
            }
            decision = {
                "classification": "ACCEPTANCE_REGISTRATION_REQUIRED",
                "currentPhase": "design",
                "requiredAgent": "ProjectManagerAgent",
                "candidate": {"revision": "DES-R02"},
            }
            calls = []
            ensure = mock.patch.object(
                wk.acceptance, "command_ensure_review",
                side_effect=lambda args: calls.append(args) or 0,
            )
            rc, result = self._step_apply_with_payload(
                task_dir, payload, ensure, decision=decision,
            )
        self.assertEqual(0, rc)
        self.assertEqual(1, len(calls))
        self.assertEqual("ProjectManagerAgent", calls[0].agent)
        self.assertEqual("design", calls[0].phase)
        self.assertEqual("DES-R02", calls[0].revision)
        self.assertEqual("APPLIED", result["status"])

    def test_step_apply_stops_after_first_atomic_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            task_dir, _ = self._start_task(Path(temp) / "project_doc")
            payload = {
                "status": "READY", "taskDir": str(task_dir), "planVersion": 1,
                "planDigest": "sha256:" + "2" * 64,
                "steps": [
                    {"permissionCommandId": "long_task.finalize-phase", "autoApplyAllowed": True},
                    {"permissionCommandId": "long_task.advance-phase", "autoApplyAllowed": True},
                ],
                "applySteps": ["long_task.finalize-phase", "long_task.advance-phase"],
            }
            calls = []
            finalize = mock.patch.object(wk.long_task, "command_finalize_phase", side_effect=lambda args: calls.append("finalize") or 7)
            advance = mock.patch.object(wk.long_task, "command_advance_phase", side_effect=lambda args: calls.append("advance") or 0)
            rc, result = self._step_apply_with_payload(task_dir, payload, finalize, advance)
        self.assertEqual(2, rc)
        self.assertEqual(["finalize"], calls)
        self.assertEqual("PARTIAL", result["status"])
        self.assertEqual([], result["completedSteps"])
        self.assertEqual("long_task.finalize-phase", result["failedStep"]["permissionCommandId"])
        self.assertEqual("STEP_ATOMIC_COMMAND_FAILED", result["failedStep"]["error"]["code"])
        self.assertRegex(result["resumeToken"], r"^sha256:[0-9a-f]{64}$")
        self.assertTrue(result["repairCommand"])

    def test_step_apply_rejects_non_whitelist_action_without_invoking_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            task_dir, _ = self._start_task(Path(temp) / "project_doc")
            payload = {
                "status": "READY", "taskDir": str(task_dir), "planVersion": 1,
                "planDigest": "sha256:" + "3" * 64,
                "steps": [{"permissionCommandId": "shell.exec", "autoApplyAllowed": False}],
                "applySteps": ["shell.exec"],
            }
            forbidden_mock = mock.Mock()
            forbidden = mock.patch.object(wk, "command_reconcile", forbidden_mock)
            rc, result = self._step_apply_with_payload(task_dir, payload, forbidden)
        self.assertEqual(2, rc)
        self.assertEqual("PARTIAL", result["status"])
        self.assertEqual("STEP_ACTION_NOT_ALLOWED", result["failedStep"]["error"]["code"])
        forbidden_mock.assert_not_called()

    def test_step_apply_treats_atomic_no_safe_mutation_as_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            task_dir, _ = self._start_task(Path(temp) / "project_doc")
            payload = {
                "status": "READY", "taskDir": str(task_dir), "planVersion": 1,
                "planDigest": "sha256:" + "4" * 64,
                "steps": [{"permissionCommandId": "wk.reconcile", "autoApplyAllowed": True}],
                "applySteps": ["wk.reconcile"],
            }
            reconcile = mock.patch.object(wk, "command_reconcile", side_effect=lambda args: print(json.dumps({"status": "NO_SAFE_MUTATION"})) or 0)
            rc, result = self._step_apply_with_payload(task_dir, payload, reconcile)
        self.assertEqual(2, rc)
        self.assertEqual("PARTIAL", result["status"])
        self.assertEqual("STEP_ATOMIC_COMMAND_FAILED", result["failedStep"]["error"]["code"])
        self.assertFalse(result["mutationsApplied"])

    def test_step_preview_rejects_unauthorized_agent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            task_dir, _ = self._start_task(Path(temp) / "project_doc")
            output = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(output):
                rc = wk.main([
                    "step", "-g", "DevelopAgent", "--task-dir", str(task_dir),
                    "--preview", "--json",
                ])
        self.assertEqual(1, rc)
        self.assertIn("not authorized for wk.step", output.getvalue())


if __name__ == "__main__":
    unittest.main()
