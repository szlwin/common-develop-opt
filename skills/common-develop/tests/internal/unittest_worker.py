#!/usr/bin/env python3
"""Run selected unittest targets and exit without waiting for unrelated helper children."""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[2]
for _path in (ROOT / "tests", ROOT / "scripts"):
    lexical = str(_path)
    if lexical not in sys.path:
        sys.path.insert(0, lexical)


def descendant_pids(root_pid: int) -> set[int]:
    """Return descendants using /proc when available, with a portable ps fallback."""
    parent_map: dict[int, list[int]] = {}
    proc = Path("/proc")
    if proc.is_dir():
        rows = []
        for entry in proc.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                fields = (entry / "stat").read_text(encoding="utf-8").split()
                rows.append((int(fields[0]), int(fields[3])))
            except (OSError, ValueError, IndexError):
                continue
    else:
        try:
            completed = subprocess.run(
                ["ps", "-axo", "pid=,ppid="],
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError:
            return set()
        if completed.returncode != 0:
            return set()
        rows = []
        for line in completed.stdout.splitlines():
            fields = line.split()
            if len(fields) != 2:
                continue
            try:
                rows.append((int(fields[0]), int(fields[1])))
            except ValueError:
                continue
    for pid, parent in rows:
        parent_map.setdefault(parent, []).append(pid)
    result: set[int] = set()
    pending = list(parent_map.get(root_pid, []))
    while pending:
        pid = pending.pop()
        if pid in result:
            continue
        result.add(pid)
        pending.extend(parent_map.get(pid, []))
    return result


def terminate_descendants(root_pid: int | None = None) -> None:
    """Terminate only helper descendants created by this unittest worker."""
    owner = os.getpid() if root_pid is None else root_pid
    descendants = descendant_pids(owner)
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for pid in sorted(descendants, reverse=True):
            try:
                os.kill(pid, sig)
            except (ProcessLookupError, PermissionError):
                pass
        if sig == signal.SIGTERM and descendants:
            time.sleep(0.05)


def run_targets(targets: Iterable[str], *, quiet: bool = False) -> int:
    names = list(targets)
    if not names:
        print("ERROR: at least one unittest target is required", file=sys.stderr)
        return 2
    suite = unittest.defaultTestLoader.loadTestsFromNames(names)
    result = unittest.TextTestRunner(verbosity=1 if quiet else 2).run(suite)
    return 0 if result.wasSuccessful() else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-q", "--quiet", action="store_true")
    parser.add_argument("targets", nargs="+")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_targets(args.targets, quiet=args.quiet)


def cli() -> None:
    code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    # Some test utilities launch environment helper children that can outlive
    # unittest after the result is already final.  Stop only this worker's
    # descendants, then bypass normal interpreter shutdown waits.
    terminate_descendants()
    os._exit(code)


if __name__ == "__main__":
    cli()
