"""Shared structured-file readers for static package checks."""
from __future__ import annotations

import json 
from pathlib import Path
from typing import Any

from document_core import simple_yaml
from resource_paths import formalize

ROOT = Path(__file__).resolve().parents[3]


def load_json(path: Path, result: Any) -> Any:
    try:
        return json.loads(formalize(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        result.errors.append(f"{path.relative_to(ROOT)}: invalid JSON: {exc}")
        return None


def load_yaml(path: Path, result: Any) -> Any:
    try:
        return simple_yaml.load(formalize(path))
    except (OSError, simple_yaml.YamlError) as exc:
        result.errors.append(f"{path.relative_to(ROOT)}: invalid supported YAML: {exc}")
        return None


__all__ = ["load_json", "load_yaml"]
