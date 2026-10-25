from __future__ import annotations

import json
import unittest
from pathlib import Path

import design_doc
from _core import implementation_strategy


ROOT = Path(__file__).resolve().parent.parent


class ImplementationStrategyContractTests(unittest.TestCase):
    def test_shared_kernel_reads_the_single_semantic_vocabulary(self) -> None:
        semantic = json.loads((ROOT / "assets" / "semantic-contract.json").read_text(encoding="utf-8"))
        contract = semantic["canonicalTerms"]["implementationStrategyDecision"]
        self.assertEqual(contract["contractVersion"], implementation_strategy.CONTRACT_VERSION)
        self.assertEqual(tuple(contract["decisionOrder"]), implementation_strategy.DECISION_ORDER)
        self.assertEqual(set(contract["sharedLogicDispositions"]), set(implementation_strategy.SHARED_LOGIC_DISPOSITIONS))
        self.assertEqual(("REUSE", "COMPATIBLE_EXTEND", "MODIFY", "CREATE"), implementation_strategy.DECISION_ORDER)

    def test_design_and_plan_contracts_use_the_shared_version(self) -> None:
        structure = design_doc.validate_design(design_doc.TEMPLATE, "structure")
        self.assertTrue(structure.valid, structure.errors)
        self.assertEqual(implementation_strategy.CONTRACT_VERSION, structure.counts["implementationStrategyContractVersion"])
        schema = json.loads((ROOT / "assets" / "development-task-plan.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(
            implementation_strategy.CONTRACT_VERSION,
            schema["properties"]["implementation_strategy_contract_version"]["const"],
        )

    def test_review_profiles_cover_strategy_create_and_shared_logic(self) -> None:
        review = json.loads((ROOT / "assets" / "review-contract.json").read_text(encoding="utf-8"))
        self.assertIn("RC-SPEC-007", review["profiles"]["code_review:SpecComplianceReviewAgent"]["criterionIds"])
        self.assertIn("RC-MAINT-008", review["profiles"]["code_review:EngineeringStandardsReviewAgent"]["criterionIds"])
        self.assertIn("RC-ARCH-008", review["profiles"]["design:ArchitectureReviewAgent"]["criterionIds"])

    def test_example_is_project_independent_and_has_positive_negative_cases(self) -> None:
        text = (ROOT / "references" / "implementation-strategy-example.md").read_text(encoding="utf-8")
        for token in (
            "独立示例", "数字图书馆", "IMPL-DEC-LIBRARY-IMPORT", "平行新建",
            "布尔参数伪复用", "跨域万能工具类", "#implementation-strategy-decisions",
        ):
            self.assertIn(token, text)
        self.assertIsNone(design_doc.PLACEHOLDER_RE.search(text))

    def test_detailed_references_are_on_demand(self) -> None:
        routing = json.loads((ROOT / "context-routing.json").read_text(encoding="utf-8"))
        self.assertTrue(routing["resourceCatalog"]["implementation-strategy-example"]["onDemandOnly"])
        base_resources = {resource for route in routing["routes"] for resource in route.get("resources", [])}
        self.assertNotIn("implementation-strategy-example", base_resources)


if __name__ == "__main__":
    unittest.main()
