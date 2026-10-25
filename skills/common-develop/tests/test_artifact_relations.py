from __future__ import annotations

import tempfile
import unittest
import argparse
import contextlib
import io
import json
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import artifact_relations
import simple_yaml


class ArtifactRelationsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "project_doc"
        module = self.root / "version" / "V_1.0" / "doc" / "ORD"
        project_module = self.root / "docs" / "ORD"
        relations = self.root / "docs" / "_relations"
        flows = self.root / "docs" / "_flows"
        for path in (module, project_module, relations, flows):
            path.mkdir(parents=True, exist_ok=True)
        (self.root / "docs" / "README.md").write_text("# 首页\n", encoding="utf-8")
        (module / "ORD_design.md").write_text(
            "# 订单设计\n\n"
            "> Design revision：`DESIGN-R01@demo`\n"
            "> Base revision：`DESIGN-R00@init`\n"
            "> Design topic：`DESIGN-ORD-LIFECYCLE`\n"
            "> Requirement：`REQ-ORD-015`\n\n"
            "REQ-ORD-015-F01 PAGE-ORD-DETAIL PAGEACT-ORD-CANCEL FLOW-ORD-CANCEL AGG-ORDER CMI-ORD-CANCEL AC-ORD-015\n",
            encoding="utf-8",
        )
        requirement_dir = self.root / "version" / "V_1.0" / "doc" / "REQ-ORD-015"
        requirement_dir.mkdir(parents=True)
        (requirement_dir / "requirement.md").write_text("# REQ-ORD-015\n", encoding="utf-8")
        (requirement_dir / "test_case.md").write_text(
            "# REQ-ORD-015 测试设计\n\n"
            "<!-- TEST-CASE: CASE-ORD-CANCEL-001 -->\n"
            '<a id="CASE-ORD-CANCEL-001"></a>\n'
            "## CASE-ORD-CANCEL-001 取消订单\n\n"
            "REQ-ORD-015 REQ-ORD-015-F01 PAGE-ORD-DETAIL PAGEACT-ORD-CANCEL "
            "DESIGN-ORD-LIFECYCLE FLOW-ORD-CANCEL STEP-ORD-CANCEL-01\n",
            encoding="utf-8",
        )
        (project_module / "ORD_desc.md").write_text(
            "# ORD 模块说明\n\n## 模块功能\n\n"
            "| 功能编码 | 功能名称 | 功能说明 | 所属能力 | 关联需求 | API 引用 | DB 引用 | 功能状态 |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
            "| REQ-ORD-015-F01 | 取消订单 | 取消未履约订单 | CAP-ORD | REQ-ORD-015 | 无 | 无 | ACTIVE |\n",
            encoding="utf-8",
        )
        simple_yaml.dump(
            {
                "artifact": "business_model", "revision": "BM-R01@demo",
                "aggregates": [{"id": "AGG-ORDER", "name": "订单"}],
                "invariants": [], "stateMachines": [], "events": [], "entities": [],
                "valueObjects": [], "services": [], "policies": [], "terms": [],
                "traceability": [{"traceId": "TR-ORD-015", "featureRefs": ["REQ-ORD-015-F01"], "modelRefs": ["AGG-ORDER"]}],
            },
            project_module / "ORD_business_model.yaml",
        )
        simple_yaml.dump(
            {
                "artifact": "dependency_impact", "revision": "DI-R01@demo",
                "nodes": [{"id": "REQ-ORD-015-F01", "name": "取消订单"}],
                "relationships": [], "impactPolicies": [],
                "crossModuleImplementations": [{"id": "CMI-ORD-CANCEL", "name": "订单取消协作", "steps": []}],
            },
            relations / "dependency_impact.yaml",
        )
        simple_yaml.dump(
            {
                "artifact": "business_flow", "revision": "FLOW-R01@demo",
                "flows": [{
                    "id": "FLOW-ORD-CANCEL", "name": "取消订单", "status": "ACTIVE",
                    "requirementRefs": [{"id": "REQ-ORD-015"}],
                    "featureRefs": [{"id": "REQ-ORD-015-F01"}],
                    "steps": [{"id": "STEP-ORD-CANCEL-01", "name": "校验订单"}],
                    "businessModelRefs": ["AGG-ORDER"],
                    "dependencyImpactRefs": ["CMI-ORD-CANCEL"],
                    "designRefs": ["DESIGN-ORD-LIFECYCLE"],
                    "testCaseIds": ["AC-ORD-015"],
                }],
            },
            flows / "ORDER_flow.yaml",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_builds_bidirectional_human_trace_views(self) -> None:
        payloads = artifact_relations.build_payloads(self.root)
        graph_path = (self.root / "docs" / "_relations" / "engineering_graph.yaml").resolve()
        graph = simple_yaml.loads(payloads[graph_path].decode("utf-8"))
        node_ids = {item["id"] for item in graph["nodes"]}
        self.assertTrue({
            "REQ-ORD-015", "REQ-ORD-015-F01", "PAGE-ORD-DETAIL",
            "DESIGN-ORD-LIFECYCLE", "FLOW-ORD-CANCEL", "AGG-ORDER",
            "CMI-ORD-CANCEL", "AC-ORD-015",
            "CASE-ORD-CANCEL-001",
        }.issubset(node_ids))
        relations = {(item["from"], item["to"], item["type"]) for item in graph["relationships"]}
        self.assertIn(("REQ-ORD-015", "REQ-ORD-015-F01", "CONTAINS"), relations)
        self.assertIn(("REQ-ORD-015-F01", "DESIGN-ORD-LIFECYCLE", "DESIGNED_BY"), relations)
        self.assertIn(("PAGEACT-ORD-CANCEL", "CASE-ORD-CANCEL-001", "VERIFIED_BY"), relations)
        self.assertIn(("DESIGN-ORD-LIFECYCLE", "CASE-ORD-CANCEL-001", "VERIFIED_BY"), relations)
        reachable = artifact_relations._reachable(graph, "REQ-ORD-015")
        self.assertTrue({
            "REQ-ORD-015-F01", "PAGE-ORD-DETAIL", "FLOW-ORD-CANCEL", "AGG-ORDER",
            "CMI-ORD-CANCEL", "DESIGN-ORD-LIFECYCLE", "AC-ORD-015",
        }.issubset(reachable))
        trace = payloads[(self.root / "docs" / "_relations" / "traceability_matrix.md").resolve()].decode("utf-8")
        self.assertIn("[REQ-ORD-015]", trace)
        self.assertIn("[DESIGN-ORD-LIFECYCLE]", trace)
        self.assertIn("## 设计反向来源", trace)

    def test_missing_relationship_node_fails_closed(self) -> None:
        graph = artifact_relations.Builder(self.root).build()
        graph["relationships"].append({
            "id": "ARREL-0000000000000000", "from": "REQ-ORD-015",
            "to": "DESIGN-MISSING-TOPIC", "type": "DESIGNED_BY",
            "sourceRefs": ["version/V_1.0/doc/ORD/ORD_design.md"],
        })
        with self.assertRaisesRegex(artifact_relations.ArtifactRelationError, "missing node"):
            artifact_relations.validate_document(graph, self.root)

    def test_query_supports_reverse_design_lookup(self) -> None:
        artifact_relations.write_views(self.root)
        graph = artifact_relations.load_graph(
            self.root / "docs" / "_relations" / "engineering_graph.yaml", self.root
        )
        incoming = [
            item for item in graph["relationships"]
            if item["to"] == "DESIGN-ORD-LIFECYCLE" and item["type"] == "DESIGNED_BY"
        ]
        self.assertTrue(any(item["from"] == "REQ-ORD-015-F01" for item in incoming))

    def test_requirement_query_returns_transitive_categories(self) -> None:
        artifact_relations.write_views(self.root)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            artifact_relations.command_query(argparse.Namespace(
                doc_root=str(self.root), graph="", ref="REQ-ORD-015", depth=5,
                json=True, agent="ProjectManagerAgent",
            ))
        payload = json.loads(output.getvalue())
        self.assertTrue({
            "feature", "page", "business_flow", "business_model", "cross_module",
            "design", "validation",
        }.issubset(payload["relatedByKind"]))

    def test_current_design_topic_and_section_anchors_are_preferred(self) -> None:
        current = self.root / "docs" / "ORD" / "design" / "lifecycle.md"
        current.parent.mkdir(parents=True)
        current.write_text(
            "# 当前订单设计\n\n"
            "> Design revision：`DESIGN-R01@demo`\n"
            "> Design topic：`DESIGN-ORD-LIFECYCLE`\n\n"
            '<a id="page-behavior"></a>\nPAGE-ORD-DETAIL\n'
            '<a id="data-write"></a>\nAGG-ORDER\n'
            '<a id="api-mapping"></a>\nREQ-ORD-015-F01\n'
            '<a id="code-blueprint"></a>\nFLOW-ORD-CANCEL\n',
            encoding="utf-8",
        )
        graph = artifact_relations.Builder(self.root).build()
        design = next(item for item in graph["nodes"] if item["id"] == "DESIGN-ORD-LIFECYCLE")
        self.assertEqual("docs/ORD/design/lifecycle.md", design["documentRef"])
        self.assertIn("docs/ORD/design/lifecycle.md#code-blueprint", design["sourceRefs"])


if __name__ == "__main__":
    unittest.main()
