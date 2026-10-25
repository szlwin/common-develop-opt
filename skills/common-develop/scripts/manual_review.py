#!/usr/bin/env python3
"""Guide MANUAL_REVIEW assertions through wizard, Markdown, or Agent-draft input.

All three input modes compile into the same structured Review conclusion,
Evidence Registry records, and acceptance assertion update.
"""
from __future__ import annotations

import argparse
import builtins
import copy
import hashlib
import json
import re
import shlex
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

import acceptance
import agent_context
import contract_normalization
import evidence
import long_task
import review_store
import review_contract

ROOT = Path(__file__).resolve().parent.parent
GUIDES_PATH = ROOT / "assets" / "manual-review-guides.json"
RUNTIME_PATH = ROOT / "assets" / "runtime-contract.json"

GUIDES = json.loads(GUIDES_PATH.read_text(encoding="utf-8"))
REVIEW_CONTRACT = review_contract.load_contract()
RUNTIME = contract_normalization.load_runtime_contract(RUNTIME_PATH)
CRITERIA = REVIEW_CONTRACT["criteriaCatalog"]
PROFILES = REVIEW_CONTRACT["profiles"]
PHASES = {item["id"]: item for item in RUNTIME["canonicalPhases"]}
ANSWERS = {"YES", "NO", "UNKNOWN", "NOT_APPLICABLE"}
META_PATTERN = re.compile(r"<!--\s*manual-review-meta\s*\n(.*?)\n-->", re.DOTALL)
QUESTION_PATTERN = re.compile(
    r"^###\s+\[(?P<id>[^\]]+)\].*?$(?P<body>.*?)(?=^###\s+\[|^##\s+|\Z)",
    re.MULTILINE | re.DOTALL,
)
CHECKBOX_PATTERN = re.compile(r"^-\s*\[(?P<checked>[xX ])\]\s*(?P<label>.+?)\s*$", re.MULTILINE)
EVIDENCE_PATTERN = re.compile(r"^-\s*\[(?P<checked>[xX ])\]\s*(?P<id>EVD-[0-9]{6})\b.*$", re.MULTILINE)
ANSWER_LABELS = {
    "是": "YES",
    "否": "NO",
    "无法判断": "UNKNOWN",
    "不适用": "NOT_APPLICABLE",
}
ANSWER_DISPLAY = {
    "YES": "是",
    "NO": "否",
    "UNKNOWN": "无法判断",
    "NOT_APPLICABLE": "不适用",
}


@dataclass
class ReviewContext:
    task_dir: Path
    assertion_document: dict[str, Any]
    assertion: dict[str, Any]
    state_document: str
    state: dict[str, Any]
    issues: list[dict[str, Any]]
    reviewer: str
    review_phase: str
    artifact_revision: str
    profile_id: str
    profile: dict[str, Any]
    questions: list[dict[str, Any]]
    recommended_evidence: list[dict[str, Any]]
    context_digest: str


def canonical_digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def find_assertion(document: dict[str, Any], assertion_id: str) -> dict[str, Any]:
    item = next((
        value for value in document.get("assertions", [])
        if isinstance(value, dict) and value.get("assertion_id") == assertion_id
    ), None)
    if not isinstance(item, dict):
        raise ValueError(f"unknown assertion_id: {assertion_id}")
    if item.get("type") != "MANUAL_REVIEW":
        raise ValueError(f"assertion is not MANUAL_REVIEW: {assertion_id}")
    return item


def load_guides() -> dict[str, Any]:
    if GUIDES.get("schemaVersion") != 1:
        raise ValueError("manual-review-guides schemaVersion must be 1")
    if not isinstance(GUIDES.get("defaultGuide"), dict):
        raise ValueError("manual-review-guides missing defaultGuide")
    return GUIDES


def profile_criterion_ids(profile: dict[str, Any]) -> list[str]:
    values = profile.get("criterionIds")
    if not isinstance(values, list) or not values or any(not isinstance(value, str) for value in values):
        raise ValueError("Review profile criterionIds are invalid")
    return values


def matches_selector(criterion_id: str, selector: str) -> bool:
    return criterion_id == selector or criterion_id.startswith(selector + "-") or criterion_id.startswith(selector)


def build_questions(reviewer: str, profile: dict[str, Any]) -> list[dict[str, Any]]:
    guides = load_guides()
    guide = guides.get("reviewerGuides", {}).get(reviewer) or guides["defaultGuide"]
    criterion_ids = profile_criterion_ids(profile)
    assigned: set[str] = set()
    questions: list[dict[str, Any]] = []
    for raw in guide.get("questions", []):
        if not isinstance(raw, dict):
            continue
        selectors = raw.get("criterionPrefixes", [])
        explicit = raw.get("criterionIds", [])
        mapped = [
            criterion_id for criterion_id in criterion_ids
            if criterion_id not in assigned and (
                criterion_id in explicit
                or any(matches_selector(criterion_id, selector) for selector in selectors if isinstance(selector, str))
            )
        ]
        if not mapped:
            continue
        assigned.update(mapped)
        questions.append({
            "question_id": f"MRQ-{raw.get('id')}",
            "text": str(raw.get("text") or "请确认关联 Review criterion 是否满足。"),
            "criterion_ids": mapped,
            "allow_not_applicable": all(CRITERIA[cid].get("allowNotApplicable") is True for cid in mapped),
        })
    remaining = [criterion_id for criterion_id in criterion_ids if criterion_id not in assigned]
    if remaining:
        titles = "、".join(str(CRITERIA[cid].get("title") or cid) for cid in remaining)
        questions.append({
            "question_id": "MRQ-OTHER",
            "text": f"其余检查项（{titles}）是否均满足？",
            "criterion_ids": remaining,
            "allow_not_applicable": all(CRITERIA[cid].get("allowNotApplicable") is True for cid in remaining),
        })
    if not questions:
        raise ValueError(f"manual Review guide produced no questions for {reviewer}")
    return questions


def evidence_type_matches_criterion(entry: dict[str, Any], criterion_id: str) -> bool:
    criterion = CRITERIA[criterion_id]
    policy = criterion.get("evidencePolicy", {})
    entry_types = evidence.evidence_types(entry)
    any_types = set(policy.get("anyOfTypes", []))
    all_types = set(policy.get("allOfTypes", []))
    return not (any_types or all_types) or bool(entry_types & (any_types | all_types))


