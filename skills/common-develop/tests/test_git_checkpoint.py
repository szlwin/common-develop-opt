#!/usr/bin/env python3
"""Regression tests for sequential Git phase checkpoints."""
from __future__ import annotations

import json
import argparse
import contextlib
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import git_checkpoint
import git_connector_transport
import long_task
from _long_task import task_events
import simple_yaml
import work_mode


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout.strip()


def write_block(path: Path, name: str, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# fixture\n\n```json {name}\n{json.dumps(value, ensure_ascii=False, indent=2)}\n```\n", encoding="utf-8")


class GitCheckpointTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        git(self.repo, "init")
        git(self.repo, "config", "user.name", "Common Develop Test")
        git(self.repo, "config", "user.email", "common-develop@example.test")
        git(self.repo, "checkout", "-b", "wk/V_TEST/MOD0001")
        self.doc_root = self.repo / "project_doc"
        self.version_root = self.doc_root / "version" / "V_TEST"
        self.task_dir = self.version_root / "task" / "MOD0001"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, long_task.command_init(argparse.Namespace(
                doc_root=str(self.doc_root), version="V_TEST", target="MOD0001", reuse=False,
                request_intake="", agent="ProjectManagerAgent"
            )))
        work_mode.apply_mode(self.version_root / "work.md", work_mode.WorkMode(git_checkpoint=True))
        _doc, self.state = long_task.read_task_state_cache(self.task_dir)
        self.state["current_phase"] = "design"
        self.state["execution_mode"] = "SEQUENTIAL"
        self.state["artifact_revisions"]["design"].update({
            "status":"PASSED", "revision":"DESIGN-R01@hash",
            "iteration_id":"ITER-MOD0001-DESIGN-001", "iteration_no":1
        })
        self.outcome = {
            "id": "SO-MOD0001-DESIGN-I001", "phase": "design", "iteration_id": "ITER-MOD0001-DESIGN-001",
            "iteration_no": 1, "is_current": True, "status": "PASSED", "output_revision": "DESIGN-R01@hash",
        }
        self.sync_runtime(self.state, [
            {"type":"ATTEMPT_FINISHED","entity_id":"ATTEMPT-1","payload":{"attempt":{
                "attempt_id":"ATTEMPT-1","task_id":"DESIGN-1","phase":"design",
                "iteration_id":"ITER-MOD0001-DESIGN-001","attempt_no":1,"status":"PASSED",
                "summary":"update product design contract","modified_files":[],"output_revision":"DESIGN-R01@hash"
            }}},
            {"type":"STAGE_OUTCOME_RECORDED","entity_id":self.outcome["id"],"payload":{"outcome":self.outcome}},
        ])
        git_checkpoint.ensure_document(self.task_dir, "MOD0001", "V_TEST")
        (self.task_dir / "design.md").write_text("baseline\n", encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "chore: baseline")
        (self.task_dir / "design.md").write_text("updated design\n", encoding="utf-8")

    def sync_runtime(self, state: dict, specs: list[dict] | None = None) -> None:
        document, _current = long_task.read_task_state_cache(self.task_dir)
        payloads, _projected, _reduced = long_task.append_task_events_payloads(
            self.task_dir, long_task.task_state_path(self.task_dir), document, state, specs or [],
            synced_at="2026-08-15T00:00:00Z",
        )
        long_task.transactional_text_write(payloads)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def validate_patch(self):
        return mock.patch("long_task.validate_task", return_value=([], [], self.state, []))

    def test_commit_creates_concise_message_and_resolvable_yaml_checkpoint(self) -> None:
        with self.validate_patch():
            result = git_checkpoint.commit_checkpoint(
                self.task_dir, agent="ProjectManagerAgent",
                changes=["define shelf-life ownership", "update API contract"],
            )
        self.assertEqual("COMMITTED", result["status"])
        message = git(self.repo, "show", "-s", "--format=%B", "HEAD")
        self.assertIn("docs(design): complete MOD0001 design", message)
        self.assertIn("WK-Checkpoint: CP-V_TEST-MOD0001-DESIGN-001", message)
        self.assertIn("WK-Revision: DESIGN-R01@hash", message)
        self.assertNotIn("WK-Evidence", message)
        document = simple_yaml.load(self.task_dir / "git_checkpoints.yaml")
        self.assertEqual(2, document["schema_version"])
        self.assertEqual(1, len(document["checkpoints"]))
        checkpoint = document["checkpoints"][0]
        self.assertNotIn("commit_sha", checkpoint)
        self.assertNotIn("decision", checkpoint)
        self.assertNotIn("commit_locator", checkpoint)
        errors, _, resolved = git_checkpoint.validate_document(self.task_dir, require_resolved=True)
        self.assertEqual([], errors)
        self.assertEqual([result["commitSha"]], resolved[0]["resolved_commit_shas"])
        self.assertTrue(result["indexRefreshed"])
        self.assertEqual("REFRESHED", result["indexSync"]["status"])
        self.assertEqual("CLEAN_INDEX_UNCHANGED", result["indexSync"]["reason"])
        self.assertEqual("", git(self.repo, "diff", "--cached", "--name-only"))
        self.assertEqual("", git(self.repo, "diff", "--name-only"))
        self.assertEqual(git(self.repo, "rev-parse", "HEAD^{tree}"), git(self.repo, "write-tree"))

    def test_checkpoint_rejects_trailing_whitespace_in_normal_task_document(self) -> None:
        (self.task_dir / "design.md").write_text("invalid trailing whitespace  \n", encoding="utf-8")
        with self.validate_patch(), self.assertRaisesRegex(
            git_checkpoint.GitCheckpointError, "git diff --cached --check failed"
        ):
            git_checkpoint.commit_checkpoint(self.task_dir, agent="ProjectManagerAgent")

    def test_checkpoint_rejects_trailing_whitespace_in_evidence_registry_file(self) -> None:
        registry = self.task_dir / "evidence" / "evidence_index.json"
        registry.parent.mkdir(parents=True, exist_ok=True)
        registry.write_text('{"schema": 1}  \n', encoding="utf-8")
        with self.validate_patch(), self.assertRaisesRegex(
            git_checkpoint.GitCheckpointError, "git diff --cached --check failed"
        ):
            git_checkpoint.commit_checkpoint(self.task_dir, agent="ProjectManagerAgent")

    def test_repeated_commit_is_idempotent(self) -> None:
        with self.validate_patch():
            first = git_checkpoint.commit_checkpoint(self.task_dir, agent="ProjectManagerAgent")
            second = git_checkpoint.commit_checkpoint(self.task_dir, agent="ProjectManagerAgent")
        self.assertEqual("COMMITTED", first["status"])
        self.assertEqual("ALREADY_COMMITTED", second["status"])
        self.assertEqual(first["commitSha"], second["commitSha"])
        self.assertEqual(
            {"canonicalPassed": True, "localCheckpointed": True, "remoteCheckpointed": False},
            first["checkpointState"],
        )
        self.assertEqual(first["checkpointState"], second["checkpointState"])

    def test_checkpoint_rejects_dirty_index_without_mutating_user_staged_content(self) -> None:
        staged = self.repo / "user-staged.txt"
        staged.write_text("keep staged\n", encoding="utf-8")
        git(self.repo, "add", str(staged))
        before_digest = git_checkpoint.index_digest(self.repo)
        before_status = git(self.repo, "status", "--porcelain")
        with self.validate_patch(), self.assertRaisesRegex(git_checkpoint.GitCheckpointError, "preserved index digest"):
            git_checkpoint.commit_checkpoint(self.task_dir, agent="ProjectManagerAgent")
        self.assertEqual(before_digest, git_checkpoint.index_digest(self.repo))
        self.assertEqual(before_status, git(self.repo, "status", "--porcelain"))
        document = simple_yaml.load(self.task_dir / "git_checkpoints.yaml")
        self.assertEqual([], document.get("checkpoints"))

    def test_index_refresh_preserves_external_staged_change(self) -> None:
        expected_head = git(self.repo, "rev-parse", "HEAD")
        expected_digest = git_checkpoint.index_digest(self.repo)
        expected_tree = git_checkpoint.index_tree(self.repo)
        staged = self.repo / "user-staged.txt"
        staged.write_text("keep staged\n", encoding="utf-8")
        git(self.repo, "add", str(staged))

        result = git_checkpoint.refresh_real_index_to_head(
            self.repo,
            expected_head=expected_head,
            expected_index_digest=expected_digest,
            expected_index_tree=expected_tree,
        )

        self.assertEqual("PRESERVED_CONCURRENT_CHANGE", result["status"])
        self.assertEqual("INDEX_CHANGED_BEFORE_REFRESH", result["reason"])
        self.assertIn("user-staged.txt", git(self.repo, "diff", "--cached", "--name-only"))

    def test_index_refresh_preserves_existing_index_lock(self) -> None:
        lock = git_checkpoint.git_index_path(self.repo).with_name("index.lock")
        lock.write_text("external lock\n", encoding="utf-8")
        try:
            with self.validate_patch(), self.assertRaisesRegex(
                git_checkpoint.GitCheckpointError, "Git index.lock is present"
            ):
                git_checkpoint.commit_checkpoint(self.task_dir, agent="ProjectManagerAgent")
            self.assertEqual("external lock\n", lock.read_text(encoding="utf-8"))
            self.assertEqual("", git(self.repo, "diff", "--cached", "--name-only"))
        finally:
            lock.unlink(missing_ok=True)

    def test_index_refresh_rejects_head_change(self) -> None:
        expected_head = git(self.repo, "rev-parse", "HEAD")
        expected_digest = git_checkpoint.index_digest(self.repo)
        expected_tree = git_checkpoint.index_tree(self.repo)
        git(self.repo, "commit", "--allow-empty", "-m", "chore: external commit")

        result = git_checkpoint.refresh_real_index_to_head(
            self.repo,
            expected_head=expected_head,
            expected_index_digest=expected_digest,
            expected_index_tree=expected_tree,
        )

        self.assertEqual("PRESERVED_HEAD_CHANGE", result["status"])
        self.assertEqual("HEAD_CHANGED_DURING_REFRESH", result["reason"])

    def test_checkpoint_isolated_from_pre_commit_and_commit_msg_hooks(self) -> None:
        hooks = self.repo / ".git" / "hooks"
        pre_commit = hooks / "pre-commit"
        pre_commit.write_text(
            "#!/bin/sh\nprintf 'hook mutation\n' >> project_doc/version/V_TEST/task/MOD0001/design.md\nexit 0\n",
            encoding="utf-8",
        )
        commit_msg = hooks / "commit-msg"
        commit_msg.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        pre_commit.chmod(0o755)
        commit_msg.chmod(0o755)
        with self.validate_patch():
            result = git_checkpoint.commit_checkpoint(self.task_dir, agent="ProjectManagerAgent")
        self.assertEqual("COMMITTED", result["status"])
        self.assertFalse(result["indexPreserved"])
        self.assertTrue(result["indexRefreshed"])
        self.assertEqual("REFRESHED", result["indexSync"]["status"])
        self.assertEqual("", git(self.repo, "status", "--porcelain"))
        self.assertNotIn("hook mutation", git(self.repo, "show", "HEAD:project_doc/version/V_TEST/task/MOD0001/design.md"))

    def test_checkpoint_writer_lock_blocks_overlapping_writer(self) -> None:
        repo = git_checkpoint.repository_root(self.task_dir)
        with git_checkpoint.checkpoint_lock(repo):
            with self.validate_patch(), self.assertRaisesRegex(git_checkpoint.GitCheckpointError, "another Git checkpoint writer"):
                git_checkpoint.commit_checkpoint(self.task_dir, agent="ProjectManagerAgent")

    def enable_git_push_mode(self) -> Path:
        remote = Path(self.temp.name) / "remote.git"
        subprocess.run(["git", "init", "--bare", str(remote)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        git(self.repo, "remote", "add", "origin", str(remote))
        work_mode.apply_mode(
            self.version_root / "work.md",
            work_mode.WorkMode(git_checkpoint=True, git_push=True),
        )
        return remote

    def test_push_checkpoint_pushes_current_checkpoint_and_verifies_remote_head(self) -> None:
        remote = self.enable_git_push_mode()
        with self.validate_patch():
            committed = git_checkpoint.commit_checkpoint(self.task_dir, agent="ProjectManagerAgent")
        pushed = git_checkpoint.push_checkpoint(
            self.task_dir, agent="ProjectManagerAgent", checkpoint_id=committed["checkpointId"]
        )
        self.assertEqual("PUSHED", pushed["status"])
        self.assertTrue(pushed["remoteVerified"])
        self.assertEqual(committed["commitSha"], pushed["remoteHeadSha"])
        self.assertEqual(
            {"canonicalPassed": True, "localCheckpointed": True, "remoteCheckpointed": True},
            pushed["checkpointState"],
        )
        remote_head = subprocess.run(
            ["git", "--git-dir", str(remote), "rev-parse", "refs/heads/wk/V_TEST/MOD0001"],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        ).stdout.strip()
        self.assertEqual(committed["commitSha"], remote_head)
        self.assertEqual("OTHER", pushed["pullRequestVerification"]["provider"])

    def test_push_checkpoint_is_idempotent_after_remote_verification(self) -> None:
        self.enable_git_push_mode()
        with self.validate_patch():
            committed = git_checkpoint.commit_checkpoint(self.task_dir, agent="ProjectManagerAgent")
        first = git_checkpoint.push_checkpoint(self.task_dir, agent="ProjectManagerAgent")
        second = git_checkpoint.push_checkpoint(self.task_dir, agent="ProjectManagerAgent")
        self.assertEqual("PUSHED", first["status"])
        self.assertEqual("ALREADY_PUSHED", second["status"])
        self.assertEqual(first["remoteHeadSha"], second["remoteHeadSha"])

    def test_push_checkpoint_refuses_uncheckpointed_local_head(self) -> None:
        self.enable_git_push_mode()
        with self.validate_patch():
            committed = git_checkpoint.commit_checkpoint(self.task_dir, agent="ProjectManagerAgent")
        (self.repo / "extra.txt").write_text("extra\n", encoding="utf-8")
        git(self.repo, "add", "extra.txt")
        git(self.repo, "commit", "-m", "chore: uncheckpointed")
        with self.assertRaisesRegex(git_checkpoint.GitCheckpointError, "uncheckpointed commits"):
            git_checkpoint.push_checkpoint(
                self.task_dir, agent="ProjectManagerAgent", checkpoint_id=committed["checkpointId"]
            )

    def test_push_checkpoint_requires_git_push_mode(self) -> None:
        with self.validate_patch():
            git_checkpoint.commit_checkpoint(self.task_dir, agent="ProjectManagerAgent")
        with self.assertRaisesRegex(git_checkpoint.GitCheckpointError, "wk -gp/--git-push is not enabled"):
            git_checkpoint.push_checkpoint(self.task_dir, agent="ProjectManagerAgent")


    def enable_github_push_mode(self) -> None:
        git(self.repo, "remote", "add", "origin", "https://github.com/example/project.git")
        work_mode.apply_mode(
            self.version_root / "work.md",
            work_mode.WorkMode(git_checkpoint=True, git_push=True),
        )

    def connector_receipt(self, request: dict, remote_sha: str = "f" * 40) -> dict:
        expected_blobs = [
            {"path": item["path"], "sha": item["localObjectSha"]}
            for item in request["treeChanges"]
            if item.get("operation") == "UPSERT" and item.get("type") == "blob"
        ]
        return {
            "schemaVersion": 1,
            "provider": "GITHUB_CONNECTOR",
            "resultMode": "PUBLISHED",
            "requestDigest": request["requestDigest"],
            "repository": request["repository"],
            "branch": request["branch"],
            "checkpointId": request["checkpointId"],
            "resolvedParent": {
                "sha": request["localBase"]["commitSha"],
                "treeSha": request["localBase"]["treeSha"],
                "normalizedMessageSha256": request["localBase"]["normalizedMessageSha256"],
            },
            "blobResults": expected_blobs,
            "tree": {"sha": request["localCheckpoint"]["treeSha"]},
            "commit": {
                "sha": remote_sha,
                "parentSha": request["localBase"]["commitSha"],
                "treeSha": request["localCheckpoint"]["treeSha"],
                "normalizedMessageSha256": request["localCheckpoint"]["normalizedMessageSha256"],
            },
            "refUpdate": {
                "operation": "UPDATE_REF_NON_FORCE",
                "branch": request["branch"],
                "force": False,
                "beforeSha": request["localBase"]["commitSha"],
                "afterSha": remote_sha,
            },
            "remoteVerification": {"branchHeadSha": remote_sha},
            "pullRequestVerification": {"status": "NO_ASSOCIATED_PR", "refs": []},
            "verifiedAt": "2026-08-18T08:00:00Z",
        }

    def test_auto_push_falls_back_to_connector_when_native_transport_is_unavailable(self) -> None:
        self.enable_github_push_mode()
        with self.validate_patch():
            committed = git_checkpoint.commit_checkpoint(self.task_dir, agent="ProjectManagerAgent")
        with mock.patch(
            "git_checkpoint.remote_branch_head",
            side_effect=git_checkpoint.GitCheckpointError(
                "remote verification failed: fatal: unable to access https://github.com/example/project.git: Could not resolve host: github.com"
            ),
        ):
            result = git_checkpoint.push_checkpoint(
                self.task_dir, agent="ProjectManagerAgent", checkpoint_id=committed["checkpointId"]
            )
        self.assertEqual("CONNECTOR_REQUIRED", result["status"])
        self.assertEqual("GITHUB_CONNECTOR", result["transport"])
        self.assertFalse(result["remoteVerified"])
        request_path = Path(result["connectorRequestPath"])
        self.assertTrue(request_path.is_file())
        request = json.loads(request_path.read_text(encoding="utf-8"))
        self.assertEqual(committed["commitSha"], request["localCheckpoint"]["commitSha"])
        self.assertEqual("example/project", request["repository"])

    def test_auto_push_does_not_fallback_on_git_divergence(self) -> None:
        self.enable_github_push_mode()
        with self.validate_patch():
            committed = git_checkpoint.commit_checkpoint(self.task_dir, agent="ProjectManagerAgent")
        original_run_git = git_checkpoint.run_git
        def side_effect(repo, *args, **kwargs):
            if args and args[0] == "push":
                return subprocess.CompletedProcess(
                    ["git"], 1, stdout="", stderr="! [rejected] HEAD -> branch (non-fast-forward)"
                )
            return original_run_git(repo, *args, **kwargs)
        with mock.patch("git_checkpoint.remote_branch_head", return_value="a" * 40), mock.patch(
            "git_checkpoint.run_git", side_effect=side_effect
        ):
            with self.assertRaisesRegex(git_checkpoint.GitCheckpointError, "git_divergence"):
                git_checkpoint.push_checkpoint(
                    self.task_dir, agent="ProjectManagerAgent", checkpoint_id=committed["checkpointId"]
                )

    def test_connector_complete_marks_remote_checkpoint_without_rewriting_local_head(self) -> None:
        self.enable_github_push_mode()
        with self.validate_patch():
            committed = git_checkpoint.commit_checkpoint(self.task_dir, agent="ProjectManagerAgent")
        repo = git_checkpoint.repository_root(self.task_dir)
        record = git_checkpoint.latest_checkpoint_record(self.task_dir, committed["checkpointId"])
        request = git_connector_transport.build_request(
            repo, remote="origin", remote_url="https://github.com/example/project.git",
            branch=git_checkpoint.branch_name(repo), checkpoint_id=committed["checkpointId"],
            local_commit_sha=committed["commitSha"], base_commit_sha=record["base_commit"],
        )
        receipt = self.connector_receipt(request)
        completed = git_checkpoint.complete_connector_push(
            self.task_dir, agent="ProjectManagerAgent", receipt=receipt, checkpoint_id=committed["checkpointId"]
        )
        self.assertTrue(completed["remoteVerified"])
        self.assertEqual("GITHUB_CONNECTOR", completed["transport"])
        self.assertEqual("f" * 40, completed["remoteHeadSha"])
        self.assertEqual(committed["commitSha"], git(self.repo, "rev-parse", "HEAD"))
        self.assertEqual("", git(self.repo, "status", "--porcelain"))
        retried = git_checkpoint.push_checkpoint(
            self.task_dir, agent="ProjectManagerAgent", checkpoint_id=committed["checkpointId"]
        )
        self.assertEqual("ALREADY_PUSHED_CONNECTOR", retried["status"])
        self.assertTrue(retried["remoteVerified"])
        self.assertEqual("f" * 40, retried["remoteCommitSha"])
        self.assertNotEqual(retried["commitSha"], retried["remoteCommitSha"])
        self.assertEqual(retried["remoteTreeSha"], git(self.repo, "rev-parse", "HEAD^{tree}"))

    def test_connector_complete_rejects_mismatched_receipt(self) -> None:
        self.enable_github_push_mode()
        with self.validate_patch():
            committed = git_checkpoint.commit_checkpoint(self.task_dir, agent="ProjectManagerAgent")
        repo = git_checkpoint.repository_root(self.task_dir)
        record = git_checkpoint.latest_checkpoint_record(self.task_dir, committed["checkpointId"])
        request = git_connector_transport.build_request(
            repo, remote="origin", remote_url="https://github.com/example/project.git",
            branch=git_checkpoint.branch_name(repo), checkpoint_id=committed["checkpointId"],
            local_commit_sha=committed["commitSha"], base_commit_sha=record["base_commit"],
        )
        receipt = self.connector_receipt(request)
        receipt["tree"]["sha"] = "a" * 40
        with self.assertRaisesRegex(git_checkpoint.GitCheckpointError, "connector_checkpoint_verification_failed"):
            git_checkpoint.complete_connector_push(
                self.task_dir, agent="ProjectManagerAgent", receipt=receipt, checkpoint_id=committed["checkpointId"]
            )

    def test_protected_branch_is_blocked_without_mutating_checkpoint_file(self) -> None:
        git(self.repo, "branch", "-m", "main")
        before = (self.task_dir / "git_checkpoints.yaml").read_bytes()
        with self.validate_patch(), self.assertRaisesRegex(git_checkpoint.GitCheckpointError, "protected branch"):
            git_checkpoint.commit_checkpoint(self.task_dir, agent="ProjectManagerAgent")
        self.assertEqual(before, (self.task_dir / "git_checkpoints.yaml").read_bytes())
        self.assertEqual("", git(self.repo, "diff", "--cached", "--name-only"))

    def test_non_sequential_execution_is_blocked(self) -> None:
        self.state["execution_mode"] = "UNSUPPORTED"
        self.sync_runtime(self.state)
        with self.validate_patch(), self.assertRaisesRegex(git_checkpoint.GitCheckpointError, "SEQUENTIAL"):
            git_checkpoint.commit_checkpoint(self.task_dir, agent="ProjectManagerAgent")

    def test_only_project_manager_can_commit(self) -> None:
        with self.assertRaisesRegex(git_checkpoint.GitCheckpointError, "ProjectManagerAgent"):
            git_checkpoint.commit_checkpoint(self.task_dir, agent="DesignAgent")

    def test_prior_passed_phase_can_be_selected_for_backfill(self) -> None:
        state = dict(self.state)
        state["artifact_revisions"] = dict(state["artifact_revisions"])
        state["artifact_revisions"]["requirement_confirmation"] = {
            "status": "PASSED", "revision": "REQCONF-R01@hash",
            "iteration_id": "ITER-MOD0001-REQUIREMENT-CONFIRMATION-001", "iteration_no": 1,
        }
        self.sync_runtime(state)
        outcomes = [self.outcome, {
            "id": "SO-MOD0001-REQUIREMENT-CONFIRMATION-I001",
            "phase": "requirement_confirmation",
            "iteration_id": "ITER-MOD0001-REQUIREMENT-CONFIRMATION-001",
            "iteration_no": 1, "is_current": True, "status": "PASSED",
            "output_revision": "REQCONF-R01@hash",
        }]
        self.sync_runtime(state, [{"type":"STAGE_OUTCOME_RECORDED","entity_id":outcomes[-1]["id"],"payload":{"outcome":outcomes[-1]}}])
        _, _, _, outcome = git_checkpoint.current_gate(
            self.task_dir, phase_override="requirement_confirmation"
        )
        self.assertEqual("REQCONF-R01@hash", outcome["output_revision"])

    def test_default_title_is_bounded_to_seventy_two_characters(self) -> None:
        title = git_checkpoint.default_title("TARGET-" + "X" * 120, "design")
        self.assertLessEqual(len(title), 72)
        self.assertTrue(title.startswith("docs(design): complete "))

    def test_markdown_checkpoint_file_is_not_used(self) -> None:
        self.assertFalse((self.task_dir / "git_checkpoints.md").exists())
        self.assertTrue((self.task_dir / "git_checkpoints.yaml").is_file())


if __name__ == "__main__":
    unittest.main()
