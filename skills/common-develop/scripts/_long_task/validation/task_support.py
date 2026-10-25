"""Shared imports for task validation domain modules."""
from __future__ import annotations

import re
import doc_layout
import evidence
import work_record
import acceptance
import risk_detect
import review_store
import request_intake
import task_plan
import storage
from _long_task import task_events
from pathlib import Path
from typing import Any, Optional
from _long_task.contracts import *
from _long_task.record_io import *
from _long_task.review_validation import *
from _long_task.architecture_gate import expected_required_reviewers, validate_architecture_review_state
from _long_task.phase_policy import phase_predecessors, phase_skip_allowed, validate_work_mode_state
from _long_task.state_access import (
    load_task_history, runtime_issues, runtime_state, task_state_path,
)
from _long_task.validation.acceptance import validate_acceptance_contract
from _long_task.validation.attempt import validate_attempt_history
from _long_task.validation.risk import is_current_risk_reviewer_registration, validate_risk_gate
from _long_task.validation.task_plan import validate_task_plan
