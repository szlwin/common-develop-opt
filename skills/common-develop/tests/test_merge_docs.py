#!/usr/bin/env python3

from __future__ import annotations

import argparse
import copy
import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import merge_docs
import business_flow
import document_facts
import evidence
import simple_yaml
import task_plan
import work_mode
import wk
from _merge_docs import archive_validation
try:
    from tests.internal.test_markers import slow
except ModuleNotFoundError:  # quality-gate worker places tests/ directly on sys.path
    from internal.test_markers import slow

try:
    import jsonschema
except ImportError:  # pragma: no cover - optional test dependency
    jsonschema = None


@dataclass(frozen=True)
class MergeScenario:
    project_name: str
    version: str
    module: str
    feature_code: str
    entity_key: str
    entity_name: str
    entity_camel: str

    @property
    def revision_tag(self) -> str:
        return self.feature_code.lower()

    @property
    def change_set_id(self) -> str:
        return f"CHG-{self.version}-{self.module}-{self.feature_code}"

    @property
    def trace_id(self) -> str:
        return f"TR-{self.module}-001"

    @property
    def table_id(self) -> str:
        return f"DB-{self.module}-{self.entity_key}"

    @property
    def foreign_key_id(self) -> str:
        return f"FK-{self.module}-{self.entity_key}-PARENT"

    @property
    def entity_id(self) -> str:
        return f"ENT-{self.entity_key}"

    @property
    def invariant_id(self) -> str:
        return f"INV-{self.entity_key}-CODE"

    @property
    def aggregate_id(self) -> str:
        return f"AGG-{self.entity_key}"

    @property
    def api_id(self) -> str:
        return f"API-{self.module}-{self.entity_key}-QUERY"

    @property
    def table_name(self) -> str:
        return f"t_{self.module.lower()}_{self.entity_camel}"

    @property
    def change_filename(self) -> str:
        return f"{self.feature_code}.yaml"




