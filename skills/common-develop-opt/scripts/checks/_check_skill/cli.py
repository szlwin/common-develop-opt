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

from document_core import simple_yaml
from document_core import agent_context
from checks import semantic_audit
import reference_reader
from runtime import context_resolver
from runtime import contract_normalization
from runtime import design_doc
from runtime import case_doc
from checks import cli_help
import merge_docs
from runtime import long_task as long_task_runtime
from runtime._core import capability_packs, implementation_strategy
from checks._check_skill.context import *
from checks._check_skill.model import CheckResult
from checks._check_skill.io import load_json, load_yaml
from checks._check_skill.module_loader import *
from checks._check_skill.routing import *
from checks._check_skill.manifest import *

try:
    import jsonschema 
except ImportError:  # pragma: no cover
    jsonschema = None
def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description=__doc__)
    agent_context.add_agent_argument(command)
    command.add_argument("--json", action="store_true", help="emit machine-readable result")
    command.add_argument("--write-manifest", action="store_true", help="regenerate FILE_MANIFEST.sha256 before checks")
    command.add_argument("--command-surface-inventory", type=Path,
                         help="also validate an explicitly supplied repository v1 inventory")
    command.add_argument('--profile', choices=('development', 'release'), default='development', help='release additionally requires a prepared and clean release baseline')
    return command
