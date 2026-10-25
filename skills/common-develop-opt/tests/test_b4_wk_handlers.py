import unittest
from types import SimpleNamespace

from document_core.wk_handlers import build_registry, missing


class B4WKHandlerTests(unittest.TestCase):
    def test_registry_is_explicit_and_reports_unmigrated_handlers(self):
        source = SimpleNamespace(command_status=lambda args: 0, command_context=lambda args: 0)
        registry = build_registry(source)
        self.assertEqual("status", next(name for name in registry if name == "status"))
        self.assertIn("start", missing(registry))


if __name__ == "__main__":
    unittest.main()
