"""Isolated script-module loading used by static package checks."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def _import_script(result: Any, filename: str, module_name: str, *, register: bool = False):
    path = ROOT / "scripts" / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        result.errors.append(f"cannot import scripts/{filename}")
        return None
    module = importlib.util.module_from_spec(spec)
    if register:
        sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def import_long_task(result):
    return _import_script(result, "long_task.py", "common_develop_long_task_check", register=True)

def import_relationship_renderer(result):
    return _import_script(result, "render_relationships.py", "common_develop_relationship_check")

def import_review_contract(result):
    return _import_script(result, "review_contract.py", "common_develop_review_contract_check")

def import_review_session(result):
    return _import_script(result, "review_session.py", "common_develop_review_session_check")

def import_manual_review(result):
    return _import_script(result, "manual_review.py", "common_develop_manual_review_check", register=True)

def import_context_resolver(result):
    return _import_script(result, "context_resolver.py", "common_develop_context_resolver_check", register=True)

def import_ai_context(result):
    return _import_script(result, "ai_context.py", "common_develop_ai_context_check", register=True)

def import_requirement_doc(result):
    return _import_script(result, "requirement_doc.py", "common_develop_requirement_doc_check", register=True)

def import_business_flow(result):
    return _import_script(result, "business_flow.py", "common_develop_business_flow_check", register=True)

def import_doc_layout(result):
    return _import_script(result, "doc_layout.py", "common_develop_doc_layout_check", register=True)

def import_git_checkpoint(result):
    return _import_script(result, "git_checkpoint.py", "common_develop_git_checkpoint_check", register=True)

def import_merge_docs(result):
    return _import_script(result, "merge_docs.py", "common_develop_merge_docs_check", register=True)

def import_baseline_guard(result):
    return _import_script(result, "baseline_guard.py", "common_develop_baseline_guard_check", register=True)

__all__ = [name for name in globals() if name.startswith("import_")]
