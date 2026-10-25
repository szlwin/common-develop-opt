from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import baseline_guard


class BaselineGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.name", "Test"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.email", "test@example.invalid"], check=True)
        (self.repo / "assets").mkdir()
        release = {
            "baselineTag": "common-develop-v1-test",
            "criticalBaselineFiles": ["SKILL.md", "CHANGELOG.md", "assets/release-contract.json"],
        }
        (self.repo / "assets" / "release-contract.json").write_text(
            json.dumps(release, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (self.repo / "SKILL.md").write_text("skill\n", encoding="utf-8")
        (self.repo / "CHANGELOG.md").write_text("change\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "baseline"], check=True)
        subprocess.run(
            ["git", "-C", str(self.repo), "tag", "common-develop-v1-test"], check=True
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_clean_baseline(self) -> None:
        payload = baseline_guard.inspect_baseline(self.repo)
        self.assertEqual("CLEAN", payload["status"])
        self.assertTrue(payload["headMatchesBaseline"])
        self.assertTrue(payload["worktreeClean"])
        self.assertEqual([], payload["criticalFileDrift"])

    def test_modified_critical_file_is_reported_without_mutation(self) -> None:
        path = self.repo / "SKILL.md"
        path.write_text("mounted overwrite\n", encoding="utf-8")
        before = path.read_bytes()
        payload = baseline_guard.inspect_baseline(self.repo)
        self.assertEqual("DIRTY", payload["status"])
        self.assertEqual("MODIFIED", payload["criticalFileDrift"][0]["state"])
        self.assertEqual(before, path.read_bytes())

    def test_head_divergence_is_distinct_from_worktree_drift(self) -> None:
        (self.repo / "new.txt").write_text("new\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "add", "new.txt"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "next"], check=True)
        payload = baseline_guard.inspect_baseline(self.repo)
        self.assertEqual("DIVERGED", payload["status"])
        self.assertFalse(payload["headMatchesBaseline"])
        self.assertTrue(payload["worktreeClean"])

    def test_release_envelope_commit_is_clean_against_implementation_baseline(self) -> None:
        release_path = self.repo / "assets" / "release-contract.json"
        release = json.loads(release_path.read_text(encoding="utf-8"))
        release["baselineMode"] = "IMPLEMENTATION_COMMIT_WITH_RELEASE_ENVELOPE"
        release["releaseEnvelopeFiles"] = ["CHANGELOG.md", "assets/release-contract.json"]
        release_path.write_text(json.dumps(release, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (self.repo / "CHANGELOG.md").write_text("release envelope\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "release envelope"], check=True)

        payload = baseline_guard.inspect_baseline(self.repo)

        self.assertEqual("RELEASE_ENVELOPE_CLEAN", payload["status"])
        self.assertFalse(payload["headMatchesBaseline"])
        self.assertTrue(payload["worktreeClean"])
        self.assertTrue(payload["releaseEnvelope"]["parentMatchesBaseline"])
        self.assertTrue(payload["releaseEnvelope"]["singleCommitFromBaseline"])
        self.assertTrue(payload["releaseEnvelope"]["driftIsEnvelopeOnly"])

    def test_missing_tag_returns_invalid_baseline(self) -> None:
        payload = baseline_guard.inspect_baseline(self.repo, "missing-tag")
        self.assertEqual("INVALID_BASELINE", payload["status"])
        self.assertEqual("CREATE_OR_REPAIR_BASELINE_TAG", payload["recommendedAction"])

    def test_parser_has_agent_selector(self) -> None:
        options = {option for action in baseline_guard.build_parser()._actions for option in action.option_strings}
        self.assertIn("-g", options)
        self.assertIn("--agent", options)


if __name__ == "__main__":
    unittest.main()
