#!/usr/bin/env python3
"""Public CLI facade for common-develop long-task orchestration."""
from __future__ import annotations

import sys as _sys
from _long_task import api as _api
from _long_task.api import *

_sys.modules[__name__] = _api

if __name__ == "__main__":
    raise SystemExit(main())
