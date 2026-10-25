"""Read-only consistency checks for human summaries versus reducer authority."""
from __future__ import annotations
import re
from pathlib import Path
from typing import Any

import long_task


def check(task_dir: Path) -> dict[str, Any]:
    task_dir = Path(task_dir).resolve()
    state = long_task.runtime_state(task_dir)
    phase = str(state.get('current_phase') or '')
    status = str(state.get('task_status') or '')
    # /project_doc/version/V/task/T -> project_doc is parents[3].
    root = task_dir.parents[3]
    candidates = [root / 'README.md', root / 'docs' / 'README.md', root / 'version' / task_dir.parents[1].name / 'version_summary.md']
    claims = []
    for path in candidates:
        if not path.is_file():
            continue
        text = path.read_text(encoding='utf-8', errors='replace')
        if re.search(r'P[0-9].*(?:已完成|PASSED|complete)', text, re.I):
            if status not in {'PASSED', 'COMPLETED', 'DONE'}:
                claims.append({'source': str(path), 'claim': 'completion', 'machineStatus': status, 'machinePhase': phase, 'classification': 'SUMMARY_DRIFT'})
    return {'status': 'WARNING' if claims else 'PASSED', 'authority': 'task_events.jsonl reducer', 'claims': claims}
