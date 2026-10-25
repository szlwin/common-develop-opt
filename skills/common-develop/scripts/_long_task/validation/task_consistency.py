"""Compatibility facade for task consistency validation."""
from __future__ import annotations

from _long_task.validation.task_traceability import _validate_task_section_2
from _long_task.validation.task_reviews import _validate_task_section_3

__all__ = ["_validate_task_section_2", "_validate_task_section_3"]
