from __future__ import annotations

import argparse
import unittest

import business_flow
import evidence
import requirement_doc


class CliArgumentParityTests(unittest.TestCase):
    def subparsers(self, parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
        action = next(
            item for item in parser._actions
            if isinstance(item, argparse._SubParsersAction)
        )
        return dict(action.choices)

    def test_domain_parsers_keep_explicit_command_sets(self) -> None:
        self.assertEqual(
            {"init", "validate", "render", "impact"},
            set(self.subparsers(business_flow.parser())),
        )
        self.assertEqual(
            {"init", "sync-list", "validate", "status", "sync-traceability", "finalize-requirement-analysis"},
            set(self.subparsers(requirement_doc.build_parser())),
        )
        self.assertEqual(
            {
                "register",
                "snapshot-register",
                "bundle-register",
                "validate",
                "query",
                "show",
                "status",
                "repair-history",
            },
            set(self.subparsers(evidence.build_parser())),
        )

    def test_all_domain_commands_use_common_agent_and_handler_contract(self) -> None:
        parsers = (
            self.subparsers(business_flow.parser()),
            self.subparsers(requirement_doc.build_parser()),
            self.subparsers(evidence.build_parser()),
        )
        for commands in parsers:
            for name, parser in commands.items():
                destinations = {action.dest for action in parser._actions}
                self.assertIn("agent", destinations, name)
                self.assertIn("handler", parser._defaults, name)
                self.assertIs(parser._defaults["handler"], parser._defaults["func"], name)

    def test_common_destinations_are_stable(self) -> None:
        business = self.subparsers(business_flow.parser())
        requirement = self.subparsers(requirement_doc.build_parser())
        evidence_parsers = self.subparsers(evidence.build_parser())
        self.assertIn("doc_root", {a.dest for a in business["init"]._actions})
        self.assertIn("file", {a.dest for a in business["validate"]._actions})
        self.assertIn("task_dir", {a.dest for a in evidence_parsers["register"]._actions})
        self.assertIn("json", {a.dest for a in requirement["validate"]._actions})
        self.assertIn("doc_root", {a.dest for a in requirement["init"]._actions})

    def test_business_flow_parser_public_alias_remains_callable(self) -> None:
        parser = business_flow.parser()
        args = parser.parse_args(["impact", "--agent", "ProjectManagerAgent", "--doc-root", "project_doc"])
        self.assertEqual("impact", args.command)
        self.assertEqual("ProjectManagerAgent", args.agent)
        self.assertEqual(args.handler, args.func)