class MergeDocsTest(unittest.TestCase):
    def setUp(self) -> None:
        evidence.clear_validation_caches()
        self.temp = tempfile.TemporaryDirectory()
        self.scenario = MergeScenario(
            project_name="catalog-project",
            version="V_3.4",
            module="CAT",
            feature_code="CAT1001",
            entity_key="PRODUCT",
            entity_name="商品",
            entity_camel="product",
        )
        self.root = Path(self.temp.name) / self.scenario.project_name / "project_doc"
        self.assets = Path(__file__).resolve().parent.parent / "assets" / "structured-docs"
        self.assertEqual(0, self.call(merge_docs.command_init, self.args(changeset=None)))
        work_record = self.root / "version" / self.scenario.version / "work_record.md"
        self.assertTrue(work_record.is_file())
        text = work_record.read_text(encoding="utf-8")
        self.assertIn("<!-- managed-by: common-develop/work-record-v5 -->", text)
        self.assertNotIn("work-record-meta:", text)
        self.assertFalse((self.root / "docs" / "_flows" / "flow_index.yaml").exists())
        self.change = (
            merge_docs.changes_root(self.root, self.scenario.version, self.scenario.module)
            / self.scenario.change_filename
        )
        simple_yaml.dump(self.changeset(), self.change)
        # Successful archive fixtures include the canonical knowledge roots
        # declared by archive_output, even when only structured facts change.
        module_dir = self.root / "docs" / self.scenario.module
        module_dir.mkdir(parents=True, exist_ok=True)
        description = module_dir / f"{self.scenario.module}_desc.md"
        if not description.exists():
            description.write_text(document_facts.module_desc_template(
                self.scenario.module, self.scenario.version, project=True,
            ), encoding="utf-8")
        design = module_dir / f"{self.scenario.module}_design.md"
        if not design.exists():
            design.write_text(f"# {self.scenario.module} design\n\n## 商品查询\n\nQuery the declared product catalog.\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()
        evidence.clear_validation_caches()

    @staticmethod
    def call(function, args):
        if function in {merge_docs.command_archive, merge_docs.command_apply, merge_docs.command_apply_cli}:
            args.agent = getattr(args, "agent", "IncrementalArchiveAgent")
            if args.agent == "ProjectManagerAgent":
                args.agent = "IncrementalArchiveAgent"
        elif not hasattr(args, "agent"):
            args.agent = "ProjectManagerAgent"
        with contextlib.redirect_stdout(io.StringIO()):
            return function(args)

    def changeset(self, scenario: MergeScenario | None = None) -> dict:
        scenario = scenario or self.scenario
        return {
            "schemaVersion": 1,
            "changeSet": {
                "id": scenario.change_set_id,
                "version": scenario.version,
                "module": scenario.module,
                "featureCode": scenario.feature_code,
                "reason": f"新增{scenario.entity_name}查询能力",
                "baseRevisions": {
                    "business_model": "BM-R00@init",
                    "api": "API-R00@init",
                    "db": "DB-R00@init",
                },
                "resultRevisions": {
                    "business_model": f"BM-R01@{scenario.revision_tag}",
                    "api": f"API-R01@{scenario.revision_tag}",
                    "db": f"DB-R01@{scenario.revision_tag}",
                },
                "affectedTraceIds": [scenario.trace_id],
            },
            "operations": [
                {
                    "artifact": "db",
                    "operation": "add",
                    "targetId": scenario.table_id,
                    "reason": f"增加{scenario.entity_name}表",
                    "after": {
                        "name": scenario.table_name,
                        "columns": [
                            {"name": "id", "type": "bigint", "nullable": False, "constraints": ["PRIMARY KEY"]},
                            {"name": f"{scenario.entity_camel}_code", "type": "varchar(32)", "nullable": False, "constraints": ["UNIQUE"]},
                            {"name": "parent_id", "type": "bigint", "nullable": True, "constraints": []},
                        ],
                        "indexes": [],
                        "foreignKeys": [
                            {
                                "id": scenario.foreign_key_id,
                                "columns": ["parent_id"],
                                "targetTable": scenario.table_name,
                                "targetColumns": ["id"],
                                "cardinality": "many-to-one",
                            }
                        ],
                        "traceIds": [scenario.trace_id],
                    },
                },
                {
                    "artifact": "business_model",
                    "collection": "entities",
                    "operation": "add",
                    "targetId": scenario.entity_id,
                    "reason": f"定义{scenario.entity_name}实体",
                    "after": {
                        "name": scenario.entity_name,
                        "identity": f"{scenario.entity_camel}Id",
                        "attributes": [
                            {"name": f"{scenario.entity_camel}Id", "type": "long", "required": True, "constraints": []},
                            {"name": f"{scenario.entity_camel}Code", "type": "string", "required": True, "constraints": ["unique"]},
                        ],
                        "behaviors": ["queryPage"],
                        "traceIds": [scenario.trace_id],
                    },
                },
                {
                    "artifact": "business_model",
                    "collection": "invariants",
                    "operation": "add",
                    "targetId": scenario.invariant_id,
                    "reason": f"{scenario.entity_name}编码唯一",
                    "after": {
                        "statement": f"{scenario.entity_name}编码必须唯一",
                        "trigger": f"创建{scenario.entity_name}",
                        "failure": f"{scenario.entity_key}_CODE_DUPLICATED",
                        "traceIds": [scenario.trace_id],
                    },
                },
                {
                    "artifact": "business_model",
                    "collection": "aggregates",
                    "operation": "add",
                    "targetId": scenario.aggregate_id,
                    "reason": f"定义{scenario.entity_name}聚合",
                    "after": {
                        "name": f"{scenario.entity_name}聚合",
                        "root": scenario.entity_id,
                        "members": [scenario.entity_id],
                        "invariantIds": [scenario.invariant_id],
                        "transactionBoundary": f"单{scenario.entity_name}事务",
                        "traceIds": [scenario.trace_id],
                    },
                },
                {
                    "artifact": "business_model",
                    "collection": "traceability",
                    "operation": "add",
                    "targetId": scenario.trace_id,
                    "reason": "建立需求到模型追踪",
                    "after": {"modelRefs": [scenario.entity_id, scenario.invariant_id, scenario.aggregate_id]},
                },
                {
                    "artifact": "api",
                    "operation": "add",
                    "targetId": scenario.api_id,
                    "reason": f"增加{scenario.entity_name}分页查询接口",
                    "after": {
                        "featureCode": scenario.feature_code,
                        "method": "POST",
                        "path": f"/admin/{scenario.module.lower()}/{scenario.entity_camel}/queryPage",
                        "status": "active",
                        "auth": {"permission": f"{scenario.module.lower()}:{scenario.entity_camel}:query"},
                        "request": {"type": f"{scenario.entity_key.title()}Query"},
                        "response": {"type": f"Page<{scenario.entity_key.title()}>"},
                        "errors": ["FORBIDDEN"],
                        "traceIds": [scenario.trace_id],
                    },
                },
            ],
        }

    def seed_archive_scope_trace(self, task_dir: Path, trace_id: str | None = None) -> None:
        trace = {
            "id": trace_id or self.scenario.trace_id,
            "description": "archive gate fixture trace",
            "status": "COVERED",
            "requirement_refs": ["requirement.md#fixture"],
            "acceptance_criteria": ["archive gate passes"],
            "business_model_refs": ["business_model.yaml#fixture"],
            "design_refs": ["design.md#fixture"],
            "test_case_ids": ["CASE-ARCHIVE-001"],
            "contract_refs": ["api.yaml#fixture"],
            "implementation_refs": ["src/archive_fixture.py"],
            "plan_task_ids": ["PLAN-ARCHIVE-FIXTURE"],
            "verification_evidence_ids": [],
            "verified_by_agents": ["TestEvidenceReviewAgent"],
            "impact_required": False,
            "dependency_impact_refs": [],
            "cross_module_implementation_required": False,
            "cross_module_implementation_refs": [],
        }
        (task_dir / "traceability.json").write_text(
            json.dumps({"items": [trace]}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def args(
        self,
        *,
        root: Path | None = None,
        scenario: MergeScenario | None = None,
        changeset: Path | None | object = ...,
    ) -> argparse.Namespace:
        scenario = scenario or self.scenario
        root = root or self.root
        if changeset is ...:
            changeset = self.change
        return argparse.Namespace(
            doc_root=str(root),
            module=scenario.module,
            version=scenario.version,
            changeset=None if changeset is None else [str(changeset)],
            agent="ProjectManagerAgent",
        )

    def _write_state(self, task_dir: Path, state: dict) -> None:
        state_document, _ = merge_docs.long_task.read_task_state_cache(task_dir)
        payloads, _, _ = merge_docs.long_task.append_task_events_payloads(
            task_dir, merge_docs.long_task.task_state_path(task_dir), state_document, state, [],
            synced_at=str(state.get("checkpoint_at") or "2026-08-15T00:00:00Z"),
        )
        merge_docs.long_task.transactional_text_write(payloads)

    def _write_outcomes(self, task_dir: Path, outcomes: list[dict], desired_state: dict | None = None) -> None:
        specs = []
        for outcome in outcomes:
            specs.append({
                "type": "STAGE_OUTCOME_FINALIZED" if outcome.get("status") in {"PASSED", "SKIPPED"} else "STAGE_OUTCOME_RECORDED",
                "entity_id": str(outcome.get("id") or ""),
                "payload": {"outcome": outcome},
                "timestamp": str(outcome.get("completed_at") or outcome.get("started_at") or ""),
                "source": "test-setup",
            })
        state_document, current_state = merge_docs.long_task.read_task_state_cache(task_dir)
        state = desired_state if isinstance(desired_state, dict) else current_state
        payloads, _, _ = merge_docs.long_task.append_task_events_payloads(
            task_dir, merge_docs.long_task.task_state_path(task_dir), state_document, state, specs,
            synced_at=str(state.get("checkpoint_at") or "2026-08-15T00:00:00Z"),
        )
        merge_docs.long_task.transactional_text_write(payloads)

    def phase_reviewer_conclusion(
        self, task_dir: Path, phase: str, reviewer: str, revision: str
    ) -> dict:
        profile = merge_docs.long_task.review_profile(phase, reviewer)
        evidence_dir = task_dir / "evidence" / "reviews" / phase
        criteria_results = []
        for criterion_id in profile["criterionIds"]:
            policy = merge_docs.long_task.REVIEW_CRITERIA[criterion_id]["evidencePolicy"]
            kinds = list(policy.get("allOfTypes", []))
            any_of = list(policy.get("anyOfTypes", []))
            if any_of and not any(kind in kinds for kind in any_of):
                kinds.append(any_of[0])
            while len(kinds) < policy.get("minimumEvidenceCount", 1):
                kinds.append(any_of[0] if any_of else "document_ref")
            evidence = []
            for index, kind in enumerate(kinds, 1):
                path = evidence_dir / f"{reviewer}-{criterion_id}-{index}.txt"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("current revision verified", encoding="utf-8")
                entry = merge_docs.long_task.evidence.register_evidence(
                    task_dir, agent=reviewer, evidence_type=kind,
                    ref=str(path.relative_to(task_dir)), revision=revision, phase=phase,
                    scope_refs=[criterion_id],
                )
                evidence.append({"evidence_id": entry["evidence_id"], "claim": "verified"})
            criteria_results.append({
                "criterion_id": criterion_id, "status": "PASSED", "evidence": evidence,
                "finding_ids": [], "reason": "",
            })
        return {
            "profile_id": f"{phase}:{reviewer}", "revision": revision,
            "conclusion": "PASSED", "checked_scope": [f"{phase}:{revision}"],
            "criteria_results": criteria_results, "finding_ids": [], "limitations": [],
            "reviewed_at": "2026-07-11T11:45:00+08:00",
        }

    def complete_plan(
        self,
        plan: dict,
        user_confirmed: bool = False,
        task_target: str = "ARCHIVE-GATES",
    ) -> dict:
        task_dir = self.root / "version" / self.scenario.version / "task" / task_target
        if not task_dir.exists():
            init_result = self.call(
                merge_docs.long_task.command_init,
                argparse.Namespace(
                    doc_root=str(self.root), version=self.scenario.version, target=task_target,
                    reuse=False, agent="ProjectManagerAgent",
                ),
            )
            self.assertEqual(0, init_result)
        self.call(
            merge_docs.long_task.command_sync_work_mode,
            argparse.Namespace(task_dir=str(task_dir), agent="ProjectManagerAgent"),
        )
        state_document, state = merge_docs.long_task.read_task_state_cache(task_dir)
        outcomes = merge_docs.long_task.load_task_history(task_dir)["outcomes"]
        traceability = [{
            "id": self.scenario.trace_id,
            "description": "archive gate fixture trace",
            "status": "COVERED",
            "requirement_refs": ["requirement.md#fixture"],
            "acceptance_criteria": ["archive gate passes"],
            "business_model_refs": ["business_model.yaml#fixture"],
            "design_refs": ["design.md#fixture"],
            "test_case_ids": ["CASE-ARCHIVE-001"],
            "contract_refs": ["api.yaml#fixture"],
            "implementation_refs": ["src/archive_fixture.py"],
            "plan_task_ids": ["PLAN-ARCHIVE-FIXTURE"],
            "verification_evidence_ids": [],
            "verified_by_agents": ["TestEvidenceReviewAgent"],
            "impact_required": False,
            "dependency_impact_refs": [],
            "cross_module_implementation_required": False,
            "cross_module_implementation_refs": [],
        }]
        traceability_path = task_dir / "traceability.json"
        traceability_path.write_text(
            json.dumps({"items": traceability}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        evidence_dir = task_dir / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        gate_to_phase = {gate: phase for phase, gate in merge_docs._ARCHIVE_GATE_MAP.items()}
        mode_record = plan.get("workMode", {})
        minimal = bool(mode_record.get("minimal"))
        lightweight = bool(mode_record.get("lightweight") or mode_record.get("lightweightWaived"))
        active_suppression = (
            merge_docs.work_mode.LIGHTWEIGHT_SUPPRESSED_REVIEWERS if lightweight
            else merge_docs.work_mode.MINIMAL_SUPPRESSED_REVIEWERS if minimal
            else {}
        )
        skipped_phases = set(mode_record.get("skippedPhases") or [])

        def expected_fixture_reviewers(phase: str, phase_definition: dict) -> set[str]:
            reviewers = set(phase_definition.get("requiredReviewerAgents", []))
            reviewers -= set(active_suppression.get(phase, []))
            return reviewers

        def fixture_predecessors(phase: str) -> list[str]:
            # The validator switches to the minimal predecessor graph whenever
            # either a current or historical mode skips phases.  Build the
            # fixture against that same graph instead of deleting skipped
            # predecessors from the standard graph.
            return merge_docs.long_task.phase_predecessors(
                phase, minimal or lightweight or bool(skipped_phases)
            )

        def criterion_evidence(criterion_id: str, revision: str, phase: str, reviewer: str) -> list[dict]:
            criterion = merge_docs.long_task.REVIEW_CRITERIA[criterion_id]
            policy = criterion["evidencePolicy"]
            kinds = list(policy.get("allOfTypes", []))
            any_of = list(policy.get("anyOfTypes", []))
            if any_of and not any(kind in kinds for kind in any_of):
                kinds.append(any_of[0])
            while len(kinds) < policy.get("minimumEvidenceCount", 1):
                kinds.append(any_of[0] if any_of else "document_ref")
            result = []
            for index, kind in enumerate(kinds, 1):
                path = evidence_dir / "reviews" / phase / f"{criterion_id}-{index}.txt"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("current revision verified", encoding="utf-8")
                entry = merge_docs.long_task.evidence.register_evidence(
                    task_dir, agent=reviewer, evidence_type=kind,
                    ref=str(path.relative_to(task_dir)), revision=revision, phase=phase,
                    scope_refs=[criterion_id],
                )
                result.append({
                    "evidence_id": entry["evidence_id"],
                    "claim": "current revision verified",
                })
            return result

        def reviewer_conclusion(phase: str, reviewer: str, revision: str) -> dict:
            profile = merge_docs.long_task.review_profile(phase, reviewer)
            return {
                "profile_id": f"{phase}:{reviewer}",
                "revision": revision,
                "conclusion": "PASSED",
                "checked_scope": [f"{phase}:{revision}"],
                "criteria_results": [
                    {
                        "criterion_id": criterion_id,
                        "status": "PASSED",
                        "evidence": criterion_evidence(criterion_id, revision, phase, reviewer),
                        "finding_ids": [],
                        "reason": "",
                    }
                    for criterion_id in profile["criterionIds"]
                ],
                "finding_ids": [],
                "limitations": [],
                "reviewed_at": "2026-07-11T11:45:00+08:00",
            }

        gate_records: dict[str, tuple[str, str, str, list[str]]] = {}
        for name, gate in plan["archiveGate"]["gates"].items():
            if gate["status"] == "NOT_APPLICABLE":
                continue
            phase = gate_to_phase[name]
            phase_definition = next(
                item for item in merge_docs.RUNTIME_CONTRACT["canonicalPhases"]
                if item["id"] == phase
            )
            artifact = state["artifact_revisions"][phase]
            existing_outcome = next((
                outcome for outcome in outcomes
                if outcome.get("phase") == phase and outcome.get("is_current") is True
                and outcome.get("status") == "PASSED"
                and outcome.get("iteration_id") == artifact.get("iteration_id")
                and outcome.get("output_revision") == artifact.get("revision")
            ), None)
            if existing_outcome is not None:
                existing_review = state.get("collaboration_reviews", {}).get(phase, {})
                reviewers = sorted((existing_review.get("independent_conclusions") or {}).keys())
                if not reviewers:
                    reviewers = [phase_definition["ownerAgent"]]
                gate.update({
                    "status": "PASSED", "revision": artifact["revision"],
                    "stageOutcomeRefs": [existing_outcome["id"]],
                    "evidenceIds": list(existing_outcome.get("evidence_ids") or []),
                    "reviewerAgents": reviewers, "reason": "",
                })
                continue
            revision = f"{name}-R01@current"
            outcome_id = f"SO-{name}-R01"
            evidence_ref = f"evidence/{name}.md"
            (evidence_dir / f"{name}.md").write_text(
                f"verified {name} {revision}\n", encoding="utf-8"
            )
            evidence_id = merge_docs.long_task.evidence.register_evidence(
                task_dir, agent=phase_definition["ownerAgent"], evidence_type="runtime_ref",
                ref=evidence_ref, revision=revision, phase=phase, scope_refs=[name],
            )["evidence_id"]
            artifact["revision"] = revision
            artifact["status"] = "PASSED"
            mandatory_reviewers = expected_fixture_reviewers(phase, phase_definition)
            gate_reviewers = sorted(mandatory_reviewers) or [phase_definition["ownerAgent"]]
            gate.update(
                {
                    "status": "PASSED",
                    "revision": revision,
                    "stageOutcomeRefs": [outcome_id],
                    "evidenceIds": [evidence_id],
                    "reviewerAgents": gate_reviewers,
                    "reason": "",
                }
            )
            gate_records[name] = (phase, revision, outcome_id, gate_reviewers, evidence_id)

        for name, (phase, revision, outcome_id, gate_reviewers, evidence_id) in gate_records.items():
            artifact = state["artifact_revisions"][phase]
            phase_definition = next(
                item for item in merge_docs.RUNTIME_CONTRACT["canonicalPhases"]
                if item["id"] == phase
            )
            evidence_ref = f"evidence/{name}.md"
            input_revisions = {
                predecessor: state["artifact_revisions"][predecessor]["revision"]
                for predecessor in fixture_predecessors(phase)
            }
            outcomes.append(
                {
                    "id": outcome_id,
                    "phase": phase,
                    "round": f"{phase.upper()}-R01",
                    "iteration_id": artifact["iteration_id"],
                    "iteration_no": artifact["iteration_no"],
                    "is_current": True,
                    "superseded_by": "",
                    "status": "PASSED",
                    "produced_by_agent": phase_definition["ownerAgent"],
                    "input_revisions": input_revisions,
                    "output_revision": revision,
                    "evidence_ids": [evidence_id],
                    "open_risks": [],
                    "started_at": "2026-07-11T11:00:00+08:00",
                    "completed_at": "2026-07-11T11:30:00+08:00",
                }
            )
            review = state.get("collaboration_reviews", {}).get(phase)
            if isinstance(review, dict):
                review["current_iteration_id"] = artifact["iteration_id"]
                review["artifact_revision"] = revision
                review["status"] = "PASSED"
                review["independent_conclusions"] = {
                    reviewer: reviewer_conclusion(phase, reviewer, revision)
                    for reviewer in sorted(expected_fixture_reviewers(phase, phase_definition))
                }

        source_file = task_dir / "src" / "archive_fixture.py"
        source_file.parent.mkdir(parents=True, exist_ok=True)
        source_file.write_text("def archive_fixture():\n    return True\n", encoding="utf-8")
        development_revision = state["artifact_revisions"]["development"]["revision"]
        merge_docs.long_task.risk_detect.scan(
            task_dir, agent="ProjectManagerAgent", revision=development_revision,
            refs=[str(source_file.relative_to(task_dir))],
        )
        completion_revision = state["artifact_revisions"]["completion_verification"]["revision"]
        acceptance_file = evidence_dir / "acceptance-archive.json"
        acceptance_file.write_text('{"status": "PASSED"}', encoding="utf-8")
        acceptance_evidence = merge_docs.long_task.evidence.register_evidence(
            task_dir, agent="CompletionVerificationAgent", evidence_type="test_ref",
            ref=str(acceptance_file.relative_to(task_dir)), revision=completion_revision,
            phase="completion_verification", scope_refs=["archive gate passes", self.scenario.trace_id],
        )
        traceability[0]["verification_evidence_ids"] = [acceptance_evidence["evidence_id"]]
        traceability_path.write_text(
            json.dumps({"items": traceability}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        acceptance_document = merge_docs.long_task.acceptance.load_document(task_dir)
        acceptance_document["assertions"] = [{
            "assertion_id": "ASRT-ARCHIVE-001", "acceptance_id": "archive gate passes",
            "statement": "archive gate passes", "type": "TEST_CASE",
            "phase": "completion_verification", "revision": completion_revision, "blocking": True,
            "status": "VERIFIED", "parameters": {"test_case_id": "archive gate passes", "expected_status": "PASSED"},
            "source_refs": [f"traceability.json#{self.scenario.trace_id}"],
            "evidence_ids": [acceptance_evidence["evidence_id"]],
            "verified_by_agent": "CompletionVerificationAgent",
            "verified_at": "2026-07-11T11:50:00+08:00", "reason": "",
        }]
        merge_docs.long_task.acceptance.save_document(task_dir, acceptance_document)

        state["current_phase"] = "completion_verification"
        state["current_round"] = "COMPLETION-VERIFICATION-R01"
        state["task_status"] = "READY"
        state["current_agent"] = "ProjectManagerAgent"
        state["next_agent"] = "CompletionVerificationAgent"
        state["next_action"] = "archive current verified increment"
        state["checkpoint_at"] = "2026-07-11T12:00:00+08:00"
        self._write_outcomes(task_dir, outcomes, desired_state=state)

        plan["archiveGate"].update(
            {
                "status": "READY",
                "authorizedBy": "ProjectManagerAgent",
                "authorizedAt": "2026-07-11T12:00:00+08:00",
                "decisionRef": "DEC-ARCHIVE-001",
                "userConfirmed": user_confirmed,
            }
        )
        errors, _, _, _ = merge_docs.long_task.validate_task(task_dir)
        self.assertEqual([], errors, "\n".join(errors))
        return plan

    def test_commands_are_portable_across_projects_versions_and_modules(self) -> None:
        scenarios = [
            MergeScenario("catalog-project", "V_1.2", "CAT", "CAT2001", "PRODUCT", "商品", "product"),
            MergeScenario("order-project", "R_2026_07", "ORD", "ORD9007", "ORDER", "订单", "order"),
        ]
        for scenario in scenarios:
            with self.subTest(project=scenario.project_name, version=scenario.version, module=scenario.module):
                root = Path(self.temp.name) / "portable" / scenario.project_name / "project_doc"
                init_args = self.args(root=root, scenario=scenario, changeset=None)
                self.assertEqual(0, self.call(merge_docs.command_init, init_args))
                change = merge_docs.changes_root(root, scenario.version, scenario.module) / scenario.change_filename
                simple_yaml.dump(self.changeset(scenario), change)
                args = self.args(root=root, scenario=scenario, changeset=change)
                self.assertEqual(0, self.call(merge_docs.command_preview, args))
                self.assertEqual(0, self.call(merge_docs.command_apply, args))
                model = simple_yaml.load(merge_docs.canonical_path(root, scenario.module, "business_model"))
                self.assertEqual(scenario.module, model["module"]["code"])
                self.assertEqual(f"BM-R01@{scenario.revision_tag}", model["revision"])
                self.assertEqual(scenario.entity_id, model["entities"][0]["id"])

    @unittest.skipIf(jsonschema is None, "jsonschema is not installed")
    def test_init_outputs_match_current_schemas(self) -> None:
        files = {
            "business-model.schema.json": merge_docs.canonical_path(self.root, self.scenario.module, "business_model"),
        }
        for schema_name, document_path in files.items():
            schema = json.loads((self.assets / schema_name).read_text(encoding="utf-8"))
            jsonschema.validate(simple_yaml.load(document_path), schema)
        self.assertFalse(merge_docs.canonical_path(self.root, self.scenario.module, "api").exists())
        self.assertFalse(merge_docs.canonical_path(self.root, self.scenario.module, "db").exists())

    def test_init_creates_non_active_changeset_template(self) -> None:
        template = self.change.parent / "changeset.template.yaml"
        dependency_template = self.change.parent / "dependency-impact.changeset.template.yaml"
        self.assertTrue(template.is_file())
        self.assertTrue(dependency_template.is_file())
        version_work = work_mode.work_path(self.root, self.scenario.version)
        self.assertTrue(version_work.is_file())
        self.assertEqual(work_mode.WorkMode(), work_mode.validate_work_file(version_work))
        draft = simple_yaml.load(template)
        self.assertEqual(self.scenario.version, draft["changeSet"]["version"])
        self.assertEqual(self.scenario.module, draft["changeSet"]["module"])
        self.assertEqual([], draft["operations"])
        dependency_draft = simple_yaml.load(dependency_template)
        self.assertEqual("dependency_impact_changeset", dependency_draft["artifact"])
        dependency_schema = json.loads(
            (self.assets / "dependency-impact.changeset.schema.json").read_text(encoding="utf-8")
        )
        if jsonschema is not None:
            jsonschema.validate(dependency_draft, dependency_schema)
        args = argparse.Namespace(
            doc_root=str(self.root), module=self.scenario.module, version=self.scenario.version, changeset=None
        )
        self.assertEqual([self.change], merge_docs.discover_changesets(args))

    def test_api_db_delta_must_stay_in_current_version(self) -> None:
        outside = self.root / "outside-change.yaml"
        simple_yaml.dump(self.changeset(), outside)
        args = self.args(changeset=outside)
        with self.assertRaisesRegex(merge_docs.MergeError, "must stay under the current version"):
            merge_docs.simulate(args)

    def test_preview_apply_and_idempotency(self) -> None:
        self.assertEqual(0, self.call(merge_docs.command_preview, self.args()))
        generated = merge_docs.generated_root(self.root, self.scenario.version, self.scenario.module)
        self.assertTrue((generated / f"{self.scenario.module}_business_model_preview.yaml").is_file())
        self.assertTrue((generated / f"{self.scenario.module}_api_delta_preview.md").is_file())
        self.assertTrue((generated / f"{self.scenario.module}_db_delta_preview.yaml").is_file())
        self.assertFalse((generated / f"{self.scenario.module}_api_preview.md").exists())
        self.assertFalse((generated / f"{self.scenario.module}_ER_preview.md").exists())
        plan = simple_yaml.load(generated / "merge_plan.yaml")
        self.assertEqual(1, plan["schemaVersion"])
        self.assertEqual("VERSION_DELTA_ARCHIVE", plan["mergeMode"])
        self.assertEqual(["api", "business_model", "db"], plan["structuredArtifacts"])
        self.assertEqual(["business_model"], plan["projectFactArtifacts"])
        self.assertEqual(["api", "db"], plan["versionDeltaArtifacts"])
        self.assertEqual(False, plan["workMode"]["modelCode"])
        self.assertEqual(False, plan["workMode"]["pageDesign"])
        self.assertEqual(False, plan["workMode"]["minimal"])
        self.assertEqual(False, plan["workMode"]["auto"])
        self.assertEqual(False, plan["workMode"]["architectureReview"])
        self.assertEqual([], plan["workMode"]["skippedPhases"])
        self.assertEqual(f"version/{self.scenario.version}/work.md", plan["workMode"]["workRef"])
        self.assertEqual(64, len(plan["workMode"]["workDigest"]))
        self.assertEqual("BM-R00@init", plan["baseRevisions"]["business_model"])
        self.assertEqual(f"BM-R01@{self.scenario.revision_tag}", plan["resultRevisions"]["business_model"])
        self.assertEqual(0, self.call(merge_docs.command_apply, self.args()))
        model = simple_yaml.load(merge_docs.canonical_path(self.root, self.scenario.module, "business_model"))
        self.assertEqual(f"BM-R01@{self.scenario.revision_tag}", model["revision"])
        self.assertEqual(self.scenario.entity_id, model["entities"][0]["id"])
        self.assertFalse(merge_docs.canonical_path(self.root, self.scenario.module, "api").exists())
        self.assertFalse(merge_docs.canonical_path(self.root, self.scenario.module, "db").exists())
        self.assertFalse((self.root / "docs" / self.scenario.module / f"{self.scenario.module}_ER.md").exists())
        manifest = simple_yaml.load(self.root / "archive_manifest.yaml")
        archived = manifest["appliedChangeSets"][0]
        self.assertEqual("VERSION_DELTA_ARCHIVE", archived["archiveMode"])
        self.assertEqual(["api", "db"], archived["versionDeltaArtifacts"])
        self.assertTrue(archived["sourceRef"].startswith(f"version/{self.scenario.version}/"))
        self.assertEqual(64, len(archived["sourceDigest"]))
        self.assertEqual(0, self.call(merge_docs.command_apply, self.args()))
        model_again = simple_yaml.load(merge_docs.canonical_path(self.root, self.scenario.module, "business_model"))
        self.assertEqual(f"BM-R01@{self.scenario.revision_tag}", model_again["revision"])

    @slow
    def test_apply_archives_design_dependency_and_clickable_relation_views_atomically(self) -> None:
        module_root = self.root / "version" / self.scenario.version / "doc" / self.scenario.module
        design = module_root / f"{self.scenario.module}_design.md"
        design.write_text(
            "# 商品查询设计\n\n"
            "> Design revision：`DESIGN-R01@cat1001`\n"
            "> Base revision：`DESIGN-R00@init`\n"
            "> Design topic：`DESIGN-CAT-QUERY`\n"
            "> Requirement：`REQ-CAT1001`\n\n"
            '<a id="page-behavior"></a>\nPAGE-CAT-LIST CAT1001-F01\n'
            f'<a id="data-write"></a>\n{self.scenario.aggregate_id}\n'
            '<a id="api-mapping"></a>\nAPI-CAT-PRODUCT-QUERY\n'
            '<a id="code-blueprint"></a>\nCASE-CAT-QUERY\n',
            encoding="utf-8",
        )
        dependency = merge_docs.changes_root(
            self.root, self.scenario.version, self.scenario.module
        ) / "query.dependency-impact.changeset.yaml"
        simple_yaml.dump(
            {
                "schemaVersion": 1,
                "artifact": "dependency_impact_changeset",
                "changeSet": {
                    "id": "DICHG-V_3.4-CAT-QUERY", "version": self.scenario.version,
                    "module": self.scenario.module, "reason": "登记商品查询模块影响",
                    "baseRevision": "DI-R00@init", "resultRevision": "DI-R01@cat1001",
                },
                "operations": [{
                    "operation": "add", "collection": "nodes", "targetId": "MODULE-CAT",
                    "reason": "登记商品模块", "after": {
                        "id": "MODULE-CAT", "type": "MODULE", "name": "商品模块",
                        "module": "CAT", "refs": ["docs/CAT/CAT_desc.md"],
                    },
                }],
            },
            dependency,
        )
        args = self.args(changeset=None)
        self.assertEqual(0, self.call(merge_docs.command_apply, args))
        topic = self.root / "docs" / "CAT" / "design" / "query.md"
        index = self.root / "docs" / "CAT" / "CAT_design.md"
        relation_root = self.root / "docs" / "_relations"
        for path in (
            topic, index, relation_root / "dependency_impact.yaml",
            relation_root / "dependency_impact.md", relation_root / "engineering_graph.yaml",
            relation_root / "traceability_matrix.md", relation_root / "dependency_graph.md",
            self.root / "docs" / "README.md",
        ):
            self.assertTrue(path.is_file(), path)
        self.assertIn("[打开](design/query.md)", index.read_text(encoding="utf-8"))
        self.assertIn(
            "[项目文档首页](../README.md)",
            (relation_root / "dependency_impact.md").read_text(encoding="utf-8"),
        )
        graph = simple_yaml.load(relation_root / "engineering_graph.yaml")
        design_node = next(item for item in graph["nodes"] if item["id"] == "DESIGN-CAT-QUERY")
        self.assertEqual("docs/CAT/design/query.md", design_node["documentRef"])
        self.assertIn(
            "DESIGN-CAT-QUERY",
            (relation_root / "traceability_matrix.md").read_text(encoding="utf-8"),
        )
        self.assertEqual(0, self.call(merge_docs.command_apply, args))

    def test_page_design_work_mode_is_recorded_without_skipping_tests(self) -> None:
        path = work_mode.work_path(self.root, self.scenario.version)
        work_mode.apply_mode(path, work_mode.WorkMode(page_design=True))
        self.assertEqual(0, self.call(merge_docs.command_preview, self.args()))
        generated = merge_docs.generated_root(self.root, self.scenario.version, self.scenario.module)
        plan = simple_yaml.load(generated / "merge_plan.yaml")
        self.assertTrue(plan["workMode"]["pageDesign"])
        self.assertFalse(plan["workMode"]["minimal"])
        self.assertEqual([], plan["workMode"]["skippedPhases"])

    def test_architecture_review_work_mode_is_recorded(self) -> None:
        path = work_mode.work_path(self.root, self.scenario.version)
        work_mode.apply_mode(path, work_mode.WorkMode(architecture_review=True))
        self.assertEqual(0, self.call(merge_docs.command_preview, self.args()))
        generated = merge_docs.generated_root(self.root, self.scenario.version, self.scenario.module)
        plan = simple_yaml.load(generated / "merge_plan.yaml")
        self.assertTrue(plan["workMode"]["architectureReview"])
        self.assertFalse(plan["workMode"]["minimal"])
        self.assertIn("architecture_review=true", (generated / "merge_report.md").read_text(encoding="utf-8"))

    @slow
    def test_closed_lightweight_mode_can_preview_document_archive(self) -> None:
        work_path = work_mode.work_path(self.root, self.scenario.version)
        work_mode.apply_mode_transaction(
            self.root,
            self.scenario.version,
            work_path,
            work_mode.WorkMode(lightweight=True),
            sync_tasks=False,
            agent="ProjectManagerAgent",
        )
        task_dir = self.root / "version" / self.scenario.version / "task" / "LIGHTWEIGHT-ARCHIVE"
        self.call(
            merge_docs.long_task.command_init,
            argparse.Namespace(
                doc_root=str(self.root), version=self.scenario.version,
                target="LIGHTWEIGHT-ARCHIVE", reuse=False, agent="ProjectManagerAgent",
            ),
        )
        self.call(
            merge_docs.long_task.command_sync_work_mode,
            argparse.Namespace(task_dir=str(task_dir), agent="ProjectManagerAgent"),
        )
        archive_task_dir = self.root / "version" / self.scenario.version / "task" / "ARCHIVE-GATES"
        self.call(
            merge_docs.long_task.command_init,
            argparse.Namespace(
                doc_root=str(self.root), version=self.scenario.version,
                target="ARCHIVE-GATES", reuse=False, agent="ProjectManagerAgent",
            ),
        )
        self.call(
            merge_docs.long_task.command_sync_work_mode,
            argparse.Namespace(task_dir=str(archive_task_dir), agent="ProjectManagerAgent"),
        )
        work_mode.apply_mode_transaction(
            self.root,
            self.scenario.version,
            work_path,
            work_mode.WorkMode(),
            sync_tasks=False,
            agent="ProjectManagerAgent",
        )
        for current_task_dir in (task_dir, archive_task_dir):
            self.call(
                merge_docs.long_task.command_sync_work_mode,
                argparse.Namespace(task_dir=str(current_task_dir), agent="ProjectManagerAgent"),
            )
        historical = merge_docs.historical_lightweight_state(self.root, self.scenario.version)
        self.assertTrue(historical["waived"])
        self.assertEqual(
            list(work_mode.LIGHTWEIGHT_PHASES), historical["skippedPhases"]
        )

        result = self.call(
            wk.command_document_archive,
            argparse.Namespace(
                doc_root=str(self.root), module=self.scenario.module,
                version=self.scenario.version, changeset=None, apply=False,
                agent="ProjectManagerAgent",
            ),
        )
        self.assertEqual(0, result)
        generated = merge_docs.generated_root(self.root, self.scenario.version, self.scenario.module)
        plan = simple_yaml.load(generated / "merge_plan.yaml")
        self.assertFalse(plan["workMode"]["lightweight"])
        self.assertTrue(plan["workMode"]["lightweightWaived"])
        self.assertEqual(list(work_mode.LIGHTWEIGHT_PHASES), plan["workMode"]["skippedPhases"])
        for gate in work_mode.lightweight_archive_gate_keys():
            self.assertEqual("NOT_APPLICABLE", plan["archiveGate"]["gates"][gate]["status"])

        # Complete the generated plan with the real task/evidence fixture, then
        # execute the low-level archive path without mocking command_archive.
        # wk document-archive is intentionally a preview-plus-apply wrapper;
        # applying a completed plan must not replace it with a fresh preview.
        plan = self.complete_plan(plan)
        simple_yaml.dump(plan, generated / "merge_plan.yaml")
        archive_args = argparse.Namespace(
            doc_root=str(self.root), module=self.scenario.module,
            version=self.scenario.version, changeset=None,
            require_user_confirmation=False, agent="ProjectManagerAgent",
        )
        self.assertEqual(0, self.call(merge_docs.command_archive, archive_args))
        manifest = simple_yaml.load(self.root / "archive_manifest.yaml")
        archived = manifest["appliedChangeSets"][0]["mergePlan"]
        self.assertTrue(archived["workMode"]["lightweightWaived"])
        for gate in work_mode.lightweight_archive_gate_keys():
            self.assertEqual("NOT_APPLICABLE", archived["gates"][gate]["status"])

    def test_standard_mode_without_lightweight_history_cannot_document_archive(self) -> None:
        with self.assertRaises(wk.WKError):
            self.call(
                wk.command_document_archive,
                argparse.Namespace(
                    doc_root=str(self.root), module=self.scenario.module,
                    version=self.scenario.version, changeset=None, apply=False,
                    agent="ProjectManagerAgent",
                ),
            )

    def test_minimal_work_mode_marks_test_gates_not_applicable(self) -> None:
        path = work_mode.work_path(self.root, self.scenario.version)
        work_mode.apply_mode(path, work_mode.WorkMode(minimal=True))
        self.assertEqual(0, self.call(merge_docs.command_preview, self.args()))
        generated = merge_docs.generated_root(self.root, self.scenario.version, self.scenario.module)
        plan = simple_yaml.load(generated / "merge_plan.yaml")
        self.assertTrue(plan["workMode"]["minimal"])
        self.assertEqual(["test_design", "testing"], plan["workMode"]["skippedPhases"])
        for gate in ("testDesign", "testing"):
            self.assertEqual("NOT_APPLICABLE", plan["archiveGate"]["gates"][gate]["status"])
            self.assertIn("wk -min", plan["archiveGate"]["gates"][gate]["reason"])

    def test_minimal_work_mode_archive_accepts_only_declared_test_gate_skips(self) -> None:
        path = work_mode.work_path(self.root, self.scenario.version)
        work_mode.apply_mode(path, work_mode.WorkMode(minimal=True))
        args = self.args()
        args.require_user_confirmation = False
        self.call(merge_docs.command_preview, args)
        generated = merge_docs.generated_root(self.root, self.scenario.version, self.scenario.module)
        plan = self.complete_plan(simple_yaml.load(generated / "merge_plan.yaml"))
        simple_yaml.dump(plan, generated / "merge_plan.yaml")
        self.assertEqual(0, self.call(merge_docs.command_archive, args))
        manifest = simple_yaml.load(self.root / "archive_manifest.yaml")
        archived_mode = manifest["appliedChangeSets"][0]["mergePlan"]["workMode"]
        self.assertTrue(archived_mode["minimal"])
        for gate in ("testDesign", "testing"):
            self.assertEqual("NOT_APPLICABLE", manifest["appliedChangeSets"][0]["mergePlan"]["gates"][gate]["status"])

    def test_standard_user_waivers_are_projected_and_accepted_by_archive(self) -> None:
        task_dir = self.root / "version" / self.scenario.version / "task" / "ARCHIVE-GATES"
        self.assertEqual(0, self.call(
            merge_docs.long_task.command_init,
            argparse.Namespace(
                doc_root=str(self.root), version=self.scenario.version, target="ARCHIVE-GATES",
                reuse=False, agent="ProjectManagerAgent",
            ),
        ))
        reasons = {
            "code_review": "user accepts independent Review risk for this revision",
            "testing": "user accepts unverified test risk for this revision",
        }
        for phase, reason in reasons.items():
            confirmation_output = io.StringIO()
            with contextlib.redirect_stdout(confirmation_output):
                self.assertEqual(0, wk.command_confirm(argparse.Namespace(
                    task_dir=str(task_dir), until="", reopen_from="", skip_phase=phase,
                    changed_phase="", source_revision="", reason=reason,
                    agent="ProjectManagerAgent", preview=False, apply=True, json=True,
                )))
            confirmation_ref = json.loads(confirmation_output.getvalue())["requestFingerprint"]
            self.assertEqual(0, self.call(
                merge_docs.long_task.command_skip_phase,
                argparse.Namespace(
                    task_dir=str(task_dir), phase=phase, reason=reason,
                    confirmation_ref=confirmation_ref,
                    agent="ProjectManagerAgent", json=True,
                ),
            ))

        self.seed_archive_scope_trace(task_dir)
        other_task_dir = self.root / "version" / self.scenario.version / "task" / "ARCHIVE-OTHER"
        self.assertEqual(0, self.call(
            merge_docs.long_task.command_init,
            argparse.Namespace(
                doc_root=str(self.root), version=self.scenario.version, target="ARCHIVE-OTHER",
                reuse=False, agent="ProjectManagerAgent",
            ),
        ))
        other_reason = "user accepts testing risk for another module"
        other_confirmation = io.StringIO()
        with contextlib.redirect_stdout(other_confirmation):
            self.assertEqual(0, wk.command_confirm(argparse.Namespace(
                task_dir=str(other_task_dir), until="", reopen_from="", skip_phase="testing",
                changed_phase="", source_revision="", reason=other_reason,
                agent="ProjectManagerAgent", preview=False, apply=True, json=True,
            )))
        self.assertEqual(0, self.call(
            merge_docs.long_task.command_skip_phase,
            argparse.Namespace(
                task_dir=str(other_task_dir), phase="testing", reason=other_reason,
                confirmation_ref=json.loads(other_confirmation.getvalue())["requestFingerprint"],
                agent="ProjectManagerAgent", json=True,
            ),
        ))
        self.seed_archive_scope_trace(other_task_dir, "TR-OTHER-001")
        args = self.args()
        args.require_user_confirmation = False
        self.assertEqual(0, self.call(merge_docs.command_preview, args))
        generated = merge_docs.generated_root(self.root, self.scenario.version, self.scenario.module)
        plan = simple_yaml.load(generated / "merge_plan.yaml")
        for gate_name in ("codeReview", "testing"):
            gate = plan["archiveGate"]["gates"][gate_name]
            self.assertEqual("NOT_APPLICABLE", gate["status"])
            self.assertTrue(gate["stageOutcomeRefs"])
            self.assertEqual([], gate["evidenceIds"])
            self.assertEqual([], gate["reviewerAgents"])
            self.assertNotIn(other_reason, gate["reason"])
        plan = self.complete_plan(plan)
        tampered = copy.deepcopy(plan)
        tampered_gate = tampered["archiveGate"]["gates"]["codeReview"]
        tampered_gate.update({
            "status": "PASSED", "revision": "CODE-REVIEW-OTHER",
            "stageOutcomeRefs": ["SO-OTHER-PASSED"],
            "evidenceIds": ["EVD-OTHER-PASSED"],
            "reviewerAgents": ["SpecComplianceReviewAgent"], "reason": "",
        })
        simple_yaml.dump(tampered, generated / "merge_plan.yaml")
        with self.assertRaisesRegex(merge_docs.MergeError, "must remain NOT_APPLICABLE"):
            self.call(merge_docs.command_archive, args)
        simple_yaml.dump(plan, generated / "merge_plan.yaml")
        simple_yaml.dump(plan, generated / "merge_plan.yaml")
        self.assertEqual(0, self.call(merge_docs.command_archive, args))
        archived = simple_yaml.load(self.root / "archive_manifest.yaml")["appliedChangeSets"][0]["mergePlan"]
        for gate_name in ("codeReview", "testing"):
            self.assertEqual("NOT_APPLICABLE", archived["gates"][gate_name]["status"])

    def test_standard_waiver_uses_module_task_id_when_affected_traces_are_empty(self) -> None:
        document = self.changeset()
        document["changeSet"]["affectedTraceIds"] = []
        simple_yaml.dump(document, self.change)
        task_target = self.scenario.feature_code
        task_dir = self.root / "version" / self.scenario.version / "task" / task_target
        self.assertEqual(0, self.call(
            merge_docs.long_task.command_init,
            argparse.Namespace(
                doc_root=str(self.root), version=self.scenario.version, target=task_target,
                reuse=False, agent="ProjectManagerAgent",
            ),
        ))
        reason = "user accepts code Review risk for the module task"
        confirmation_output = io.StringIO()
        with contextlib.redirect_stdout(confirmation_output):
            self.assertEqual(0, wk.command_confirm(argparse.Namespace(
                task_dir=str(task_dir), until="", reopen_from="", skip_phase="code_review",
                changed_phase="", source_revision="", reason=reason,
                agent="ProjectManagerAgent", preview=False, apply=True, json=True,
            )))
        self.assertEqual(0, self.call(
            merge_docs.long_task.command_skip_phase,
            argparse.Namespace(
                task_dir=str(task_dir), phase="code_review", reason=reason,
                confirmation_ref=json.loads(confirmation_output.getvalue())["requestFingerprint"],
                agent="ProjectManagerAgent", json=True,
            ),
        ))

        args = self.args()
        args.require_user_confirmation = False
        self.assertEqual(0, self.call(merge_docs.command_preview, args))
        generated = merge_docs.generated_root(self.root, self.scenario.version, self.scenario.module)
        plan = simple_yaml.load(generated / "merge_plan.yaml")
        gate = plan["archiveGate"]["gates"]["codeReview"]
        self.assertEqual("NOT_APPLICABLE", gate["status"])
        self.assertIn(reason, gate["reason"])

        plan = self.complete_plan(plan, task_target=task_target)
        simple_yaml.dump(plan, generated / "merge_plan.yaml")
        self.assertEqual(0, self.call(merge_docs.command_archive, args))
        archived_gate = simple_yaml.load(self.root / "archive_manifest.yaml")["appliedChangeSets"][0]["mergePlan"]["gates"]["codeReview"]
        self.assertEqual("NOT_APPLICABLE", archived_gate["status"])

    def test_archive_scope_module_fallback_requires_an_id_boundary(self) -> None:
        def record(target_id: str) -> dict:
            return {"state": {"target_id": target_id}, "trace_ids": set()}

        for target_id in ("CAT", "CAT-1001", "CAT_1001"):
            with self.subTest(target_id=target_id):
                self.assertTrue(archive_validation._task_in_archive_scope(
                    record(target_id), module="CAT", affected_trace_ids=set(), feature_codes=set()
                ))
        self.assertTrue(archive_validation._task_in_archive_scope(
            record("CAT1001"), module="CAT", affected_trace_ids=set(), feature_codes={"CAT1001"}
        ))
        for target_id in ("CATALOG1001", "CAT2001"):
            with self.subTest(target_id=target_id):
                self.assertFalse(archive_validation._task_in_archive_scope(
                    record(target_id), module="CAT", affected_trace_ids=set(), feature_codes={"CAT1001"}
                ))
        self.assertFalse(archive_validation._task_in_archive_scope(
            record("CAT2001"), module="CAT2", affected_trace_ids=set(), feature_codes={"CAT1001"}
        ))
        explicitly_cat2 = {
            "state": {"target_id": "CAT2001"}, "trace_ids": set(),
            "module_bindings": {"CAT2"}, "feature_ids": set(),
        }
        self.assertFalse(archive_validation._task_in_archive_scope(
            explicitly_cat2, module="CAT", affected_trace_ids=set(), feature_codes={"CAT2001"}
        ))
        self.assertTrue(archive_validation._task_in_archive_scope(
            explicitly_cat2, module="CAT2", affected_trace_ids=set(), feature_codes={"CAT2001"}
        ))

    def test_development_task_plan_owner_module_is_used_by_archive_runtime_scope(self) -> None:
        task_target = "CAT2-WAIVER"
        task_dir = self.root / "version" / self.scenario.version / "task" / task_target
        self.assertEqual(0, self.call(
            merge_docs.long_task.command_init,
            argparse.Namespace(
                doc_root=str(self.root), version=self.scenario.version, target=task_target,
                reuse=False, agent="ProjectManagerAgent",
            ),
        ))
        reason = "user accepts code Review risk for the CAT2 task"
        confirmation_output = io.StringIO()
        with contextlib.redirect_stdout(confirmation_output):
            self.assertEqual(0, wk.command_confirm(argparse.Namespace(
                task_dir=str(task_dir), until="", reopen_from="", skip_phase="code_review",
                changed_phase="", source_revision="", reason=reason,
                agent="ProjectManagerAgent", preview=False, apply=True, json=True,
            )))
        self.assertEqual(0, self.call(
            merge_docs.long_task.command_skip_phase,
            argparse.Namespace(
                task_dir=str(task_dir), phase="code_review", reason=reason,
                confirmation_ref=json.loads(confirmation_output.getvalue())["requestFingerprint"],
                agent="ProjectManagerAgent", json=True,
            ),
        ))

        self.assertEqual(0, task_plan.command_init(argparse.Namespace(
            task_dir=str(task_dir), version=self.scenario.version, target_id=task_target,
            requirement_analysis_revision="REQAN-R01@test",
            business_model_revision="BM-R01@test", design_revision="DESIGN-R01@test",
            test_design_revision="TESTDESIGN-R01@test", minimal=False,
            architecture_review=False, force=False, agent="ImplementationPlanAgent",
        )))
        development_plan = task_plan.load_plan(task_dir)
        development_plan["tasks"] = [{
            "task_id": "TASK-CAT2-WAIVER-001",
            "title": "验证 CAT2 归档 waiver 范围",
            "goal": "验证正式任务计划中的模块绑定能够隔离归档 waiver 范围。",
            "implementation": {
                "owner_module": "CAT2", "owner_component": "archive",
                "steps": ["读取任务模块绑定", "验证归档范围隔离"],
                "affected_files": ["skills/common-develop/**"],
                "forbidden_changes": ["不得扩大归档模块范围"],
                "implementation_decision_ref": "IMPL-DEC-CAT2-WAIVER-SCOPE",
                "strategy": "MODIFY",
                "existing_code_refs": ["skills/common-develop/scripts/_merge_docs/archive_validation.py"],
                "shared_logic_disposition": "EXTRACT_EXISTING",
                "shared_logic_owner": "archive_validation.scope",
                "compatibility_verifications": ["运行 CAT 与 CAT2 归档范围隔离回归"],
                "source_doc_refs": ["docs/CAT2_design.md#implementation-strategy-decisions"],
            },
            "acceptance_criteria": [{
                "id": "AC-CAT2-WAIVER-001",
                "assertion": "CAT2 waiver 不得被 CAT 模块归档消费。",
            }],
            "depends_on": [], "owner_agent": "DevelopAgent",
            "reviewer_agents": list(task_plan.STANDARD_REVIEWERS),
            "validation_commands": ["python -m unittest"],
            "expected_results": ["归档范围保持隔离"],
            "deliverables": ["范围隔离验证"],
            "stop_conditions": ["发现模块范围污染"],
            "trace_ids": ["TR-CAT2-WAIVER-001"], "flow_refs": [],
            "flow_step_refs": [], "risk_triggers": [], "git_checkpoint_required": True,
        }]
        (task_dir / "development_task_plan.json").write_text(
            json.dumps(development_plan, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        projected_for_cat = archive_validation.standard_skip_archive_gates(
            self.root, self.scenario.version,
            archive_gate_map=merge_docs._ARCHIVE_GATE_MAP,
            long_task_runtime=merge_docs.long_task,
            module="CAT", affected_trace_ids=set(), feature_codes={"CAT2001"},
        )
        self.assertNotIn("codeReview", projected_for_cat)

        projected_for_cat2 = archive_validation.standard_skip_archive_gates(
            self.root, self.scenario.version,
            archive_gate_map=merge_docs._ARCHIVE_GATE_MAP,
            long_task_runtime=merge_docs.long_task,
            module="CAT2", affected_trace_ids=set(), feature_codes={"CAT2001"},
        )
        cat2_refs = projected_for_cat2["codeReview"]["stageOutcomeRefs"]
        self.assertEqual(1, len(cat2_refs))
        self.assertIn("SO-CAT2-WAIVER-CODE_REVIEW-SKIP-I001", cat2_refs[0])

    def test_standard_user_waiver_archive_rejects_tampered_binding(self) -> None:
        task_dir = self.root / "version" / self.scenario.version / "task" / "ARCHIVE-GATES"
        self.assertEqual(0, self.call(
            merge_docs.long_task.command_init,
            argparse.Namespace(
                doc_root=str(self.root), version=self.scenario.version, target="ARCHIVE-GATES",
                reuse=False, agent="ProjectManagerAgent",
            ),
        ))
        reason = "user accepts unverified testing risk for this revision"
        confirmation_output = io.StringIO()
        with contextlib.redirect_stdout(confirmation_output):
            self.assertEqual(0, wk.command_confirm(argparse.Namespace(
                task_dir=str(task_dir), until="", reopen_from="", skip_phase="testing",
                changed_phase="", source_revision="", reason=reason,
                agent="ProjectManagerAgent", preview=False, apply=True, json=True,
            )))
        confirmation_ref = json.loads(confirmation_output.getvalue())["requestFingerprint"]
        self.assertEqual(0, self.call(
            merge_docs.long_task.command_skip_phase,
            argparse.Namespace(
                task_dir=str(task_dir), phase="testing", reason=reason,
                confirmation_ref=confirmation_ref,
                agent="ProjectManagerAgent", json=True,
            ),
        ))
        self.seed_archive_scope_trace(task_dir)
        args = self.args()
        args.require_user_confirmation = False
        self.assertEqual(0, self.call(merge_docs.command_preview, args))
        generated = merge_docs.generated_root(self.root, self.scenario.version, self.scenario.module)
        plan = self.complete_plan(simple_yaml.load(generated / "merge_plan.yaml"))
        plan["archiveGate"]["gates"]["testing"]["stageOutcomeRefs"] = ["SO-BOGUS"]
        simple_yaml.dump(plan, generated / "merge_plan.yaml")
        with self.assertRaisesRegex(merge_docs.MergeError, "StageOutcome refs do not match"):
            self.call(merge_docs.command_archive, args)

    def test_standard_waiver_reopen_rerun_and_archive_as_passed(self) -> None:
        task_dir = self.root / "version" / self.scenario.version / "task" / "ARCHIVE-GATES"
        self.assertEqual(0, self.call(
            merge_docs.long_task.command_init,
            argparse.Namespace(
                doc_root=str(self.root), version=self.scenario.version, target="ARCHIVE-GATES",
                reuse=False, agent="ProjectManagerAgent",
            ),
        ))
        reason = "temporarily skip code Review"
        confirmation_output = io.StringIO()
        with contextlib.redirect_stdout(confirmation_output):
            self.assertEqual(0, wk.command_confirm(argparse.Namespace(
                task_dir=str(task_dir), until="", reopen_from="", skip_phase="code_review",
                changed_phase="", source_revision="", reason=reason,
                agent="ProjectManagerAgent", preview=False, apply=True, json=True,
            )))
        self.assertEqual(0, self.call(
            merge_docs.long_task.command_skip_phase,
            argparse.Namespace(
                task_dir=str(task_dir), phase="code_review", reason=reason,
                confirmation_ref=json.loads(confirmation_output.getvalue())["requestFingerprint"],
                agent="ProjectManagerAgent", json=True,
            ),
        ))
        self.seed_archive_scope_trace(task_dir)
        args = self.args()
        args.require_user_confirmation = False
        self.assertEqual(0, self.call(merge_docs.command_preview, args))
        generated = merge_docs.generated_root(self.root, self.scenario.version, self.scenario.module)
        skipped_plan = self.complete_plan(simple_yaml.load(generated / "merge_plan.yaml"))
        self.assertEqual("NOT_APPLICABLE", skipped_plan["archiveGate"]["gates"]["codeReview"]["status"])

        state = merge_docs.long_task.runtime_state(task_dir)
        code_review = state["artifact_revisions"]["code_review"]
        task = {
            "id": "TASK-CODE-REVIEW", "logical_task_id": "LOGICAL-TASK-CODE-REVIEW",
            "feature_id": self.scenario.feature_code, "iteration_id": code_review["iteration_id"],
            "iteration_no": code_review["iteration_no"], "supersedes_iteration_id": "",
            "revision_reason": "restore skipped Review", "title": "Run code Review",
            "objective": "produce current code Review evidence", "phase": "code_review",
            "depends_on": [], "owner_agent": "ProjectManagerAgent",
            "reviewer_agents": ["SpecComplianceReviewAgent", "EngineeringStandardsReviewAgent"],
            "input_revisions": {"development": state["artifact_revisions"]["development"]["revision"]},
            "allowed_files": ["evidence/code-review/**"], "acceptance_trace_ids": [],
            "flow_refs": [], "flow_step_refs": [], "validation_commands": ["review-current-revision"],
            "expected_results": [], "stop_conditions": ["blocking finding"],
            "risk_triggers": [], "max_attempts": 1,
        }
        (task_dir / "task_plan.json").write_text(
            json.dumps({"tasks": [task]}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        reopen_reason = "restore code Review and downstream verification"
        reopen_output = io.StringIO()
        source_revision = state["artifact_revisions"]["completion_verification"]["revision"]
        with contextlib.redirect_stdout(reopen_output):
            self.assertEqual(0, wk.command_confirm(argparse.Namespace(
                task_dir=str(task_dir), until="", reopen_from="code_review",
                changed_phase="completion_verification", source_revision=source_revision,
                reason=reopen_reason, agent="ProjectManagerAgent", preview=False,
                apply=True, json=True, skip_phase="",
            )))
        self.assertEqual(0, self.call(
            merge_docs.long_task.command_reopen_phase,
            argparse.Namespace(
                task_dir=str(task_dir), from_phase="code_review",
                changed_phase="completion_verification", source_revision=source_revision,
                reason=reopen_reason, round="", user_confirmed=True,
                confirmation_ref=json.loads(reopen_output.getvalue())["requestFingerprint"],
                agent="ProjectManagerAgent",
            ),
        ))

        start_output = io.StringIO()
        with contextlib.redirect_stdout(start_output):
            self.assertEqual(0, merge_docs.long_task.command_start_attempt(argparse.Namespace(
                task_dir=str(task_dir), task_id="TASK-CODE-REVIEW", input_revision="",
                summary="Review current revision", next_action="", agent="ProjectManagerAgent",
            )))
        attempt_id = json.loads(start_output.getvalue())["attemptId"]
        revision = "CODE-REVIEW-R02@restored"
        evidence_dir = task_dir / "evidence" / "code-review"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        log_path = evidence_dir / "review.log"
        log_path.write_text("Review passes", encoding="utf-8")
        command_path = evidence_dir / "command.json"
        command_path.write_text(json.dumps({
            "schema_version": 1, "kind": "command_result", "command": "review-current-revision",
            "exit_code": 0, "executed_at": "2026-07-22T10:00:00-05:00", "revision": revision,
            "output_ref": str(log_path.relative_to(task_dir)),
            "output_digest": merge_docs.long_task.sha256_file(log_path),
        }), encoding="utf-8")
        command_entry = merge_docs.long_task.evidence.register_evidence(
            task_dir, agent="ProjectManagerAgent", evidence_type="command_ref",
            ref=str(command_path.relative_to(task_dir)), revision=revision, phase="code_review",
            scope_refs=["review-current-revision"], command_result_ref=str(command_path.relative_to(task_dir)),
        )
        summary_path = evidence_dir / "summary.txt"
        summary_path.write_text("current code Review passed", encoding="utf-8")
        summary_entry = merge_docs.long_task.evidence.register_evidence(
            task_dir, agent="ProjectManagerAgent", evidence_type="runtime_ref",
            ref=str(summary_path.relative_to(task_dir)), revision=revision, phase="code_review",
            scope_refs=["TASK-CODE-REVIEW"],
        )
        self.assertEqual(0, self.call(
            merge_docs.long_task.command_finish_attempt,
            argparse.Namespace(
                task_dir=str(task_dir), attempt_id=attempt_id, status="PASSED",
                output_revision=revision, failure_type="", failure_reason="", modified_file=[],
                command_ref=[command_entry["evidence_id"]], evidence_ref=[summary_entry["evidence_id"]],
                summary="Review restored", next_action="publish", next_agent="ProjectManagerAgent",
                agent="ProjectManagerAgent", auto_evidence=False, check="", timeout=30,
            ),
        ))
        merge_docs.long_task.acceptance.ensure_review_assertions(
            task_dir,
            phase="code_review",
            revision=revision,
            reviewers=["SpecComplianceReviewAgent", "EngineeringStandardsReviewAgent"],
            acceptance_ids=["AC-CODE-REVIEW-RESTORED"],
            source_refs=["TASK-CODE-REVIEW#expected_results/0"],
        )
        self.assertEqual(0, self.call(
            merge_docs.long_task.command_publish_artifact,
            argparse.Namespace(
                task_dir=str(task_dir), attempt_id=attempt_id, evidence_id=[], open_risk=[],
                stage_result="", agent="ProjectManagerAgent",
            ),
        ))
        state_document, state = merge_docs.long_task.read_task_state_cache(task_dir)
        review = state["collaboration_reviews"]["code_review"]
        review["status"] = "PASSED"
        review["independent_conclusions"] = {
            reviewer: self.phase_reviewer_conclusion(task_dir, "code_review", reviewer, revision)
            for reviewer in ("SpecComplianceReviewAgent", "EngineeringStandardsReviewAgent")
        }
        self._write_state(task_dir, state)
        self.assertEqual(0, self.call(
            merge_docs.long_task.command_finalize_phase,
            argparse.Namespace(task_dir=str(task_dir), agent="ProjectManagerAgent"),
        ))
        self.assertEqual(0, self.call(
            merge_docs.long_task.command_advance_phase,
            argparse.Namespace(task_dir=str(task_dir), agent="ProjectManagerAgent"),
        ))

        self.assertEqual(0, self.call(merge_docs.command_preview, args))
        restored_plan = self.complete_plan(simple_yaml.load(generated / "merge_plan.yaml"))
        self.assertEqual("PASSED", restored_plan["archiveGate"]["gates"]["codeReview"]["status"])
        simple_yaml.dump(restored_plan, generated / "merge_plan.yaml")
        self.assertEqual(0, self.call(merge_docs.command_archive, args))
        archived_gate = simple_yaml.load(self.root / "archive_manifest.yaml")["appliedChangeSets"][0]["mergePlan"]["gates"]["codeReview"]
        self.assertEqual("PASSED", archived_gate["status"])

    def test_auto_work_mode_archive_does_not_require_user_confirmation(self) -> None:
        path = work_mode.work_path(self.root, self.scenario.version)
        work_mode.apply_mode(path, work_mode.WorkMode(auto=True))
        args = self.args()
        args.require_user_confirmation = False
        self.call(merge_docs.command_preview, args)
        generated = merge_docs.generated_root(self.root, self.scenario.version, self.scenario.module)
        plan = simple_yaml.load(generated / "merge_plan.yaml")
        self.assertTrue(plan["workMode"]["auto"])
        plan = self.complete_plan(simple_yaml.load(generated / "merge_plan.yaml"))
        self.assertFalse(plan["archiveGate"]["userConfirmed"])
        simple_yaml.dump(plan, generated / "merge_plan.yaml")
        self.assertEqual(0, self.call(merge_docs.command_archive, args))
        manifest = simple_yaml.load(self.root / "archive_manifest.yaml")
        self.assertTrue(manifest["appliedChangeSets"][0]["mergePlan"]["workMode"]["auto"])

    def test_auto_work_mode_rejects_require_user_confirmation_flag(self) -> None:
        path = work_mode.work_path(self.root, self.scenario.version)
        work_mode.apply_mode(path, work_mode.WorkMode(auto=True))
        args = self.args()
        args.require_user_confirmation = True
        self.call(merge_docs.command_preview, args)
        generated = merge_docs.generated_root(self.root, self.scenario.version, self.scenario.module)
        plan = self.complete_plan(simple_yaml.load(generated / "merge_plan.yaml"))
        plan["archiveGate"]["userConfirmed"] = True
        simple_yaml.dump(plan, generated / "merge_plan.yaml")
        args.agent = "IncrementalArchiveAgent"
        with self.assertRaisesRegex(merge_docs.MergeError, "wk -auto does not allow"):
            merge_docs.command_archive(args)

    @slow
    def test_archive_rejects_work_mode_change_after_preview(self) -> None:
        args = self.args()
        args.require_user_confirmation = False
        self.call(merge_docs.command_preview, args)
        generated = merge_docs.generated_root(self.root, self.scenario.version, self.scenario.module)
        plan = self.complete_plan(simple_yaml.load(generated / "merge_plan.yaml"))
        simple_yaml.dump(plan, generated / "merge_plan.yaml")
        path = work_mode.work_path(self.root, self.scenario.version)
        work_mode.apply_mode(path, work_mode.WorkMode(model_code=True))
        args.agent = "IncrementalArchiveAgent"
        with self.assertRaisesRegex(merge_docs.MergeError, "work.md mode or content changed after preview"):
            merge_docs.command_archive(args)

    def test_stale_revision_is_rejected(self) -> None:
        document = self.changeset()
        document["changeSet"]["baseRevisions"]["db"] = "DB-R99@stale"
        simple_yaml.dump(document, self.change)
        with self.assertRaisesRegex(merge_docs.MergeError, "stale base revision"):
            merge_docs.simulate(self.args())

    def test_missing_business_reference_is_rejected(self) -> None:
        document = self.changeset()
        aggregate = next(item for item in document["operations"] if item["targetId"] == self.scenario.aggregate_id)
        aggregate["after"]["members"] = ["ENT-MISSING"]
        simple_yaml.dump(document, self.change)
        with self.assertRaisesRegex(merge_docs.MergeError, "missing member"):
            merge_docs.simulate(self.args())

    def test_update_merges_nested_items_by_name(self) -> None:
        self.call(merge_docs.command_apply, self.args())
        scenario = self.scenario
        second_feature = f"{scenario.module}1002"
        second_trace = f"TR-{scenario.module}-002"
        second_revision_tag = second_feature.lower()
        second = {
            "schemaVersion": 1,
            "changeSet": {
                "id": f"CHG-{scenario.version}-{scenario.module}-{second_feature}",
                "version": scenario.version,
                "module": scenario.module,
                "featureCode": second_feature,
                "reason": "增加状态字段",
                "baseRevisions": {
                    "business_model": f"BM-R01@{scenario.revision_tag}",
                    "db": f"DB-R01@{scenario.revision_tag}",
                },
                "resultRevisions": {
                    "business_model": f"BM-R02@{second_revision_tag}",
                    "db": f"DB-R02@{second_revision_tag}",
                },
                "affectedTraceIds": [second_trace],
            },
            "operations": [
                {
                    "artifact": "db",
                    "operation": "update",
                    "targetId": scenario.table_id,
                    "reason": f"增加{scenario.entity_name}状态",
                    "after": {
                        "columns": [
                            {"name": "status", "type": "tinyint", "nullable": False, "constraints": []}
                        ],
                        "traceIds": [scenario.trace_id, second_trace],
                    },
                },
                {
                    "artifact": "business_model",
                    "collection": "entities",
                    "operation": "update",
                    "targetId": scenario.entity_id,
                    "reason": f"增加{scenario.entity_name}状态属性",
                    "after": {
                        "attributes": [
                            {"name": "status", "type": "enum", "required": True, "constraints": []}
                        ],
                        "traceIds": [scenario.trace_id, second_trace],
                    },
                },
                {
                    "artifact": "business_model",
                    "collection": "traceability",
                    "operation": "add",
                    "targetId": second_trace,
                    "reason": "建立状态字段追踪",
                    "after": {"modelRefs": [scenario.entity_id]},
                },
            ],
        }
        second_path = self.change.with_name(f"{second_feature}.yaml")
        simple_yaml.dump(second, second_path)
        args = self.args()
        args.changeset = [str(second_path)]
        state, _, _, _ = merge_docs.simulate(args)
        self.assertEqual(3, len(state["business_model"]["entities"][0]["attributes"]))
        self.assertEqual(4, len(state["db"]["tables"][0]["columns"]))
        self.assertEqual(f"BM-R02@{second_revision_tag}", state["business_model"]["revision"])

    def test_existing_hand_written_markdown_is_preserved(self) -> None:
        api_markdown = merge_docs.markdown_path(self.root, self.scenario.module, "api")
        api_markdown.write_text("# 人工 API 文档\n", encoding="utf-8")
        self.call(merge_docs.command_apply, self.args())
        self.assertEqual("# 人工 API 文档\n", api_markdown.read_text(encoding="utf-8"))
        self.assertFalse(api_markdown.with_name(f"{self.scenario.module}_api.generated.md").exists())

    def test_invalid_stable_id_is_rejected(self) -> None:
        document = self.changeset()
        document["operations"][0]["targetId"] = "WRONG-TABLE"
        simple_yaml.dump(document, self.change)
        with self.assertRaisesRegex(merge_docs.MergeError, "db schema violation at tables.0.id"):
            merge_docs.simulate(self.args())

    def test_yaml_list_scalar_with_url_colon(self) -> None:
        value = simple_yaml.loads('values:\n  - "https://example.com/a:b"\n  - http://example.com\n')
        self.assertEqual(["https://example.com/a:b", "http://example.com"], value["values"])

    def test_archive_requires_current_ready_merge_plan(self) -> None:
        args = self.args()
        args.require_user_confirmation = True
        self.call(merge_docs.command_preview, args)
        with self.assertRaisesRegex(merge_docs.MergeError, "merge plan archiveGate.status must be READY"):
            self.call(merge_docs.command_archive, args)
        generated = merge_docs.generated_root(self.root, self.scenario.version, self.scenario.module)
        plan = self.complete_plan(simple_yaml.load(generated / "merge_plan.yaml"))
        plan_path = generated / "merge_plan.yaml"
        simple_yaml.dump(plan, plan_path)
        with self.assertRaisesRegex(merge_docs.MergeError, "userConfirmed=true"):
            self.call(merge_docs.command_archive, args)
        plan["archiveGate"]["userConfirmed"] = True
        simple_yaml.dump(plan, plan_path)
        self.assertEqual(0, self.call(merge_docs.command_archive, args))
        manifest = simple_yaml.load(self.root / "archive_manifest.yaml")
        self.assertEqual(plan["resultDigest"], manifest["appliedChangeSets"][0]["mergeDigest"])
        self.assertEqual("PASSED", manifest["appliedChangeSets"][0]["mergePlan"]["gates"]["codeReview"]["status"])
        self.assertEqual(plan["archiveGate"]["decisionRef"], manifest["appliedChangeSets"][0]["mergePlan"]["decisionRef"])

    @slow
    def test_wk_d_archive_rewrites_requirement_document_link(self) -> None:
        requirement_path = (
            self.root / "version" / self.scenario.version / "doc" / "V_3.4_0029" / "requirement.md"
        )
        requirement_path.parent.mkdir(parents=True, exist_ok=True)
        requirement_path.write_text("# 示例需求\n", encoding="utf-8")
        merge_docs.document_facts.upsert_requirement_list_row(
            self.root / "version" / self.scenario.version / "requirement_list.md",
            code="PRD0013",
            name="商品实例流向与区域销售追溯",
            version=self.scenario.version,
            document_target="doc/V_3.4_0029/requirement.md",
            description="验证 wk -d 链接改写",
            changed_code="-",
            status="已完成",
        )
        args = self.args()
        args.require_user_confirmation = False
        self.assertEqual(0, self.call(merge_docs.command_preview, args))
        generated = merge_docs.generated_root(self.root, self.scenario.version, self.scenario.module)
        plan = self.complete_plan(simple_yaml.load(generated / "merge_plan.yaml"))
        simple_yaml.dump(plan, generated / "merge_plan.yaml")
        self.assertEqual(0, self.call(merge_docs.command_archive, args))
        project_list = (self.root / "docs" / "requirement_list.md").read_text(encoding="utf-8")
        self.assertIn(
            f"[requirement.md](../version/{self.scenario.version}/doc/V_3.4_0029/requirement.md)",
            project_list,
        )

    @slow
    def test_wk_d_archives_business_flow_and_rewrites_links_for_project_directory(self) -> None:
        requirement_target = "V_3.4_0099"
        requirement_path = (
            self.root / "version" / self.scenario.version / "doc" / requirement_target / "requirement.md"
        )
        requirement_path.parent.mkdir(parents=True, exist_ok=True)
        requirement_path.write_text("# CAT1001 商品查询需求\nCAT1001-F01\nBR-CAT1001-001\nAC-CAT1001-001\n" + self.scenario.trace_id + "\n", encoding="utf-8")

        source_desc_path = (
            self.root / "version" / self.scenario.version / "doc" / self.scenario.module
            / f"{self.scenario.module}_desc.md"
        )
        desc_path = self.root / "docs" / self.scenario.module / f"{self.scenario.module}_desc.md"
        desc_lines = source_desc_path.read_text(encoding="utf-8").splitlines()
        cap_start, cap_end, _ = document_facts.find_table(
            desc_lines, "## 业务能力", document_facts.CAPABILITY_HEADERS
        )
        desc_lines[cap_start:cap_end] = document_facts.render_table(
            document_facts.CAPABILITY_HEADERS,
            [["CAP-CAT-CORE", "商品能力", "商品查询", "商品域", "ACTIVE"]],
        )
        feat_start, feat_end, _ = document_facts.find_table(
            desc_lines, "## 模块功能", document_facts.FEATURE_HEADERS
        )
        desc_lines[feat_start:feat_end] = document_facts.render_table(
            document_facts.FEATURE_HEADERS,
            [["CAT1001-F01", "商品查询", "分页查询商品", "CAP-CAT-CORE", "CAT1001", "API-CAT-PRODUCT-QUERY", "DB-CAT-PRODUCT", "ACTIVE"]],
        )
        source_desc_path.write_text("\n".join(desc_lines) + "\n", encoding="utf-8")

        group = "CATALOG_QUERY"
        change_dir = business_flow.changes_root(self.root, self.scenario.version, group)
        change_dir.mkdir(parents=True, exist_ok=True)
        flow_change = {
            "schemaVersion": 1,
            "artifact": "business_flow_changeset",
            "changeSet": {
                "id": "FLOW-CHG-V_3.4-CATALOG-001",
                "version": self.scenario.version,
                "module": self.scenario.module,
                "flowGroup": group,
                "flowGroupName": "商品查询流程",
                "reason": "建立商品查询关键流程",
                "baseRevision": business_flow.INITIAL_REVISION,
                "resultRevision": "FLOW-R01@catalog",
                "affectedTraceIds": [self.scenario.trace_id],
            },
            "operations": [{
                "operation": "add",
                "targetId": "FLOW-CATALOG-QUERY",
                "reason": "新增商品查询流程",
                "after": {
                    "id": "FLOW-CATALOG-QUERY",
                    "name": "商品查询",
                    "level": "L1",
                    "type": "MAIN",
                    "ownerModule": "CAT",
                    "participantModules": ["CAT"],
                    "goal": "查询可展示的商品分页结果",
                    "trigger": "运营人员提交商品查询条件",
                    "requirementRefs": [{
                        "id": "CAT1001",
                        "documentRef": f"version/{self.scenario.version}/doc/{requirement_target}/requirement.md",
                    }],
                    "featureRefs": [{"id": "CAT1001-F01", "module": "CAT"}],
                    "ruleRefs": ["BR-CAT1001-001"],
                    "acceptanceRefs": ["AC-CAT1001-001"],
                    "traceIds": [self.scenario.trace_id],
                    "preconditions": ["用户具有查询权限"],
                    "steps": [{
                        "id": "STEP-CATALOG-QUERY-01",
                        "order": 1,
                        "name": "查询商品",
                        "ownerModule": "CAT",
                        "featureRef": "CAT1001-F01",
                        "preconditions": [],
                        "postconditions": ["返回分页结果"],
                    }],
                    "variants": [],
                    "failurePaths": [],
                    "successCriteria": ["结果符合查询条件"],
                    "businessModelRefs": [self.scenario.aggregate_id],
                    "dependencyImpactRefs": [],
                    "designRefs": ["docs/CAT/CAT_design.md#商品查询"],
                    "testCaseIds": ["CASE-CAT-QUERY-001"],
                    "status": "ACTIVE",
                    "createdVersion": self.scenario.version,
                    "lastChangedVersion": self.scenario.version,
                    "supersedes": [],
                },
            }],
        }
        simple_yaml.dump(flow_change, change_dir / "catalog.yaml")

        args = self.args()
        self.assertEqual(0, self.call(merge_docs.command_preview, args))
        plan_path = merge_docs.generated_root(
            self.root, self.scenario.version, self.scenario.module
        ) / "merge_plan.yaml"
        plan = simple_yaml.load(plan_path)
        self.assertEqual(group, plan["businessFlowMerges"][0]["flowGroup"])
        self.assertEqual(["business_flow", "business_model"], plan["projectFactArtifacts"])
        self.assertEqual(["api", "db"], plan["versionDeltaArtifacts"])
        preview_flow = (
            self.root / "version" / self.scenario.version / "doc" / "_flows" / group
            / "generated" / f"{group}_flow.preview.md"
        )
        preview_link = f"../../../{requirement_target}/requirement.md"
        preview_text = preview_flow.read_text(encoding="utf-8")
        self.assertIn(preview_link, preview_text)
        self.assertTrue((preview_flow.parent / preview_link).resolve().is_file())
        self.assertNotIn(f"../../version/{self.scenario.version}", preview_text)
        plan = self.complete_plan(plan)
        simple_yaml.dump(plan, plan_path)
        self.assertEqual(0, self.call(merge_docs.command_archive, args))

        project_flow = self.root / "docs" / "_flows" / f"{group}_flow.md"
        text = project_flow.read_text(encoding="utf-8")
        expected_link = f"../../version/{self.scenario.version}/doc/{requirement_target}/requirement.md"
        self.assertIn(expected_link, text)
        self.assertTrue((project_flow.parent / expected_link).resolve().is_file())
        desc = desc_path.read_text(encoding="utf-8")
        self.assertIn("FLOW-CATALOG-QUERY", desc)
        self.assertIn(f"../_flows/{group}_flow.md#flow-catalog-query", desc)
        self.assertTrue((self.root / "docs" / "_flows" / "flow_index.yaml").is_file())

    def test_invalid_foreign_key_reference_is_rejected(self) -> None:
        document = self.changeset()
        document["operations"][0]["after"]["foreignKeys"][0]["targetColumns"] = ["missing_id"]
        simple_yaml.dump(document, self.change)
        with self.assertRaisesRegex(merge_docs.MergeError, "missing DB field"):
            merge_docs.simulate(self.args())

    @slow
    def test_archive_rejects_stale_preview_digest(self) -> None:
        args = self.args()
        args.require_user_confirmation = False
        self.call(merge_docs.command_preview, args)
        generated = merge_docs.generated_root(self.root, self.scenario.version, self.scenario.module)
        plan = self.complete_plan(simple_yaml.load(generated / "merge_plan.yaml"))
        simple_yaml.dump(plan, generated / "merge_plan.yaml")
        changed = self.changeset()
        changed["operations"][-1]["after"]["path"] = "/admin/dev/device/queryPageV2"
        simple_yaml.dump(changed, self.change)
        with self.assertRaisesRegex(merge_docs.MergeError, "digest is stale"):
            self.call(merge_docs.command_archive, args)

    def test_archive_requires_development_gate_from_canonical_lifecycle(self) -> None:
        args = self.args()
        args.require_user_confirmation = False
        self.call(merge_docs.command_preview, args)
        generated = merge_docs.generated_root(self.root, self.scenario.version, self.scenario.module)
        plan = self.complete_plan(simple_yaml.load(generated / "merge_plan.yaml"))
        self.assertIn("development", plan["archiveGate"]["gates"])
        del plan["archiveGate"]["gates"]["development"]
        simple_yaml.dump(plan, generated / "merge_plan.yaml")
        with self.assertRaisesRegex(merge_docs.MergeError, "schema violation.*development"):
            self.call(merge_docs.command_archive, args)

    @slow
    def test_archive_rejects_gate_without_evidence(self) -> None:
        args = self.args()
        args.require_user_confirmation = False
        self.call(merge_docs.command_preview, args)
        generated = merge_docs.generated_root(self.root, self.scenario.version, self.scenario.module)
        plan = self.complete_plan(simple_yaml.load(generated / "merge_plan.yaml"))
        plan["archiveGate"]["gates"]["codeReview"]["evidenceIds"] = []
        simple_yaml.dump(plan, generated / "merge_plan.yaml")
        with self.assertRaisesRegex(merge_docs.MergeError, "gates.codeReview.evidenceIds"):
            self.call(merge_docs.command_archive, args)

    @slow
    def test_archive_rejects_revision_map_not_matching_plan(self) -> None:
        args = self.args()
        args.require_user_confirmation = False
        self.call(merge_docs.command_preview, args)
        generated = merge_docs.generated_root(self.root, self.scenario.version, self.scenario.module)
        plan = self.complete_plan(simple_yaml.load(generated / "merge_plan.yaml"))
        plan["resultRevisions"]["api"] = "API-R99@wrong"
        simple_yaml.dump(plan, generated / "merge_plan.yaml")
        with self.assertRaisesRegex(merge_docs.MergeError, "content is stale or was modified outside archiveGate/conflictDecisions"):
            self.call(merge_docs.command_archive, args)

    def test_revision_maps_must_exactly_match_changed_artifacts(self) -> None:
        document = self.changeset()
        document["changeSet"]["baseRevisions"]["unused"] = "R00"
        document["changeSet"]["resultRevisions"]["unused"] = "R01"
        simple_yaml.dump(document, self.change)
        with self.assertRaisesRegex(merge_docs.MergeError, "unsupported revision artifacts"):
            merge_docs.simulate(self.args())

    def test_operation_cannot_change_stable_identifier(self) -> None:
        document = self.changeset()
        document["operations"][0]["after"]["id"] = "DB-OTHER"
        simple_yaml.dump(document, self.change)
        with self.assertRaisesRegex(merge_docs.MergeError, "must match targetId"):
            merge_docs.simulate(self.args())

    def test_duplicate_changeset_id_in_current_input_is_rejected(self) -> None:
        duplicate = self.change.with_name("duplicate.yaml")
        simple_yaml.dump(self.changeset(), duplicate)
        args = self.args()
        args.changeset = [str(self.change), str(duplicate)]
        with self.assertRaisesRegex(merge_docs.MergeError, "duplicate changeSet id"):
            merge_docs.simulate(args)

    def test_api_trace_reference_must_exist(self) -> None:
        document = self.changeset()
        document["operations"][-1]["after"]["traceIds"] = ["TR-MISSING"]
        simple_yaml.dump(document, self.change)
        with self.assertRaisesRegex(merge_docs.MergeError, "missing traceId TR-MISSING"):
            merge_docs.simulate(self.args())

    @slow
    def test_archive_rejects_unverified_related_document_merge(self) -> None:
        args = self.args()
        args.require_user_confirmation = False
        self.call(merge_docs.command_preview, args)
        generated = merge_docs.generated_root(self.root, self.scenario.version, self.scenario.module)
        plan = self.complete_plan(simple_yaml.load(generated / "merge_plan.yaml"))
        plan["documentMerges"] = [{
            "artifactType": "requirement",
            "sourceRef": "version/requirement.md",
            "targetRef": "docs/requirement.md",
            "mergeMode": "SECTION_MERGE",
            "sourceRevision": "REQ-R02",
            "resultRevision": "REQ-FULL-R08",
            "status": "MERGED",
            "sourceDigest": "",
            "targetDigest": "",
            "evidenceRefs": [],
            "reason": "",
        }]
        simple_yaml.dump(plan, generated / "merge_plan.yaml")
        with self.assertRaisesRegex(merge_docs.MergeError, "documentMerges.*unexpected"):
            self.call(merge_docs.command_archive, args)

    @slow
    def test_archive_rejects_pending_related_document_merge(self) -> None:
        args = self.args()
        args.require_user_confirmation = False
        self.call(merge_docs.command_preview, args)
        generated = merge_docs.generated_root(self.root, self.scenario.version, self.scenario.module)
        plan = self.complete_plan(simple_yaml.load(generated / "merge_plan.yaml"))
        plan["artifactMerges"] = [{
            "artifactType": "design_topic",
            "sourceRef": "version/requirement.md",
            "targetRef": "docs/requirement.md",
            "stableId": "DESIGN-CAT-MANUAL",
            "module": "CAT",
            "baseRevision": "DESIGN-R00@init",
            "sourceRevision": "REQ-R02",
            "resultRevision": "REQ-FULL-R08",
            "status": "PENDING",
            "sourceDigest": "0" * 64,
            "targetDigest": "0" * 64,
            "resultDigest": "0" * 64,
            "affectedStableIds": ["DESIGN-CAT-MANUAL"],
            "evidenceRefs": [],
        }]
        simple_yaml.dump(plan, generated / "merge_plan.yaml")
        with self.assertRaisesRegex(merge_docs.MergeError, "READY.*was expected"):
            self.call(merge_docs.command_archive, args)

    @slow
    def test_archive_rejects_incomplete_schema_long_task_runtime(self) -> None:
        args = self.args()
        args.require_user_confirmation = False
        self.call(merge_docs.command_preview, args)
        generated = merge_docs.generated_root(self.root, self.scenario.version, self.scenario.module)
        plan = self.complete_plan(simple_yaml.load(generated / "merge_plan.yaml"))
        task_dir = self.root / "version" / self.scenario.version / "task" / "ARCHIVE-GATES"
        (task_dir / "task_events.jsonl").unlink()
        simple_yaml.dump(plan, generated / "merge_plan.yaml")
        with self.assertRaisesRegex(merge_docs.MergeError, "long task runtime is incomplete"):
            self.call(merge_docs.command_archive, args)

    @slow
    def test_archive_rejects_missing_stage_outcome_reference(self) -> None:
        args = self.args()
        args.require_user_confirmation = False
        self.call(merge_docs.command_preview, args)
        generated = merge_docs.generated_root(self.root, self.scenario.version, self.scenario.module)
        plan = self.complete_plan(simple_yaml.load(generated / "merge_plan.yaml"))
        plan["archiveGate"]["gates"]["design"]["stageOutcomeRefs"] = ["SO-MISSING"]
        simple_yaml.dump(plan, generated / "merge_plan.yaml")
        with self.assertRaisesRegex(merge_docs.MergeError, "StageOutcome does not exist"):
            self.call(merge_docs.command_archive, args)

    @slow
    def test_archive_rejects_nonexistent_gate_evidence(self) -> None:
        args = self.args()
        args.require_user_confirmation = False
        self.call(merge_docs.command_preview, args)
        generated = merge_docs.generated_root(self.root, self.scenario.version, self.scenario.module)
        plan = self.complete_plan(simple_yaml.load(generated / "merge_plan.yaml"))
        plan["archiveGate"]["gates"]["design"]["evidenceIds"] = ["evidence/missing.md"]
        simple_yaml.dump(plan, generated / "merge_plan.yaml")
        with self.assertRaisesRegex(merge_docs.MergeError, "evidence ID does not exist"):
            self.call(merge_docs.command_archive, args)

    @slow
    def test_archive_rejects_reviewer_without_passed_conclusion(self) -> None:
        args = self.args()
        args.require_user_confirmation = False
        self.call(merge_docs.command_preview, args)
        generated = merge_docs.generated_root(self.root, self.scenario.version, self.scenario.module)
        plan = self.complete_plan(simple_yaml.load(generated / "merge_plan.yaml"))
        task_dir = self.root / "version" / self.scenario.version / "task" / "ARCHIVE-GATES"
        state = merge_docs.long_task.runtime_state(task_dir)
        state["collaboration_reviews"]["design"]["independent_conclusions"] = {}
        self._write_state(task_dir, state)
        simple_yaml.dump(plan, generated / "merge_plan.yaml")
        with self.assertRaisesRegex(merge_docs.MergeError, "missing independent conclusion"):
            self.call(merge_docs.command_archive, args)

    @slow
    def test_archive_rejects_noncanonical_decision_reference(self) -> None:
        args = self.args()
        args.require_user_confirmation = False
        self.call(merge_docs.command_preview, args)
        generated = merge_docs.generated_root(self.root, self.scenario.version, self.scenario.module)
        plan = self.complete_plan(simple_yaml.load(generated / "merge_plan.yaml"))
        plan["archiveGate"]["decisionRef"] = "INVALID-DEC-ARCHIVE-001"
        simple_yaml.dump(plan, generated / "merge_plan.yaml")
        with self.assertRaisesRegex(merge_docs.MergeError, r"current DEC-\* or IMPL-DEC-\*"):
            self.call(merge_docs.command_archive, args)

    @slow
    def test_archive_rejects_unresolved_acceptance_assertion(self) -> None:
        args = self.args()
        args.require_user_confirmation = False
        self.call(merge_docs.command_preview, args)
        generated = merge_docs.generated_root(self.root, self.scenario.version, self.scenario.module)
        plan = self.complete_plan(simple_yaml.load(generated / "merge_plan.yaml"))
        task_dir = self.root / "version" / self.scenario.version / "task" / "ARCHIVE-GATES"
        document = merge_docs.long_task.acceptance.load_document(task_dir)
        assertion = document["assertions"][0]
        assertion.update({
            "status": "UNRESOLVED", "reason": "business threshold is undefined",
            "evidence_ids": [], "verified_by_agent": "", "verified_at": "",
        })
        merge_docs.long_task.acceptance.save_document(task_dir, document)
        simple_yaml.dump(plan, generated / "merge_plan.yaml")
        with self.assertRaisesRegex(merge_docs.MergeError, "unverified blocking assertions"):
            self.call(merge_docs.command_archive, args)

    @slow
    def test_archive_rejects_stale_risk_detection_input(self) -> None:
        args = self.args()
        args.require_user_confirmation = False
        self.call(merge_docs.command_preview, args)
        generated = merge_docs.generated_root(self.root, self.scenario.version, self.scenario.module)
        plan = self.complete_plan(simple_yaml.load(generated / "merge_plan.yaml"))
        task_dir = self.root / "version" / self.scenario.version / "task" / "ARCHIVE-GATES"
        source_file = task_dir / "src" / "archive_fixture.py"
        source_file.write_text("def archive_fixture():\n    return False\n", encoding="utf-8")
        simple_yaml.dump(plan, generated / "merge_plan.yaml")
        with self.assertRaisesRegex(merge_docs.MergeError, "risk detection input_digest is stale"):
            self.call(merge_docs.command_archive, args)

    @slow
    def test_archive_rejects_tampered_review_criterion_evidence(self) -> None:
        args = self.args()
        args.require_user_confirmation = False
        self.call(merge_docs.command_preview, args)
        generated = merge_docs.generated_root(self.root, self.scenario.version, self.scenario.module)
        plan = self.complete_plan(simple_yaml.load(generated / "merge_plan.yaml"))
        task_dir = self.root / "version" / self.scenario.version / "task" / "ARCHIVE-GATES"
        state = merge_docs.long_task.runtime_state(task_dir)
        conclusions = state["collaboration_reviews"]["design"]["independent_conclusions"]
        first = next(iter(conclusions.values()))
        evidence_id = first["criteria_results"][0]["evidence"][0]["evidence_id"]
        index = merge_docs.long_task.evidence.load_registry(task_dir)
        entry = next(item for item in index["evidences"] if item["evidence_id"] == evidence_id)
        path = merge_docs.long_task.evidence.resolve_task_ref(task_dir, entry["ref"])
        path.write_text(path.read_text(encoding="utf-8") + "\ntampered", encoding="utf-8")
        simple_yaml.dump(plan, generated / "merge_plan.yaml")
        with self.assertRaisesRegex(merge_docs.MergeError, "digest mismatch"):
            self.call(merge_docs.command_archive, args)

    def test_archive_requires_exact_structured_artifacts(self) -> None:
        args = self.args()
        args.require_user_confirmation = False
        self.call(merge_docs.command_preview, args)
        generated = merge_docs.generated_root(self.root, self.scenario.version, self.scenario.module)
        plan = self.complete_plan(simple_yaml.load(generated / "merge_plan.yaml"))
        plan["structuredArtifacts"] = plan["structuredArtifacts"][:-1]
        simple_yaml.dump(plan, generated / "merge_plan.yaml")
        with self.assertRaisesRegex(merge_docs.MergeError, "content is stale or was modified outside archiveGate/conflictDecisions"):
            self.call(merge_docs.command_archive, args)

    def test_archive_preserves_but_blocks_unresolved_conflict_decision(self) -> None:
        args = self.args()
        args.require_user_confirmation = False
        self.call(merge_docs.command_preview, args)
        generated = merge_docs.generated_root(self.root, self.scenario.version, self.scenario.module)
        plan = self.complete_plan(simple_yaml.load(generated / "merge_plan.yaml"))
        plan["conflictDecisions"] = [{
            "stableId": "DESIGN-CAT-QUERY", "conflictType": "BASE_REVISION",
            "decision": "REBASE_REQUIRED", "decidedBy": "ProjectManagerAgent",
            "decisionRef": "DEC-ARCHIVE-001",
        }]
        simple_yaml.dump(plan, generated / "merge_plan.yaml")
        with self.assertRaisesRegex(merge_docs.MergeError, "remains blocking"):
            self.call(merge_docs.command_archive, args)


    def test_affected_trace_ids_must_exist_after_merge(self) -> None:
        document = self.changeset()
        document["changeSet"]["affectedTraceIds"] = ["TR-MISSING"]
        simple_yaml.dump(document, self.change)
        with self.assertRaisesRegex(merge_docs.MergeError, "affectedTraceIds reference missing"):
            merge_docs.simulate(self.args())

    def test_manifest_rejects_duplicate_changeset_ids(self) -> None:
        manifest = {
            "schemaVersion": 1,
            "appliedChangeSets": [{"id": "CHG-DUP"}, {"id": "CHG-DUP"}],
        }
        simple_yaml.dump(manifest, self.root / "archive_manifest.yaml")
        with self.assertRaisesRegex(merge_docs.MergeError, "duplicate changeSet id in archive manifest"):
            merge_docs.load_manifest(self.root)

    def test_merge_digest_is_independent_of_local_source_path(self) -> None:
        state, records, _, _ = merge_docs.simulate(self.args())
        moved = json.loads(json.dumps(records))
        moved[0]["source"] = f"/different/workspace/{self.scenario.change_filename}"
        self.assertEqual(merge_docs.merge_digest(state, records), merge_docs.merge_digest(state, moved))

    def test_api_full_markdown_files_are_not_touched_by_archive(self) -> None:
        canonical = merge_docs.markdown_path(self.root, self.scenario.module, "api")
        sibling = canonical.with_name(f"{self.scenario.module}_api.generated.md")
        canonical.parent.mkdir(parents=True, exist_ok=True)
        canonical.write_text("# 人工 API 文档\n", encoding="utf-8")
        sibling.write_text("# 另一份人工文档\n", encoding="utf-8")
        self.call(merge_docs.command_apply, self.args())
        self.assertEqual("# 人工 API 文档\n", canonical.read_text(encoding="utf-8"))
        self.assertEqual("# 另一份人工文档\n", sibling.read_text(encoding="utf-8"))

    def test_transactional_write_rolls_back_replaced_files(self) -> None:
        first = Path(self.temp.name) / "first.txt"
        second = Path(self.temp.name) / "second.txt"
        first.write_text("original-first", encoding="utf-8")
        second.write_text("original-second", encoding="utf-8")
        real_replace = merge_docs.os.replace
        calls = 0

        def flaky_replace(source, target):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("simulated replace failure")
            return real_replace(source, target)

        with mock.patch.object(merge_docs.os, "replace", side_effect=flaky_replace):
            with self.assertRaisesRegex(OSError, "simulated replace failure"):
                merge_docs.transactional_write({first: b"new-first", second: b"new-second"})
        self.assertEqual("original-first", first.read_text(encoding="utf-8"))
        self.assertEqual("original-second", second.read_text(encoding="utf-8"))

    def test_merge_plan_authorization_timestamp_requires_timezone(self) -> None:
        args = self.args()
        args.require_user_confirmation = False
        self.call(merge_docs.command_preview, args)
        generated = merge_docs.generated_root(self.root, self.scenario.version, self.scenario.module)
        plan = self.complete_plan(simple_yaml.load(generated / "merge_plan.yaml"))
        plan["archiveGate"]["authorizedAt"] = "2026-07-11T12:00:00"
        simple_yaml.dump(plan, generated / "merge_plan.yaml")
        with self.assertRaisesRegex(merge_docs.MergeError, "must include a timezone"):
            self.call(merge_docs.command_archive, args)

    def test_manual_related_document_merge_cannot_be_injected(self) -> None:
        args = self.args()
        args.require_user_confirmation = False
        self.call(merge_docs.command_preview, args)
        generated = merge_docs.generated_root(self.root, self.scenario.version, self.scenario.module)
        plan = self.complete_plan(simple_yaml.load(generated / "merge_plan.yaml"))
        plan["documentMerges"] = [{
            "artifactType": "requirement",
            "sourceRef": "version/requirement.md",
            "targetRef": "docs/requirement.md",
            "mergeMode": "SECTION_MERGE",
            "sourceRevision": "REQ-R02",
            "resultRevision": "REQ-FULL-R08",
            "status": "VERIFIED",
            "sourceDigest": "0" * 64,
            "targetDigest": "0" * 64,
            "evidenceRefs": ["evidence/requirement-merge.md"],
            "reason": "",
        }]
        simple_yaml.dump(plan, generated / "merge_plan.yaml")
        with self.assertRaisesRegex(merge_docs.MergeError, "documentMerges.*unexpected"):
            self.call(merge_docs.command_archive, args)

    def test_low_level_apply_cli_requires_explicit_flag(self) -> None:
        args = self.args()
        args.agent = "IncrementalArchiveAgent"
        args.allow_low_level_apply = False
        with self.assertRaisesRegex(merge_docs.MergeError, "writes structured document changes directly"):
            merge_docs.command_apply_cli(args)


    def test_archive_rejects_wrong_current_agent(self) -> None:
        args = self.args()
        args.agent = "ProjectManagerAgent"
        with self.assertRaisesRegex(Exception, "not authorized for merge_docs.archive"):
            merge_docs.command_archive(args)

    def test_all_merge_subcommands_parse_current_agent(self) -> None:
        parser = merge_docs.parser()
        for command in ("init", "validate", "preview", "apply", "archive", "status"):
            values = [command, "-g", "ProjectManagerAgent", "--module", self.scenario.module]
            if command != "status":
                values.extend(["--version", self.scenario.version])
            parsed = parser.parse_args(values)
            self.assertEqual(parsed.agent, "ProjectManagerAgent")


if __name__ == "__main__":
    unittest.main()
