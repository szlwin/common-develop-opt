"""Text-block, template, and work-record helpers for long tasks."""
from __future__ import annotations

import json
import os
import tempfile
import re
from pathlib import Path
from typing import Any

from common_utils import atomic_write_text

import work_record
from _long_task.contracts import (
    COLLABORATION_REVIEWERS,
    CONDITIONAL_REVIEWERS,
    LONG_TASK_SCHEMA_VERSION,
    PHASES,
)

def block_pattern(name: str) -> re.Pattern[str]:
    return re.compile(f'```json\\s+{re.escape(name)}\\s*\\n(.*?)\\n```', re.DOTALL)

def read_block(path: Path, name: str) -> tuple[str, Any]:
    text = path.read_text(encoding='utf-8')
    match = block_pattern(name).search(text)
    if not match:
        raise ValueError(f"{path}: missing fenced JSON block '{name}'")
    try:
        return (text, json.loads(match.group(1)))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: invalid JSON in '{name}': {exc}") from exc

def render_block(path: Path, name: str, document: str, data: Any) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
    replacement = f'```json {name}\n{payload}\n```'
    # Use a callable replacement so JSON escape sequences such as ``\\n``
    # remain literal characters instead of being interpreted by re.sub.
    updated, count = block_pattern(name).subn(lambda _match: replacement, document, count=1)
    if count != 1:
        raise ValueError(f"{path}: unable to replace JSON block '{name}'")
    return updated

def write_block(path: Path, name: str, document: str, data: Any) -> None:
    atomic_write_text(path, render_block(path, name, document, data))

def transactional_text_write(payloads: dict[Path, str]) -> None:
    """Write canonical records atomically and preserve the original failure.

    Every target is staged in a unique same-directory file and fsynced before
    replacement. If a later replacement fails, already replaced files are
    restored; rollback failures are attached to the original OSError rather
    than masking it.
    """
    backups: dict[Path, bytes | None] = {}
    temporary: dict[Path, Path] = {}
    replaced: list[Path] = []
    try:
        for path, text in payloads.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            backups[path] = path.read_bytes() if path.exists() else None
            fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
            temp = Path(name)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            temporary[path] = temp
        for path, temp in temporary.items():
            os.replace(temp, path)
            replaced.append(path)
    except OSError as original_error:
        rollback_errors: list[str] = []
        recovered_paths: list[str] = []
        for temp in temporary.values():
            try:
                temp.unlink(missing_ok=True)
            except OSError as exc:
                rollback_errors.append(f"cleanup {temp}: {exc}")
        for path in reversed(replaced):
            previous = backups[path]
            try:
                if previous is None:
                    path.unlink(missing_ok=True)
                else:
                    fd, name = tempfile.mkstemp(prefix=f".{path.name}.rollback.", suffix=".tmp", dir=str(path.parent))
                    rollback = Path(name)
                    try:
                        with os.fdopen(fd, "wb") as stream:
                            stream.write(previous)
                            stream.flush()
                            os.fsync(stream.fileno())
                        os.replace(rollback, path)
                    finally:
                        rollback.unlink(missing_ok=True)
                recovered_paths.append(str(path))
            except OSError as exc:
                rollback_errors.append(f"restore {path}: {exc}")
        details = {
            "rollbackStatus": "COMPLETE" if not rollback_errors else ("PARTIAL" if recovered_paths else "FAILED"),
            "recoveredPaths": recovered_paths,
            "rollbackErrors": rollback_errors,
        }
        setattr(original_error, "rollback_details", details)
        if rollback_errors:
            setattr(original_error, "rollback_errors", rollback_errors)
        raise


def render_template(source: Path, target: Path, replacements: dict[str, str]) -> None:
    text = source.read_text(encoding='utf-8')
    for key, value in replacements.items():
        text = text.replace('{{' + key + '}}', value)
    target.write_text(text, encoding='utf-8')

def templates_root() -> Path:
    return Path(__file__).resolve().parents[2] / 'assets' / 'long-task'

def work_record_template() -> Path:
    return Path(__file__).resolve().parents[2] / 'assets' / 'work_record.template.md'

def version_root_from_task_dir(task_dir: Path) -> Path:
    if task_dir.parent.name != 'task':
        raise ValueError(f'task directory must be under version/{{VERSION}}/task: {task_dir}')
    return task_dir.parent.parent

def work_record_path_for_task(task_dir: Path) -> Path:
    return version_root_from_task_dir(task_dir) / 'work_record.md'

def ensure_work_record(version_root: Path, version: str) -> Path:
    path = version_root / 'work_record.md'
    if not path.exists():
        text = work_record_template().read_text(encoding='utf-8').replace('{{VERSION}}', version)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, text)
    return path

def read_work_events(path: Path) -> tuple[str, list[dict[str, Any]]]:
    document, events, errors = work_record.read_events(path)
    if errors:
        raise ValueError('; '.join(errors))
    return document, events

def append_work_event(path: Path, event: dict[str, Any], *, version: str = '') -> tuple[str, list[dict[str, Any]]]:
    resolved_version = version or work_record.document_version(path.read_text(encoding='utf-8'), path.parent.name)
    return work_record.append_event(path, event, version=resolved_version)

def phases_for(schema_version: int) -> list[str]:
    if schema_version != LONG_TASK_SCHEMA_VERSION:
        raise ValueError(f'unsupported schema_version: {schema_version}; expected {LONG_TASK_SCHEMA_VERSION}')
    return PHASES

def reviewers_for(schema_version: int) -> dict[str, set[str]]:
    phases_for(schema_version)
    return COLLABORATION_REVIEWERS

def conditional_reviewers_for(schema_version: int) -> dict[str, dict[str, str]]:
    phases_for(schema_version)
    return CONDITIONAL_REVIEWERS

def is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())

def validate_string_list(value: Any) -> bool:
    return isinstance(value, list) and all((is_nonempty_string(item) for item in value))

def validate_nonempty_string_list(value: Any) -> bool:
    return validate_string_list(value) and bool(value)

__all__ = [
    "block_pattern", "read_block", "render_block", "write_block",
    "transactional_text_write", "render_template", "templates_root",
    "work_record_template", "version_root_from_task_dir",
    "work_record_path_for_task", "ensure_work_record", "read_work_events",
    "append_work_event", "phases_for", "reviewers_for",
    "conditional_reviewers_for", "is_nonempty_string",
    "validate_string_list", "validate_nonempty_string_list",
]