def recommend_evidence(
    task_dir: Path,
    *,
    review_phase: str,
    artifact_revision: str,
    assertion: dict[str, Any],
    questions: list[dict[str, Any]],
    limit: int | None = 20,
) -> list[dict[str, Any]]:
    errors, entries = evidence.validate_registry(
        task_dir,
        current_revisions={artifact_revision} if artifact_revision else None,
    )
    if errors:
        raise ValueError("Evidence Registry is invalid: " + "; ".join(errors))
    criterion_ids = [cid for question in questions for cid in question["criterion_ids"]]
    source_refs = set(assertion.get("source_refs") or [])
    acceptance_id = str(assertion.get("acceptance_id") or "")
    assertion_id = str(assertion.get("assertion_id") or "")
    scored: list[tuple[int, dict[str, Any]]] = []
    for entry in entries.values():
        if entry.get("status") != "ACTIVE":
            continue
        if entry.get("phase") != review_phase or entry.get("revision") != artifact_revision:
            continue
        supported = [cid for cid in criterion_ids if evidence_type_matches_criterion(entry, cid)]
        if not supported:
            continue
        scopes = set(entry.get("scope_refs") or [])
        score = len(supported)
        score += 4 * len(scopes & set(criterion_ids))
        score += 3 if acceptance_id in scopes else 0
        score += 3 if assertion_id in scopes else 0
        score += 2 * len(scopes & source_refs)
        scored.append((score, entry))
    scored.sort(key=lambda pair: (-pair[0], str(pair[1].get("evidence_id"))))
    return [entry for _, entry in scored[:limit]]


def build_context(task_dir: Path, assertion_id: str) -> ReviewContext:
    task_dir = task_dir.resolve()
    assertion_document = acceptance.load_document(task_dir)
    state_document, _cached_state = long_task.read_task_state_cache(task_dir, authoritative=True)
    state = long_task.runtime_state(task_dir)
    issues = long_task.runtime_issues(task_dir)
    if not isinstance(state, dict) or not isinstance(issues, list):
        raise ValueError("long-task reducer state or issue projection is invalid")
    try:
        assertion = find_assertion(assertion_document, assertion_id)
    except ValueError as exc:
        phase = str(state.get("current_phase") or "")
        artifact = ((state.get("artifact_revisions") or {}).get(phase) or {})
        revision = str(artifact.get("revision") or "")
        current = acceptance.matching_manual_review_assertions(
            assertion_document,
            phase=phase,
            revision=revision,
        )
        if current:
            valid_ids = [str(item.get("assertion_id") or "") for item in current]
            raise ValueError(
                f"unknown assertion_id: {assertion_id}; current {phase}@{revision} "
                f"Review assertion IDs: {valid_ids}"
            ) from exc
        repair = (
            f"python3 {shlex.quote(str(acceptance.__file__))} ensure-review -g ProjectManagerAgent "
            f"--task-dir {shlex.quote(str(task_dir))} --phase {shlex.quote(phase)} "
            f"--revision {shlex.quote(revision)}"
        )
        raise ValueError(
            f"unknown assertion_id: {assertion_id}; current {phase}@{revision} has no "
            f"MANUAL_REVIEW assertion; ProjectManagerAgent must run: {repair}"
        ) from exc
    parameters = assertion.get("parameters") if isinstance(assertion.get("parameters"), dict) else {}
    reviewer = str(parameters.get("reviewer_agent") or "")
    review_phase = str(parameters.get("review_phase") or assertion.get("phase") or "")
    profile_id = f"{review_phase}:{reviewer}"
    try:
        profile = review_contract.resolve_profile(REVIEW_CONTRACT, review_phase, reviewer)
    except ValueError:
        profile = None
    if reviewer not in evidence.KNOWN_AGENTS:
        raise ValueError(f"MANUAL_REVIEW reviewer is unknown: {reviewer}")
    if review_phase not in PHASES:
        raise ValueError(f"MANUAL_REVIEW review_phase is unknown: {review_phase}")
    if not isinstance(profile, dict):
        raise ValueError(f"MANUAL_REVIEW has no Review profile: {profile_id}")
    artifact = (state.get("artifact_revisions") or {}).get(review_phase)
    artifact_revision = str(artifact.get("revision") or "") if isinstance(artifact, dict) else ""
    if not artifact_revision:
        raise ValueError(f"MANUAL_REVIEW requires current artifact revision for {review_phase}")
    questions = build_questions(reviewer, profile)
    recommended = recommend_evidence(
        task_dir,
        review_phase=review_phase,
        artifact_revision=artifact_revision,
        assertion=assertion,
        questions=questions,
        limit=None,
    )
    digest_payload = {
        "assertion_id": assertion_id,
        "acceptance_id": assertion.get("acceptance_id"),
        "assertion_revision": assertion.get("revision"),
        "assertion_phase": assertion.get("phase"),
        "reviewer": reviewer,
        "review_phase": review_phase,
        "artifact_revision": artifact_revision,
        "profile_id": profile_id,
        "questions": questions,
        "evidence_bindings": recommended,
    }
    from _core.review_inputs import collect_review_inputs
    digest_payload["input_digests"] = collect_review_inputs(
        task_dir, review_phase, artifact_revision, recommended,
    )
    return ReviewContext(
        task_dir=task_dir,
        assertion_document=assertion_document,
        assertion=assertion,
        state_document=state_document,
        state=state,
        issues=issues,
        reviewer=reviewer,
        review_phase=review_phase,
        artifact_revision=artifact_revision,
        profile_id=profile_id,
        profile=profile,
        questions=questions,
        recommended_evidence=recommended[:20],
        context_digest=canonical_digest(digest_payload),
    )


def response_template(
    context: ReviewContext,
    *,
    mode: str,
    drafted_by_agent: str = "",
    answers: Optional[dict[str, str]] = None,
    details: Optional[dict[str, str]] = None,
    selected_evidence_ids: Optional[list[str]] = None,
    summary: str = "",
    notes: Optional[list[str]] = None,
) -> dict[str, Any]:
    answers = answers or {}
    details = details or {}
    selected = selected_evidence_ids
    if selected is None:
        selected = [str(entry.get("evidence_id")) for entry in context.recommended_evidence]
    return {
        "schema_version": 1,
        "assertion_id": context.assertion.get("assertion_id"),
        "acceptance_id": context.assertion.get("acceptance_id"),
        "reviewer_agent": context.reviewer,
        "review_phase": context.review_phase,
        "artifact_revision": context.artifact_revision,
        "assertion_phase": context.assertion.get("phase"),
        "assertion_revision": context.assertion.get("revision"),
        "profile_id": context.profile_id,
        "mode": mode,
        "drafted_by_agent": drafted_by_agent,
        "context_digest": context.context_digest,
        "questions": [
            {
                **question,
                "answer": answers.get(question["question_id"], "UNKNOWN"),
                "detail": details.get(question["question_id"], ""),
            }
            for question in context.questions
        ],
        "selected_evidence_ids": list(dict.fromkeys(selected)),
        "summary": summary,
        "notes": list(notes or []),
    }


