#!/usr/bin/env python3
"""Public CLI facade for common-develop static consistency checks."""
from __future__ import annotations

from _check_skill.context import *
from _check_skill.io import *
from _check_skill.module_loader import *
from _check_skill.routing import *
from _check_skill.manifest import *
from _check_skill.model import *
from _check_skill.python_checks import *
from _check_skill.runtime_checks import *
from _check_skill.asset_checks import *
from _check_skill.document_checks import *
from _check_skill.review_checks import *
from _check_skill.reference_checks import *
from _check_skill.release_checks import *
from _check_skill.convergence_checks import *
from _check_skill.cli import *
from _check_skill.orchestrator import *


if __name__ == "__main__":
    raise SystemExit(main())
