"""Regression tests for the current contract evidence, acceptance, and risk machine gates."""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import long_task


class MachineVerificationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.doc_root = Path(self.tmp.name) / "project_doc"
        with contextlib.redirect_stdout(io.StringIO()):
            long_task.command_init(argparse.Namespace(
                doc_root=str(self.doc_root), version="V_TEST", target="TARGET-VERIFY",
                reuse=False, agent="ProjectManagerAgent",
            ))
        self.task_dir = self.doc_root / "version" / "V_TEST" / "task" / "TARGET-VERIFY"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def register_json(self, name: str, payload: dict, revision: str, phase: str, *, kind: str = "test_ref", agent: str = "TestAgent", scope: list[str] | None = None) -> dict:
        path = self.task_dir / "evidence" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
        return long_task.evidence.register_evidence(
            self.task_dir, agent=agent, evidence_type=kind,
            ref=str(path.relative_to(self.task_dir)), revision=revision, phase=phase,
            scope_refs=scope or [phase],
        )

    def test_evidence_reference_cannot_escape_project_root(self) -> None:
        outside = Path(self.tmp.name).parent / "common-develop-outside-evidence.json"
        outside.write_text('{"status":"PASSED"}', encoding="utf-8")
        try:
            with self.assertRaisesRegex(ValueError, "escapes project root"):
                long_task.evidence.register_evidence(
                    self.task_dir, agent="TestAgent", evidence_type="test_ref",
                    ref=str(outside), revision="TEST-R01", phase="testing", scope_refs=["CASE-OUTSIDE"],
                )
        finally:
            outside.unlink(missing_ok=True)

    def test_evidence_digest_tamper_is_rejected(self) -> None:
        entry = self.register_json("tamper.json", {"status": "PASSED"}, "TEST-R01", "testing")
        path = self.task_dir / entry["ref"]
        path.write_text('{"status":"FAILED"}', encoding="utf-8")
        errors, _ = long_task.evidence.validate_registry(self.task_dir)
        self.assertTrue(any("digest mismatch" in error for error in errors))

    def test_inline_command_result_avoids_separate_output_file(self) -> None:
        output = "5 passed\n"
        command_result = self.task_dir / "evidence" / "inline-command-result.json"
        command_result.parent.mkdir(parents=True, exist_ok=True)
        command_result.write_text(json.dumps({
            "schema_version": 2, "kind": "command_result", "command": "pytest -q",
            "exit_code": 0, "executed_at": "2026-07-22T10:00:00-05:00",
            "revision": "TEST-R01", "output": output,
            "output_digest": hashlib.sha256(output.encode("utf-8")).hexdigest(),
        }), encoding="utf-8")
        payload = long_task.command_result_payload(self.task_dir, "evidence/inline-command-result.json")
        self.assertEqual(output, payload["output"])
        self.assertNotIn("output_ref", payload)

    def test_command_result_file_tamper_is_rejected(self) -> None:
        output = self.task_dir / "evidence" / "command-output.log"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("passed", encoding="utf-8")
        command_result = self.task_dir / "evidence" / "command-result.json"
        command_result.write_text(json.dumps({
            "schema_version": 1, "kind": "command_result", "command": "pytest -q",
            "exit_code": 0, "executed_at": "2026-07-22T10:00:00-05:00",
            "revision": "TEST-R01", "output_ref": "evidence/command-output.log",
            "output_digest": long_task.sha256_file(output),
        }), encoding="utf-8")
        long_task.evidence.register_evidence(
            self.task_dir, agent="TestAgent", evidence_type="command_ref",
            ref="evidence/command-output.log", revision="TEST-R01", phase="testing",
            scope_refs=["pytest -q"], command_result_ref="evidence/command-result.json",
        )
        payload = json.loads(command_result.read_text(encoding="utf-8"))
        payload["exit_code"] = 1
        command_result.write_text(json.dumps(payload), encoding="utf-8")
        errors, _ = long_task.evidence.validate_registry(self.task_dir)
        self.assertTrue(any("command_result_digest mismatch" in error for error in errors))

    def test_superseded_evidence_cannot_be_used_as_current(self) -> None:
        old = self.register_json("old.json", {"value": 1}, "DES-R01", "design", kind="design_ref", agent="DesignAgent")
        new = self.register_json("new.json", {"value": 2}, "DES-R02", "design", kind="design_ref", agent="DesignAgent")
        long_task.evidence.change_status(
            self.task_dir, evidence_id=old["evidence_id"], status="SUPERSEDED",
            replacement_id=new["evidence_id"],
        )
        with self.assertRaisesRegex(ValueError, "evidence is not ACTIVE"):
            long_task.evidence.require_evidence(self.task_dir, old["evidence_id"])

    def test_review_criterion_requires_current_registered_evidence(self) -> None:
        entry = self.register_json("review.json", {"ok": True}, "DES-R01", "design", kind="design_ref", agent="ArchitectureReviewAgent")
        errors: list[str] = []
        value = [{"evidence_id": entry["evidence_id"], "claim": "design verified"}]
        verified = long_task.validate_review_evidence(
            self.task_dir, value, "criterion.evidence", errors, expected_revision="DES-R02"
        )
        self.assertEqual([], verified)
        self.assertTrue(any("evidence revision mismatch" in error for error in errors))

    def test_review_criterion_evidence_must_cover_criterion_scope(self) -> None:
        entry = self.register_json(
            "scope.json", {"ok": True}, "DES-R01", "design",
            kind="design_ref", agent="ArchitectureReviewAgent", scope=["OTHER-CRITERION"],
        )
        conclusion = {
            "profile_id": "design:ArchitectureReviewAgent", "revision": "DES-R01",
            "conclusion": "PASSED", "checked_scope": ["design/current"],
            "criteria_results": [], "finding_ids": [], "limitations": [],
            "reviewed_at": "2026-07-22T10:00:00-05:00",
        }
        profile = long_task.review_profile("design", "ArchitectureReviewAgent")
        for criterion_id in profile["criterionIds"]:
            evidence_items = []
            if not conclusion["criteria_results"]:
                evidence_items = [{"evidence_id": entry["evidence_id"], "claim": "wrong scope"}]
            else:
                evidence_items = self.register_review_evidence(criterion_id, "DES-R01")
            conclusion["criteria_results"].append({
                "criterion_id": criterion_id, "status": "PASSED",
                "evidence": evidence_items, "finding_ids": [], "reason": "",
            })
        errors: list[str] = []
        long_task.validate_independent_conclusion(
            self.task_dir, "design", "ArchitectureReviewAgent", conclusion, "DES-R01", [], errors,
        )
        self.assertTrue(any("does not cover criterion" in error for error in errors))

    def register_review_evidence(self, criterion_id: str, revision: str) -> list[dict]:
        criterion = long_task.REVIEW_CRITERIA[criterion_id]
        policy = criterion["evidencePolicy"]
        types = list(policy.get("allOfTypes", []))
        any_types = list(policy.get("anyOfTypes", []))
        if any_types and not any(value in types for value in any_types):
            types.append(any_types[0])
        while len(types) < policy.get("minimumEvidenceCount", 1):
            types.append(any_types[0] if any_types else "document_ref")
        result = []
        for index, evidence_type in enumerate(types, 1):
            entry = self.register_json(
                f"review-{criterion_id}-{index}.json", {"ok": True}, revision, "design",
                kind=evidence_type, agent="ArchitectureReviewAgent", scope=[criterion_id],
            )
            result.append({"evidence_id": entry["evidence_id"], "claim": "covered"})
        return result

    def test_test_case_assertion_is_deterministically_verified(self) -> None:
        entry = self.register_json(
            "case.json", {"status": "PASSED"}, "TEST-R01", "testing",
            scope=["CASE-001"],
        )
        item = long_task.acceptance.add_assertion(
            self.task_dir, assertion_id="ASRT-001", acceptance_id="AC-001",
            statement="case passes", assertion_type="TEST_CASE", phase="testing",
            revision="TEST-R01", blocking=True,
            parameters={"test_case_id": "CASE-001", "expected_status": "PASSED"},
        )
        item["evidence_ids"] = [entry["evidence_id"]]
        status, reason = long_task.acceptance.verify_assertion(self.task_dir, item, "TestAgent")
        self.assertEqual("VERIFIED", status)
        self.assertEqual("", reason)

    def test_add_assertion_rejects_invalid_parameters_immediately(self) -> None:
        with self.assertRaisesRegex(ValueError, "test_case_id"):
            long_task.acceptance.add_assertion(
                self.task_dir, assertion_id="ASRT-INVALID", acceptance_id="AC-INVALID",
                statement="invalid test assertion", assertion_type="TEST_CASE", phase="testing",
                revision="TEST-R01", blocking=True, parameters={"expected_status": "PASSED"},
                source_refs=["TASK-TEST-001#expected_results/0"],
            )

    def test_ensure_review_assertions_is_idempotent_and_revision_scoped(self) -> None:
        first = long_task.acceptance.ensure_review_assertions(
            self.task_dir,
            phase="design",
            revision="DES-R01",
            reviewers=["ArchitectureReviewAgent"],
            acceptance_ids=["AC-DES-001", "AC-DES-002"],
            source_refs=["TR-DES-001", "TASK-DES-001#expected_results/0"],
        )
        second = long_task.acceptance.ensure_review_assertions(
            self.task_dir,
            phase="design",
            revision="DES-R01",
            reviewers=["ArchitectureReviewAgent"],
            acceptance_ids=["AC-DES-001", "AC-DES-002"],
            source_refs=["TR-DES-001", "TASK-DES-001#expected_results/0"],
        )
        next_revision = long_task.acceptance.ensure_review_assertions(
            self.task_dir,
            phase="design",
            revision="DES-R02",
            reviewers=["ArchitectureReviewAgent"],
            acceptance_ids=["AC-DES-001", "AC-DES-002"],
            source_refs=["TR-DES-001", "TASK-DES-001#expected_results/0"],
        )
        self.assertEqual("CREATED", first["status"])
        self.assertEqual("ALREADY_PRESENT", second["status"])
        self.assertEqual(first["createdAssertionIds"], second["existingAssertionIds"])
        self.assertNotEqual(first["createdAssertionIds"], next_revision["createdAssertionIds"])
        assertions = long_task.acceptance.load_document(self.task_dir)["assertions"]
        self.assertEqual(4, len(assertions))
        self.assertEqual(
            {"design:ArchitectureReviewAgent"},
            {item["parameters"]["profile_id"] for item in assertions},
        )
        self.assertEqual(
            2,
            len({item["parameters"]["review_binding_id"] for item in assertions}),
        )

    def test_ensure_review_assertions_does_not_read_legacy_file(self) -> None:
        (self.task_dir / "acceptance_assertions.json").write_text(json.dumps({
            "schema_version": 1,
            "target_id": "TARGET-VERIFY",
            "version": "V_TEST",
            "assertions": [{"assertion_id": "LEGACY-ONLY"}],
        }), encoding="utf-8")
        result = long_task.acceptance.ensure_review_assertions(
            self.task_dir,
            phase="design",
            revision="DES-R01",
            reviewers=["ArchitectureReviewAgent"],
            acceptance_ids=["AC-DES-001"],
            source_refs=["TR-DES-001"],
        )
        self.assertEqual("CREATED", result["status"])
        canonical = long_task.acceptance.load_document(self.task_dir)
        self.assertNotIn("LEGACY-ONLY", {item["assertion_id"] for item in canonical["assertions"]})

    def test_test_case_assertion_rejects_non_test_evidence_type(self) -> None:
        entry = self.register_json(
            "wrong-test-type.json", {"status": "PASSED"}, "TEST-R01", "testing",
            kind="metric_ref", scope=["CASE-001"],
        )
        item = long_task.acceptance.add_assertion(
            self.task_dir, assertion_id="ASRT-WRONG-TYPE", acceptance_id="AC-WRONG-TYPE",
            statement="case passes", assertion_type="TEST_CASE", phase="testing",
            revision="TEST-R01", blocking=True,
            parameters={"test_case_id": "CASE-001", "expected_status": "PASSED"},
        )
        item["evidence_ids"] = [entry["evidence_id"]]
        with self.assertRaisesRegex(ValueError, "TEST_CASE cannot use evidence type metric_ref"):
            long_task.acceptance.verify_assertion(self.task_dir, item, "TestAgent")

    def test_sql_value_assertion_requires_query_scope(self) -> None:
        entry = self.register_json(
            "sql-value.json", {"value": 1}, "TEST-R01", "testing",
            kind="runtime_ref", scope=["queries/other.sql"],
        )
        item = long_task.acceptance.add_assertion(
            self.task_dir, assertion_id="ASRT-SQL-SCOPE", acceptance_id="AC-SQL-SCOPE",
            statement="refund row exists", assertion_type="SQL_VALUE", phase="testing",
            revision="TEST-R01", blocking=True,
            parameters={"query_ref": "queries/refund-count.sql", "operator": "EQUALS", "expected": 1},
        )
        item["evidence_ids"] = [entry["evidence_id"]]
        with self.assertRaisesRegex(ValueError, "scope does not contain query_ref"):
            long_task.acceptance.verify_assertion(self.task_dir, item, "TestAgent")

    def test_metric_threshold_requires_metric_evidence_and_scope(self) -> None:
        entry = self.register_json(
            "metric.json", {"p95_latency_ms": 150}, "TEST-R01", "testing",
            kind="metric_ref", scope=["p95_latency_ms"],
        )
        item = long_task.acceptance.add_assertion(
            self.task_dir, assertion_id="ASRT-METRIC", acceptance_id="AC-METRIC",
            statement="latency is within target", assertion_type="METRIC_THRESHOLD", phase="testing",
            revision="TEST-R01", blocking=True,
            parameters={"metric": "p95_latency_ms", "operator": "LESS_THAN_OR_EQUAL", "expected": 200},
        )
        item["evidence_ids"] = [entry["evidence_id"]]
        status, reason = long_task.acceptance.verify_assertion(self.task_dir, item, "PerformanceReviewAgent")
        self.assertEqual("VERIFIED", status)
        self.assertEqual("", reason)

    def test_command_exit_requires_command_evidence_with_result_binding(self) -> None:
        entry = self.register_json(
            "command-evidence.json", {"exit_code": 0}, "TEST-R01", "testing",
            kind="command_ref", scope=["CMD-001"],
        )
        item = long_task.acceptance.add_assertion(
            self.task_dir, assertion_id="ASRT-CMD", acceptance_id="AC-CMD",
            statement="validation command succeeds", assertion_type="COMMAND_EXIT", phase="testing",
            revision="TEST-R01", blocking=True, parameters={"operator": "EQUALS", "expected": 0},
        )
        item["evidence_ids"] = [entry["evidence_id"]]
        with self.assertRaisesRegex(ValueError, "must bind command_result_ref"):
            long_task.acceptance.verify_assertion(self.task_dir, item, "TestAgent")

    def test_unresolved_acceptance_blocks_completion(self) -> None:
        document = long_task.acceptance.load_document(self.task_dir)
        document["assertions"] = [{
            "assertion_id": "ASRT-001", "acceptance_id": "AC-001",
            "statement": "business expectation", "type": "MANUAL_REVIEW",
            "phase": "completion_verification", "revision": "COMP-R01", "blocking": True,
            "status": "UNRESOLVED", "parameters": {"reviewer_agent": "RequirementReviewAgent"},
            "source_refs": ["acceptance:AC-001"],
            "evidence_ids": [], "verified_by_agent": "", "verified_at": "",
            "reason": "business threshold is undefined",
        }]
        long_task.acceptance.save_document(self.task_dir, document)
        errors: list[str] = []
        long_task.validate_acceptance_contract(
            self.task_dir, {"ASRT-001": document["assertions"][0]},
            [{"status": "COVERED", "acceptance_criteria": ["AC-001"]}],
            {"completion_verification": {"revision": "COMP-R01"}}, {}, [],
            completion_required=True, errors=errors,
        )
        self.assertTrue(any("unverified blocking assertions" in error for error in errors))
        self.assertTrue(any("unresolved acceptance assertions" in error for error in errors))

    def test_acceptance_criterion_cannot_use_nonblocking_placeholder(self) -> None:
        assertion = {
            "assertion_id": "ASRT-AC-NONBLOCKING", "acceptance_id": "AC-001",
            "statement": "business expectation", "type": "TEST_CASE",
            "phase": "testing", "revision": "TEST-R01", "blocking": False,
            "status": "PENDING", "parameters": {"test_case_id": "CASE-001", "expected_status": "PASSED"},
            "source_refs": ["requirement.md#AC-001"], "evidence_ids": [],
            "verified_by_agent": "", "verified_at": "", "reason": "",
        }
        errors: list[str] = []
        long_task.validate_acceptance_contract(
            self.task_dir, {"ASRT-AC-NONBLOCKING": assertion},
            [{"status": "COVERED", "acceptance_criteria": ["AC-001"]}],
            {"testing": {"revision": "TEST-R01"}, "completion_verification": {"revision": "COMP-R01"}},
            {}, [], completion_required=True, errors=errors,
        )
        self.assertTrue(any("acceptance criterion has no blocking assertion" in error for error in errors))

    def test_task_expected_result_requires_stable_assertion_source_ref(self) -> None:
        assertion = {
            "assertion_id": "ASRT-001", "acceptance_id": "AC-001",
            "statement": "unit test passes", "type": "MANUAL_REVIEW",
            "phase": "completion_verification", "revision": "COMP-R01", "blocking": True,
            "status": "UNRESOLVED", "parameters": {"reviewer_agent": "RequirementReviewAgent"},
            "source_refs": ["acceptance:AC-001"], "evidence_ids": [],
            "verified_by_agent": "", "verified_at": "", "reason": "not compiled",
        }
        task = {
            "id": "TASK-DEV-001", "phase": "development", "status": "PASSED",
            "iteration_id": "ITER-DEVELOPMENT-001", "expected_results": ["unit test passes"],
        }
        errors: list[str] = []
        long_task.validate_acceptance_contract(
            self.task_dir, {"ASRT-001": assertion},
            [{"status": "COVERED", "acceptance_criteria": ["AC-001"]}],
            {
                "development": {"revision": "DEV-R01", "iteration_id": "ITER-DEVELOPMENT-001"},
                "completion_verification": {"revision": "COMP-R01"},
            }, {}, [task], completion_required=True, errors=errors,
        )
        self.assertTrue(any("expected_results without structured assertions" in error for error in errors))

    def test_task_expected_result_is_satisfied_by_exact_zero_based_source_ref(self) -> None:
        entry = self.register_json(
            "expected-result.json", {"status": "PASSED"}, "TEST-R01", "testing",
            kind="test_ref", scope=["CASE-001"],
        )
        assertion = {
            "assertion_id": "ASRT-001", "acceptance_id": "AC-001",
            "statement": "unit test passes", "type": "TEST_CASE",
            "phase": "testing", "revision": "TEST-R01", "blocking": True,
            "status": "VERIFIED", "parameters": {"test_case_id": "CASE-001", "expected_status": "PASSED"},
            "source_refs": ["TASK-DEV-001#expected_results/0"], "evidence_ids": [entry["evidence_id"]],
            "verified_by_agent": "TestAgent", "verified_at": "2026-07-22T10:00:00-05:00", "reason": "",
        }
        task = {
            "id": "TASK-DEV-001", "phase": "development", "status": "PASSED",
            "iteration_id": "ITER-DEVELOPMENT-001", "expected_results": ["unit test passes"],
        }
        errors: list[str] = []
        long_task.validate_acceptance_contract(
            self.task_dir, {"ASRT-001": assertion}, [],
            {
                "development": {"revision": "DEV-R01", "iteration_id": "ITER-DEVELOPMENT-001"},
                "testing": {"revision": "TEST-R01"},
                "completion_verification": {"revision": "COMP-R01"},
            }, {}, [task], completion_required=True, errors=errors,
        )
        self.assertFalse(any("expected_results without structured assertions" in error for error in errors))
        self.assertFalse(any("expected_result source" in error for error in errors))

    def test_task_expected_result_cannot_use_nonblocking_placeholder(self) -> None:
        assertion = {
            "assertion_id": "ASRT-NONBLOCKING", "acceptance_id": "AC-NONBLOCKING",
            "statement": "unit test passes", "type": "TEST_CASE",
            "phase": "testing", "revision": "TEST-R01", "blocking": False,
            "status": "PENDING", "parameters": {"test_case_id": "CASE-001", "expected_status": "PASSED"},
            "source_refs": ["TASK-DEV-001#expected_results/0"], "evidence_ids": [],
            "verified_by_agent": "", "verified_at": "", "reason": "",
        }
        task = {
            "id": "TASK-DEV-001", "phase": "development", "status": "PASSED",
            "iteration_id": "ITER-DEVELOPMENT-001", "expected_results": ["unit test passes"],
        }
        errors: list[str] = []
        long_task.validate_acceptance_contract(
            self.task_dir, {"ASRT-NONBLOCKING": assertion}, [],
            {
                "development": {"revision": "DEV-R01", "iteration_id": "ITER-DEVELOPMENT-001"},
                "testing": {"revision": "TEST-R01"},
                "completion_verification": {"revision": "COMP-R01"},
            }, {}, [task], completion_required=True, errors=errors,
        )
        self.assertTrue(any("no blocking assertion" in error for error in errors))

    def test_manual_risk_requires_development_phase_evidence(self) -> None:
        source = self.task_dir / "src" / "service.py"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("def process(): return True", encoding="utf-8")
        long_task.risk_detect.scan(
            self.task_dir, agent="ProjectManagerAgent", revision="DEV-R01",
            refs=[str(source.relative_to(self.task_dir))],
        )
        entry = self.register_json(
            "wrong-phase-risk.json", {"risk": "race"}, "DEV-R01", "testing",
            kind="design_ref", agent="ProjectManagerAgent",
        )
        with self.assertRaisesRegex(ValueError, "evidence phase mismatch"):
            long_task.risk_detect.add_manual_detection(
                self.task_dir, agent="ProjectManagerAgent", risk_type="concurrency", confidence=8,
                reason="distributed race", evidence_ids=[entry["evidence_id"]],
            )

    def test_manual_review_can_bind_a_different_review_phase_revision(self) -> None:
        entry = self.register_json(
            "manual-positive.json", {"decision": "accepted"}, "COMP-R01", "completion_verification",
            kind="decision_ref", agent="RequirementReviewAgent",
        )
        assertion = {
            "assertion_id": "ASRT-MANUAL-POS", "acceptance_id": "AC-MANUAL-POS",
            "statement": "business responsibility is accepted", "type": "MANUAL_REVIEW",
            "phase": "completion_verification", "revision": "COMP-R01", "blocking": True,
            "status": "VERIFIED",
            "parameters": {"reviewer_agent": "RequirementReviewAgent", "review_phase": "requirement_confirmation"},
            "source_refs": ["acceptance:AC-MANUAL-POS"],
            "evidence_ids": [entry["evidence_id"]], "verified_by_agent": "RequirementReviewAgent",
            "verified_at": "2026-07-22T10:00:00-05:00", "reason": "reviewed",
        }
        reviews = {
            "requirement_confirmation": {
                "independent_conclusions": {
                    "RequirementReviewAgent": {"conclusion": "PASSED", "revision": "REQ-R01"}
                }
            }
        }
        artifacts = {
            "requirement_confirmation": {"revision": "REQ-R01"},
            "completion_verification": {"revision": "COMP-R01"},
        }
        errors: list[str] = []
        long_task.validate_acceptance_contract(
            self.task_dir, {"ASRT-MANUAL-POS": assertion},
            [{"status": "COVERED", "acceptance_criteria": ["AC-MANUAL-POS"]}],
            artifacts, reviews, [], completion_required=True, errors=errors,
        )
        self.assertEqual([], errors)

    def test_manual_review_requires_current_passed_conclusion(self) -> None:
        entry = self.register_json(
            "manual.json", {"decision": "accepted"}, "COMP-R01", "completion_verification",
            kind="decision_ref", agent="RequirementReviewAgent",
        )
        assertion = {
            "assertion_id": "ASRT-MANUAL", "acceptance_id": "AC-MANUAL",
            "statement": "business responsibility is acceptable", "type": "MANUAL_REVIEW",
            "phase": "completion_verification", "revision": "COMP-R01", "blocking": True,
            "status": "VERIFIED",
            "parameters": {"reviewer_agent": "RequirementReviewAgent", "review_phase": "requirement_confirmation"},
            "source_refs": ["acceptance:AC-MANUAL"],
            "evidence_ids": [entry["evidence_id"]], "verified_by_agent": "RequirementReviewAgent",
            "verified_at": "2026-07-22T10:00:00-05:00", "reason": "reviewed",
        }
        errors: list[str] = []
        long_task.validate_acceptance_contract(
            self.task_dir, {"ASRT-MANUAL": assertion},
            [{"status": "COVERED", "acceptance_criteria": ["AC-MANUAL"]}],
            {"completion_verification": {"revision": "COMP-R01"}}, {}, [],
            completion_required=True, errors=errors,
        )
        self.assertTrue(any("lacks current PASSED conclusion" in error for error in errors))

    def test_risk_detector_finds_security_and_detects_stale_input(self) -> None:
        source = self.task_dir / "src" / "auth" / "TokenService.py"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("authorize token permission", encoding="utf-8")
        report = long_task.risk_detect.scan(
            self.task_dir, agent="ProjectManagerAgent", revision="DEV-R01",
            refs=[str(source.relative_to(self.task_dir))],
        )
        self.assertIn("security", long_task.risk_detect.active_risk_types(report))
        source.write_text("authorize token permission changed", encoding="utf-8")
        errors, _ = long_task.risk_detect.validate_report(self.task_dir)
        self.assertIn("risk detection input_digest is stale", errors)

    def test_high_confidence_waiver_requires_specialist_and_project_manager(self) -> None:
        source = self.task_dir / "src" / "auth" / "TokenService.py"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("authorize token permission", encoding="utf-8")
        report = long_task.risk_detect.scan(
            self.task_dir, agent="ProjectManagerAgent", revision="DEV-R01",
            refs=[str(source.relative_to(self.task_dir))],
        )
        waiver_entry = self.register_json(
            "waiver.json", {"reason": "false positive"}, "DEV-R01", "development",
            kind="decision_ref", agent="ProjectManagerAgent",
        )
        detection = next(item for item in report["detections"] if item["risk_type"] == "security")
        report["waivers"] = [{
            "detection_id": detection["detection_id"], "reason": "false positive",
            "approved_by": ["ProjectManagerAgent"],
            "evidence_ids": [waiver_entry["evidence_id"]],
            "approved_at": "2026-07-22T10:00:00-05:00",
        }]
        long_task.risk_detect.save_report(self.task_dir, report)
        errors, _ = long_task.risk_detect.validate_report(self.task_dir)
        self.assertTrue(any("requires approvals" in error for error in errors))

    def test_risk_detection_id_and_waiver_survive_rescan(self) -> None:
        source = self.task_dir / "src" / "auth" / "TokenService.py"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("authorize token permission", encoding="utf-8")
        first = long_task.risk_detect.scan(
            self.task_dir, agent="ProjectManagerAgent", revision="DEV-R01",
            refs=[str(source.relative_to(self.task_dir))],
        )
        detection = next(item for item in first["detections"] if item["risk_type"] == "security")
        manager_waiver = self.register_json(
            "waiver-manager.json", {"reason": "generated fixture"}, "DEV-R01", "development",
            kind="decision_ref", agent="ProjectManagerAgent", scope=[detection["detection_id"]],
        )
        reviewer_waiver = self.register_json(
            "waiver-reviewer.json", {"reason": "reviewed false positive"}, "DEV-R01", "development",
            kind="decision_ref", agent="SecurityReviewAgent", scope=[detection["detection_id"]],
        )
        long_task.risk_detect.waive_detection(
            self.task_dir, detection_id=detection["detection_id"], reason="fixture false positive",
            approved_by=["SecurityReviewAgent", "ProjectManagerAgent"],
            evidence_ids=[manager_waiver["evidence_id"], reviewer_waiver["evidence_id"]],
        )
        second = long_task.risk_detect.scan(
            self.task_dir, agent="ProjectManagerAgent", revision="DEV-R01",
            refs=[str(source.relative_to(self.task_dir))],
        )
        rescanned = next(item for item in second["detections"] if item["risk_type"] == "security")
        self.assertEqual(detection["detection_id"], rescanned["detection_id"])
        self.assertEqual("WAIVED", rescanned["status"])
        self.assertNotIn("security", long_task.risk_detect.active_risk_types(second))
        errors, _ = long_task.risk_detect.validate_report(self.task_dir)
        self.assertEqual([], errors)

    def test_waiver_cannot_forge_specialist_approval_without_specialist_evidence(self) -> None:
        source = self.task_dir / "src" / "auth" / "TokenService.py"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("authorize token permission", encoding="utf-8")
        report = long_task.risk_detect.scan(
            self.task_dir, agent="ProjectManagerAgent", revision="DEV-R01",
            refs=[str(source.relative_to(self.task_dir))],
        )
        detection = next(item for item in report["detections"] if item["risk_type"] == "security")
        manager_waiver = self.register_json(
            "forged-waiver.json", {"reason": "claimed approval"}, "DEV-R01", "development",
            kind="decision_ref", agent="ProjectManagerAgent", scope=[detection["detection_id"]],
        )
        with self.assertRaisesRegex(ValueError, "lacks decision evidence from approvers"):
            long_task.risk_detect.waive_detection(
                self.task_dir, detection_id=detection["detection_id"], reason="false positive",
                approved_by=["SecurityReviewAgent", "ProjectManagerAgent"],
                evidence_ids=[manager_waiver["evidence_id"]],
            )

    def test_agent_can_add_semantic_risk_after_machine_scan(self) -> None:
        source = self.task_dir / "src" / "service.py"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("def process(): return True", encoding="utf-8")
        long_task.risk_detect.scan(
            self.task_dir, agent="ProjectManagerAgent", revision="DEV-R01",
            refs=[str(source.relative_to(self.task_dir))],
        )
        entry = self.register_json(
            "semantic-risk.json", {"risk": "distributed race"}, "DEV-R01", "development",
            kind="design_ref", agent="ProjectManagerAgent",
        )
        detection = long_task.risk_detect.add_manual_detection(
            self.task_dir, agent="ProjectManagerAgent", risk_type="concurrency", confidence=8,
            reason="distributed consumers may race", evidence_ids=[entry["evidence_id"]],
        )
        self.assertEqual("AGENT", detection["source"])
        report = long_task.risk_detect.load_report(self.task_dir)
        self.assertIn("concurrency", long_task.risk_detect.active_risk_types(report))


if __name__ == "__main__":
    unittest.main()
