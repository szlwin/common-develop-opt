"""Public facade for the complete package consistency checks."""
from checks._check_skill.context import *
from checks._check_skill.io import *
from checks._check_skill.module_loader import *
from checks._check_skill.routing import *
from checks._check_skill.manifest import *
from checks._check_skill.model import *
from checks._check_skill.python_checks import *
from checks._check_skill.runtime_checks import *
from checks._check_skill.asset_checks import *
from checks._check_skill.document_checks import *
from checks._check_skill.review_checks import *
from checks._check_skill.reference_checks import *
from checks._check_skill.release_checks import *
from checks._check_skill.convergence_checks import *
from checks._check_skill.orchestrator import main, main_for_args, run_checks
from checks._check_skill.manifest import write_manifest
from checks._check_skill.cli import parser

build_parser = parser

if __name__ == '__main__':
    raise SystemExit(main())
