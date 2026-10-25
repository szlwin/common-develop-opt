"""Stable sections shared by the AI context resolver."""
from __future__ import annotations

EXECUTION_ORDER = [
    "read_project_facts",
    "freeze_input_revisions",
    "produce_or_review_current_revision",
    "register_evidence",
    "run_machine_gates",
    "advance_or_reopen_via_script",
    "report_done_partial_or_blocked",
]

FORBIDDEN_SHORTCUTS = [
    "load_all_references",
    "edit_current_phase_directly",
    "reuse_stale_revision_evidence",
    "self_approve_blocking_review",
    "infer_work_mode_from_conversation",
    "load_entire_git_checkpoint_history_by_default",
    "load_entire_evidence_registry_by_default",
    "load_all_review_results_by_default",
    "run_multiple_targets_or_tasks_concurrently",
    "treat_html_as_fact_source",
]