def quote_lines(value: str) -> list[str]:
    lines = value.strip().splitlines() if value.strip() else [""]
    return [f"> {line}" if line else ">" for line in lines]


def render_markdown(context: ReviewContext, response: dict[str, Any]) -> str:
    guide = load_guides().get("reviewerGuides", {}).get(context.reviewer) or load_guides()["defaultGuide"]
    metadata = {
        key: response[key]
        for key in (
            "schema_version", "assertion_id", "acceptance_id", "reviewer_agent", "review_phase",
            "artifact_revision", "assertion_phase", "assertion_revision", "profile_id", "mode",
            "drafted_by_agent", "context_digest",
        )
    }
    lines = [
        "# 人工 Review 确认单",
        "",
        "<!-- manual-review-meta",
        json.dumps(metadata, ensure_ascii=False, indent=2),
        "-->",
        "",
        "## 基本信息（系统生成，请勿修改）",
        "",
        f"- Review：{guide.get('title', '专业 Review')}",
        f"- Assertion：`{response['assertion_id']}`",
        f"- Acceptance：`{response['acceptance_id']}`",
        f"- Reviewer：`{response['reviewer_agent']}`",
        f"- Review 产物：`{response['review_phase']}@{response['artifact_revision']}`",
        f"- 验收产物：`{response['assertion_phase']}@{response['assertion_revision']}`",
        f"- 输入模式：`{response['mode']}`",
        "",
        "## 检查项",
        "",
        "每题只勾选一项；发现未解决问题时选择“否”，证据不足时选择“无法判断”。",
        "",
    ]
    for question in response["questions"]:
        lines.extend([
            f"### [{question['question_id']}] {question['text']}",
            "",
            "关联 criterion：" + "、".join(f"`{cid}`" for cid in question["criterion_ids"]),
            "",
        ])
        available = ["YES", "NO", "UNKNOWN"]
        if question.get("allow_not_applicable"):
            available.append("NOT_APPLICABLE")
        for answer in available:
            checked = "x" if question.get("answer") == answer else " "
            lines.append(f"- [{checked}] {ANSWER_DISPLAY[answer]}")
        lines.extend(["", "说明（选择“否”或“无法判断”时必填）：", ""])
        lines.extend(quote_lines(str(question.get("detail") or "")))
        lines.append("")
    selected = set(response.get("selected_evidence_ids") or [])
    lines.extend([
        "## 推荐证据",
        "",
        "脚本仅列出当前 Review phase/revision 且类型适用的 ACTIVE evidence。",
        "",
    ])
    if context.recommended_evidence:
        for entry in context.recommended_evidence:
            evidence_id = str(entry.get("evidence_id"))
            checked = "x" if evidence_id in selected else " "
            lines.append(
                f"- [{checked}] {evidence_id} | {entry.get('type')} | `{entry.get('ref')}`"
            )
    else:
        lines.append("- 无可用证据；补充并注册证据后才能提交通过结论。")
    lines.extend(["", "## 主要结论", ""])
    lines.extend(quote_lines(str(response.get("summary") or "")))
    lines.extend([
        "",
        "## 非阻断补充说明",
        "",
        "此处只能记录不影响当前结论的范围说明；存在未解决问题时必须在对应检查项选择“否”或“无法判断”。",
        "",
    ])
    notes = response.get("notes") or []
    lines.extend([f"- {note}" for note in notes] or ["- 无"])
    if response.get("mode") == "AGENT_DRAFT":
        lines.extend([
            "",
            "## 独立确认要求",
            "",
            f"本确认单由 `{response.get('drafted_by_agent')}` 草拟，必须由 `{response.get('reviewer_agent')}` 独立提交；两者不得相同。",
        ])
    return "\n".join(lines).rstrip() + "\n"


