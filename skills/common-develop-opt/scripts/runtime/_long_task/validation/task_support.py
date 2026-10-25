"""Shared imports for task validation domain modules."""
from __future__ import annotations

import re
from runtime import doc_layout
from document_core import evidence
from runtime import work_record
from document_core import acceptance
from runtime import risk_detect
from document_core import review_store
from runtime import request_intake
from document_core import task_plan
from document_core import storage
from runtime._long_task import task_events
from pathlib import Path
from typing import Any, Optional
from runtime._long_task.contracts import *
from runtime._long_task.record_io import *
from runtime._long_task.review_validation import *
from runtime._long_task.architecture_gate import expected_required_reviewers, validate_architecture_review_state
from runtime._long_task.phase_policy import phase_predecessors, phase_skip_allowed, validate_work_mode_state
from runtime._long_task.state_access import (
    load_task_history, runtime_issues, runtime_state, task_state_path,
)
from runtime._long_task.validation.acceptance import validate_acceptance_contract
from runtime._long_task.validation.attempt import validate_attempt_history
from runtime._long_task.validation.risk import is_current_risk_reviewer_registration, validate_risk_gate
from runtime._long_task.validation.task_plan import validate_task_plan
