#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import io
import json
import unittest

import agent_context
import review_contract


class ReviewContractCliTests(unittest.TestCase):
    def test_list_filters_phase(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = review_contract.main([
                "list", "-g", "ProjectManagerAgent", "--phase", "testing", "--json"
            ])
        self.assertEqual(0, code)
        payload = json.loads(output.getvalue())
        self.assertEqual(1, payload["count"])
        self.assertEqual("testing:TestEvidenceReviewAgent", payload["profiles"][0]["profileId"])

    def test_show_expands_criteria(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = review_contract.main([
                "show", "-g", "SecurityReviewAgent", "--profile",
                "code_review:SecurityReviewAgent", "--json"
            ])
        self.assertEqual(0, code)
        payload = json.loads(output.getvalue())
        self.assertEqual("SecurityReviewAgent", payload["reviewerAgent"])
        self.assertGreater(len(payload["criteria"]), 0)
        self.assertIn("evidenceTypes", payload)

    def test_declared_agent_permission_rejects_unknown(self) -> None:
        with self.assertRaises(agent_context.AgentAuthorizationError):
            agent_context.authorize_declared("UnknownAgent", "review_contract", "list")


    def test_profile_rejects_extra_reviewer_selector(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot be combined"):
            review_contract.main([
                "show", "-g", "ProjectManagerAgent", "--profile",
                "design:ArchitectureReviewAgent", "--reviewer", "ArchitectureReviewAgent"
            ])

    def test_phase_requires_reviewer(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires --reviewer"):
            review_contract.main([
                "show", "-g", "ProjectManagerAgent", "--phase", "design"
            ])


if __name__ == "__main__":
    unittest.main()
