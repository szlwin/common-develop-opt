"""Canonical task runtime: lifecycle state machine, command handlers and gates.

This package is the renamed successor of the compat runtime; it owns task
state, evidence registration and review progression. Deterministic parsers
and document projections live in ``document_core``, package checks in
``checks``.
"""
from __future__ import annotations

# Package import must be side-effect free.  CLI/bootstrap boundaries prepare
# the scripts path when loading this installed package.
