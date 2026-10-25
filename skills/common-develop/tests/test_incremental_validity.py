#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import io
import json
import shlex
import os
import tempfile
import unittest
from pathlib import Path

import impact_analysis
import review_contract
import wk
from _long_task import revision_provenance
from _long_task.amendment_validation import validate_bounded_impact_plan


class IncrementalValidityImpactTests(unittest.TestCase):
    def _start(self, root: Path) -> Path:
        args = [
            "start", "-g", "ProjectManagerAgent", "--doc-root", str(root), "--version", "V_TEST",
            "-f", "文案微调", "--classification", "simple-optimization", "--related-feature", "F01",
        ]
        for flag in (
            "business-logic", "data-model", "api-contract", "permission", "workflow",
            "persistence", "external-side-effect", "migration", "dependency",
        ):
            args += [f"--{flag}-impact", "NONE"]
        args.append("--apply")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(0, wk.main(args))
        payload = json.loads(out.getvalue()[out.getvalue().index("{"):])
        return Path(payload["taskDir"])

    @staticmethod
    def _development_profile() -> tuple[str, list[str]]:
        contract = review_contract.load_contract()
        profile_id = "development:TDDReviewAgent"
        return profile_id, list(contract["profiles"][profile_id]["criterionIds"])

    def test_process_only_requires_no_semantic_revalidation(self) -> None:
        profile_id, criteria = self._development_profile()
        plan = impact_analysis.build_plan(
            task_dir=Path("/tmp/task"), phase="development", base_revision="DEV-R09@abc",
            revision="DEV-R09@abc", change_class="PROCESS_ONLY", profile_ids=[profile_id],
        )
        self.assertEqual("PROCESS_ONLY_NO_SEMANTIC_REVALIDATION", plan["fallbackPolicy"])
        self.assertFalse(plan["revalidationRequired"])
        self.assertEqual([], plan["minimumRevalidation"]["reviews"])
        self.assertEqual(criteria, plan["unaffected"]["reviewCriteria"][0]["criterionIds"])
        self.assertFalse(plan["reusePolicy"]["reviewResultReuseAcrossExactRevision"])
        self.assertFalse(plan["reusePolicy"]["criterionEvidenceIdReuseAcrossExactRevision"])
        self.assertEqual("CONDITIONAL_RECAPTURE_OR_REBIND", plan["reusePolicy"]["unaffectedEvidenceSourceReuse"])

    def test_process_only_ignores_adapter_semantic_scope(self) -> None:
        profile_id, criteria = self._development_profile()
        plan = impact_analysis.build_plan(
            task_dir=Path("/tmp/task"), phase="development", base_revision="DEV-R09@abc",
            revision="DEV-R09@abc", change_class="PROCESS_ONLY", profile_ids=[profile_id],
            adapter_result={
                "schemaVersion": 1,
                "confidence": "HIGH",
                "affectedReviewCriteria": [criteria[0]],
                "requiredCapabilities": ["fastValidation"],
                "affectedScopes": ["storage-metadata"],
            },
        )
        self.assertEqual("PROCESS_ONLY_NO_SEMANTIC_REVALIDATION", plan["fallbackPolicy"])
        self.assertFalse(plan["revalidationRequired"])
        self.assertEqual([], plan["minimumRevalidation"]["reviews"])
        self.assertEqual([], plan["minimumRevalidation"]["validationCapabilities"])
        self.assertEqual(criteria, plan["unaffected"]["reviewCriteria"][0]["criterionIds"])
        self.assertFalse(plan["adapter"]["used"])
        self.assertEqual("NONE", plan["adapter"]["confidence"])
        self.assertEqual([], plan["adapter"]["affectedScopes"])

    def test_non_process_change_without_adapter_fails_conservatively_to_full_profile(self) -> None:
        profile_id, criteria = self._development_profile()
        plan = impact_analysis.build_plan(
            task_dir=Path("/tmp/task"), phase="development", base_revision="DEV-R09@abc",
            revision="DEV-R10@def", change_class="PRODUCTION", profile_ids=[profile_id],
        )
        self.assertEqual("CONSERVATIVE_FULL_PROFILE", plan["fallbackPolicy"])
        self.assertTrue(plan["revalidationRequired"])
        self.assertEqual(criteria, plan["minimumRevalidation"]["reviews"][0]["criterionIds"])
        self.assertIn("IMPACT_UNKNOWN_CONSERVATIVE", plan["reasonCodes"])

    def test_high_confidence_adapter_can_narrow_to_affected_criterion(self) -> None:
        profile_id, criteria = self._development_profile()
        affected = criteria[0]
        adapter = {
            "schemaVersion": 1,
            "baseRevision": "DEV-R09@abc",
            "revision": "DEV-R10@def",
            "confidence": "HIGH",
            "affectedReviewCriteria": [affected],
            "requiredCapabilities": ["fastValidation"],
        }
        plan = impact_analysis.build_plan(
            task_dir=Path("/tmp/task"), phase="development", base_revision="DEV-R09@abc",
            revision="DEV-R10@def", change_class="PRODUCTION", profile_ids=[profile_id],
            adapter_result=adapter,
        )
        self.assertEqual("ADAPTER_SCOPED", plan["fallbackPolicy"])
        self.assertEqual([affected], plan["minimumRevalidation"]["reviews"][0]["criterionIds"])
        self.assertEqual(criteria[1:], plan["unaffected"]["reviewCriteria"][0]["criterionIds"])
        self.assertEqual(["fastValidation"], plan["minimumRevalidation"]["validationCapabilities"])

    def test_low_confidence_adapter_cannot_narrow_review(self) -> None:
        profile_id, criteria = self._development_profile()
        adapter = {
            "schemaVersion": 1,
            "confidence": "LOW",
            "affectedReviewCriteria": [criteria[0]],
        }
        plan = impact_analysis.build_plan(
            task_dir=Path("/tmp/task"), phase="development", base_revision="DEV-R09@abc",
            revision="DEV-R10@def", change_class="PRODUCTION", profile_ids=[profile_id],
            adapter_result=adapter,
        )
        self.assertEqual("CONSERVATIVE_FULL_PROFILE", plan["fallbackPolicy"])
        self.assertEqual(criteria, plan["minimumRevalidation"]["reviews"][0]["criterionIds"])

    def test_adapter_must_bind_matching_revision(self) -> None:
        profile_id, _ = self._development_profile()
        adapter = {"schemaVersion": 1, "revision": "OTHER@123", "confidence": "HIGH"}
        with self.assertRaises(impact_analysis.ImpactAnalysisError):
            impact_analysis.build_plan(
                task_dir=Path("/tmp/task"), phase="development", base_revision="DEV-R09@abc",
                revision="DEV-R10@def", change_class="PRODUCTION", profile_ids=[profile_id],
                adapter_result=adapter,
            )

    def test_dynamic_risk_profile_is_supported_for_its_allowed_phase(self) -> None:
        profile_id = "design:ConcurrencyReviewAgent"
        profile = review_contract.resolve_profile(
            review_contract.load_contract(), "design", "ConcurrencyReviewAgent"
        )
        plan = impact_analysis.build_plan(
            task_dir=Path("/tmp/task"), phase="design", base_revision="DES-R01@abc",
            revision="DES-R02@def", change_class="PRODUCTION", profile_ids=[profile_id],
            adapter_result={
                "schemaVersion": 1,
                "confidence": "HIGH",
                "affectedProfiles": [profile_id],
            },
        )
        self.assertEqual(profile["criterionIds"], plan["minimumRevalidation"]["reviews"][0]["criterionIds"])
        self.assertEqual(profile_id, plan["minimumRevalidation"]["reviews"][0]["profileId"])

    def test_review_profile_from_another_phase_is_rejected(self) -> None:
        with self.assertRaisesRegex(impact_analysis.ImpactAnalysisError, "belongs to phase development"):
            impact_analysis.build_plan(
                task_dir=Path("/tmp/task"), phase="requirement_analysis",
                base_revision="ANL-R01@abc", revision="ANL-R02@def",
                change_class="PRODUCTION", profile_ids=["development:TDDReviewAgent"],
            )

    def test_graph_risk_materializes_dynamic_review_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            graph_path = Path(temp) / "graph.json"
            graph_path.write_text(json.dumps({
                "schemaVersion": 1,
                "authority": "DERIVED",
                "kind": "engineering_knowledge_graph",
                "nodes": [{
                    "id": "API-X", "type": "API", "name": "Public API",
                    "sourceRefs": ["api.md"], "contextRef": "BC-A",
                }],
                "relationships": [],
            }), encoding="utf-8")
            plan = impact_analysis.build_plan(
                task_dir=Path("/tmp/task"), phase="design", base_revision="DES-R01@abc",
                revision="DES-R02@def", change_class="PRODUCTION", graph_path=graph_path,
                changed_refs=["API-X"],
            )
        self.assertIn("api_contract", plan["requiredRiskTemplates"])
        dynamic_ids = {
            item["profileId"] for item in plan["minimumRevalidation"]["reviews"]
        }
        self.assertIn("design:ApiContractReviewAgent", dynamic_ids)
        self.assertIn("design:ImpactAnalysisReviewAgent", dynamic_ids)
        self.assertEqual("GRAPH_SCOPED", plan["fallbackPolicy"])

    def test_wk_impact_blocks_graph_risk_not_allowed_for_phase(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            task_dir = self._start(root / "project_doc")
            graph_path = root / "graph.json"
            graph_path.write_text(json.dumps({
                "schemaVersion": 1,
                "authority": "DERIVED",
                "kind": "engineering_knowledge_graph",
                "nodes": [{
                    "id": "API-X", "type": "API", "name": "Public API",
                    "sourceRefs": ["api.md"], "contextRef": "BC-A",
                }],
                "relationships": [],
            }), encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = wk.main([
                    "impact", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir),
                    "--phase", "requirement_analysis", "--base-revision", "ANL-R01@base",
                    "--revision", "ANL-R02@next", "--change-class", "PRODUCTION",
                    "--graph", str(graph_path), "--changed-ref", "API-X",
                ])
            payload = json.loads(output.getvalue())
        self.assertEqual(2, exit_code)
        self.assertEqual("BLOCKED", payload["status"])
        self.assertIn("IMPACT_ANALYSIS_BLOCKED", payload["reasonCodes"])
        self.assertTrue(any("api_contract is not allowed" in item for item in payload["blockers"]))
        self.assertEqual("long_task.py reopen-phase", payload["fallbackCommand"])

    def test_unknown_impact_phase_is_blocked_and_supersedes_default_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            task_dir = self._start(root / "project_doc")
            default_plan = task_dir / "cache" / "current_impact.json"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(0, wk.main([
                    "impact", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir),
                    "--revision", "REQCONF-R02@old", "--change-class", "DOCUMENTATION_ONLY",
                ]))
            custom_output = root / "unknown-phase-impact.json"
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(2, wk.main([
                    "impact", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir),
                    "--phase", "NOT_A_PHASE", "--revision", "REQCONF-R03@invalid-phase",
                    "--change-class", "PRODUCTION", "--output", str(custom_output),
                ]))
            payload = json.loads(output.getvalue())
            self.assertEqual("BLOCKED", payload["status"])
            self.assertTrue(any("unknown impact-analysis phase" in item for item in payload["blockers"]), payload)
            self.assertEqual("BLOCKED", json.loads(custom_output.read_text(encoding="utf-8"))["status"])
            current = json.loads(default_plan.read_text(encoding="utf-8"))
            self.assertEqual("BLOCKED", current["status"])
            self.assertEqual("NOT_A_PHASE", current["phase"])
            self.assertEqual("REQCONF-R03@invalid-phase", current["provenance"]["revision"])
            manifest = json.loads((task_dir / "cache" / "current_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual("BLOCKED", manifest["derivedImpact"]["status"])
            self.assertEqual("REQCONF-R03@invalid-phase", manifest["derivedImpact"]["revision"])

    def test_impact_api_rejects_unknown_phase(self) -> None:
        with self.assertRaisesRegex(impact_analysis.ImpactAnalysisError, "unknown impact-analysis phase"):
            impact_analysis.build_plan(
                task_dir=Path("/tmp/task"), phase="NOT_A_PHASE", base_revision="R1",
                revision="R2", change_class="PRODUCTION",
            )

    def test_failed_impact_supersedes_fresh_default_cache_and_next_uses_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            task_dir = self._start(root / "project_doc")
            default_plan = task_dir / "cache" / "current_impact.json"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(0, wk.main([
                    "impact", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir),
                    "--revision", "REQCONF-R02@old", "--change-class", "DOCUMENTATION_ONLY",
                ]))
            old_plan = json.loads(default_plan.read_text(encoding="utf-8"))
            self.assertEqual("REQCONF-R02@old", old_plan["provenance"]["revision"])

            graph_path = root / "graph.json"
            graph_path.write_text(json.dumps({
                "schemaVersion": 1,
                "authority": "DERIVED",
                "kind": "engineering_knowledge_graph",
                "nodes": [{
                    "id": "API-X", "type": "API", "name": "Public API",
                    "sourceRefs": ["api.md"], "contextRef": "BC-A",
                }],
                "relationships": [],
            }), encoding="utf-8")
            custom_output = root / "blocked-impact.json"
            blocked_out = io.StringIO()
            with contextlib.redirect_stdout(blocked_out):
                self.assertEqual(2, wk.main([
                    "impact", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir),
                    "--revision", "REQCONF-R03@blocked", "--change-class", "PRODUCTION",
                    "--graph", str(graph_path), "--changed-ref", "API-X",
                    "--output", str(custom_output),
                ]))

            blocked_plan = json.loads(default_plan.read_text(encoding="utf-8"))
            self.assertEqual("BLOCKED", blocked_plan["status"])
            self.assertEqual("REQCONF-R03@blocked", blocked_plan["provenance"]["revision"])
            self.assertEqual("BLOCKED", json.loads(custom_output.read_text(encoding="utf-8"))["status"])
            manifest = json.loads((task_dir / "cache" / "current_manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["derivedImpact"]["fresh"])
            self.assertEqual("BLOCKED", manifest["derivedImpact"]["status"])
            self.assertEqual("REQCONF-R03@blocked", manifest["derivedImpact"]["revision"])

            next_out = io.StringIO()
            with contextlib.redirect_stdout(next_out):
                self.assertEqual(0, wk.main([
                    "next", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir), "--explain",
                ]))
            next_payload = json.loads(next_out.getvalue())
            self.assertEqual("BLOCKED", next_payload["decision"]["classification"])
            self.assertIn("IMPACT_ANALYSIS_BLOCKED", next_payload["decision"]["reasonCodes"])
            next_projection = next_payload["nextCommand"]
            self.assertIn("wk.impact", next_payload["decision"]["legalActions"])
            command_args = shlex.split(next_projection["command"])
            self.assertEqual(["python3", str(Path(wk.__file__).resolve()), "impact"], command_args[:3])
            self.assertIn("wk.impact", next_payload["allowedCommands"])
            self.assertEqual(next_projection["command"], next_payload["recommendedCommand"])
            self.assertEqual("ProjectManagerAgent", next_projection["requiredAgent"])
            self.assertEqual("CURRENT_SESSION_ALLOWED", next_projection["executionContext"])
            self.assertFalse(next_projection["readOnly"])
            # Regenerating derived impact data is routine repair, not scope authorization.
            self.assertFalse(next_projection["requiresConfirmation"])

    def test_custom_output_blocked_plan_supersedes_default_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            task_dir = self._start(root / "project_doc")
            default_plan = task_dir / "cache" / "current_impact.json"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(0, wk.main([
                    "impact", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir),
                    "--revision", "REQCONF-R02@old", "--change-class", "DOCUMENTATION_ONLY",
                ]))
            custom_output = root / "blocked-impact.json"
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(2, wk.main([
                    "impact", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir),
                    "--revision", "REQCONF-R03@blocked", "--change-class", "PRODUCTION",
                    "--required-capability", "missing-capability", "--project-root", str(root),
                    "--output", str(custom_output),
                ]))
            result = json.loads(output.getvalue())
            self.assertEqual("BLOCKED", result["status"])
            self.assertEqual("BLOCKED", json.loads(custom_output.read_text(encoding="utf-8"))["status"])
            current = json.loads(default_plan.read_text(encoding="utf-8"))
            self.assertEqual("BLOCKED", current["status"])
            self.assertEqual("REQCONF-R03@blocked", current["provenance"]["revision"])
            manifest = json.loads((task_dir / "cache" / "current_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual("BLOCKED", manifest["derivedImpact"]["status"])
            self.assertEqual("REQCONF-R03@blocked", manifest["derivedImpact"]["revision"])

    def test_custom_output_path_alias_keeps_manifest_fresh(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            task_dir = self._start(root / "project_doc")
            default_plan = task_dir / "cache" / "current_impact.json"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(0, wk.main([
                    "impact", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir),
                    "--revision", "REQCONF-R02@old", "--change-class", "DOCUMENTATION_ONLY",
                ]))
            aliased_output = task_dir / "cache" / ".." / "cache" / "current_impact.json"
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(2, wk.main([
                    "impact", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir),
                    "--revision", "REQCONF-R03@blocked", "--change-class", "PRODUCTION",
                    "--required-capability", "missing-capability", "--project-root", str(root),
                    "--output", str(aliased_output),
                ]))
            payload = json.loads(output.getvalue())
            self.assertEqual("BLOCKED", payload["status"])
            self.assertEqual("BLOCKED", json.loads(default_plan.read_text(encoding="utf-8"))["status"])
            manifest = json.loads((task_dir / "cache" / "current_manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["derivedImpact"]["fresh"])
            self.assertEqual("REQCONF-R03@blocked", manifest["derivedImpact"]["revision"])

    def test_custom_output_success_updates_default_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            task_dir = self._start(root / "project_doc")
            default_plan = task_dir / "cache" / "current_impact.json"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(0, wk.main([
                    "impact", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir),
                    "--revision", "REQCONF-R02@old", "--change-class", "DOCUMENTATION_ONLY",
                ]))
            custom_output = root / "custom-impact.json"
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(0, wk.main([
                    "impact", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir),
                    "--revision", "REQCONF-R03@custom", "--change-class", "DOCUMENTATION_ONLY",
                    "--output", str(custom_output),
                ]))
            payload = json.loads(output.getvalue())
            self.assertEqual("PASSED", payload["status"])
            self.assertEqual("REQCONF-R03@custom", payload["provenance"]["revision"])
            self.assertEqual("REQCONF-R03@custom", json.loads(default_plan.read_text(encoding="utf-8"))["provenance"]["revision"])
            self.assertEqual("REQCONF-R03@custom", json.loads(custom_output.read_text(encoding="utf-8"))["provenance"]["revision"])
            manifest = json.loads((task_dir / "cache" / "current_manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["derivedImpact"]["fresh"])
            self.assertEqual("REQCONF-R03@custom", manifest["derivedImpact"]["revision"])

    def test_output_write_failure_returns_blocked_and_invalidates_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            task_dir = self._start(root / "project_doc")
            default_plan = task_dir / "cache" / "current_impact.json"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(0, wk.main([
                    "impact", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir),
                    "--revision", "REQCONF-R02@old", "--change-class", "DOCUMENTATION_ONLY",
                ]))
            output_dir = root / "existing-directory"
            output_dir.mkdir()
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(2, wk.main([
                    "impact", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir),
                    "--revision", "REQCONF-R03@write-failed", "--change-class", "DOCUMENTATION_ONLY",
                    "--output", str(output_dir),
                ]))
            payload = json.loads(output.getvalue())
            self.assertEqual("BLOCKED", payload["status"])
            self.assertTrue(any("impact output write failed" in item for item in payload["blockers"]), payload)
            current = json.loads(default_plan.read_text(encoding="utf-8"))
            self.assertEqual("BLOCKED", current["status"])
            self.assertEqual("REQCONF-R03@write-failed", current["provenance"]["revision"])
            manifest = json.loads((task_dir / "cache" / "current_manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["derivedImpact"]["fresh"])
            self.assertEqual("REQCONF-R03@write-failed", manifest["derivedImpact"]["revision"])

    def test_output_symlink_loop_returns_structured_blocked_and_invalidates_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            task_dir = self._start(root / "project_doc")
            default_plan = task_dir / "cache" / "current_impact.json"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(0, wk.main([
                    "impact", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir),
                    "--revision", "REQCONF-R02@old", "--change-class", "DOCUMENTATION_ONLY",
                ]))
            loop_path = task_dir / "cache" / "impact-loop.json"
            os.symlink(loop_path.name, loop_path)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(2, wk.main([
                    "impact", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir),
                    "--revision", "REQCONF-R03@loop", "--change-class", "PRODUCTION",
                    "--output", str(loop_path),
                ]))
            payload = json.loads(output.getvalue())
            self.assertEqual("BLOCKED", payload["status"])
            self.assertTrue(any("symlink" in item.lower() or "symbolic link" in item.lower() for item in payload["blockers"]), payload)
            self.assertEqual("BLOCKED", json.loads(default_plan.read_text(encoding="utf-8"))["status"])
            manifest = json.loads((task_dir / "cache" / "current_manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["derivedImpact"]["fresh"])
            self.assertEqual("REQCONF-R03@loop", manifest["derivedImpact"]["revision"])

    def test_argparse_validates_impact_values_inside_cache_safe_command_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            task_dir = self._start(Path(temp) / "project_doc")
            default_plan = task_dir / "cache" / "current_impact.json"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(0, wk.main([
                    "impact", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir),
                    "--revision", "REQCONF-R02@old", "--change-class", "DOCUMENTATION_ONLY",
                ]))

            for revision, arguments, expected_message in (
                (
                    "REQCONF-R03@bad-class",
                    ["--change-class", "NO_SUCH_CLASS"],
                    "invalid change class",
                ),
                (
                    "REQCONF-R04@bad-tri",
                    ["--change-class", "PRODUCTION", "--semantic-artifact-changed", "NOT_A_TRI_STATE"],
                    "invalid tri-state value",
                ),
            ):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(2, wk.main([
                        "impact", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir),
                        "--revision", revision, *arguments,
                    ]))
                payload = json.loads(output.getvalue())
                self.assertEqual("BLOCKED", payload["status"])
                self.assertTrue(any(expected_message in item for item in payload["blockers"]), payload)
                current = json.loads(default_plan.read_text(encoding="utf-8"))
                self.assertEqual("BLOCKED", current["status"])
                self.assertEqual(revision, current["provenance"]["revision"])

    def test_missing_impact_required_values_supersede_stale_default_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            task_dir = self._start(Path(temp) / "project_doc")
            default_plan = task_dir / "cache" / "current_impact.json"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(0, wk.main([
                    "impact", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir),
                    "--revision", "REQCONF-R02@old", "--change-class", "DOCUMENTATION_ONLY",
                ]))

            for revision, arguments, expected_message in (
                (
                    "REQCONF-R03@missing-class",
                    ["--revision", "REQCONF-R03@missing-class"],
                    "change class is required",
                ),
                (
                    "",
                    ["--change-class", "PRODUCTION"],
                    "revision is required",
                ),
            ):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(2, wk.main([
                        "impact", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir),
                        *arguments,
                    ]))
                payload = json.loads(output.getvalue())
                self.assertEqual("BLOCKED", payload["status"])
                self.assertTrue(any(expected_message in item for item in payload["blockers"]), payload)
                current = json.loads(default_plan.read_text(encoding="utf-8"))
                self.assertEqual("BLOCKED", current["status"])
                self.assertEqual(revision, current["provenance"]["revision"])

    def test_invalid_impact_input_supersedes_stale_default_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            task_dir = self._start(Path(temp) / "project_doc")
            default_plan = task_dir / "cache" / "current_impact.json"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(0, wk.main([
                    "impact", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir),
                    "--revision", "REQCONF-R02@old", "--change-class", "DOCUMENTATION_ONLY",
                ]))
            self.assertEqual("PASSED", json.loads(default_plan.read_text(encoding="utf-8"))["status"])

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(2, wk.main([
                    "impact", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir),
                    "--revision", "REQCONF-R03@invalid", "--change-class", "PROCESS_ONLY",
                    "--semantic-artifact-changed", "TRUE",
                ]))
            blocked = json.loads(output.getvalue())
            self.assertEqual("BLOCKED", blocked["status"])
            self.assertTrue(any("PROCESS_ONLY" in item for item in blocked["blockers"]))

            current = json.loads(default_plan.read_text(encoding="utf-8"))
            self.assertEqual("BLOCKED", current["status"])
            self.assertEqual("REQCONF-R03@invalid", current["provenance"]["revision"])
            self.assertEqual("PROCESS_ONLY", current["provenance"]["changeClass"])
            self.assertFalse(current["provenance"]["processOnly"])
            manifest = json.loads((task_dir / "cache" / "current_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual("BLOCKED", manifest["derivedImpact"]["status"])
            self.assertEqual("REQCONF-R03@invalid", manifest["derivedImpact"]["revision"])

            next_output = io.StringIO()
            with contextlib.redirect_stdout(next_output):
                self.assertEqual(0, wk.main([
                    "next", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir), "--explain",
                ]))
            next_payload = json.loads(next_output.getvalue())
            self.assertEqual("BLOCKED", next_payload["decision"]["classification"])
            self.assertIn("IMPACT_ANALYSIS_BLOCKED", next_payload["decision"]["reasonCodes"])

    def test_unknown_and_wrong_phase_risk_templates_are_rejected_by_bounded_gate(self) -> None:
        profile_id, criteria = self._development_profile()
        plan = impact_analysis.build_plan(
            task_dir=Path("/tmp/task"), phase="development", base_revision="DEV-R09@abc",
            revision="DEV-R10@def", change_class="PRODUCTION", profile_ids=[profile_id],
            adapter_result={"schemaVersion": 1, "confidence": "HIGH", "affectedReviewCriteria": [criteria[0]]},
        )
        plan["requiredRiskTemplates"] = ["NOT-A-RISK-TEMPLATE"]
        errors = validate_bounded_impact_plan(plan, change_class="PRODUCTION")
        self.assertTrue(any("unknown risk Review template" in error for error in errors), errors)

        plan["requiredRiskTemplates"] = ["security"]
        errors = validate_bounded_impact_plan(plan, change_class="PRODUCTION")
        self.assertTrue(any("is not allowed for phase development" in error for error in errors), errors)

    def test_risk_template_requires_its_full_affected_review_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            graph_path = Path(temp) / "graph.json"
            graph_path.write_text(json.dumps({
                "schemaVersion": 1,
                "authority": "DERIVED",
                "kind": "engineering_knowledge_graph",
                "nodes": [{
                    "id": "API-X", "type": "API", "name": "Public API",
                    "sourceRefs": ["api.md"], "contextRef": "BC-A",
                }],
                "relationships": [],
            }), encoding="utf-8")
            plan = impact_analysis.build_plan(
                task_dir=Path("/tmp/task"), phase="design", base_revision="DES-R01@abc",
                revision="DES-R02@def", change_class="PRODUCTION", graph_path=graph_path,
                changed_refs=["API-X"],
            )
        plan["minimumRevalidation"]["reviews"] = [
            item for item in plan["minimumRevalidation"]["reviews"]
            if item["profileId"] != "design:ApiContractReviewAgent"
        ]
        errors = validate_bounded_impact_plan(plan, change_class="PRODUCTION")
        self.assertTrue(any("requires all criteria of affected profile design:ApiContractReviewAgent" in error for error in errors), errors)

    def test_unknown_adapter_criterion_is_rejected(self) -> None:
        profile_id, _ = self._development_profile()
        adapter = {
            "schemaVersion": 1, "confidence": "HIGH",
            "affectedReviewCriteria": ["NOT-A-REAL-CRITERION"],
        }
        with self.assertRaises(impact_analysis.ImpactAnalysisError):
            impact_analysis.build_plan(
                task_dir=Path("/tmp/task"), phase="development", base_revision="DEV-R09@abc",
                revision="DEV-R10@def", change_class="PRODUCTION", profile_ids=[profile_id],
                adapter_result=adapter,
            )

    def test_capability_names_are_project_declared_and_missing_capability_blocks(self) -> None:
        profile_id, criteria = self._development_profile()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "project_env.md").write_text(
                "# Environment\n\n```yaml common-develop-capabilities\ncapabilities:\n  build:\n    available: true\n    command: ./build\n```\n",
                encoding="utf-8",
            )
            adapter = {
                "schemaVersion": 1, "confidence": "HIGH",
                "affectedReviewCriteria": [criteria[0]],
                "requiredCapabilities": ["integrationValidation"],
            }
            plan = impact_analysis.build_plan(
                task_dir=Path("/tmp/task"), phase="development", base_revision="DEV-R09@abc",
                revision="DEV-R10@def", change_class="PRODUCTION", profile_ids=[profile_id],
                adapter_result=adapter, project_root=root,
            )
            self.assertEqual("BLOCKED", plan["status"])
            self.assertEqual(["integrationValidation"], plan["capabilityResolution"]["missing"])
            self.assertIn("IMPACT_CAPABILITY_BLOCKED", plan["reasonCodes"])

    def test_wk_impact_generates_derived_plan_and_next_can_only_be_tightened(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            task_dir = self._start(Path(temp) / "project_doc")
            profile_id, _ = self._development_profile()
            plan_file = Path(temp) / "impact.json"
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(0, wk.main([
                    "impact", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir),
                    "--revision", "DEV-R10@def", "--change-class", "PRODUCTION",
                    "--profile", profile_id, "--output", str(plan_file),
                ]))
            plan = json.loads(out.getvalue())
            self.assertEqual("DERIVED", plan["authority"])
            self.assertTrue(plan["revalidationRequired"])
            before = (task_dir / "task_events.jsonl").read_bytes()
            next_out = io.StringIO()
            with contextlib.redirect_stdout(next_out):
                self.assertEqual(0, wk.main([
                    "next", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir),
                    "--explain", "--impact-plan", str(plan_file),
                ]))
            next_payload = json.loads(next_out.getvalue())
            self.assertEqual("REVALIDATION_REQUIRED", next_payload["decision"]["classification"])
            self.assertEqual(before, (task_dir / "task_events.jsonl").read_bytes())

    def test_process_only_plan_does_not_override_lifecycle_gate(self) -> None:
        decision = {
            "taskDir": "/tmp/task", "currentPhase": "development",
            "classification": "CLOSURE_ONLY", "currentState": "CLOSURE_READY",
            "reasonCodes": ["CLOSURE_READY"], "missing": [], "blockers": [],
        }
        plan = impact_analysis.build_plan(
            task_dir=Path("/tmp/task"), phase="development", base_revision="DEV-R09@abc",
            revision="DEV-R09@abc", change_class="PROCESS_ONLY", profile_ids=[],
        )
        tightened = impact_analysis.tighten_lifecycle_decision(decision, plan)
        self.assertEqual("CLOSURE_ONLY", tightened["classification"])
        self.assertFalse(plan["revalidationRequired"])

    def test_all_non_process_classes_without_adapter_use_conservative_scope(self) -> None:
        profile_id, criteria = self._development_profile()
        for change_class in revision_provenance.CHANGE_CLASSES:
            if change_class == "PROCESS_ONLY":
                continue
            with self.subTest(change_class=change_class):
                plan = impact_analysis.build_plan(
                    task_dir=Path("/tmp/task"), phase="development", base_revision="BASE@1",
                    revision="NEW@2", change_class=change_class, profile_ids=[profile_id],
                )
                self.assertEqual("CONSERVATIVE_FULL_PROFILE", plan["fallbackPolicy"])
                self.assertEqual(criteria, plan["minimumRevalidation"]["reviews"][0]["criterionIds"])

    def test_adapter_criterion_must_belong_to_selected_profiles(self) -> None:
        profile_id, _ = self._development_profile()
        contract = review_contract.load_contract()
        other_profile = "development:ArchitectureReviewAgent"
        other_criterion = review_contract.resolve_profile(contract, "development", "ArchitectureReviewAgent")["criterionIds"][0]
        adapter = {
            "schemaVersion": 1, "confidence": "HIGH",
            "affectedReviewCriteria": [other_criterion],
        }
        with self.assertRaises(impact_analysis.ImpactAnalysisError):
            impact_analysis.build_plan(
                task_dir=Path("/tmp/task"), phase="development", base_revision="DEV-R09@abc",
                revision="DEV-R10@def", change_class="PRODUCTION", profile_ids=[profile_id],
                adapter_result=adapter,
            )

    def test_wk_env_check_validates_optional_impact_adapter_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "project_env.md").write_text(
                "# Environment\n\n```json common-develop-capabilities\n{\"capabilities\":{\"fastValidation\":{\"available\":true,\"command\":\"./validate\"}}}\n```\n",
                encoding="utf-8",
            )
            adapter = root / "impact-adapter.json"
            adapter.write_text(json.dumps({"schemaVersion": 1, "confidence": "HIGH"}), encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(0, wk.main([
                    "env", "-g", "ProjectManagerAgent", "--project-root", str(root),
                    "--check", "--impact-adapter-result", str(adapter),
                ]))
            payload = json.loads(out.getvalue())
            self.assertEqual("PASSED", payload["status"])
            self.assertEqual("PASSED", payload["impactAdapter"]["status"])

    def test_reconcile_apply_cannot_bypass_required_revalidation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            task_dir = self._start(Path(temp) / "project_doc")
            profile_id, _ = self._development_profile()
            plan = impact_analysis.build_plan(
                task_dir=task_dir, phase="development", base_revision="BASE@1",
                revision="NEW@2", change_class="PRODUCTION", profile_ids=[profile_id],
            )
            plan_file = Path(temp) / "impact.json"
            plan_file.write_text(json.dumps(plan), encoding="utf-8")
            ledger = task_dir / "task_events.jsonl"
            before = ledger.read_bytes()
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(0, wk.main([
                    "reconcile", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir),
                    "--impact-plan", str(plan_file), "--apply",
                ]))
            payload = json.loads(out.getvalue())
            self.assertEqual("NO_SAFE_MUTATION", payload["status"])
            self.assertEqual("REVALIDATION_REQUIRED", payload["decision"]["classification"])
            self.assertEqual(before, ledger.read_bytes())

    def test_tighten_only_property_never_promotes_a_gate(self) -> None:
        profile_id, _ = self._development_profile()
        plan = impact_analysis.build_plan(
            task_dir=Path("/tmp/task"), phase="development", base_revision="R1",
            revision="R2", change_class="PRODUCTION", profile_ids=[profile_id],
        )
        for classification in ("EXECUTION_REQUIRED", "PUBLISH_READY", "CLOSURE_ONLY", "ADVANCE_READY", "COMPLETED"):
            with self.subTest(classification=classification):
                decision = {
                    "taskDir": "/tmp/task", "currentPhase": "development",
                    "classification": classification, "currentState": classification,
                    "reasonCodes": [], "missing": [], "blockers": [],
                }
                tightened = impact_analysis.tighten_lifecycle_decision(decision, plan)
                self.assertEqual("REVALIDATION_REQUIRED", tightened["classification"])
                self.assertNotIn(tightened["classification"], {"ADVANCE_READY", "COMPLETED"})


class IncrementalValidityProvenanceTests(unittest.TestCase):
    def test_process_only_cannot_claim_semantic_or_production_change(self) -> None:
        with self.assertRaises(ValueError):
            revision_provenance.build(
                base_revision="R1", revision="R1", change_class="PROCESS_ONLY",
                semantic_artifact_changed="TRUE",
            )

    def test_production_change_defaults_are_explicit(self) -> None:
        payload = revision_provenance.build(
            base_revision="R1", revision="R2", change_class="PRODUCTION",
            changed_scopes=["implementation", "implementation"],
        )
        self.assertEqual("FALSE", payload["semanticArtifactChanged"])
        self.assertEqual("TRUE", payload["productionChanged"])
        self.assertEqual(["implementation"], payload["changedScopes"])


if __name__ == "__main__":
    unittest.main()
