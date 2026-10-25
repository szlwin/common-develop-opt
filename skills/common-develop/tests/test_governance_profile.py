#!/usr/bin/env python3
"""Tests for APG-inspired advisory governance recommendations."""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import governance_profile
import wk

try:
    import jsonschema
except ImportError:  # pragma: no cover
    jsonschema = None


class GovernanceProfileTests(unittest.TestCase):
    def test_empty_project_is_small(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            payload = governance_profile.recommend(Path(temp))
        self.assertEqual("SMALL", payload["profile"])
        self.assertEqual("MINIMAL_FOR_LOW_RISK", payload["recommendedWorkflow"])
        self.assertEqual("-min", payload["recommendedMode"])
        self.assertTrue(payload["advisory"])
        self.assertTrue(payload["overridable"])

    def test_high_impact_signals_recommend_high_risk(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            payload = governance_profile.recommend(
                Path(temp), signals=["database", "permissions"]
            )
        self.assertEqual("HIGH_RISK", payload["profile"])
        self.assertEqual("STANDARD_WITH_RISK_GATES", payload["recommendedWorkflow"])
        self.assertEqual("-ar -gc", payload["recommendedMode"])
        self.assertEqual(["database", "permissions"], payload["signals"])
        self.assertEqual("HIGH", payload["confidence"])

    def test_impact_assessment_can_raise_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            payload = governance_profile.recommend(
                Path(temp),
                impact_assessment={"dataModel": "HIGH", "permission": "MEDIUM"},
            )
        self.assertEqual("HIGH_RISK", payload["profile"])
        self.assertNotIn("impactSignals", payload)

    def test_simple_optimization_keeps_fast_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            payload = governance_profile.recommend(
                Path(temp), classification="SIMPLE_OPTIMIZATION"
            )
        self.assertEqual("SMALL", payload["profile"])
        self.assertEqual("SIMPLE_OPTIMIZATION_FLOW", payload["recommendedWorkflow"])
        self.assertEqual("standard", payload["recommendedMode"])
        self.assertIn("简单优化", payload["reason"])

    def test_unknown_signal_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(governance_profile.GovernanceProfileError):
                governance_profile.recommend(Path(temp), signals=["not-a-signal"])

    def test_decision_guide_contains_non_expert_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            recommendation = governance_profile.recommend(Path(temp), signals=["database"])
            guide = governance_profile.build_decision_guide(
                recommendation,
                next_command={
                    "command": "wk next --task-dir task",
                    "reason": "继续当前阶段",
                    "requiredAgent": "ProjectManagerAgent",
                    "requiresConfirmation": False,
                    "readOnly": True,
                },
                current_facts="已识别数据库影响。",
                actual_risk="需要保留数据变更门禁。",
            )
        self.assertEqual(
            {
                "currentFacts", "actualRisk", "recommendedChoice", "why",
                "whatWillHappen", "whatWillNotChange", "nextCommand",
            },
            set(guide) & {
                "currentFacts", "actualRisk", "recommendedChoice", "why",
                "whatWillHappen", "whatWillNotChange", "nextCommand",
            },
        )
        self.assertTrue(guide["advisory"])
        self.assertTrue(guide["overrideAllowed"])
        self.assertTrue(guide["overrideReasonRequired"])
        self.assertTrue(guide["nextCommand"]["readOnly"])
        self.assertIn("不会覆盖", guide["whatWillNotChange"])

    def test_wk_start_preview_exposes_advisory_decision_guide(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project_doc"
            args = [
                "start", "-g", "ProjectManagerAgent", "--doc-root", str(root),
                "--version", "V_TEST", "-f", "改标题", "--classification", "simple-optimization", "--related-feature", "PRD0013-F01",
            ]
            for flag in (
                "business-logic", "data-model", "api-contract", "permission", "workflow",
                "persistence", "external-side-effect", "migration", "dependency",
            ):
                args.extend([f"--{flag}-impact", "NONE"])
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(0, wk.main(args))
            payload = json.loads(output.getvalue())
            guide = payload["decisionGuide"]
            self.assertTrue(guide["advisory"])
            self.assertTrue(guide["overrideAllowed"])
            self.assertEqual("SIMPLE_OPTIMIZATION_FLOW", payload["governanceProfile"]["recommendedWorkflow"])
            self.assertIn("governanceProfile", payload)

    @unittest.skipIf(jsonschema is None, "jsonschema is not installed")
    def test_public_profile_and_decision_guide_are_strict_projections(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            internal = governance_profile._recommend_internal(root, signals=["database"])
            profile = governance_profile.public_governance_profile(internal)
            guide = governance_profile.build_decision_guide(internal)
        schema = json.loads(
            (Path(governance_profile.__file__).resolve().parent.parent / "assets" / "project-governance.schema.json")
            .read_text(encoding="utf-8")
        )
        jsonschema.Draft202012Validator(schema).validate(profile)
        guide_schema = {
            "$schema": schema["$schema"],
            "$defs": schema["$defs"],
            **schema["$defs"]["decisionGuide"],
        }
        jsonschema.Draft202012Validator(guide_schema).validate(guide)
        self.assertNotIn("inputDigest", profile)
        self.assertNotIn("signals", guide)


if __name__ == "__main__":
    unittest.main()
