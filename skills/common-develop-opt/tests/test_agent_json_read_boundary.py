"""Ensure Agent-facing JSON/JSONL reads remain query-only."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from runtime import ai_context, task_verify


class AgentJsonReadBoundaryTests(unittest.TestCase):
    def test_diagnostics_return_query_commands_not_raw_refs(self):
        task_dir = Path("/tmp/common-develop-agent-read-boundary-test")
        for message in (
            "digest mismatch",
            "review gate failed",
            "traceability invalid",
            "acceptance assertion invalid",
            "risk gate failed",
            "work mode out of sync",
            "open issue gate failed",
            "unclassified validation failure",
        ):
            with self.subTest(message=message):
                reads = task_verify._diagnostic(message, task_dir)["recommendedReads"]
                self.assertTrue(reads)
                for item in reads:
                    self.assertNotIn("ref", item)
                    self.assertEqual("QUERY_ONLY", item.get("readMode"))
                    self.assertTrue(item.get("command"))
                    self.assertIsInstance(item.get("selectors"), list)

    def test_architecture_context_exposes_reducer_query(self):
        context = ai_context._architecture_review_context(
            "", "", "", "", {"architectureReview": True}
        )
        self.assertEqual("task runtime reducer", context["stateRef"])
        self.assertNotRegex(context["stateRef"], r"\.jsonl?$")
        self.assertIn("wk.py status", context["stateQueryCommand"])

    def test_task_events_policy_is_query_only(self):
        policy = ai_context._project_fact_read_policy("project_doc", "V_1.0", "TARGET")
        events = policy["runtimeFiles"]["task_events.jsonl"]
        self.assertEqual("QUERY_ONLY", events["readMode"])
        self.assertTrue(events["query"])
        self.assertIn("limit", events["selectors"])

    def test_manifest_has_no_agent_direct_read_exception(self):
        policy = ai_context._project_fact_read_policy("project_doc", "V_1.0", "TARGET")
        manifest = policy["runtimeFiles"]["cache/current_manifest.json"]
        self.assertEqual("QUERY_ONLY", manifest["readMode"])
        self.assertEqual([], policy["directReadExceptions"])
        self.assertNotIn("cache/current_manifest.json", policy["conditionalReadSet"])
        self.assertTrue(manifest["query"])

    def test_unassigned_phase_agent_is_rejected(self):
        with self.assertRaisesRegex(ai_context.AIContextError, "not assigned to phase requirement_analysis"):
            ai_context._validate_phase_agent_role(
                {
                    "id": "requirement_analysis",
                    "selectedAgentRole": "NOT_ASSIGNED",
                    "requiredReviewerAgents": ["BusinessModelAgent", "TestDesignAgent"],
                },
                "RequirementReviewAgent",
                "review",
            )

    def test_project_fact_paths_do_not_expose_json_files(self):
        paths = ai_context._project_fact_paths("project_doc", "V_1.0", "TARGET")
        self.assertTrue(paths)
        self.assertFalse(any(str(value).endswith((".json", ".jsonl")) for value in paths.values()))
        semantic = ai_context._task_semantic_inputs(
            "project_doc", "V_1.0", "TARGET", "unknown_phase", paths
        )
        self.assertTrue(semantic)
        self.assertTrue(all(item.get("readMode") == "QUERY_ONLY" for item in semantic))


if __name__ == "__main__":
    unittest.main()