def parse_meta(text: str) -> dict[str, Any]:
    match = META_PATTERN.search(text)
    if not match:
        raise ValueError("manual Review file lacks manual-review-meta")
    try:
        value = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise ValueError(f"manual Review metadata is invalid: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("manual Review metadata must be an object")
    return value


def section_text(text: str, heading: str) -> str:
    pattern = re.compile(rf"^##\s+{re.escape(heading)}\s*$\n(?P<body>.*?)(?=^##\s+|\Z)", re.MULTILINE | re.DOTALL)
    match = pattern.search(text)
    return match.group("body") if match else ""


def parse_blockquote(value: str) -> str:
    lines = []
    for line in value.splitlines():
        stripped = line.strip()
        if stripped.startswith(">"):
            lines.append(stripped[1:].lstrip())
    return "\n".join(lines).strip()


def parse_notes(value: str) -> list[str]:
    result = []
    for line in value.splitlines():
        match = re.match(r"^\s*-\s+(.+?)\s*$", line)
        if match and match.group(1) not in {"无", "无。"}:
            result.append(match.group(1))
    return result


def parse_markdown(path: Path, context: ReviewContext) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    meta = parse_meta(text)
    expected_meta = {
        "assertion_id": context.assertion.get("assertion_id"),
        "acceptance_id": context.assertion.get("acceptance_id"),
        "reviewer_agent": context.reviewer,
        "review_phase": context.review_phase,
        "artifact_revision": context.artifact_revision,
        "assertion_phase": context.assertion.get("phase"),
        "assertion_revision": context.assertion.get("revision"),
        "profile_id": context.profile_id,
    }
    for field, expected in expected_meta.items():
        if meta.get(field) != expected:
            raise ValueError(f"manual Review file is stale or tampered: {field}")
    if meta.get("context_digest") != context.context_digest:
        raise ValueError("manual Review file is stale; run prepare/draft again")
    question_meta = {question["question_id"]: question for question in context.questions}
    parsed_questions: dict[str, dict[str, Any]] = {}
    for match in QUESTION_PATTERN.finditer(text):
        question_id = match.group("id").strip()
        if question_id not in question_meta:
            continue
        body = match.group("body")
        checked_answers = []
        for checkbox in CHECKBOX_PATTERN.finditer(body):
            if checkbox.group("checked").strip().lower() != "x":
                continue
            label = checkbox.group("label").strip()
            if label in ANSWER_LABELS:
                checked_answers.append(ANSWER_LABELS[label])
        if len(checked_answers) != 1:
            raise ValueError(f"question {question_id} must select exactly one answer")
        detail_match = re.search(r"说明（.*?）：\s*\n(?P<detail>.*)$", body, re.DOTALL)
        detail = parse_blockquote(detail_match.group("detail")) if detail_match else ""
        parsed_questions[question_id] = {
            **question_meta[question_id],
            "answer": checked_answers[0],
            "detail": detail,
        }
    missing = sorted(set(question_meta) - set(parsed_questions))
    if missing:
        raise ValueError(f"manual Review file is missing questions: {missing}")
    selected = [
        match.group("id") for match in EVIDENCE_PATTERN.finditer(section_text(text, "推荐证据"))
        if match.group("checked").strip().lower() == "x"
    ]
    return {
        **meta,
        "questions": [parsed_questions[question["question_id"]] for question in context.questions],
        "selected_evidence_ids": list(dict.fromkeys(selected)),
        "summary": parse_blockquote(section_text(text, "主要结论")),
        "notes": parse_notes(section_text(text, "非阻断补充说明")),
    }


def answer_rank(answer: str) -> int:
    return {"YES": 0, "NOT_APPLICABLE": 1, "NO": 2, "UNKNOWN": 3}.get(answer, 4)


def derived_result(response: dict[str, Any]) -> str:
    answers = [str(item.get("answer") or "") for item in response.get("questions", [])]
    if any(answer not in ANSWERS for answer in answers):
        raise ValueError("manual Review response contains invalid answer")
    if "UNKNOWN" in answers:
        return "BLOCKED"
    if "NO" in answers:
        return "NEEDS_CHANGES"
    return "PASSED"


def selected_entries(context: ReviewContext, response: dict[str, Any]) -> list[dict[str, Any]]:
    entries = []
    for evidence_id in response.get("selected_evidence_ids") or []:
        entry = evidence.require_evidence(
            context.task_dir,
            evidence_id,
            expected_revision=context.artifact_revision,
            expected_phase=context.review_phase,
        )
        entries.append(entry)
    return entries


def question_for_criterion(response: dict[str, Any], criterion_id: str) -> dict[str, Any]:
    matches = [item for item in response["questions"] if criterion_id in item.get("criterion_ids", [])]
    if not matches:
        raise ValueError(f"manual Review response does not cover criterion {criterion_id}")
    return max(matches, key=lambda item: answer_rank(str(item.get("answer") or "")))


def policy_types(criterion_id: str) -> set[str]:
    policy = CRITERIA[criterion_id].get("evidencePolicy", {})
    return set(policy.get("anyOfTypes", [])) | set(policy.get("allOfTypes", []))


def normalize_review_evidence(
    context: ReviewContext,
    response: dict[str, Any],
    entries: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, str]]]]:
    criterion_ids = profile_criterion_ids(context.profile)
    normalized_entries: list[dict[str, Any]] = []
    for entry in entries:
        supported = [
            criterion_id for criterion_id in criterion_ids
            if evidence_type_matches_criterion(entry, criterion_id)
        ]
        if not supported:
            continue
        scopes = list(dict.fromkeys(
            list(entry.get("scope_refs") or [])
            + supported
            + [
                str(context.assertion.get("assertion_id")),
                str(context.assertion.get("acceptance_id")),
            ]
        ))
        normalized = evidence.link_usage(
            context.task_dir,
            str(entry.get("evidence_id")),
            agent=context.reviewer,
            scope_refs=scopes,
        )
        normalized_entries.append(normalized)
    criterion_evidence: dict[str, list[dict[str, str]]] = {}
    for criterion_id in criterion_ids:
        applicable = [
            entry for entry in normalized_entries
            if evidence_type_matches_criterion(entry, criterion_id)
        ]
        valid, message = long_task.evidence_satisfies_policy(applicable, CRITERIA[criterion_id])
        answer = str(question_for_criterion(response, criterion_id).get("answer"))
        if answer in {"PASSED", "YES", "NO"} and not valid:
            raise ValueError(f"criterion {criterion_id} evidence {message}")
        criterion_evidence[criterion_id] = [
            {
                "evidence_id": str(entry.get("evidence_id")),
                "claim": f"guided manual Review supports {criterion_id}: {CRITERIA[criterion_id].get('title')}",
            }
            for entry in applicable
        ]
    return normalized_entries, criterion_evidence


def next_issue_id(issues: list[dict[str, Any]]) -> str:
    highest = 0
    for item in issues:
        value = str(item.get("id") or "") if isinstance(item, dict) else ""
        match = re.fullmatch(r"ISSUE-MR-([0-9]{4})", value)
        if match:
            highest = max(highest, int(match.group(1)))
    return f"ISSUE-MR-{highest + 1:04d}"


def severity_for(criteria_ids: list[str]) -> str:
    rank = {"P0": 3, "P1": 2, "P2": 1}
    values = [str(CRITERIA[cid].get("failureSeverityFloor") or "P1") for cid in criteria_ids]
    return max(values, key=lambda value: rank.get(value, 0), default="P1")


def create_issue(
    context: ReviewContext,
    response: dict[str, Any],
    question: dict[str, Any],
    *,
    issue_phase: str,
    artifact_revision: str,
    motivating_evidence: list[str],
) -> dict[str, Any]:
    answer = str(question.get("answer"))
    owner = str(PHASES[context.review_phase].get("ownerAgent") or context.state.get("project_manager_agent") or "ProjectManagerAgent")
    issue_id = next_issue_id(context.issues)
    description = str(question.get("detail") or "").strip()
    if not description:
        description = (
            f"Reviewer 对检查项给出否定结论：{question['text']}"
            if answer == "NO"
            else f"Reviewer 无法判断检查项：{question['text']}"
        )
    return {
        "id": issue_id,
        "issue_type": "EVIDENCE_GAP" if answer == "UNKNOWN" else "INCONSISTENCY",
        "axis": context.reviewer.removesuffix("Agent").upper(),
        "severity": severity_for(question["criterion_ids"]),
        "confidence": 9 if answer == "NO" else 8,
        "status": "OPEN",
        "phase": issue_phase,
        "round": str(context.state.get("current_round") or f"MANUAL-{issue_phase}"),
        "artifact_revision": artifact_revision,
        "raised_by_agent": context.reviewer,
        "owner_agent": owner,
        "title": question["text"],
        "description": description,
        "impact": f"MANUAL_REVIEW assertion {context.assertion.get('assertion_id')} cannot pass",
        "motivating_evidence": motivating_evidence or list(context.assertion.get("source_refs") or [f"acceptance:{context.assertion.get('acceptance_id')}"]),
        "question": "请补充或修正后重新执行人工 Review。",
        "question_to": [owner],
        "responses": [],
        "recommendation": "修正相关产物并补充当前 revision 的证据，然后重新 Review。",
        "affected_artifacts": list(dict.fromkeys([context.review_phase, str(context.assertion.get("phase") or "")])),
        "affected_trace_ids": [],
        "decision": "",
        "resolution_revision": "",
        "resolution_evidence": "",
        "verified_by_agent": "",
        "verified_at": "",
        "defer_reason": "",
    }


