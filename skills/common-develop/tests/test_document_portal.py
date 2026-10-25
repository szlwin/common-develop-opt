from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import document_portal


class DocumentPortalTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "project_doc"
        self.version = "V_1.0"
        self.module = "ORDER"
        self.version_root = self.root / "version" / self.version
        self.module_root = self.version_root / "doc" / self.module
        self.module_root.mkdir(parents=True)
        self.root = self.root.resolve()
        self.version_root = self.root / "version" / self.version
        self.module_root = self.version_root / "doc" / self.module
        (self.version_root / "requirement_list.md").write_text("# requirements\n", encoding="utf-8")
        requirement = self.version_root / "doc" / "REQ-001" / "requirement.md"
        requirement.parent.mkdir(parents=True)
        requirement.write_text("# requirement\n", encoding="utf-8")
        (requirement.parent / "test_case.md").write_text("# test cases\n", encoding="utf-8")
        (self.module_root / "ORDER_desc.md").write_text("# module\n", encoding="utf-8")
        (self.module_root / "ORDER_design.md").write_text("# design\n", encoding="utf-8")
        changes = self.module_root / "changes"
        changes.mkdir()
        (changes / "order.yaml").write_text("schemaVersion: 1\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_builds_linked_project_and_version_portals(self) -> None:
        payloads = document_portal.build_payloads(self.root, versions=[self.version])
        project_path = self.root / "docs" / "README.md"
        version_path = self.version_root / "version_summary.md"
        self.assertEqual({project_path, version_path}, set(payloads))
        project = payloads[project_path].decode("utf-8")
        version = payloads[version_path].decode("utf-8")
        self.assertIn("[版本摘要](../version/V_1.0/version_summary.md)", project)
        self.assertIn("[项目文档首页](../../docs/README.md)", version)
        self.assertIn("[设计](doc/ORDER/ORDER_design.md)", version)
        self.assertIn("[需求正文](doc/REQ-001/requirement.md)", version)
        self.assertIn("[测试设计](doc/REQ-001/test_case.md)", version)
        self.assertIn("doc/ORDER/changes/order.yaml", version)

    def test_write_is_idempotent_and_validate_checks_links(self) -> None:
        first = document_portal.write_portals(self.root, versions=[self.version])
        before = {path: path.read_bytes() for path in first}
        second = document_portal.write_portals(self.root, versions=[self.version])
        self.assertEqual(before, {path: path.read_bytes() for path in second})
        result = document_portal.validate_existing(self.root, versions=[self.version])
        self.assertTrue(result["valid"], result["errors"])

    def test_validate_rejects_missing_and_escaping_links(self) -> None:
        portal = self.root / "docs" / "README.md"
        portal.parent.mkdir(parents=True)
        errors = document_portal.validate_links(
            portal,
            "[missing](missing.md) [escape](../../outside.md)",
            self.root,
        )
        self.assertTrue(any("does not exist" in value for value in errors))
        self.assertTrue(any("escapes DOC_ROOT" in value for value in errors))

    def test_pending_archive_payload_is_visible_to_portal(self) -> None:
        project_desc = self.root / "docs" / self.module / "ORDER_desc.md"
        project_model = self.root / "docs" / self.module / "ORDER_business_model.md"
        pending = {project_desc: b"# module\n", project_model: b"# model\n"}
        payloads = document_portal.build_payloads(self.root, versions=[self.version], pending=pending)
        project = payloads[self.root / "docs" / "README.md"].decode("utf-8")
        self.assertIn("[模块说明](ORDER/ORDER_desc.md)", project)
        self.assertIn("[业务模型](ORDER/ORDER_business_model.md)", project)


if __name__ == "__main__":
    unittest.main()
