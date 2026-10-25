from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import reference_resolution
import project_constraints
import governance_profile


class Stage4ReferenceGovernanceTests(unittest.TestCase):
    def test_markdown_heading_and_explicit_anchor_are_canonical(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            doc = root / "policy.md"
            doc.write_text(
                "# Policy\n\n## Review Gates\n\n<a id=\"custom-gate\"></a>\n## Other\n",
                encoding="utf-8",
            )
            heading = reference_resolution.resolve_reference("policy.md#review-gates", root, doc)
            anchor = reference_resolution.resolve_reference("policy.md#custom-gate", root, doc)
        self.assertEqual("heading", heading.kind)
        self.assertEqual("anchor", anchor.kind)

    def test_plain_text_fragment_cannot_forge_markdown_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            doc = root / "policy.md"
            doc.write_text("This mentions APG-NOT-CANONICAL but has no such heading.\n", encoding="utf-8")
            with self.assertRaises(reference_resolution.ReferenceResolutionError):
                reference_resolution.resolve_reference("policy.md#APG-NOT-CANONICAL", root, doc)

    def test_document_stem_is_a_deterministic_document_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            doc = root / "policy.md"
            doc.write_text("plain policy\n", encoding="utf-8")
            result = reference_resolution.resolve_reference("policy.md#policy", root, doc)
        self.assertEqual("document", result.kind)

    def test_json_pointer_resolves_exact_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            doc = root / "contract.json"
            doc.write_text(json.dumps({"gates": {"timeout": {"seconds": 300}}}), encoding="utf-8")
            result = reference_resolution.resolve_reference("contract.json#/gates/timeout/seconds", root, root / "decl.md")
        self.assertEqual("json-pointer", result.kind)
        self.assertEqual("/gates/timeout/seconds", result.pointer)

    def test_json_stable_id_requires_structured_identity_field(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "facts.json").write_text(
                json.dumps({"items": [{"id": "ART-001", "name": "canonical"}], "notes": "ART-FAKE"}),
                encoding="utf-8",
            )
            found = reference_resolution.resolve_reference("ART-001", root)
            self.assertEqual("/items/0", found.pointer)
            with self.assertRaises(reference_resolution.ReferenceResolutionError):
                reference_resolution.resolve_reference("ART-FAKE", root)

    def test_yaml_pointer_and_jsonl_stable_id_resolve(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "facts.yaml").write_text("items:\n  - id: YAML-001\n    name: item\n", encoding="utf-8")
            (root / "events.jsonl").write_text(json.dumps({"id": "EVENT-001", "status": "ACTIVE"}) + "\n", encoding="utf-8")
            yaml_result = reference_resolution.resolve_reference("facts.yaml#/items/0/name", root)
            jsonl_result = reference_resolution.resolve_reference("EVENT-001", root)
        self.assertEqual("json-pointer", yaml_result.kind)
        self.assertEqual("stable-id", jsonl_result.kind)
        self.assertEqual("/0", jsonl_result.pointer)

    def test_duplicate_stable_id_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ("a.json", "b.json"):
                (root / name).write_text(json.dumps({"id": "DUP-001"}), encoding="utf-8")
            index = reference_resolution.CanonicalArtifactIndex(root)
            self.assertEqual(["DUP-001"], index.summary()["duplicateIds"])
            with self.assertRaises(reference_resolution.ReferenceResolutionError):
                index.resolve("DUP-001")

    def test_relative_paths_use_declaration_document_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            declaration = root / "project_doc" / "project_env.md"
            declaration.parent.mkdir()
            declaration.write_text("", encoding="utf-8")
            (root / "project_doc" / "evidence.md").write_text("# Evidence\n", encoding="utf-8")
            result = reference_resolution.resolve_reference("evidence.md#evidence", root, declaration)
        self.assertEqual("project_doc/evidence.md", result.as_details(root)["path"])

    def test_project_constraints_passes_nested_declaration_references(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            docroot = root / "project_doc"
            docroot.mkdir()
            (docroot / "evidence.md").write_text("# Evidence\n## TASK\n", encoding="utf-8")
            (docroot / "project_env.md").write_text(
                "```json common-develop-constraints\n" + json.dumps({"constraints": [{
                    "id": "ASSUMPTION-001", "type": "ASSUMPTION", "statement": "x",
                    "owner": "ProjectManagerAgent", "source": "evidence.md#evidence",
                    "validation": "run", "verificationTask": "evidence.md#task",
                    "evidenceRefs": ["evidence.md#evidence"], "status": "VALIDATED"
                }]}) + "\n```\n", encoding="utf-8")
            payload = project_constraints.validate_project_constraints(root)
        self.assertEqual("PASSED", payload["status"])

    def test_public_governance_projection_has_no_internal_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            internal = governance_profile._recommend_internal(Path(temp), signals=["database"])
            public = governance_profile.recommend(Path(temp), signals=["database"])
            guide = governance_profile.build_decision_guide(internal)
        self.assertEqual(set(governance_profile._PUBLIC_PROFILE_FIELDS), set(public))
        self.assertNotIn("inputDigest", public)
        self.assertNotIn("graphImpact", public)
        self.assertNotIn("signals", guide)
        self.assertNotIn("override", guide)


if __name__ == "__main__":
    unittest.main()