def current_conclusion(context: ReviewContext) -> Optional[dict[str, Any]]:
    review = (context.state.get("collaboration_reviews") or {}).get(context.review_phase)
    conclusions = review.get("independent_conclusions") if isinstance(review, dict) else None
    compact = conclusions.get(context.reviewer) if isinstance(conclusions, dict) else None
    if not isinstance(compact, dict):
        return None
    try:
        conclusion = review_store.materialize_conclusion(context.task_dir, compact)
    except ValueError:
        return None
    if conclusion.get("revision") == context.artifact_revision:
        return conclusion
    return None


def expected_reviewers(context: ReviewContext) -> set[str]:
    review = (context.state.get("collaboration_reviews") or {}).get(context.review_phase)
    if not isinstance(review, dict):
        return {context.reviewer}
    result = set(review.get("required_reviewers") or [])
    result.update((review.get("additional_reviewers") or {}).keys())
    conditional = review.get("conditional_reviewers") or {}
    try:
        import storage
        trace_document = storage.read_json(storage.path(context.task_dir, "traceability"), default={})
        traces = trace_document.get("items", []) if isinstance(trace_document, dict) else []
    except (OSError, ValueError):
        traces = []
    if isinstance(conditional, dict) and isinstance(traces, list):
        for reviewer, flag in conditional.items():
            if any(isinstance(trace, dict) and trace.get(flag) is True for trace in traces):
                result.add(reviewer)
    return result


def install_conclusion(
    context: ReviewContext,
    response: dict[str, Any],
    criterion_evidence: dict[str, list[dict[str, str]]],
    result: str,
    issue_ids_by_question: dict[str, list[str]],
) -> dict[str, Any]:
    criteria_results = []
    union_findings: list[str] = []
    for criterion_id in profile_criterion_ids(context.profile):
        question = question_for_criterion(response, criterion_id)
        answer = str(question.get("answer"))
        status = {
            "YES": "PASSED",
            "NO": "FAILED",
            "UNKNOWN": "BLOCKED",
            "NOT_APPLICABLE": "NOT_APPLICABLE",
        }[answer]
        if status == "NOT_APPLICABLE" and CRITERIA[criterion_id].get("allowNotApplicable") is not True:
            raise ValueError(f"criterion {criterion_id} does not allow NOT_APPLICABLE")
        findings = issue_ids_by_question.get(question["question_id"], []) if status == "FAILED" else []
        union_findings.extend(findings)
        reason = str(question.get("detail") or "").strip()
        if status in {"BLOCKED", "NOT_APPLICABLE"} and not reason:
            reason = "Reviewer 无法基于当前证据形成肯定结论。" if status == "BLOCKED" else "当前变更不适用。"
        criteria_results.append({
            "criterion_id": criterion_id,
            "status": status,
            "evidence": criterion_evidence.get(criterion_id, []),
            "finding_ids": findings,
            "reason": reason,
        })
    conclusion = {
        "profile_id": context.profile_id,
        "revision": context.artifact_revision,
        "conclusion": result,
        "checked_scope": list(dict.fromkeys(
            list(context.assertion.get("source_refs") or [])
            + [str(context.assertion.get("assertion_id")), str(context.assertion.get("acceptance_id"))]
            + [item["question_id"] for item in response["questions"]]
            + list(response.get("selected_evidence_ids") or [])
        )),
        "criteria_results": criteria_results,
        "finding_ids": list(dict.fromkeys(union_findings)),
        "limitations": [] if result == "PASSED" else [
            str(item.get("detail") or item.get("text"))
            for item in response["questions"]
            if item.get("answer") in {"NO", "UNKNOWN"}
        ],
        "reviewed_at": acceptance.now(),
    }
    review = (context.state.get("collaboration_reviews") or {}).get(context.review_phase)
    if not isinstance(review, dict):
        raise ValueError(f"task-state lacks collaboration review for {context.review_phase}")
    if review.get("artifact_revision") not in {"", context.artifact_revision}:
        raise ValueError("collaboration Review artifact_revision differs from current artifact")
    review["artifact_revision"] = context.artifact_revision
    review.setdefault("independent_conclusions", {})[context.reviewer] = conclusion
    if result == "BLOCKED":
        review["status"] = "BLOCKED"
    elif result == "NEEDS_CHANGES":
        review["status"] = "REWORK"
    else:
        conclusions = review.get("independent_conclusions") or {}
        review["status"] = "PASSED" if all(
            isinstance(conclusions.get(reviewer), dict)
            and conclusions[reviewer].get("conclusion") == "PASSED"
            and conclusions[reviewer].get("revision") == context.artifact_revision
            for reviewer in expected_reviewers(context)
        ) else "IN_REVIEW"
    return conclusion


def default_review_form_path(context: ReviewContext, suffix: str = ".md") -> Path:
    directory = Path(tempfile.gettempdir()) / "common-develop" / "review-forms" / context.task_dir.name
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{context.assertion.get('assertion_id')}{suffix}"


