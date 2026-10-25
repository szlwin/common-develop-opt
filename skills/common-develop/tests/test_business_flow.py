#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import business_flow
import document_facts
import simple_yaml


class BusinessFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "project_doc"
        self.version = "V_1.0"
        self.target = "V_1.0_0030"
        req = self.root / "version" / self.version / "doc" / self.target / "requirement.md"
        req.parent.mkdir(parents=True, exist_ok=True)
        req.write_text("# requirement\nORD0013\nORD0013-F03\nORD0013-F04\nBR-ORD0013-001\nAC-ORD0013-001\nTR-ORD0013-001\n", encoding="utf-8")
        for module, feature in (("ORD", "ORD0013-F03"), ("PAY", "ORD0013-F04")):
            path = self.root / "docs" / module / f"{module}_desc.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            text = document_facts.module_desc_template(module, self.version, project=True)
            lines = text.splitlines()
            start, end, _ = document_facts.find_table(lines, "## 模块功能", document_facts.FEATURE_HEADERS)
            lines[start:end] = document_facts.render_table(document_facts.FEATURE_HEADERS, [[
                feature, "功能", "说明", f"CAP-{module}-CORE", "ORD0013", "无", "无", "ACTIVE"
            ]])
            cap_start, cap_end, _ = document_facts.find_table(lines, "## 业务能力", document_facts.CAPABILITY_HEADERS)
            lines[cap_start:cap_end] = document_facts.render_table(document_facts.CAPABILITY_HEADERS, [[
                f"CAP-{module}-CORE", "核心能力", "说明", "边界", "ACTIVE"
            ]])
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def flow(self) -> dict:
        return {
            "id": "FLOW-ORDER-E2E", "name": "订单端到端", "level": "L0", "type": "MAIN",
            "ownerModule": "ORD", "participantModules": ["ORD", "PAY"],
            "goal": "完成订单支付", "trigger": "用户提交订单",
            "requirementRefs": [{"id": "ORD0013", "documentRef": f"version/{self.version}/doc/{self.target}/requirement.md"}],
            "featureRefs": [{"id": "ORD0013-F03", "module": "ORD"}, {"id": "ORD0013-F04", "module": "PAY"}],
            "ruleRefs": ["BR-ORD0013-001"], "acceptanceRefs": ["AC-ORD0013-001"], "traceIds": ["TR-ORD0013-001"],
            "preconditions": ["用户有效"],
            "steps": [
                {"id": "STEP-ORDER-01", "order": 1, "name": "创建订单", "ownerModule": "ORD", "featureRef": "ORD0013-F03", "preconditions": [], "postconditions": ["待支付"]},
                {"id": "STEP-ORDER-02", "order": 2, "name": "支付", "ownerModule": "PAY", "featureRef": "ORD0013-F04", "preconditions": ["订单可支付"], "postconditions": ["形成结果"]},
            ],
            "variants": [],
            "failurePaths": [{"id": "FAIL-ORDER-001", "atStep": "STEP-ORDER-02", "condition": "失败", "result": "保持待支付", "compensation": "不履约", "blocking": True, "acceptanceRefs": ["AC-ORD0013-001"]}],
            "successCriteria": ["订单与支付一致"],
            "businessModelRefs": [], "dependencyImpactRefs": [], "designRefs": [], "testCaseIds": [],
            "status": "ACTIVE", "createdVersion": self.version, "lastChangedVersion": self.version, "supersedes": [],
        }

    def document(self) -> dict:
        doc = business_flow.empty_document("ORDER_LIFECYCLE", "订单生命周期")
        doc["revision"] = "FLOW-R01@test"
        doc["baseRevision"] = business_flow.INITIAL_REVISION
        doc["flows"] = [self.flow()]
        return doc

    def test_validate_and_render_links_for_both_directories(self) -> None:
        doc = self.document()
        business_flow.validate_document(doc, self.root, require_links=True)
        project_md = self.root / "docs" / "_flows" / "ORDER_LIFECYCLE_flow.md"
        preview_md = self.root / "version" / self.version / "doc" / "_flows" / "ORDER_LIFECYCLE" / "generated" / "preview.md"
        project_text = business_flow.render_markdown(doc, self.root, project_md)
        preview_text = business_flow.render_markdown(doc, self.root, preview_md)
        self.assertIn(f"../../version/{self.version}/doc/{self.target}/requirement.md", project_text)
        self.assertIn(f"../../../{self.target}/requirement.md", preview_text)
        self.assertNotEqual(project_text, preview_text)

    def test_parent_cycle_is_rejected(self) -> None:
        doc = self.document()
        child = copy.deepcopy(doc["flows"][0])
        child["id"] = "FLOW-CHILD"
        child["level"] = "L1"
        child["parentFlowId"] = "FLOW-ORDER-E2E"
        child["steps"][0]["id"] = "STEP-CHILD-01"
        child["steps"] = child["steps"][:1]
        doc["flows"][0]["parentFlowId"] = "FLOW-CHILD"
        doc["flows"].append(child)
        with self.assertRaisesRegex(business_flow.BusinessFlowError, "cycle"):
            business_flow.validate_document(doc)

    def test_review_rejects_preview_with_same_revision_and_old_content(self) -> None:
        from _core.review_inputs import flow_inputs
        root = business_flow.changes_root(self.root, self.version, "ORDER_LIFECYCLE")
        root.mkdir(parents=True)
        changeset = {
            "schemaVersion": 1, "artifact": "business_flow_changeset",
            "changeSet": {
                "id": "FLOW-CHG-ORDER-001", "version": self.version, "module": "ORD",
                "flowGroup": "ORDER_LIFECYCLE", "reason": "Add order flow",
                "baseRevision": business_flow.INITIAL_REVISION, "resultRevision": "FLOW-R01@test",
                "affectedTraceIds": ["TR-ORD0013-001"],
            },
            "operations": [{"operation": "add", "targetId": "FLOW-ORDER-E2E", "reason": "Add", "after": self.flow()}],
        }
        path = root / "order.yaml"
        simple_yaml.dump(changeset, path)
        simulations = business_flow.simulate(self.root, self.version, "ORD")
        business_flow.write_previews(self.root, self.version, simulations)
        first = flow_inputs(self.root, self.version, {"FLOW-ORDER-E2E"})
        self.assertTrue(first)
        unrelated = business_flow.changes_root(self.root, self.version, "UNRELATED")
        unrelated.mkdir(parents=True)
        (unrelated / "draft.yaml").write_text("notes: [unfinished", encoding="utf-8")
        self.assertEqual(first, flow_inputs(self.root, self.version, {"FLOW-ORDER-E2E"}))
        changeset["operations"][0]["after"]["successCriteria"] = ["New success boundary"]
        simple_yaml.dump(changeset, path)
        with self.assertRaisesRegex(ValueError, "STALE_FLOW_PREVIEW"):
            flow_inputs(self.root, self.version, {"FLOW-ORDER-E2E"})
        business_flow.write_previews(self.root, self.version, business_flow.simulate(
            self.root, self.version, "ORD", groups={"ORDER_LIFECYCLE"}))
        second = flow_inputs(self.root, self.version, {"FLOW-ORDER-E2E"})
        self.assertNotEqual(first, second)

    def test_review_detects_escaped_target_id_over_old_canonical_flow(self) -> None:
        from _core.review_inputs import flow_inputs
        canonical = business_flow.canonical_yaml_path(self.root, "ORDER_LIFECYCLE")
        canonical.parent.mkdir(parents=True, exist_ok=True)
        simple_yaml.dump(self.document(), canonical)
        root = business_flow.changes_root(self.root, self.version, "ORDER_LIFECYCLE")
        root.mkdir(parents=True)
        changeset = {
            "schemaVersion": 1, "artifact": "business_flow_changeset",
            "changeSet": {
                "id": "FLOW-CHG-ORDER-002", "version": self.version, "module": "ORD",
                "flowGroup": "ORDER_LIFECYCLE", "reason": "Change success boundary",
                "baseRevision": "FLOW-R01@test", "resultRevision": "FLOW-R02@test",
                "affectedTraceIds": ["TR-ORD0013-001"],
            },
            "operations": [{"operation": "update", "targetId": "FLOW-ORDER-E2E",
                            "reason": "Change", "after": {"id": "FLOW-ORDER-E2E", "successCriteria": ["New boundary"]}}],
        }
        path = root / "order.yaml"
        encoded = json.dumps(changeset).replace("FLOW-ORDER-E2E", "FLOW-\\u004fRDER-E2E")
        path.write_text(encoded, encoding="utf-8")
        self.assertNotIn("FLOW-ORDER-E2E", encoded)
        with self.assertRaisesRegex(ValueError, "STALE_FLOW_PREVIEW"):
            flow_inputs(self.root, self.version, {"FLOW-ORDER-E2E"})
        business_flow.write_previews(self.root, self.version, business_flow.simulate(self.root, self.version, "ORD"))
        self.assertTrue(flow_inputs(self.root, self.version, {"FLOW-ORDER-E2E"}))

    def test_payloads_update_flow_index_and_module_descriptions(self) -> None:
        doc = self.document()
        record = {
            "id": "FLOW-CHG-V_1.0-ORDER-001", "module": "ORD", "source": str(self.root / "source.yaml"),
            "baseRevisions": {"business_flow__ORDER_LIFECYCLE": business_flow.INITIAL_REVISION},
            "resultRevisions": {"business_flow__ORDER_LIFECYCLE": "FLOW-R01@test"},
            "affectedFlowIds": ["FLOW-ORDER-E2E"],
        }
        sim = business_flow.GroupSimulation("ORDER_LIFECYCLE", doc, [record], [], [], [])
        payloads = business_flow.payloads(self.root, [sim])
        self.assertIn(self.root / "docs" / "_flows" / "ORDER_LIFECYCLE_flow.yaml", payloads)
        self.assertIn(self.root / "docs" / "_flows" / "flow_index.yaml", payloads)
        ord_desc = payloads[self.root / "docs" / "ORD" / "ORD_desc.md"].decode()
        pay_desc = payloads[self.root / "docs" / "PAY" / "PAY_desc.md"].decode()
        self.assertIn("FLOW-ORDER-E2E", ord_desc)
        self.assertIn("OWNER", ord_desc)
        self.assertIn("PARTICIPANT", pay_desc)
        business_flow.validate_rendered_links(self.root, payloads)

    def test_init_does_not_create_project_level_flow_facts(self) -> None:
        args = type("Args", (), {
            "agent": "RequirementAnalysisAgent", "doc_root": str(self.root),
            "version": self.version, "module": "ORD", "group": "ORDER_LIFECYCLE",
            "name": "订单生命周期",
        })()
        self.assertEqual(0, business_flow.command_init(args))
        self.assertTrue((business_flow.changes_root(self.root, self.version, "ORDER_LIFECYCLE") / "business-flow.changeset.template.yaml").is_file())
        self.assertFalse(business_flow.canonical_yaml_path(self.root, "ORDER_LIFECYCLE").exists())
        self.assertFalse(business_flow.canonical_markdown_path(self.root, "ORDER_LIFECYCLE").exists())

    def test_payloads_remove_stale_module_participation_rows(self) -> None:
        old_doc = self.document()
        old_record = {
            "id": "FLOW-CHG-OLD", "module": "ORD", "source": str(self.root / "old.yaml"),
            "baseRevisions": {"business_flow__ORDER_LIFECYCLE": business_flow.INITIAL_REVISION},
            "resultRevisions": {"business_flow__ORDER_LIFECYCLE": "FLOW-R01@test"},
            "affectedFlowIds": ["FLOW-ORDER-E2E"],
        }
        old_payloads = business_flow.payloads(
            self.root, [business_flow.GroupSimulation("ORDER_LIFECYCLE", old_doc, [old_record], [], [], [])]
        )
        for path, content in old_payloads.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        new_doc = self.document()
        flow = new_doc["flows"][0]
        flow["participantModules"] = ["ORD"]
        flow["featureRefs"] = [item for item in flow["featureRefs"] if item["module"] == "ORD"]
        flow["steps"] = [flow["steps"][0]]
        new_doc["revision"] = "FLOW-R02@test"
        new_doc["baseRevision"] = "FLOW-R01@test"
        new_record = {**old_record, "id": "FLOW-CHG-NEW", "baseRevisions": {"business_flow__ORDER_LIFECYCLE": "FLOW-R01@test"}, "resultRevisions": {"business_flow__ORDER_LIFECYCLE": "FLOW-R02@test"}}
        payloads = business_flow.payloads(
            self.root, [business_flow.GroupSimulation("ORDER_LIFECYCLE", new_doc, [new_record], [], [], [])]
        )
        self.assertIn(self.root / "docs" / "PAY" / "PAY_desc.md", payloads)
        self.assertNotIn("FLOW-ORDER-E2E", payloads[self.root / "docs" / "PAY" / "PAY_desc.md"].decode())
        self.assertIn("FLOW-ORDER-E2E", payloads[self.root / "docs" / "ORD" / "ORD_desc.md"].decode())

    def test_invalid_requirement_link_is_rejected(self) -> None:
        doc = self.document()
        doc["flows"][0]["requirementRefs"][0]["documentRef"] = "../outside.md"
        with self.assertRaises(business_flow.BusinessFlowError):
            business_flow.validate_document(doc, self.root, require_links=True)

    def test_business_flow_requires_project_doc_root(self) -> None:
        version_root = self.root / "version" / self.version
        version_root.mkdir(parents=True, exist_ok=True)
        argv = [
            "business_flow.py", "init", "-g", "ProjectManagerAgent",
            "--doc-root", str(version_root), "--version", self.version,
            "--module", "ORDER", "--group", "ORDER_LIFECYCLE",
        ]
        with mock.patch("sys.argv", argv):
            self.assertEqual(1, business_flow.main())
        self.assertFalse((version_root / "docs").exists())

    def test_validate_command_accepts_any_declared_agent(self) -> None:
        path = self.root / "flow.yaml"
        simple_yaml.dump(self.document(), path)
        args = type("Args", (), {
            "agent": "TestAgent", "doc_root": str(self.root),
            "file": str(path), "require_links": True,
        })()
        self.assertEqual(0, business_flow.command_validate(args))

    def test_changeset_simulation_is_revision_safe(self) -> None:
        group = "ORDER_LIFECYCLE"
        change_dir = business_flow.changes_root(self.root, self.version, group)
        change_dir.mkdir(parents=True, exist_ok=True)
        changeset = {
            "schemaVersion": 1, "artifact": "business_flow_changeset",
            "changeSet": {
                "id": "FLOW-CHG-V_1.0-ORDER-001", "version": self.version, "module": "ORD",
                "flowGroup": group, "flowGroupName": "订单生命周期", "reason": "新增订单主流程",
                "baseRevision": business_flow.INITIAL_REVISION, "resultRevision": "FLOW-R01@test",
                "affectedTraceIds": ["TR-ORD0013-001"],
            },
            "operations": [{"operation": "add", "targetId": "FLOW-ORDER-E2E", "reason": "新增", "after": self.flow()}],
        }
        simple_yaml.dump(changeset, change_dir / "flow.yaml")
        simulations = business_flow.simulate(self.root, self.version, "ORD")
        self.assertEqual(1, len(simulations))
        self.assertEqual("FLOW-R01@test", simulations[0].document["revision"])
        changeset["changeSet"]["baseRevision"] = "FLOW-R99@stale"
        simple_yaml.dump(changeset, change_dir / "flow.yaml")
        with self.assertRaisesRegex(business_flow.BusinessFlowError, "stale"):
            business_flow.simulate(self.root, self.version, "ORD")


if __name__ == "__main__":
    unittest.main()
