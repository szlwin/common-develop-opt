from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import merge_docs  # binds the split merge model to the public runtime contract
from _merge_docs.model import MergeError, design_index_payload, simulate_design_snapshots


class DesignTopicArchiveTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "project_doc"
        self.module = "ORD"
        self.version = "V_1.0"
        self.source_root = self.root / "version" / self.version / "doc" / self.module
        self.source_root.mkdir(parents=True)
        (self.root / "docs").mkdir(parents=True)
        (self.root / "docs" / "README.md").write_text("# 项目文档首页\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_design(self, revision: str, base: str) -> Path:
        path = self.source_root / f"{self.module}_design.md"
        path.write_text(
            "# 订单生命周期设计\n\n"
            f"> Design revision：`{revision}`\n"
            f"> Base revision：`{base}`\n"
            "> Design topic：`DESIGN-ORD-LIFECYCLE`\n"
            "> Requirement：`REQ-ORD-015`\n\n"
            '<a id="page-behavior"></a>\n## 页面行为\nPAGE-ORD-DETAIL\n\n'
            '<a id="data-write"></a>\n## 数据写入\nAGG-ORDER\n\n'
            '<a id="api-mapping"></a>\n## 接口\nREQ-ORD-015-F01\n\n'
            '<a id="code-blueprint"></a>\n## 代码改造\nFLOW-ORD-CANCEL\n',
            encoding="utf-8",
        )
        return path

    def test_snapshot_is_stable_linked_and_idempotent(self) -> None:
        self.write_design("DESIGN-R01@v1", "DESIGN-R00@init")
        records = simulate_design_snapshots(self.root, self.version, self.module)
        self.assertEqual(1, len(records))
        record = records[0]
        self.assertEqual("docs/ORD/design/lifecycle.md", record["targetRef"])
        self.assertIn("[来源版本摘要]", bytes(record["resultContent"]).decode("utf-8"))
        Path(record["targetPath"]).parent.mkdir(parents=True, exist_ok=True)
        Path(record["targetPath"]).write_bytes(record["resultContent"])
        self.assertEqual([], simulate_design_snapshots(self.root, self.version, self.module))
        index_path, content = design_index_payload(self.root, self.module, [])
        self.assertEqual(self.root / "docs" / "ORD" / "ORD_design.md", index_path)
        self.assertIn("[打开](design/lifecycle.md)", content.decode("utf-8"))

    def test_stale_base_revision_fails_closed(self) -> None:
        self.write_design("DESIGN-R01@v1", "DESIGN-R00@init")
        first = simulate_design_snapshots(self.root, self.version, self.module)[0]
        Path(first["targetPath"]).parent.mkdir(parents=True, exist_ok=True)
        Path(first["targetPath"]).write_bytes(first["resultContent"])
        self.write_design("DESIGN-R02@v2", "DESIGN-R00@wrong")
        with self.assertRaisesRegex(MergeError, "stale base revision"):
            simulate_design_snapshots(self.root, self.version, self.module)

    def test_page_behavior_is_archived_once_for_all_pages(self) -> None:
        page = self.source_root / f"{self.module}_page_design.md"
        page.write_text(
            "# ORD 页面业务行为设计\n\n"
            "| 项目 | 内容 |\n| --- | --- |\n"
            "| Design revision | PAGE-R01@v1 |\n"
            "| Base revision | PAGE-R00@init |\n\n"
            "PAGE-ORD-LIST PAGE-ORD-DETAIL PAGEACT-ORD-CANCEL\n",
            encoding="utf-8",
        )
        records = simulate_design_snapshots(self.root, self.version, self.module)
        page_record = next(item for item in records if item["artifactType"] == "page_behavior")
        self.assertEqual("PAGE-ORD-BEHAVIOR", page_record["stableId"])
        self.assertEqual("docs/ORD/design/pages/ORD_page_design.md", page_record["targetRef"])
        self.assertIn("PAGE-ORD-LIST", page_record["affectedStableIds"])
        self.assertIn("PAGE-ORD-DETAIL", page_record["affectedStableIds"])


if __name__ == "__main__":
    unittest.main()
