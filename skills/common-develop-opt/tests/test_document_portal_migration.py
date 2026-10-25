from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from document_core import document_portal


def page_text(page_id: str, name: str) -> str:
    return (
        f"# {name}页面业务行为设计\n\n"
        "## 1. 设计信息\n\n"
        "| 项目 | 内容 |\n"
        "| --- | --- |\n"
        "| 模块 | IAM |\n"
        f"| 主页面编号 | {page_id} |\n"
        f"| 页面中文名称 | {name} |\n"
        "| Design revision | PAGE-R01@test |\n"
        "| Base revision | PAGE-R00@init |\n"
    )


class DocumentPortalMigrationTests(unittest.TestCase):
    def test_new_layer_builds_and_validates_portals(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project_doc"
            version = root / "version" / "V_1.0"
            (version / "requirement" / "REQ-001").mkdir(parents=True)
            (version / "requirement" / "requirement_list.md").write_text("# requirements\n", encoding="utf-8")
            (version / "requirement" / "REQ-001" / "requirement.md").write_text("# req\n", encoding="utf-8")
            payloads = document_portal.build_payloads(root, versions=["V_1.0"])
            self.assertIn(root.resolve() / "docs" / "README.md", payloads)
            document_portal.write_portals(root, versions=["V_1.0"])
            self.assertTrue(document_portal.validate_existing(root, versions=["V_1.0"])["valid"])

    def test_multiple_page_documents_are_listed_under_owner_module(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project_doc"
            page_dir = root / "version" / "V_1.0" / "module" / "IAM" / "page-design"
            page_dir.mkdir(parents=True)
            (page_dir / "login_design.md").write_text(
                page_text("PAGE-IAM-LOGIN", "登录"),
                encoding="utf-8",
            )
            (page_dir / "account_design.md").write_text(
                page_text("PAGE-IAM-ACCOUNT", "账号管理"),
                encoding="utf-8",
            )

            payloads = document_portal.build_payloads(root, versions=["V_1.0"])
            summary_path = root.resolve() / "version" / "V_1.0" / "version_summary.md"
            summary = payloads[summary_path].decode("utf-8")

            self.assertIn("page-design/login_design.md", summary)
            self.assertIn("page-design/account_design.md", summary)
            iam_row = next(line for line in summary.splitlines() if line.startswith("| IAM |"))
            self.assertIn("页面：登录", iam_row)
            self.assertIn("页面：账号管理", iam_row)

    def test_selected_version_is_not_blocked_by_broken_page_in_other_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project_doc"
            broken_dir = root / "version" / "V_1.0" / "module" / "IAM" / "page-design"
            selected_dir = root / "version" / "V_2.0" / "module" / "IAM" / "page-design"
            broken_dir.mkdir(parents=True)
            selected_dir.mkdir(parents=True)
            (broken_dir / "broken_design.md").write_text(
                page_text("PAGE-IAM-BROKEN", "旧页面") + "\n[broken](missing.md)\n",
                encoding="utf-8",
            )
            (selected_dir / "login_design.md").write_text(
                page_text("PAGE-IAM-LOGIN", "登录"),
                encoding="utf-8",
            )

            payloads = document_portal.build_payloads(root, versions=["V_2.0"])

            self.assertIn(root.resolve() / "version" / "V_2.0" / "version_summary.md", payloads)
            self.assertNotIn(root.resolve() / "version" / "V_1.0" / "version_summary.md", payloads)


if __name__ == "__main__":
    unittest.main()
