from __future__ import annotations
import hashlib, json, subprocess, time
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from diagnostic_io import TOOL_VERSION, result, write_json



def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def files(root: Path):
    return sorted(p for p in root.rglob('*') if p.is_file() and '.git' not in p.parts and '__pycache__' not in p.parts)

def rel(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())

def base(root: Path, status: str, **extra):
    return {"schemaVersion": 1, "status": status, "generatedAt": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), "toolVersion": TOOL_VERSION, "root": str(root.resolve()), **extra}

def run(cmd, cwd=None, env=None, timeout=30):
    started = time.perf_counter()
    try:
        p = subprocess.run(cmd, cwd=cwd, env=env, timeout=timeout, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except subprocess.TimeoutExpired as exc:
        return {"command": cmd, "returnCode": None, "durationMs": round((time.perf_counter()-started)*1000, 3), "stdout": str(exc.stdout or '')[:20000], "stderr": "timeout"}
    return {"command": cmd, "returnCode": p.returncode, "durationMs": round((time.perf_counter()-started)*1000, 3), "stdout": p.stdout[:20000], "stderr": p.stderr[:20000]}

def dump(value, output, protected=()):
    write_json(value, output, protected)
