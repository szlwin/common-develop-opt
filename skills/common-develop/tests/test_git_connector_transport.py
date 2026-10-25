#!/usr/bin/env python3
"""Tests for the deterministic GitHub Connector checkpoint transport adapter."""
from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import git_connector_transport as transport


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout.strip()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class GitConnectorTransportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        git(self.repo, "init")
        git(self.repo, "config", "user.name", "Connector Test")
        git(self.repo, "config", "user.email", "connector@example.test")
        git(self.repo, "checkout", "-b", "feature/connector")
        (self.repo / "a.txt").write_text("base\n", encoding="utf-8")
        (self.repo / "delete.txt").write_text("delete me\n", encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "chore: base")
        self.base = git(self.repo, "rev-parse", "HEAD")
        (self.repo / "a.txt").write_text("changed\n", encoding="utf-8")
        (self.repo / "b.bin").write_bytes(b"\x00\x01binary\xff")
        (self.repo / "delete.txt").unlink()
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-m", "docs(plan): complete connector\n\nWK-Checkpoint: CP-V1-T-DESIGN-001\nWK-Revision: DESIGN-R1")
        self.checkpoint = git(self.repo, "rev-parse", "HEAD")
        self.request = transport.build_request(
            self.repo,
            remote="origin",
            remote_url="https://github.com/example/project.git",
            branch="feature/connector",
            checkpoint_id="CP-V1-T-DESIGN-001",
            local_commit_sha=self.checkpoint,
            base_commit_sha=self.base,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def valid_receipt(self, *, mode: str = "PUBLISHED") -> dict:
        remote_sha = "f" * 40
        expected_blobs = [
            {"path": item["path"], "sha": item["localObjectSha"]}
            for item in self.request["treeChanges"]
            if item.get("operation") == "UPSERT" and item.get("type") == "blob"
        ]
        return {
            "schemaVersion": 1,
            "provider": "GITHUB_CONNECTOR",
            "resultMode": mode,
            "requestDigest": self.request["requestDigest"],
            "repository": self.request["repository"],
            "branch": self.request["branch"],
            "checkpointId": self.request["checkpointId"],
            "resolvedParent": {
                "sha": self.base,
                "treeSha": self.request["localBase"]["treeSha"],
                "normalizedMessageSha256": self.request["localBase"]["normalizedMessageSha256"],
            },
            "blobResults": expected_blobs if mode == "PUBLISHED" else [],
            "tree": {"sha": self.request["localCheckpoint"]["treeSha"]},
            "commit": {
                "sha": remote_sha,
                "parentSha": self.base,
                "treeSha": self.request["localCheckpoint"]["treeSha"],
                "normalizedMessageSha256": self.request["localCheckpoint"]["normalizedMessageSha256"],
            },
            "refUpdate": {
                "operation": "UPDATE_REF_NON_FORCE" if mode == "PUBLISHED" else "NO_CHANGE",
                "branch": self.request["branch"],
                "force": False,
                "beforeSha": self.base if mode == "PUBLISHED" else remote_sha,
                "afterSha": remote_sha,
            },
            "remoteVerification": {"branchHeadSha": remote_sha},
            "pullRequestVerification": {
                "status": "VERIFIED",
                "refs": [{"number": 36, "headSha": remote_sha}],
            },
            "verifiedAt": "2026-08-18T08:00:00Z",
        }

    def test_parse_github_repository(self) -> None:
        self.assertEqual("openai/example", transport.parse_github_repository("https://github.com/openai/example.git"))
        self.assertEqual("openai/example", transport.parse_github_repository("git@github.com:openai/example.git"))
        self.assertEqual("openai/example", transport.parse_github_repository("ssh://git@github.com/openai/example.git"))
        self.assertEqual("", transport.parse_github_repository("https://gitlab.com/openai/example.git"))

    def test_request_contains_exact_tree_blob_and_delete_plan(self) -> None:
        self.assertEqual("GITHUB_CONNECTOR", self.request["provider"])
        self.assertEqual("example/project", self.request["repository"])
        self.assertEqual(git(self.repo, "rev-parse", "HEAD^{tree}"), self.request["localCheckpoint"]["treeSha"])
        changes = {item["path"]: item for item in self.request["treeChanges"]}
        self.assertEqual("DELETE", changes["delete.txt"]["operation"])
        self.assertEqual("UPSERT", changes["a.txt"]["operation"])
        self.assertEqual("blob", changes["b.bin"]["type"])
        self.assertTrue(changes["b.bin"]["contentBase64"])
        digest_source = dict(self.request)
        digest_source.pop("createdAt", None)
        digest_source.pop("requestDigest", None)
        digest_source.pop("requestId", None)
        # requestDigest is calculated before requestId is added.
        self.assertEqual(transport.digest_json(digest_source), self.request["requestDigest"])

    def test_valid_published_receipt_produces_dual_sha_verified_state(self) -> None:
        state = transport.validate_receipt(self.request, self.valid_receipt())
        self.assertEqual("VERIFIED", state["status"])
        self.assertEqual("GITHUB_CONNECTOR", state["transport"])
        self.assertEqual(self.checkpoint, state["localCommitSha"])
        self.assertEqual("f" * 40, state["remoteCommitSha"])
        self.assertNotEqual(state["localCommitSha"], state["remoteCommitSha"])
        self.assertEqual(state["localTreeSha"], state["remoteTreeSha"])
        self.assertEqual("EXACT_TREE_MESSAGE_EQUIVALENT", state["identityMode"])

    def test_already_present_receipt_is_valid_without_blob_recreation(self) -> None:
        receipt = self.valid_receipt(mode="ALREADY_PRESENT")
        state = transport.validate_receipt(self.request, receipt)
        self.assertEqual("ALREADY_PRESENT", state["resultMode"])
        self.assertEqual("f" * 40, state["remoteHeadSha"])

    def test_receipt_rejects_force_or_wrong_target_tree(self) -> None:
        receipt = self.valid_receipt()
        receipt["refUpdate"]["force"] = True
        with self.assertRaisesRegex(transport.GitConnectorTransportError, "force=false"):
            transport.validate_receipt(self.request, receipt)
        receipt = self.valid_receipt()
        receipt["tree"]["sha"] = "a" * 40
        with self.assertRaisesRegex(transport.GitConnectorTransportError, "tree SHA"):
            transport.validate_receipt(self.request, receipt)

    def test_receipt_rejects_divergent_parent(self) -> None:
        receipt = self.valid_receipt()
        receipt["resolvedParent"]["treeSha"] = "a" * 40
        with self.assertRaisesRegex(transport.GitConnectorTransportError, "git_divergence"):
            transport.validate_receipt(self.request, receipt)

    def test_verified_state_freshness_expires(self) -> None:
        state = transport.validate_receipt(self.request, self.valid_receipt())
        self.assertTrue(transport.verified_state_is_fresh(state))
        stale = dict(state)
        stale["verifiedAt"] = "2000-01-01T00:00:00+00:00"
        self.assertFalse(transport.verified_state_is_fresh(stale))

    def test_verified_state_is_stored_under_git_metadata_not_worktree(self) -> None:
        state = transport.validate_receipt(self.request, self.valid_receipt())
        path = transport.persist_verified_state(self.repo, self.request["checkpointId"], state)
        self.assertIn(".git/common-develop/git-connector", path.as_posix())
        self.assertEqual("", git(self.repo, "status", "--porcelain"))
        loaded = transport.load_verified_state(self.repo, self.request["checkpointId"])
        self.assertEqual(state["remoteCommitSha"], loaded["remoteCommitSha"])


if __name__ == "__main__":
    unittest.main()