def write_review_result(
    context: ReviewContext,
    response: dict[str, Any],
    *,
    confirmer: str,
    result: str,
    conclusion_mode: str,
    selected_ids: list[str],
    issue_ids: list[str],
    conclusion: dict[str, Any],
) -> dict[str, Any]:
    answers = [str(item.get("answer") or "") for item in response.get("questions", [])]
    default_answer = max(set(answers), key=answers.count) if answers else "YES"
    exceptions: dict[str, dict[str, str]] = {}
    for item in response.get("questions", []):
        answer = str(item.get("answer") or "")
        detail = str(item.get("detail") or "").strip()
        if answer != default_answer or detail:
            exceptions[str(item.get("question_id"))] = {"answer": answer, "detail": detail}
    payload = {
        "schema_version": 1,
        "assertion_id": context.assertion.get("assertion_id"),
        "acceptance_id": context.assertion.get("acceptance_id"),
        "profile_id": context.profile_id,
        "reviewer": context.reviewer,
        "confirmed_by": confirmer,
        "confirmation_mode": response.get("mode"),
        "drafted_by_agent": response.get("drafted_by_agent") or "",
        "review_phase": context.review_phase,
        "review_revision": context.artifact_revision,
        "assertion_phase": context.assertion.get("phase"),
        "assertion_revision": context.assertion.get("revision"),
        "result": result,
        "conclusion_mode": conclusion_mode,
        "conclusion": conclusion,
        "selected_evidence_ids": selected_ids,
        "issue_ids": issue_ids,
        "default_answer": default_answer,
        "answer_exceptions": exceptions,
        "summary": response.get("summary") or "",
        "notes": response.get("notes") or [],
        "confirmed_at": acceptance.now(),
    }
    return review_store.append_record(context.task_dir, payload)


def latest_review_result(context: ReviewContext) -> Optional[dict[str, Any]]:
    return review_store.find_latest(
        context.task_dir,
        reviewer=context.reviewer,
        phase=context.review_phase,
        revision=context.artifact_revision,
        assertion_id=str(context.assertion.get("assertion_id") or ""),
    )

def backup_paths(paths: Iterable[Path]) -> dict[Path, Optional[bytes]]:
    return {path: path.read_bytes() if path.exists() else None for path in paths}


def restore_paths(backups: dict[Path, Optional[bytes]]) -> None:
    for path, content in backups.items():
        if content is None:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)


