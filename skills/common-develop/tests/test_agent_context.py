#!/usr/bin/env python3
"""Tests for the shared current-Agent command contract."""

from __future__ import annotations

import argparse
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

import _bootstrap
import agent_context
import check_skill
import long_task
import manual_review
import merge_docs
import render_relationships
import review_contract
import work_mode


class AgentContextTests(unittest.TestCase):
    def test_default_and_known_agent_selection(self) -> None:
        self.assertEqual("ProjectManagerAgent", agent_context.default_agent())
        self.assertEqual(
            "ProjectManagerAgent",
            agent_context.selected_agent(argparse.Namespace()),
        )
        self.assertEqual(
            "DesignAgent",
            agent_context.selected_agent(argparse.Namespace(agent="DesignAgent")),
        )

    def test_static_authorization_rejects_wrong_agent(self) -> None:
        with self.assertRaisesRegex(
            agent_context.AgentAuthorizationError,
            "not authorized for merge_docs.archive",
        ):
            agent_context.authorize_static(
                "ProjectManagerAgent", "merge_docs", "archive"
            )
        agent_context.authorize_static(
            "IncrementalArchiveAgent", "merge_docs", "archive"
        )

    def test_declared_agent_authorization(self) -> None:
        agent_context.authorize_declared("SecurityReviewAgent", "review_contract", "show")
        with self.assertRaises(agent_context.AgentAuthorizationError):
            agent_context.authorize_declared("UnknownAgent", "review_contract", "show")

    def test_authorize_command_combines_selection_and_authorization(self) -> None:
        self.assertEqual(
            "IncrementalArchiveAgent",
            agent_context.authorize_command(
                argparse.Namespace(agent="IncrementalArchiveAgent"),
                "merge_docs",
                "archive",
            ),
        )
        self.assertEqual(
            "SecurityReviewAgent",
            agent_context.authorize_command(
                argparse.Namespace(agent="SecurityReviewAgent"),
                "review_contract",
                "show",
                declared=True,
            ),
        )

    def test_phase_authorization_does_not_enable_untriggered_conditional_reviewer(self) -> None:
        with self.assertRaisesRegex(
            agent_context.AgentAuthorizationError,
            "not authorized for long_task.validate",
        ):
            agent_context.authorize_task_context(
                "ImpactAnalysisReviewAgent",
                "long_task",
                "validate",
                phase="requirement_analysis",
            )
        agent_context.authorize_task_context(
            "ImpactAnalysisReviewAgent",
            "long_task",
            "validate",
            phase="requirement_analysis",
            conditional_reviewers=["ImpactAnalysisReviewAgent"],
        )


    def test_project_manager_authorization_comes_from_task_state(self) -> None:
        with self.assertRaisesRegex(
            agent_context.AgentAuthorizationError,
            "not authorized for long_task.validate",
        ):
            agent_context.authorize_task_context(
                "ProjectManagerAgent",
                "long_task",
                "validate",
                phase="design",
            )
        agent_context.authorize_task_context(
            "ProjectManagerAgent",
            "long_task",
            "validate",
            phase="design",
            project_manager_agent="ProjectManagerAgent",
        )

    def test_all_executable_parsers_expose_agent_selector(self) -> None:
        parsers = (
            check_skill.parser(),
            long_task.build_parser(),
            merge_docs.parser(),
            render_relationships.parser(),
            review_contract.build_parser(),
            work_mode.build_parser(),
            manual_review.build_parser(),
        )
        for parser in parsers:
            self.assertTrue(agent_context.parser_has_agent_selector(parser))

    def test_long_task_init_rejects_non_manager(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            args = argparse.Namespace(
                doc_root=str(Path(temp) / "project_doc"),
                version="V_TEST",
                target="MOD0001",
                reuse=False,
                agent="DesignAgent",
            )
            with self.assertRaisesRegex(
                agent_context.AgentAuthorizationError,
                "not authorized for long_task.init",
            ):
                long_task.command_init(args)

    def test_render_generation_rejects_review_only_agent(self) -> None:
        parser = render_relationships.parser()
        parsed = parser.parse_args(
            [
                "-g",
                "SecurityReviewAgent",
                "--input",
                "dependency_impact.yaml",
                "--output",
                "relations.md",
            ]
        )
        with self.assertRaisesRegex(
            agent_context.AgentAuthorizationError,
            "not authorized for render_relationships.generate",
        ):
            agent_context.authorize_static(
                parsed.agent, "render_relationships", "generate"
            )

    def test_check_skill_cli_reports_selected_agent(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = check_skill.main_for_args(
                ["-g", "EngineeringStandardsReviewAgent", "--json"]
            )
        self.assertEqual(0, result)
        self.assertIn('"currentAgent": "EngineeringStandardsReviewAgent"', output.getvalue())


if __name__ == "__main__":
    unittest.main()
