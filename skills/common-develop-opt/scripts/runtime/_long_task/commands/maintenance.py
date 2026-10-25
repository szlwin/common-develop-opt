"""Explicit maintenance commands for the canonical task event ledger."""
from __future__ import annotations

import json
from pathlib import Path

from document_core import agent_context
from document_core import storage
from document_core.common_utils import atomic_write_bytes, now
from runtime._long_task import task_events
from runtime._long_task.record_io import transactional_text_write


def _public_report(plan: dict) -> dict:
    return {key: value for key, value in plan.items() if not key.startswith("_")}


def command_compact_events(args) -> int:
    """Preview or apply a hash-linked rollover of an oversized active ledger."""
    agent_context.authorize_command(args, "long_task", "compact-events")
    task_dir = Path(args.task_dir).resolve()
    storage.assert_new_project(task_dir)
    plan = task_events.prepare_compaction(
        task_dir,
        threshold_bytes=int(args.threshold_bytes),
        force=bool(args.force),
    )
    public = _public_report(plan)
    if not args.apply:
        public["applied"] = False
        public["nextCommand"] = (
            "python3 scripts/long_task.py compact-events -g ProjectManagerAgent "
            f"--task-dir {task_dir} --threshold-bytes {int(args.threshold_bytes)} "
            f"--plan-digest {plan['planDigest']} --apply --json"
        ) if plan["needed"] else ""
        print(json.dumps(public, ensure_ascii=False, indent=2))
        return 0
    if not plan["needed"]:
        public["applied"] = False
        print(json.dumps(public, ensure_ascii=False, indent=2))
        return 0
    expected = str(args.plan_digest or "").strip()
    if not expected or expected != str(plan["planDigest"]):
        raise ValueError("compact-events --apply requires the current preview --plan-digest")

    archive_path = task_dir / str(plan["archiveRef"])
    archive_bytes = plan["_archiveBytes"]
    created_archive = False
    if archive_path.exists():
        if archive_path.read_bytes() != archive_bytes:
            raise ValueError(f"event archive path collision: {archive_path}")
    else:
        atomic_write_bytes(archive_path, archive_bytes)
        created_archive = True

    snapshot = plan["_snapshot"]
    reduced = task_events.reduce_events([snapshot])
    projected = task_events.project_state({}, reduced, synced_at=now())
    from runtime import current_manifest
    manifest = current_manifest.build_manifest(
        task_dir,
        state=projected,
        issues=[item for item in reduced.get("issues", []) if isinstance(item, dict)],
    )
    try:
        transactional_text_write({
            task_events.event_path(task_dir): str(plan["_ledgerText"]),
            current_manifest.manifest_path(task_dir): json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        })
    except OSError:
        if created_archive:
            archive_path.unlink(missing_ok=True)
        raise

    verified = task_events.reduce_events(task_events.read_events(task_dir))
    before = plan["_reducedBefore"]
    state_keys = {
        "state_authority", "attempts", "outcomes", "stale_events",
        "auto_remediation_events", "authorization_events", "authorization_consumptions",
        "amendment_events", "traceability_revalidations", "issues", "task_runtime", "task_policies",
    }
    if any(verified.get(key) != before.get(key) for key in state_keys):
        raise ValueError("event compaction reducer verification failed")
    public.update({
        "status": "COMPACTED",
        "applied": True,
        "storageMode": str(verified.get("storage_mode") or ""),
        "sourceEventSeq": int(verified.get("last_seq") or 0),
        "sourceDigest": str(verified.get("last_digest") or ""),
    })
    print(json.dumps(public, ensure_ascii=False, indent=2))
    return 0


__all__ = ["command_compact_events"]