def process_response(context: ReviewContext, response: dict[str, Any], confirmer: str) -> dict[str, Any]:
    if confirmer != context.reviewer:
        raise ValueError(f"MANUAL_REVIEW requires reviewer {context.reviewer}, not {confirmer}")
    if response.get("context_digest") != context.context_digest:
        raise ValueError("manual Review response is stale")
    if response.get("mode") == "AGENT_DRAFT":
        drafter = str(response.get("drafted_by_agent") or "")
        if not drafter:
            raise ValueError("AGENT_DRAFT requires drafted_by_agent")
        if drafter == confirmer:
            raise ValueError("Agent draft must be confirmed by an independent Reviewer")
    for item in response.get("questions", []):
        answer = str(item.get("answer") or "")
        if answer not in ANSWERS:
            raise ValueError(f"question {item.get('question_id')} has invalid answer: {answer}")
        if answer in {"NO", "UNKNOWN"} and not str(item.get("detail") or "").strip():
            raise ValueError(f"question {item.get('question_id')} requires detail for {answer}")
        if answer == "NOT_APPLICABLE" and not item.get("allow_not_applicable"):
            raise ValueError(f"question {item.get('question_id')} does not allow NOT_APPLICABLE")
    result = derived_result(response)
    entries = selected_entries(context, response)
    from _core.review_inputs import collect_review_inputs
    collect_review_inputs(context.task_dir, context.review_phase, context.artifact_revision, entries)
    if result in {"PASSED", "NEEDS_CHANGES"} and not entries:
        raise ValueError("manual Review requires current registered evidence")
    existing = current_conclusion(context)
    may_generate_conclusion = context.state.get("current_phase") == context.review_phase
    generate_conclusion = not (
        isinstance(existing, dict)
        and existing.get("conclusion") == "PASSED"
        and context.state.get("current_phase") != context.review_phase
    )
    if generate_conclusion and not may_generate_conclusion:
        raise ValueError(
            f"no reusable PASSED conclusion exists for {context.profile_id}@{context.artifact_revision}; "
            f"reopen or enter phase {context.review_phase} before submitting"
        )
    tracked_paths = [
        long_task.task_state_path(context.task_dir),
        long_task.task_events.event_path(context.task_dir),
        evidence.registry_path(context.task_dir),
        acceptance.assertions_path(context.task_dir),
        review_store.registry_path(context.task_dir),
    ]
    backups = backup_paths(tracked_paths)
    try:
        normalized_entries: list[dict[str, Any]] = []
        criterion_evidence: dict[str, list[dict[str, str]]] = {}
        if generate_conclusion:
            normalized_entries, criterion_evidence = normalize_review_evidence(context, response, entries)
        motivating = [str(entry.get("evidence_id")) for entry in entries]
        issue_ids_by_question: dict[str, list[str]] = {}
        created_issues: list[dict[str, Any]] = []
        closed_issues: list[dict[str, Any]] = []
        issue_phase = context.review_phase if generate_conclusion else str(context.assertion.get("phase") or context.review_phase)
        issue_revision = context.artifact_revision if generate_conclusion else str(context.assertion.get("revision") or context.artifact_revision)
        for question in response["questions"]:
            if question.get("answer") not in {"NO", "UNKNOWN"}:
                continue
            issue = create_issue(
                context,
                response,
                question,
                issue_phase=issue_phase,
                artifact_revision=issue_revision,
                motivating_evidence=motivating,
            )
            context.issues.append(issue)
            created_issues.append(issue)
            issue_ids_by_question[question["question_id"]] = [issue["id"]]
        if generate_conclusion and result == "PASSED":
            selected_ids = [str(entry.get("evidence_id")) for entry in entries]
            for issue in context.issues:
                if (
                    not isinstance(issue, dict)
                    or issue.get("status") not in long_task.OPEN_ISSUE_STATUSES
                    or issue.get("phase") != context.review_phase
                    or issue.get("raised_by_agent") != context.reviewer
                    or issue.get("artifact_revision") == context.artifact_revision
                ):
                    continue
                issue["status"] = "CLOSED"
                issue["responses"] = list(issue.get("responses") or []) + [{
                    "agent": context.reviewer,
                    "revision": context.artifact_revision,
                    "response": "replacement revision passed independent re-review",
                    "created_at": acceptance.now(),
                }]
                issue["decision"] = "replacement revision verified"
                issue["resolution_revision"] = context.artifact_revision
                issue["resolution_evidence"] = selected_ids[0] if selected_ids else ""
                issue["verified_by_agent"] = context.reviewer
                issue["verified_at"] = acceptance.now()
                closed_issues.append(copy.deepcopy(issue))
        conclusion_mode = "REUSED"
        generated_conclusion = None
        if generate_conclusion:
            generated_conclusion = install_conclusion(
                context,
                response,
                criterion_evidence,
                result,
                issue_ids_by_question,
            )
            conclusion_mode = "GENERATED"
            validation_errors: list[str] = []
            long_task.validate_independent_conclusion(
                context.task_dir,
                context.review_phase,
                context.reviewer,
                generated_conclusion,
                context.artifact_revision,
                context.issues,
                validation_errors,
            )
            if validation_errors:
                raise ValueError("generated Review conclusion is invalid: " + "; ".join(validation_errors))
        selected_ids = [str(entry.get("evidence_id")) for entry in entries]
        review_record: Optional[dict[str, Any]] = None
        if generated_conclusion is not None:
            review_record = write_review_result(
                context, response, confirmer=confirmer, result=result,
                conclusion_mode=conclusion_mode, selected_ids=selected_ids,
                issue_ids=[item["id"] for item in created_issues],
                conclusion=generated_conclusion,
            )
            review = (context.state.get("collaboration_reviews") or {}).get(context.review_phase)
            if not isinstance(review, dict):
                raise ValueError(f"task-state lacks collaboration review for {context.review_phase}")
            review.setdefault("independent_conclusions", {})[context.reviewer] = review_store.compact_conclusion(review_record)
        else:
            review_record = latest_review_result(context)
            if review_record is None:
                raise ValueError("reused Review conclusion lacks review_result_ref")
        event_timestamp = acceptance.now()
        event_specs = [
            long_task.task_events.issue_event_spec(
                "ISSUE_OPENED", issue, timestamp=event_timestamp, source="manual_review.py process-response"
            )
            for issue in created_issues
        ] + [
            long_task.task_events.issue_event_spec(
                "ISSUE_CLOSED", issue, timestamp=event_timestamp, source="manual_review.py process-response"
            )
            for issue in closed_issues
        ]
        event_payloads, context.state, _reduced = long_task.append_task_events_payloads(
            context.task_dir, long_task.task_state_path(context.task_dir),
            context.state_document, context.state, event_specs, synced_at=event_timestamp
        )
        long_task.transactional_text_write(event_payloads)
        assertion = find_assertion(context.assertion_document, str(context.assertion.get("assertion_id")))
        assertion_parameters = (
            assertion.get("parameters") if isinstance(assertion.get("parameters"), dict) else {}
        )
        binding_id = str(assertion_parameters.get("review_binding_id") or "")
        bound_assertions = [assertion]
        if binding_id:
            bound_assertions = [
                item for item in context.assertion_document.get("assertions", [])
                if isinstance(item, dict)
                and item.get("type") == "MANUAL_REVIEW"
                and isinstance(item.get("parameters"), dict)
                and item["parameters"].get("review_binding_id") == binding_id
                and item.get("phase") == assertion.get("phase")
                and item.get("revision") == assertion.get("revision")
                and item["parameters"].get("reviewer_agent") == confirmer
            ]
        assertion_reason = str(response.get("summary") or "").strip() or (
            "guided manual Review passed" if result == "PASSED" else "guided manual Review did not pass"
        )
        for bound_assertion in bound_assertions:
            bound_assertion["evidence_ids"] = selected_ids
            bound_assertion["verified_by_agent"] = confirmer
            bound_assertion["verified_at"] = acceptance.now()
            bound_assertion["reason"] = assertion_reason
            if result == "PASSED":
                acceptance.require_manual_review_conclusion(context.task_dir, bound_assertion, confirmer)
                bound_assertion["status"] = "VERIFIED"
            elif result == "NEEDS_CHANGES":
                bound_assertion["status"] = "FAILED"
            else:
                bound_assertion["status"] = "BLOCKED"
        acceptance.save_document(context.task_dir, context.assertion_document)
        evidence_errors, _ = evidence.validate_registry(
            context.task_dir,
            current_revisions={context.artifact_revision} if context.artifact_revision else None,
        )
        acceptance_errors, _ = acceptance.validate_document(context.task_dir)
        if evidence_errors or acceptance_errors:
            raise ValueError("manual Review output validation failed: " + "; ".join(evidence_errors + acceptance_errors))
        return {
            "status": "VERIFIED" if result == "PASSED" else ("FAILED" if result == "NEEDS_CHANGES" else "BLOCKED"),
            "review_conclusion": result,
            "conclusion_mode": conclusion_mode,
            "assertion_id": context.assertion.get("assertion_id"),
            "bound_assertion_ids": [item.get("assertion_id") for item in bound_assertions],
            "review_result_ref": review_record.get("review_id") if review_record else "",
            "normalized_review_evidence_ids": [str(entry.get("evidence_id")) for entry in normalized_entries],
            "issue_ids": [item["id"] for item in created_issues],
        }
    except Exception:
        restore_paths(backups)
        raise


def parse_answer_arguments(values: list[str], context: ReviewContext) -> dict[str, str]:
    known = {item["question_id"] for item in context.questions}
    result: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"--answer must use QUESTION_ID=YES|NO|UNKNOWN|NOT_APPLICABLE: {value}")
        question_id, answer = value.split("=", 1)
        question_id = question_id.strip()
        answer = answer.strip().upper()
        if question_id not in known:
            raise ValueError(f"unknown question id: {question_id}")
        if answer not in ANSWERS:
            raise ValueError(f"invalid answer: {answer}")
        result[question_id] = answer
    return result


def parse_detail_arguments(values: list[str], context: ReviewContext) -> dict[str, str]:
    known = {item["question_id"] for item in context.questions}
    result: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"--detail must use QUESTION_ID=text: {value}")
        question_id, detail = value.split("=", 1)
        question_id = question_id.strip()
        if question_id not in known:
            raise ValueError(f"unknown question id: {question_id}")
        result[question_id] = detail.strip()
    return result


def command_prepare(args: argparse.Namespace) -> int:
    agent_context.authorize_declared(args.agent, "manual_review", "prepare")
    context = build_context(Path(args.task_dir), args.assertion_id)
    response = response_template(context, mode="MARKDOWN")
    output = Path(args.output) if args.output else default_review_form_path(context, ".md")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_markdown(context, response), encoding="utf-8")
    print(output)
    return 0


