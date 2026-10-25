"""Regression tests for current common-develop guided MANUAL_REVIEW modes."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import acceptance
import evidence
import long_task
import manual_review
import review_store


class ManualReviewModesTest(unittest.TestCase):
    def read_state(self):
        return long_task.runtime_state(self.task_dir)

    def write_state(self, state):
        document, _ = long_task.read_task_state_cache(self.task_dir)
        payloads, _, _ = long_task.append_task_events_payloads(
            self.task_dir, long_task.task_state_path(self.task_dir), document, state, [], synced_at=long_task.now()
        )
        long_task.transactional_text_write(payloads)

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.doc_root = Path(self.tmp.name) / "project_doc"
        with contextlib.redirect_stdout(io.StringIO()):
            long_task.command_init(argparse.Namespace(
                doc_root=str(self.doc_root), version="V_TEST", target="TARGET-MANUAL",
                reuse=False, agent="ProjectManagerAgent",
            ))
        self.task_dir = self.doc_root / "version" / "V_TEST" / "task" / "TARGET-MANUAL"
        state = self.read_state()
        state["current_phase"] = "business_model"
        state["current_agent"] = "BusinessModelReviewAgent"
        state["artifact_revisions"]["business_model"]["revision"] = "BM-R01"
        state["artifact_revisions"]["business_model"]["status"] = "IN_PROGRESS"
        state["collaboration_reviews"]["business_model"]["artifact_revision"] = "BM-R01"
        state["collaboration_reviews"]["business_model"]["status"] = "IN_REVIEW"
        self.write_state(state)
        acceptance.add_assertion(
            self.task_dir,
            assertion_id="ASRT-BM-001",
            acceptance_id="AC-BM-001",
            statement="订单、支付和库存职责边界合理",
            assertion_type="MANUAL_REVIEW",
            phase="completion_verification",
            revision="COMP-R01",
            blocking=True,
            parameters={
                "reviewer_agent": "BusinessModelReviewAgent",
                "review_phase": "business_model",
            },
            source_refs=["requirement.md#AC-BM-001"],
        )
        source = self.task_dir / "evidence" / "business-model.md"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("# 订单取消业务模型\n", encoding="utf-8")
        profile = manual_review.PROFILES["business_model:BusinessModelReviewAgent"]
        review_scope = ["AC-BM-001", "Order", "Payment", "Inventory", *profile["criterionIds"]]
        self.source_evidence = evidence.register_evidence(
            self.task_dir,
            agent="BusinessModelAgent",
            evidence_type="model_ref",
            ref="evidence/business-model.md",
            revision="BM-R01",
            phase="business_model",
            scope_refs=review_scope,
        )
        for filename, evidence_type, title in (
            ("requirement.md", "requirement_ref", "# Requirement evidence\n"),
            ("design.md", "design_ref", "# Design evidence\n"),
        ):
            path = self.task_dir / "evidence" / filename
            path.write_text(title, encoding="utf-8")
            evidence.register_evidence(
                self.task_dir,
                agent="BusinessModelAgent",
                evidence_type=evidence_type,
                ref=f"evidence/{filename}",
                revision="BM-R01",
                phase="business_model",
                scope_refs=review_scope,
            )
        self.review_evidence_ids = [
            entry["evidence_id"]
            for entry in manual_review.build_context(self.task_dir, "ASRT-BM-001").recommended_evidence
        ]

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_review_context_binds_canonical_content_even_when_revision_is_unchanged(self) -> None:
        canonical = self.task_dir.parent.parent / "doc" / self.task_dir.name
        canonical.mkdir(parents=True)
        model = canonical / "business_model.md"
        model.write_text("# Business model\nSuccess returns a result.\n", encoding="utf-8")
        first = manual_review.build_context(self.task_dir, "ASRT-BM-001")
        model.write_text("# Business model\nSuccess returns normally.\n", encoding="utf-8")
        second = manual_review.build_context(self.task_dir, "ASRT-BM-001")
        self.assertEqual(first.artifact_revision, second.artifact_revision)
        self.assertNotEqual(first.context_digest, second.context_digest)

    def test_review_rejects_stale_snapshot_source(self) -> None:
        from _core.review_inputs import collect_review_inputs
        source = self.doc_root / "model.yaml"
        source.write_text("revision: BM-R01\n", encoding="utf-8")
        entry = {"evidence_id": "EVD-TEST", "revision": "BM-R01", "capture_mode": "SNAPSHOT",
                 "source_ref": str(source), "digest": "0" * 64}
        with self.assertRaisesRegex(ValueError, "STALE_REVIEW_SOURCE"):
            collect_review_inputs(self.task_dir, "business_model", "BM-R01", [entry])

    def test_review_checks_snapshot_sources_beyond_twenty_recommendations(self) -> None:
        for index in range(21):
            source = self.task_dir / f"model-{index}.md"
            source.write_text(f"Model {index}\n", encoding="utf-8")
            entry = evidence.register_evidence(
                self.task_dir, agent="BusinessModelAgent", evidence_type="model_ref",
                ref=source.name, revision="BM-R01", phase="business_model", capture_mode="SNAPSHOT",
            )
        context = manual_review.build_context(self.task_dir, "ASRT-BM-001")
        self.assertEqual(20, len(context.recommended_evidence))
        self.assertNotIn(entry["evidence_id"], [item["evidence_id"] for item in context.recommended_evidence])
        source.write_text("Changed without a new revision\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "STALE_REVIEW_SOURCE"):
            manual_review.build_context(self.task_dir, "ASRT-BM-001")

    def prepare_form(self) -> Path:
        output = self.task_dir / "manual_reviews" / "ASRT-BM-001.md"
        with contextlib.redirect_stdout(io.StringIO()):
            result = manual_review.command_prepare(argparse.Namespace(
                agent="ProjectManagerAgent",
                task_dir=str(self.task_dir),
                assertion_id="ASRT-BM-001",
                output=str(output),
            ))
        self.assertEqual(0, result)
        return output

    @staticmethod
    def set_all_yes(path: Path, summary: str = "业务边界清晰。") -> None:
        text = path.read_text(encoding="utf-8")
        text = text.replace("- [ ] 是", "- [x] 是")
        text = text.replace("- [x] 无法判断", "- [ ] 无法判断")
        text = text.replace("## 主要结论\n\n>\n", f"## 主要结论\n\n> {summary}\n")
        path.write_text(text, encoding="utf-8")

    @staticmethod
    def set_question_answer(path: Path, question_id: str, label: str, detail: str) -> None:
        text = path.read_text(encoding="utf-8")
        pattern = manual_review.QUESTION_PATTERN
        chunks = []
        last = 0
        found = False
        for match in pattern.finditer(text):
            chunks.append(text[last:match.start()])
            block = match.group(0)
            if match.group("id") == question_id:
                found = True
                for option in ("是", "否", "无法判断", "不适用"):
                    block = block.replace(f"- [x] {option}", f"- [ ] {option}")
                block = block.replace(f"- [ ] {label}", f"- [x] {label}")
                block = block.replace("说明（选择“否”或“无法判断”时必填）：\n\n>\n", f"说明（选择“否”或“无法判断”时必填）：\n\n> {detail}\n")
            chunks.append(block)
            last = match.end()
        chunks.append(text[last:])
        if not found:
            raise AssertionError(f"question not found: {question_id}")
        path.write_text("".join(chunks), encoding="utf-8")

    def submit_form(self, path: Path) -> int:
        with contextlib.redirect_stdout(io.StringIO()):
            return manual_review.command_submit(argparse.Namespace(
                agent="BusinessModelReviewAgent",
                task_dir=str(self.task_dir),
                review_file=str(path),
            ))


    def test_guides_cover_every_profile_criterion_with_at_most_five_questions(self) -> None:
        for profile_id, profile in manual_review.PROFILES.items():
            reviewer = profile["reviewerAgent"]
            questions = manual_review.build_questions(reviewer, profile)
            covered = {criterion_id for question in questions for criterion_id in question["criterion_ids"]}
            self.assertEqual(set(profile["criterionIds"]), covered, profile_id)
            self.assertLessEqual(len(questions), 5, profile_id)

    def test_prepare_default_form_is_temporary_and_not_project_fact(self) -> None:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            result = manual_review.command_prepare(argparse.Namespace(
                agent="ProjectManagerAgent", task_dir=str(self.task_dir),
                assertion_id="ASRT-BM-001", output="",
            ))
        self.assertEqual(0, result)
        output = Path(buffer.getvalue().strip())
        self.assertTrue(output.is_file())
        self.assertFalse(output.is_relative_to(self.task_dir))
        self.assertIn("common-develop/review-forms", output.as_posix())
        self.assertFalse((self.task_dir / "review-forms").exists())

    def test_prepare_generates_contract_driven_questions_and_recommends_evidence(self) -> None:
        path = self.prepare_form()
        text = path.read_text(encoding="utf-8")
        profile = manual_review.PROFILES["business_model:BusinessModelReviewAgent"]
        expected_questions = manual_review.build_questions("BusinessModelReviewAgent", profile)
        self.assertEqual(len(expected_questions), len(list(manual_review.QUESTION_PATTERN.finditer(text))))
        self.assertLessEqual(len(expected_questions), 5)
        self.assertIn("[MRQ-BOUNDARY]", text)
        self.assertIn("[MRQ-INVARIANT]", text)
        self.assertIn(f"- [x] {self.source_evidence['evidence_id']}", text)
        self.assertIn("manual-review-meta", text)

    def test_markdown_submit_generates_review_evidence_and_verified_assertion(self) -> None:
        path = self.prepare_form()
        self.set_all_yes(path)
        self.assertEqual(0, self.submit_form(path))
        assertion = acceptance.load_document(self.task_dir)["assertions"][0]
        self.assertEqual("VERIFIED", assertion["status"])
        self.assertEqual("BusinessModelReviewAgent", assertion["verified_by_agent"])
        state = acceptance.load_task_state(self.task_dir)
        compact = state["collaboration_reviews"]["business_model"]["independent_conclusions"]["BusinessModelReviewAgent"]
        self.assertEqual("PASSED", compact["conclusion"])
        self.assertNotIn("criteria_results", compact)
        self.assertRegex(compact["review_result_ref"], r"^REV-[0-9]{6}$")
        conclusion = review_store.materialize_conclusion(self.task_dir, compact)
        expected_criteria = manual_review.PROFILES["business_model:BusinessModelReviewAgent"]["criterionIds"]
        self.assertEqual(len(expected_criteria), len(conclusion["criteria_results"]))
        self.assertTrue(all(result["evidence"] for result in conclusion["criteria_results"]))
        evidence_errors, entries = evidence.validate_registry(self.task_dir)
        self.assertEqual([], evidence_errors)
        self.assertIn(assertion["evidence_ids"][0], entries)

    def test_review_recommendation_ignores_missing_historical_evidence_source(self) -> None:
        old_source = self.task_dir / "evidence" / "historical-model.md"
        old_source.write_text("historical model", encoding="utf-8")
        evidence.register_evidence(
            self.task_dir,
            agent="BusinessModelAgent",
            evidence_type="model_ref",
            ref="evidence/historical-model.md",
            revision="BM-R00@historical",
            phase="business_model",
            scope_refs=["AC-BM-001"],
        )
        old_source.unlink()
        context = manual_review.build_context(self.task_dir, "ASRT-BM-001")
        self.assertTrue(context.recommended_evidence)
        self.assertTrue(all(item.get("revision") == "BM-R01" for item in context.recommended_evidence))

    def test_wizard_submits_all_yes_without_manual_json(self) -> None:
        profile = manual_review.PROFILES["business_model:BusinessModelReviewAgent"]
        question_count = len(manual_review.build_questions("BusinessModelReviewAgent", profile))
        answers = ["1"] * question_count + ["", "业务模型符合职责边界"]
        with mock.patch("builtins.input", side_effect=answers), contextlib.redirect_stdout(io.StringIO()):
            result = manual_review.command_wizard(argparse.Namespace(
                agent="BusinessModelReviewAgent",
                task_dir=str(self.task_dir),
                assertion_id="ASRT-BM-001",
            ))
        self.assertEqual(0, result)
        assertion = acceptance.load_document(self.task_dir)["assertions"][0]
        self.assertEqual("VERIFIED", assertion["status"])
        self.assertTrue(review_store.registry_path(self.task_dir).is_file())
        self.assertFalse((self.task_dir / "evidence" / "manual-review").exists())

    def test_agent_draft_requires_independent_reviewer(self) -> None:
        with self.assertRaisesRegex(ValueError, "other than the required Reviewer"):
            manual_review.command_draft(argparse.Namespace(
                agent="BusinessModelReviewAgent",
                task_dir=str(self.task_dir),
                assertion_id="ASRT-BM-001",
                answer=[], detail=[], evidence_id=[], summary="", note=[], output="",
            ))
        output = self.task_dir / "manual_reviews" / "agent-draft.md"
        with contextlib.redirect_stdout(io.StringIO()):
            result = manual_review.command_draft(argparse.Namespace(
                agent="BusinessModelAgent",
                task_dir=str(self.task_dir),
                assertion_id="ASRT-BM-001",
                answer=[
                    f"{question['question_id']}=YES"
                    for question in manual_review.build_questions(
                        "BusinessModelReviewAgent",
                        manual_review.PROFILES["business_model:BusinessModelReviewAgent"],
                    )
                ],
                detail=[],
                evidence_id=self.review_evidence_ids,
                summary="Agent 草拟：职责边界清晰。",
                note=[],
                output=str(output),
            ))
        self.assertEqual(0, result)
        self.assertIn("AGENT_DRAFT", output.read_text(encoding="utf-8"))
        self.assertEqual(0, self.submit_form(output))
        record = review_store.find_latest(
            self.task_dir, reviewer="BusinessModelReviewAgent", phase="business_model",
            revision="BM-R01", assertion_id="ASRT-BM-001",
        )
        self.assertIsNotNone(record)
        self.assertEqual("BusinessModelAgent", record["drafted_by_agent"])
        self.assertEqual("BusinessModelReviewAgent", record["confirmed_by"])

    def test_negative_answer_creates_finding_and_failed_assertion(self) -> None:
        path = self.prepare_form()
        self.set_all_yes(path, "发现失败恢复职责缺口。")
        self.set_question_answer(path, "MRQ-EXCEPTION", "否", "退款失败后的补偿责任没有定义。")
        self.assertEqual(1, self.submit_form(path))
        assertion = acceptance.load_document(self.task_dir)["assertions"][0]
        self.assertEqual("FAILED", assertion["status"])
        state = acceptance.load_task_state(self.task_dir)
        conclusion = state["collaboration_reviews"]["business_model"]["independent_conclusions"]["BusinessModelReviewAgent"]
        self.assertEqual("NEEDS_CHANGES", conclusion["conclusion"])
        issues = long_task.runtime_issues(self.task_dir)
        self.assertEqual(1, len(issues))
        self.assertEqual("OPEN", issues[0]["status"])
        self.assertIn(issues[0]["id"], state["open_issue_ids"])

    def test_unknown_answer_blocks_assertion(self) -> None:
        path = self.prepare_form()
        self.set_all_yes(path, "当前证据不足。")
        self.set_question_answer(path, "MRQ-TRACE", "无法判断", "缺少需求到业务模型的完整映射。")
        self.assertEqual(1, self.submit_form(path))
        assertion = acceptance.load_document(self.task_dir)["assertions"][0]
        self.assertEqual("BLOCKED", assertion["status"])
        state = acceptance.load_task_state(self.task_dir)
        conclusion = state["collaboration_reviews"]["business_model"]["independent_conclusions"]["BusinessModelReviewAgent"]
        self.assertEqual("BLOCKED", conclusion["conclusion"])


    def test_downstream_confirmation_reuses_existing_passed_conclusion(self) -> None:
        first = self.prepare_form()
        self.set_all_yes(first, "首次业务模型 Review 通过。")
        self.assertEqual(0, self.submit_form(first))
        state = self.read_state()
        state["current_phase"] = "completion_verification"
        state["artifact_revisions"]["completion_verification"]["revision"] = "COMP-R01"
        state["artifact_revisions"]["completion_verification"]["status"] = "IN_PROGRESS"
        self.write_state(state)
        second = self.task_dir / "manual_reviews" / "second.md"
        with contextlib.redirect_stdout(io.StringIO()):
            manual_review.command_prepare(argparse.Namespace(
                agent="ProjectManagerAgent", task_dir=str(self.task_dir),
                assertion_id="ASRT-BM-001", output=str(second),
            ))
        self.set_all_yes(second, "完成阶段复核通过。")
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            result = manual_review.command_submit(argparse.Namespace(
                agent="BusinessModelReviewAgent", task_dir=str(self.task_dir), review_file=str(second),
            ))
        self.assertEqual(0, result)
        payload = json.loads(buffer.getvalue())
        self.assertEqual("REUSED", payload["conclusion_mode"])
        records = review_store.load_records(self.task_dir)
        self.assertEqual(1, len(records))

    def test_downstream_negative_confirmation_keeps_old_conclusion_and_blocks_assertion(self) -> None:
        first = self.prepare_form()
        self.set_all_yes(first)
        self.assertEqual(0, self.submit_form(first))
        state = self.read_state()
        original = json.loads(json.dumps(
            state["collaboration_reviews"]["business_model"]["independent_conclusions"]["BusinessModelReviewAgent"]
        ))
        state["current_phase"] = "completion_verification"
        state["artifact_revisions"]["completion_verification"]["revision"] = "COMP-R01"
        state["artifact_revisions"]["completion_verification"]["status"] = "IN_PROGRESS"
        self.write_state(state)
        second = self.task_dir / "manual_reviews" / "negative-second.md"
        with contextlib.redirect_stdout(io.StringIO()):
            manual_review.command_prepare(argparse.Namespace(
                agent="ProjectManagerAgent", task_dir=str(self.task_dir),
                assertion_id="ASRT-BM-001", output=str(second),
            ))
        self.set_all_yes(second, "完成复核发现缺口。")
        self.set_question_answer(second, "MRQ-TRACE", "否", "完成阶段发现需求映射缺失。")
        self.assertEqual(1, self.submit_form(second))
        current = acceptance.load_task_state(self.task_dir)
        self.assertEqual(
            original,
            current["collaboration_reviews"]["business_model"]["independent_conclusions"]["BusinessModelReviewAgent"],
        )
        assertion = acceptance.load_document(self.task_dir)["assertions"][0]
        self.assertEqual("FAILED", assertion["status"])
        self.assertTrue(current["open_issue_ids"])

    def test_show_reads_one_review_result_by_ref(self) -> None:
        path = self.prepare_form()
        self.set_all_yes(path)
        self.assertEqual(0, self.submit_form(path))
        record = review_store.load_records(self.task_dir)[0]
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            result = manual_review.command_show(argparse.Namespace(
                agent="ProjectManagerAgent", task_dir=str(self.task_dir),
                assertion_id="", review_result_ref=record["review_id"],
            ))
        self.assertEqual(0, result)
        payload = json.loads(buffer.getvalue())
        self.assertEqual(record["review_id"], payload["review_result"]["review_id"])

    def test_stale_markdown_form_is_rejected(self) -> None:
        path = self.prepare_form()
        self.set_all_yes(path)
        state = self.read_state()
        state["artifact_revisions"]["business_model"]["revision"] = "BM-R02"
        state["collaboration_reviews"]["business_model"]["artifact_revision"] = "BM-R02"
        self.write_state(state)
        with self.assertRaisesRegex(ValueError, "stale"):
            self.submit_form(path)

    def test_tampered_agent_draft_cannot_self_confirm(self) -> None:
        output = self.prepare_form()
        self.set_all_yes(output)
        text = output.read_text(encoding="utf-8")
        meta = manual_review.parse_meta(text)
        meta["mode"] = "AGENT_DRAFT"
        meta["drafted_by_agent"] = "BusinessModelReviewAgent"
        replacement = "<!-- manual-review-meta\n" + json.dumps(meta, ensure_ascii=False, indent=2) + "\n-->"
        text = manual_review.META_PATTERN.sub(replacement, text, count=1)
        output.write_text(text, encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "independent Reviewer"):
            self.submit_form(output)


if __name__ == "__main__":
    unittest.main()
