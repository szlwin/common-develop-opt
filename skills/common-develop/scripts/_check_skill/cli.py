"""Cli for the internal runtime."""
from __future__ import annotations
import argparse
import ast
import hashlib
import importlib.util
import json
import py_compile
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

import simple_yaml
import agent_context
import semantic_audit
import reference_reader
import context_resolver
import contract_normalization
import design_doc
import case_doc
import cli_help
import merge_docs
import long_task as long_task_runtime
from _core import capability_packs, implementation_strategy
from _check_skill.context import *
from _check_skill.model import CheckResult
from _check_skill.io import load_json, load_yaml
from _check_skill.module_loader import *
from _check_skill.routing import *
from _check_skill.manifest import *

try:
    import jsonschema
except ImportError:  # pragma: no cover
    jsonschema = None
def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description=__doc__)
    agent_context.add_agent_argument(command)
    command.add_argument("--json", action="store_true", help="emit machine-readable result")
    command.add_argument("--write-manifest", action="store_true", help="regenerate FILE_MANIFEST.sha256 before checks")
    return command