def command_draft(args: argparse.Namespace) -> int:
    agent_context.authorize_declared(args.agent, "manual_review", "draft")
    context = build_context(Path(args.task_dir), args.assertion_id)
    if args.agent == context.reviewer:
        raise ValueError("Agent draft must be produced by an Agent other than the required Reviewer")
    answers = parse_answer_arguments(args.answer, context)
    details = parse_detail_arguments(args.detail, context)
    response = response_template(
        context,
        mode="AGENT_DRAFT",
        drafted_by_agent=args.agent,
        answers=answers,
        details=details,
        selected_evidence_ids=args.evidence_id or None,
        summary=args.summary,
        notes=args.note,
    )
    output = Path(args.output) if args.output else default_review_form_path(context, ".draft.md")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_markdown(context, response), encoding="utf-8")
    print(output)
    return 0


def prompt_choice(prompt: str, options: list[tuple[str, str]], input_fn=None) -> str:
    if input_fn is None:
        input_fn = builtins.input
    print(prompt)
    for index, (_, label) in enumerate(options, 1):
        print(f"{index}. {label}")
    while True:
        raw = input_fn("请选择：").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1][0]
        print("输入无效，请重新选择。")


def command_wizard(args: argparse.Namespace) -> int:
    agent_context.authorize_declared(args.agent, "manual_review", "wizard")
    context = build_context(Path(args.task_dir), args.assertion_id)
    if args.agent != context.reviewer:
        raise ValueError(f"wizard must be completed by {context.reviewer}")
    answers: dict[str, str] = {}
    details: dict[str, str] = {}
    for question in context.questions:
        options = [("YES", "是"), ("NO", "否"), ("UNKNOWN", "无法判断")]
        if question.get("allow_not_applicable"):
            options.append(("NOT_APPLICABLE", "不适用"))
        answer = prompt_choice(question["text"], options)
        answers[question["question_id"]] = answer
        if answer in {"NO", "UNKNOWN", "NOT_APPLICABLE"}:
            details[question["question_id"]] = input("请说明原因：").strip()
    selected = []
    if context.recommended_evidence:
        print("\n推荐证据：")
        for index, entry in enumerate(context.recommended_evidence, 1):
            print(f"{index}. {entry['evidence_id']} | {entry['type']} | {entry['ref']}")
        raw = input("选择证据序号（逗号分隔，回车=全部，none=不选）：").strip()
        if not raw:
            selected = [entry["evidence_id"] for entry in context.recommended_evidence]
        elif raw.lower() != "none":
            for part in raw.split(","):
                if not part.strip().isdigit() or not 1 <= int(part.strip()) <= len(context.recommended_evidence):
                    raise ValueError(f"invalid evidence selection: {part}")
                selected.append(context.recommended_evidence[int(part.strip()) - 1]["evidence_id"])
    summary = input("主要结论（一句话）：").strip()
    response = response_template(
        context,
        mode="WIZARD",
        answers=answers,
        details=details,
        selected_evidence_ids=selected,
        summary=summary,
    )
    payload = process_response(context, response, args.agent)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["status"] == "VERIFIED" else 1


def command_submit(args: argparse.Namespace) -> int:
    agent_context.authorize_declared(args.agent, "manual_review", "submit")
    review_file = Path(args.review_file).resolve()
    meta = parse_meta(review_file.read_text(encoding="utf-8"))
    context = build_context(Path(args.task_dir), str(meta.get("assertion_id") or ""))
    response = parse_markdown(review_file, context)
    payload = process_response(context, response, args.agent)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["status"] == "VERIFIED" else 1


def command_show(args: argparse.Namespace) -> int:
    agent_context.authorize_declared(args.agent, "manual_review", "show")
    task_dir = Path(args.task_dir)
    review_result_ref = str(getattr(args, "review_result_ref", "") or "")
    assertion_id = str(getattr(args, "assertion_id", "") or "")
    if bool(review_result_ref) == bool(assertion_id):
        raise ValueError("show requires exactly one of --assertion-id or --review-result-ref")
    if review_result_ref:
        record = review_store.require_record(task_dir, review_result_ref)
        print(json.dumps({"review_result": record}, ensure_ascii=False, indent=2))
        return 0
    context = build_context(task_dir, assertion_id)
    assertion = context.assertion
    summary = latest_review_result(context)
    payload: dict[str, Any] = {
        "assertion_id": assertion.get("assertion_id"),
        "acceptance_id": assertion.get("acceptance_id"),
        "status": assertion.get("status"),
        "reviewer": context.reviewer,
        "review_phase": context.review_phase,
        "artifact_revision": context.artifact_revision,
        "assertion_revision": assertion.get("revision"),
        "evidence_ids": assertion.get("evidence_ids"),
        "reason": assertion.get("reason"),
    }
    if summary is not None:
        payload["review_result"] = summary
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    prepare = sub.add_parser("prepare", help="generate an editable Markdown confirmation form")
    agent_context.add_agent_argument(prepare)
    prepare.add_argument("--task-dir", required=True)
    prepare.add_argument("--assertion-id", required=True)
    prepare.add_argument("--output", default="")
    prepare.set_defaults(func=command_prepare)

    draft = sub.add_parser("draft", help="create an Agent-authored draft for independent Reviewer confirmation")
    agent_context.add_agent_argument(draft)
    draft.add_argument("--task-dir", required=True)
    draft.add_argument("--assertion-id", required=True)
    draft.add_argument("--answer", action="append", default=[])
    draft.add_argument("--detail", action="append", default=[])
    draft.add_argument("--evidence-id", action="append", default=[])
    draft.add_argument("--summary", default="")
    draft.add_argument("--note", action="append", default=[])
    draft.add_argument("--output", default="")
    draft.set_defaults(func=command_draft)

    wizard = sub.add_parser("wizard", help="answer questions interactively and submit immediately")
    agent_context.add_agent_argument(wizard)
    wizard.add_argument("--task-dir", required=True)
    wizard.add_argument("--assertion-id", required=True)
    wizard.set_defaults(func=command_wizard)

    submit = sub.add_parser("submit", help="compile one Markdown form into Review, evidence, and assertion records")
    agent_context.add_agent_argument(submit)
    submit.add_argument("--task-dir", required=True)
    submit.add_argument("--review-file", required=True)
    submit.set_defaults(func=command_submit)

    show = sub.add_parser("show", help="show the current guided manual Review result")
    agent_context.add_agent_argument(show)
    show.add_argument("--task-dir", required=True)
    show.add_argument("--assertion-id", default="")
    show.add_argument("--review-result-ref", default="")
    show.set_defaults(func=command_show)
    return parser


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        return int(args.func(args))
    except (OSError, ValueError, agent_context.AgentAuthorizationError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
