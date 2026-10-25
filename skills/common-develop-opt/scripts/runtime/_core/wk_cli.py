"""Bind historical runtime callbacks to the canonical ``wk`` parser."""
from __future__ import annotations

import argparse
from types import ModuleType

from document_core import wk_dispatcher


def build_parser(wk_module: ModuleType) -> argparse.ArgumentParser:
    handlers = {
        command: getattr(wk_module, "command_" + command.replace("-", "_"))
        for command in wk_dispatcher.COMMANDS
    }
    return wk_dispatcher.build_parser(
        handlers, handler_attribute="func", description=wk_module.__doc__,
    )
