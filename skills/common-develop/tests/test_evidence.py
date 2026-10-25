#!/usr/bin/env python3
"""Regression tests for immutable evidence snapshots."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import evidence
import review_store


class EvidenceSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.task_dir = Path(self.temp.name) / "project_doc" / "version" / "V_TEST" / "task" / "TARGET"
        self.task_dir.mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_validation_caches_are_bounded_and_clearable(self) -> None:
        evidence.clear_validation_caches()
        for number in range(evidence.FILE_DIGEST_CACHE_LIMIT + 3):
            key = (f"/tmp/file-{number}", number, number, number)
            evidence._cache_put(evidence._FILE_DIGEST_CACHE, key, str(number), evidence.FILE_DIGEST_CACHE_LIMIT)
        for number in range(evidence.GIT_VALIDATION_CACHE_LIMIT + 3):
            key = ("/repo", str(number), "path", "oid", "digest", number)
            evidence._cache_put(evidence._GIT_VALIDATION_CACHE, key, [], evidence.GIT_VALIDATION_CACHE_LIMIT)
        for number in range(evidence.INDEX_VALIDATION_CACHE_LIMIT + 3):
            key = ("/task", str(number), str(number))
            evidence._cache_put(evidence._INDEX_VALIDATION_CACHE, key, ([], {}), evidence.INDEX_VALIDATION_CACHE_LIMIT)
        self.assertLessEqual(len(evidence._FILE_DIGEST_CACHE), evidence.FILE_DIGEST_CACHE_LIMIT)
        self.assertLessEqual(len(evidence._GIT_VALIDATION_CACHE), evidence.GIT_VALIDATION_CACHE_LIMIT)
        self.assertLessEqual(len(evidence._INDEX_VALIDATION_CACHE), evidence.INDEX_VALIDATION_CACHE_LIMIT)
        evidence.clear_validation_caches()
        self.assertEqual(0, len(evidence._FILE_DIGEST_CACHE))
        self.assertEqual(0, len(evidence._GIT_VALIDATION_CACHE))
        self.assertEqual(0, len(evidence._INDEX_VALIDATION_CACHE))

    def init_git_repo(self, *paths: Path) -> None:
        repo = Path(self.temp.name)
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test User"], check=True)
        subprocess.run(["git", "-C", str(repo), "add", *[str(path.relative_to(repo)) for path in paths]], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "fixture"], check=True)

    def test_plan_ref_uses_immutable_snapshot(self) -> None:
        plan = self.task_dir / "task_plan.json"
        plan.write_text("plan v1\n", encoding="utf-8")
        entry = evidence.register_evidence(
            self.task_dir,
            agent="ProjectManagerAgent",
            evidence_type="plan_ref",
            ref="task_plan.json",
            revision="PLAN-R01@abc",
            phase="implementation_plan",
            scope_refs=["PLAN-R01"],
        )
        self.assertEqual("SNAPSHOT", entry["capture_mode"])
        self.assertEqual("task_plan.json", entry["source_ref"])
        self.assertIn("evidence/objects/", entry["ref"])
        plan.write_text("plan v2\n", encoding="utf-8")
        errors, _ = evidence.validate_registry(self.task_dir)
        self.assertEqual([], errors)

    def test_validation_cache_rechecks_snapshot_content_after_equal_size_tamper(self) -> None:
        source = self.task_dir / "design.md"
        source.write_text("design-v1\n", encoding="utf-8")
        entry = evidence.register_evidence(
            self.task_dir, agent="DesignAgent", evidence_type="design_ref",
            ref="design.md", revision="DESIGN-R01@abc", phase="design",
        )
        self.assertEqual([], evidence.validate_registry(self.task_dir)[0])
        snapshot = evidence.resolve_task_ref(self.task_dir, entry["ref"])
        original = snapshot.read_bytes()
        stat = snapshot.stat()
        snapshot.write_bytes(bytes([original[0] ^ 1]) + original[1:])
        os.utime(snapshot, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        errors, _ = evidence.validate_registry(self.task_dir)
        self.assertTrue(any("digest mismatch" in item for item in errors), errors)

    def test_require_evidence_reuses_prevalidated_entries(self) -> None:
        source = self.task_dir / "design.md"
        source.write_text("design-v1\n", encoding="utf-8")
        registered = evidence.register_evidence(
            self.task_dir,
            agent="DesignAgent",
            evidence_type="design_ref",
            ref="design.md",
            revision="DESIGN-R01@abc",
            phase="design",
        )
        errors, entries = evidence.validate_registry(self.task_dir)
        self.assertEqual([], errors)
        with mock.patch.object(evidence, "validate_registry", side_effect=AssertionError("unexpected revalidation")):
            resolved = evidence.require_evidence(
                self.task_dir,
                registered["evidence_id"],
                expected_revision="DESIGN-R01@abc",
                validated_entries=entries,
            )
        self.assertEqual(registered["evidence_id"], resolved["evidence_id"])

    def test_current_revision_validation_defers_broken_historical_sources(self) -> None:
        old = self.task_dir / "old.md"
        current = self.task_dir / "current.md"
        old.write_text("old\n", encoding="utf-8")
        current.write_text("current\n", encoding="utf-8")
        evidence.register_evidence(
            self.task_dir, agent="DesignAgent", evidence_type="design_ref",
            ref="old.md", revision="DESIGN-R01@old", phase="design",
        )
        evidence.register_evidence(
            self.task_dir, agent="DesignAgent", evidence_type="design_ref",
            ref="current.md", revision="DESIGN-R02@current", phase="design",
        )
        old_entry = evidence.load_registry(self.task_dir)["evidences"][0]
        evidence.resolve_task_ref(self.task_dir, old_entry["ref"]).unlink()
        errors, entries = evidence.validate_registry(
            self.task_dir, current_revisions={"DESIGN-R02@current"}
        )
        self.assertEqual([], errors)
        self.assertEqual(2, len(entries))
        current_id = entries[next(
            key for key, value in entries.items()
            if value.get("revision") == "DESIGN-R02@current"
        )]["evidence_id"]
        self.assertEqual(
            current_id,
            evidence.require_evidence(
                self.task_dir,
                current_id,
                expected_revision="DESIGN-R02@current",
            )["evidence_id"],
        )
        full_errors, _ = evidence.validate_registry(self.task_dir)
        self.assertTrue(any("old.md" in item or "digest mismatch" in item for item in full_errors))

    def test_revision_scoped_query_ignores_historical_digest_drift(self) -> None:
        old = self.task_dir / "design.md"
        current = self.task_dir / "design-r11.md"
        old.write_text("DESIGN-P3-R09\n", encoding="utf-8")
        current.write_text("DESIGN-P3-R11\n", encoding="utf-8")
        old_entry = evidence.register_evidence(
            self.task_dir, agent="DesignAgent", evidence_type="design_ref",
            ref="design.md", revision="DESIGN-P3-R09", phase="design",
            capture_mode="SNAPSHOT",
        )
        evidence.register_evidence(
            self.task_dir, agent="DesignAgent", evidence_type="design_ref",
            ref="design-r11.md", revision="DESIGN-P3-R11", phase="design",
        )
        snapshot = evidence.resolve_task_ref(self.task_dir, old_entry["ref"])
        snapshot.write_text("tampered historical snapshot\n", encoding="utf-8")

        rows = evidence.query_entries(
            self.task_dir, phase="design", revision="DESIGN-P3-R11", limit=20,
        )
        self.assertEqual(["DESIGN-P3-R11"], [row["revision"] for row in rows])

    def test_direct_capture_rejects_mutable_source(self) -> None:
        (self.task_dir / "task_plan.json").write_text("state\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "requires SNAPSHOT"):
            evidence.register_evidence(
                self.task_dir,
                agent="ProjectManagerAgent",
                evidence_type="document_ref",
                ref="task_plan.json",
                revision="REQ-R01@abc",
                phase="requirement_confirmation",
                capture_mode="DIRECT",
            )

    def test_repair_history_rebinds_direct_record_without_changing_identity(self) -> None:
        source = self.task_dir / "requirement.md"
        original = b"requirement revision seven\n"
        source.write_bytes(original)
        registered = evidence.register_evidence(
            self.task_dir,
            agent="RequirementConfirmationAgent",
            evidence_type="requirement_ref",
            ref="requirement.md",
            revision="REQCONF-R07@old",
            phase="requirement_confirmation",
            capture_mode="DIRECT",
        )
        digest = registered["digest"]
        objects = self.task_dir / "evidence" / "objects"
        objects.mkdir(parents=True)
        (objects / f"{digest}.md").write_bytes(original)
        source.write_bytes(b"requirement revision eight\n")

        repaired = evidence.repair_history(self.task_dir, evidence_id=registered["evidence_id"])

        self.assertEqual(registered["evidence_id"], repaired["evidence_id"])
        self.assertEqual(digest, repaired["digest"])
        self.assertEqual("SNAPSHOT", repaired["capture_mode"])
        self.assertEqual("requirement.md", repaired["source_ref"])
        self.assertIn("evidence/objects/", repaired["ref"])
        self.assertEqual([], evidence.validate_registry(self.task_dir)[0])
        self.assertEqual(repaired, evidence.repair_history(self.task_dir, evidence_id=registered["evidence_id"]))

    def test_repair_history_requires_matching_content_addressed_object(self) -> None:
        source = self.task_dir / "requirement.md"
        source.write_text("requirement\n", encoding="utf-8")
        registered = evidence.register_evidence(
            self.task_dir,
            agent="RequirementConfirmationAgent",
            evidence_type="requirement_ref",
            ref="requirement.md",
            revision="REQCONF-R07@old",
            phase="requirement_confirmation",
            capture_mode="DIRECT",
        )
        (self.task_dir / "evidence" / "objects").mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, "exactly one matching immutable object"):
            evidence.repair_history(self.task_dir, evidence_id=registered["evidence_id"])

    def test_index_storage_is_dictionary_compacted_and_round_trips(self) -> None:
        logical = evidence.default_registry(self.task_dir)
        logical["evidences"] = []
        for number in range(1, 7):
            logical["evidences"].append({
                "evidence_id": f"EVD-{number:06d}", "type": "document_ref",
                "ref": f"doc-{number}.md", "source_ref": "", "capture_mode": "DIRECT",
                "digest": f"{number:064x}", "revision": "REQ-R01@abc",
                "phase": "requirement_analysis", "generated_by_agent": "RequirementAnalysisAgent",
                "used_by_agents": ["RequirementAnalysisAgent", "DesignAgent"],
                "used_as_types": ["document_ref"], "generated_at": "2026-07-27T00:00:00+00:00",
                "command_result_ref": "", "command_result_digest": "",
                "scope_refs": ["BR-P1-001", "AC-P1-001"], "status": "ACTIVE",
                "superseded_by": "", "metadata": {},
            })
        expanded_size = len(json.dumps(logical, ensure_ascii=False, separators=(",", ":")))
        evidence.save_registry(self.task_dir, logical)
        lines = [
            json.loads(line)
            for line in evidence.registry_path(self.task_dir).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual("EVIDENCE_REGISTRY", lines[0]["kind"])
        self.assertEqual(["BR-P1-001", "AC-P1-001"], lines[1]["scope_refs"])
        self.assertLess(evidence.registry_path(self.task_dir).stat().st_size, expanded_size)
        self.assertEqual(logical, evidence.load_registry(self.task_dir))

    def test_same_artifact_is_reused_across_agents_and_scopes(self) -> None:
        source = self.task_dir / "design.md"
        source.write_text("# design\n", encoding="utf-8")
        first = evidence.register_evidence(
            self.task_dir, agent="DesignAgent", evidence_type="design_ref",
            ref="design.md", revision="DESIGN-R01@abc", phase="design",
            scope_refs=["RC-ARCH-001"],
        )
        second = evidence.register_evidence(
            self.task_dir, agent="ArchitectureReviewAgent", evidence_type="diagram_ref",
            ref="design.md", revision="DESIGN-R01@abc", phase="design",
            scope_refs=["RC-QUALITY-001"],
        )
        self.assertEqual(first["evidence_id"], second["evidence_id"])
        index = evidence.load_registry(self.task_dir)
        self.assertEqual(1, len(index["evidences"]))
        item = index["evidences"][0]
        self.assertEqual(
            ["DesignAgent", "ArchitectureReviewAgent"],
            item["used_by_agents"],
        )
        self.assertEqual(["design_ref", "diagram_ref"], item["used_as_types"])
        self.assertEqual(
            ["RC-ARCH-001", "RC-QUALITY-001"],
            item["scope_refs"],
        )

    def test_supersede_allows_same_content_at_new_canonical_ref(self) -> None:
        source_copy = self.task_dir / "source-copy.md"
        canonical = self.task_dir / "canonical.md"
        source_copy.write_text("same content\n", encoding="utf-8")
        canonical.write_text("same content\n", encoding="utf-8")
        old = evidence.register_evidence(
            self.task_dir, agent="DesignAgent", evidence_type="design_ref",
            ref="source-copy.md", revision="DESIGN-R01@abc", phase="design",
        )
        new = evidence.register_evidence(
            self.task_dir, agent="DesignAgent", evidence_type="design_ref",
            ref="canonical.md", revision="DESIGN-R01@abc", phase="design",
        )
        changed = evidence.change_status(
            self.task_dir, evidence_id=old["evidence_id"], status="SUPERSEDED",
            replacement_id=new["evidence_id"],
        )
        self.assertEqual("SUPERSEDED", changed["status"])
        self.assertEqual(new["evidence_id"], changed["superseded_by"])

    def test_query_filters_and_limits_registry_context(self) -> None:
        for index, phase in enumerate(("design", "design", "business_model"), 1):
            source = self.task_dir / f"artifact-{index}.md"
            source.write_text(f"artifact {index}\n", encoding="utf-8")
            evidence.register_evidence(
                self.task_dir, agent="DesignAgent" if phase == "design" else "BusinessModelAgent",
                evidence_type="design_ref" if phase == "design" else "model_ref",
                ref=source.name, revision="DESIGN-R01@abc" if phase == "design" else "BM-R01@abc",
                phase=phase, scope_refs=[f"SCOPE-{index}"],
            )
        rows = evidence.query_entries(self.task_dir, phase="design", limit=1)
        self.assertEqual(1, len(rows))
        self.assertEqual("design", rows[0]["phase"])
        scoped = evidence.query_entries(self.task_dir, scope="SCOPE-2", limit=20)
        self.assertEqual(1, len(scoped))
        self.assertIn("SCOPE-2", scoped[0]["scope_refs"])

    def test_evidence_registry_does_not_implicitly_discover_version_docs(self) -> None:
        wrong = self.task_dir.parent.parent / "docs" / "_flows" / "FLOW.yaml"
        wrong.parent.mkdir(parents=True, exist_ok=True)
        wrong.write_text("unregistered content\n", encoding="utf-8")
        self.assertEqual([], evidence.query_entries(self.task_dir, limit=20))

    def test_bundle_register_uses_one_physical_archive_for_multiple_sources(self) -> None:
        first = self.task_dir / "first.md"
        second = self.task_dir / "second.xml"
        first.write_text("# first\n", encoding="utf-8")
        second.write_text("<second/>\n", encoding="utf-8")
        entry = evidence.register_bundle(
            self.task_dir, agent="RequirementConfirmationAgent",
            items=[
                {"type": "document_ref", "ref": "first.md"},
                {"type": "config_ref", "ref": "second.xml"},
            ],
            revision="REQ-R02@bundle", phase="requirement_confirmation",
            scope_refs=["REQ-R02"],
        )
        self.assertEqual("BUNDLE", entry["capture_mode"])
        self.assertEqual(["document_ref", "config_ref"], entry["used_as_types"])
        self.assertEqual(2, len(entry["metadata"]["members"]))
        bundle_path = evidence.resolve_task_ref(self.task_dir, entry["ref"])
        self.assertTrue(bundle_path.is_file())
        self.assertEqual([], evidence.validate_registry(self.task_dir)[0])
        physical = list((self.task_dir / "evidence" / "objects").rglob("*.tar.xz"))
        self.assertEqual(1, len(physical))

    def test_bundle_register_is_deterministic_and_reused(self) -> None:
        for name in ("a.md", "b.md"):
            (self.task_dir / name).write_text(name + "\n", encoding="utf-8")
        kwargs = dict(
            task_dir=self.task_dir, agent="RequirementConfirmationAgent",
            items=[
                {"type": "document_ref", "ref": "b.md"},
                {"type": "requirement_ref", "ref": "a.md"},
            ],
            revision="REQ-R02@bundle", phase="requirement_confirmation",
            scope_refs=["REQ-R02"],
        )
        first = evidence.register_bundle(**kwargs)
        second = evidence.register_bundle(**kwargs)
        self.assertEqual(first["evidence_id"], second["evidence_id"])
        self.assertEqual(first["digest"], second["digest"])
        self.assertEqual(1, len(evidence.load_registry(self.task_dir)["evidences"]))

    def test_clean_tracked_source_uses_git_ref_and_survives_worktree_change(self) -> None:
        plan = self.task_dir / "task_plan.json"
        plan.write_text("committed plan\n", encoding="utf-8")
        self.init_git_repo(plan)
        entry = evidence.register_evidence(
            self.task_dir,
            agent="ProjectManagerAgent",
            evidence_type="plan_ref",
            ref="task_plan.json",
            revision="PLAN-R01@git",
            phase="implementation_plan",
        )
        self.assertEqual("GIT_REF", entry["capture_mode"])
        self.assertTrue(entry["ref"].startswith("git:"))
        self.assertFalse((self.task_dir / "evidence" / "snapshots").exists())
        plan.write_text("uncommitted plan\n", encoding="utf-8")
        self.assertEqual([], evidence.validate_registry(self.task_dir)[0])

    def test_committed_bundle_members_use_one_git_ref(self) -> None:
        first = self.task_dir / "first.md"
        second = self.task_dir / "second.xml"
        first.write_text("# first\n", encoding="utf-8")
        second.write_text("<second/>\n", encoding="utf-8")
        self.init_git_repo(first, second)
        entry = evidence.register_bundle(
            self.task_dir,
            agent="RequirementConfirmationAgent",
            items=[
                {"type": "document_ref", "ref": "first.md"},
                {"type": "config_ref", "ref": "second.xml"},
            ],
            revision="REQ-R02@git",
            phase="requirement_confirmation",
        )
        self.assertEqual("GIT_REF", entry["capture_mode"])
        self.assertEqual(2, len(entry["metadata"]["git_ref"]["members"]))
        self.assertFalse((self.task_dir / "evidence" / "bundles").exists())
        self.assertEqual([], evidence.validate_registry(self.task_dir)[0])

    def test_dirty_tracked_source_falls_back_to_snapshot(self) -> None:
        plan = self.task_dir / "task_plan.json"
        plan.write_text("committed plan\n", encoding="utf-8")
        self.init_git_repo(plan)
        plan.write_text("dirty plan\n", encoding="utf-8")
        entry = evidence.register_evidence(
            self.task_dir,
            agent="ProjectManagerAgent",
            evidence_type="plan_ref",
            ref="task_plan.json",
            revision="PLAN-R02@dirty",
            phase="implementation_plan",
        )
        self.assertEqual("SNAPSHOT", entry["capture_mode"])
        self.assertTrue(evidence.resolve_task_ref(self.task_dir, entry["ref"]).is_file())

if __name__ == "__main__":
    unittest.main()

class EvidenceRolePolicyTests(unittest.TestCase):
    def test_review_policy_uses_all_compact_evidence_roles(self) -> None:
        import long_task
        ok, reason = long_task.evidence_satisfies_policy(
            [{"type": "document_ref", "types": ["document_ref", "design_ref", "runtime_ref"]}],
            {"evidencePolicy": {"minimumEvidenceCount": 1, "anyOfTypes": ["runtime_ref"]}},
        )
        self.assertTrue(ok, reason)
