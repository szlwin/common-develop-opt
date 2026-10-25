"""Archive fixture adapted from the original test_merge_docs regression suite.

Only test setup seeds lifecycle facts; archive validation and writes stay real.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from document_core import document_facts, evidence, merge_docs, simple_yaml
from runtime import work_mode



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



class ArchiveFixture(unittest.TestCase):

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
        module_dir = self.root / "docs" / "module" / self.scenario.module
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
