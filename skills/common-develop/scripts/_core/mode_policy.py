"""Shared incremental work-mode patch parsing and resolution."""
from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from typing import Any


MODE_ATTRIBUTE_BY_NAME = {
    "modelCode": "model_code",
    "model_code": "model_code",
    "pageDesign": "page_design",
    "page_design": "page_design",
    "minimal": "minimal",
    "auto": "auto",
    "architectureReview": "architecture_review",
    "architecture_review": "architecture_review",
    "gitCheckpoint": "git_checkpoint",
    "git_checkpoint": "git_checkpoint",
    "gitPush": "git_push",
    "git_push": "git_push",
    "reviewOnly": "review_only",
    "review_only": "review_only",
    "lightweight": "lightweight",
}


def mode_enabled(mode: Any, mode_name: str) -> bool:
    """Read a contract mode name from a WorkMode-like object."""
    attribute = MODE_ATTRIBUTE_BY_NAME.get(mode_name)
    if attribute is None:
        raise KeyError(mode_name)
    return bool(getattr(mode, attribute, False))


@dataclass(frozen=True)
class ModePatch:
    """A partial work-mode update; ``None`` means preserve the current value."""

    model_code: bool | None = None
    page_design: bool | None = None
    minimal: bool | None = None
    auto: bool | None = None
    architecture_review: bool | None = None
    git_checkpoint: bool | None = None
    git_push: bool | None = None
    review_only: bool | None = None
    lightweight: bool | None = None

    def is_empty(self) -> bool:
        return all(value is None for value in self.__dict__.values())


def _value(args: argparse.Namespace, name: str) -> bool | None:
    value = getattr(args, name, None)
    if value is not None:
        return bool(value)
    return None


def patch_from_args(args: argparse.Namespace) -> ModePatch:
    """Build a patch from the parser's tri-state mode arguments."""
    lightweight = _value(args, "lightweight")
    return ModePatch(
        model_code=_value(args, "model_code"),
        page_design=_value(args, "page_design"),
        minimal=_value(args, "minimal"),
        auto=_value(args, "auto"),
        architecture_review=_value(args, "architecture_review"),
        git_checkpoint=_value(args, "git_checkpoint"),
        git_push=_value(args, "git_push"),
        review_only=_value(args, "review_only"),
        lightweight=lightweight,
    )


def resolve_mode(current: Any, patch: ModePatch) -> Any:
    """Apply a partial update to a WorkMode-like dataclass."""
    values = {
        name: value
        for name, value in patch.__dict__.items()
        if value is not None
    }
    return replace(current, **values) if values else current
