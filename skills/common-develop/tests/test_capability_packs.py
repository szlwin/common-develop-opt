#!/usr/bin/env python3
"""Tests for the single-Skill internal capability-pack architecture."""
from __future__ import annotations

import contextlib
import copy
import io
import json
import unittest

import capability_packs
from _core import capability_packs as core


class CapabilityPackTests(unittest.TestCase):
    def test_contract_has_one_authority_and_exactly_three_internal_packs(self) -> None:
        contract = core.load_contract()
        self.assertEqual(2, contract["schemaVersion"])
        self.assertEqual("common-develop", contract["singleAuthoritySkill"])
        self.assertEqual("ATOMIC", contract["releaseUnit"])
        self.assertEqual(list(core.PACK_IDS), [item["id"] for item in contract["packs"]])
        self.assertEqual([], core.validate_contract(contract))

    def test_design_composes_all_packs_with_one_lifecycle_writer(self) -> None:
        contract = core.load_contract()
        resolved = core.resolve("design", "design", contract)
        modes = {item["id"]: item["mode"] for item in resolved["packs"]}
        self.assertEqual("PRODUCE", modes["document-facts"])
        self.assertEqual("ORCHESTRATE", modes["task-runtime"])
        self.assertEqual("GATE", modes["review-verification"])
        writers = [
            item["id"] for item in contract["packs"]
            if item["mutationAuthorities"]["lifecycleEvents"] == "SOLE_WRITER"
        ]
        self.assertEqual(["task-runtime"], writers)

    def test_development_reads_document_facts_without_granting_document_mutation(self) -> None:
        resolved = core.resolve("development", "development")
        modes = {item["id"]: item["mode"] for item in resolved["packs"]}
        self.assertEqual("READ_ONLY", modes["document-facts"])
        document_pack = core.pack_by_id("document-facts")
        self.assertEqual("FORBIDDEN", document_pack["mutationAuthorities"]["lifecycleEvents"])

    def test_validator_rejects_second_lifecycle_writer_or_independent_version(self) -> None:
        contract = copy.deepcopy(core.load_contract())
        contract["packs"][0]["mutationAuthorities"]["lifecycleEvents"] = "SOLE_WRITER"
        contract["packs"][0]["packageVersion"] = "9.9"
        errors = core.validate_contract(contract)
        self.assertTrue(any("only lifecycle event writer" in item for item in errors))
        self.assertTrue(any("independent packageVersion" in item for item in errors))

    def test_public_query_lists_internal_not_independent_skills(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(0, capability_packs.main(["list", "-g", "ProjectManagerAgent", "--json"]))
        payload = json.loads(output.getvalue())
        self.assertEqual("common-develop", payload["singleAuthoritySkill"])
        self.assertEqual(list(core.PACK_IDS), [item["id"] for item in payload["packs"]])

    def test_contract_distinguishes_cli_entrypoints_and_module_interfaces(self) -> None:
        runtime = core.pack_by_id("task-runtime")
        self.assertIn("scripts/long_task.py", runtime["cliEntrypoints"])
        self.assertIn("scripts/work_record.py", runtime["moduleInterfaces"])
        self.assertNotIn("scripts/work_record.py", runtime["cliEntrypoints"])


if __name__ == "__main__":
    unittest.main()
