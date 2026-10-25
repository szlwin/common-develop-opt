#!/usr/bin/env python3
"""Golden snapshots for granular and budget-aware context routing."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

import context_resolver

ROOT = Path(__file__).resolve().parent.parent


class ContextRoutingGoldenTests(unittest.TestCase):
    def test_golden_snapshots(self) -> None:
        payload = json.loads((ROOT / "tests" / "fixtures" / "context-routing-golden.json").read_text(encoding="utf-8"))
        self.assertEqual(1, payload["schemaVersion"])
        seen: set[str] = set()
        for scenario in payload["scenarios"]:
            with self.subTest(scenario=scenario["id"]):
                self.assertNotIn(scenario["id"], seen)
                seen.add(scenario["id"])
                mode_data = scenario.get("mode") or {}
                mode = context_resolver.ResolvedMode(
                    model_code=bool(mode_data.get("modelCode")),
                    page_design=bool(mode_data.get("pageDesign")),
                    minimal=bool(mode_data.get("minimal")),
                    auto=bool(mode_data.get("auto")),
                    architecture_review=bool(mode_data.get("architectureReview")),
                    git_checkpoint=bool(mode_data.get("gitCheckpoint")),
                    git_push=bool(mode_data.get("gitPush")),
                )
                result = context_resolver.resolve_context(
                    scenario["taskType"], prompt=scenario.get("prompt", ""), mode=mode
                )
                self.assertEqual(scenario["expectedConditionIds"], result["matchedConditionIds"])
                self.assertEqual(scenario["expectedResourceIds"], result["resourceIds"])
                self.assertEqual(scenario["expectedTotalTokenBudget"], result["totalTokenBudget"])
                self.assertEqual(
                    scenario["expectedOmittedIds"],
                    [item["id"] for item in result["omittedResources"]],
                )
                self.assertLessEqual(result["totalTokenBudget"], 6500)
                self.assertGreaterEqual(result["limits"]["reservedTaskFactTokenBudget"], 5500)


if __name__ == "__main__":
    unittest.main()
