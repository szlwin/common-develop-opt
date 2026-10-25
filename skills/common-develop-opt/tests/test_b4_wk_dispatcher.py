import unittest
import contextlib
import io
from unittest import mock

from document_core import wk_dispatcher
from document_core.wk_dispatcher import COMMANDS, build_parser
from runtime import wk as runtime_wk


COMMAND_ARGS = {
    "start": ["-b", "bug", "--doc-root", "project_doc", "--version", "V_1"],
    "doctor": ["--doc-root", "project_doc", "--version", "V_1"],
    "record-result": ["--task-dir", "task", "--attempt-id", "A", "--command-id", "C",
                      "--result-file", "result.json", "--revision", "R", "--phase", "development"],
    "confirm": ["--task-dir", "task", "--until", "development", "--reason", "confirmed"],
    "document-archive": ["--version", "V_1", "--module", "module"],
    "delivery": ["--action", "plan"],
    "env": ["--project-root", "."],
    "packs": ["--task-type", "standard"],
    "help": [],
}


class B4WKDispatcherTests(unittest.TestCase):
    def test_all_declared_commands_are_registered(self):
        parser = build_parser({name: lambda args: 0 for name in COMMANDS})
        for name in COMMANDS:
            subparser = parser._subparsers._group_actions[0].choices[name]
            self.assertIsNotNone(subparser)
            self.assertTrue(callable(subparser.get_default("_handler")))

    def test_mode_aliases_and_sync_flags_match_argparse_contract(self):
        for parser in (build_parser(), runtime_wk.build_parser()):
            self.assert_mode_contract(parser)

    def assert_mode_contract(self, parser):
        base = ["start", "-b", "bug", "--doc-root", "project_doc", "--version", "V_1"]
        self.assertIsNone(parser.parse_args(base).lightweight)
        self.assertTrue(parser.parse_args(base).sync_existing_tasks)
        for flags in (("-wc", "--no-ws"), ("--no-ws", "-wc"), ("-wc", "-wc")):
            self.assertFalse(parser.parse_args([*base, *flags]).lightweight)
        pairs = [(flag, "--no-" + flag[1:]) for flag in ("-mc", "-v", "-min", "-auto", "-ar", "-gc", "-gp", "-review-only", "-ws")]
        pairs.extend([("-ws", "-wc"), ("--sync-existing-tasks", "--no-sync-existing-tasks")])
        for pair in pairs:
            for flags in (pair, tuple(reversed(pair))):
                with self.subTest(flags=flags), contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as error:
                        parser.parse_args([*base, *flags])
                    self.assertEqual(error.exception.code, 2)

    def test_runtime_adapter_delegates_to_canonical_factory(self):
        with mock.patch.object(wk_dispatcher, "build_parser", wraps=build_parser) as factory:
            parser = runtime_wk.build_parser()
        factory.assert_called_once()
        self.assertIsNotNone(parser)

    def test_all_runtime_commands_preserve_namespaces_and_callback_binding(self):
        canonical = build_parser()
        runtime = runtime_wk.build_parser()
        for command in COMMANDS:
            with self.subTest(command=command):
                argv = [command, *COMMAND_ARGS.get(command, ["--task-dir", "task"])]
                expected = vars(canonical.parse_args(argv))
                actual = vars(runtime.parse_args(argv))
                expected.pop("_handler")
                callback = actual.pop("func")
                self.assertEqual(expected, actual)
                self.assertIs(callback, getattr(runtime_wk, "command_" + command.replace("-", "_")))

    def test_runtime_main_keeps_patched_callback_and_argv_compatibility(self):
        cases = [
            ("command_help", ["help", "--flag", "-ar"], "flag", "-ar"),
            ("command_document_archive", ["-wd", "--version", "V_1", "--module", "module"], "wd", True),
        ]
        for name, argv, field, value in cases:
            with self.subTest(command=name), mock.patch.object(runtime_wk, name, return_value=7) as callback:
                self.assertEqual(runtime_wk.main(argv), 7)
                args = callback.call_args.args[0]
                self.assertEqual(getattr(args, field), value)
                self.assertTrue(args._legacy_output)

    def test_runtime_main_keeps_structured_error_boundary(self):
        with mock.patch.object(runtime_wk, "command_status", side_effect=ValueError("invalid task")):
            errors = io.StringIO()
            with contextlib.redirect_stderr(errors):
                self.assertEqual(runtime_wk.main(["status", "--task-dir", "task", "--json"]), 1)
            self.assertIn("invalid task", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
