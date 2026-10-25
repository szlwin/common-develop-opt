"""Knowledge navigation and revision-bound audit references for validated archives."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

from document_core import document_portal
from document_core import reference_resolution
from document_core.document_portal import _link_target
from document_core.common_utils import now
from document_core._merge_docs.model import MergeError, validate_json_schema


REGISTRIES = {
    "traceability": "traceability.json",
    "review": "evidence/reviews.jsonl",
    "acceptance": "acceptance.json",
    "evidence": "evidence/registry.jsonl",
}


def encode(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _reference(root: Path, path: Path, content: bytes) -> dict[str, str]:
    try:
        ref = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise MergeError(f"archive reference escapes document root: {path}") from exc
    return {"path": ref, "digest": digest(content)}


def _knowledge_sources(root: Path, plan: dict, args: Any, pending: Mapping[Path, bytes]) -> dict[str, str]:
    """Follow canonical knowledge links; never traverse audit/generated documents."""
    resolved = {p.resolve(): data for p, data in pending.items()}
    queue = [root / item["targetRef"] for item in plan.get("artifactMerges", [])
             if item.get("artifactType") in {"requirement_list", "module_desc", "union_language", "design_topic", "page_behavior"}]
    # Requirement/design are canonical knowledge roots even when the change
    # only touches a structured business-model/API/DB fact and has no public
    # artifact merge entry.
    version_requirement = root / "version" / str(args.version) / "requirement"
    queue.extend(version_requirement.glob("*/requirement.md"))
    module_dir = root / "docs" / "module" / str(args.module)
    queue.extend([module_dir / f"{args.module}_desc.md", module_dir / f"{args.module}_design.md", root / "docs" / "union" / "union_language.md"])
    selected: dict[str, str] = {}
    while queue:
        path = queue.pop().resolve()
        try:
            relative = path.relative_to(root.resolve())
        except ValueError as exc:
            raise MergeError(f"knowledge link escapes document root: {path}") from exc
        ref = relative.as_posix()
        if ref in selected or path.suffix != ".md":
            continue
        if any(part in {"generated", "changes", "task", "_relations", "archives"} for part in relative.parts):
            continue
        name = path.name
        if not (name in {"requirement.md", "requirement_list.md", "union_language.md"}
                or name.endswith(("_desc.md", "_design.md", "_business_model.md", "_flow.md"))
                or "design" in relative.parts):
            continue
        content = resolved.get(path)
        if content is None:
            if not path.is_file():
                raise MergeError(f"knowledge document is missing: {ref}")
            content = path.read_bytes()
        selected[ref] = digest(content)
        for raw in reference_resolution.markdown_link_destinations(content.decode("utf-8")):
            target = _link_target(raw)
            if target and not target.startswith(("http://", "https://", "mailto:")):
                queue.append(path.parent / target)
    return dict(sorted(selected.items()))


def build_archive_output(root: Path, args: Any, plan: dict, records: list[dict],
                         pending: Mapping[Path, bytes]) -> tuple[dict, dict[Path, bytes]]:
    """Called only after the unchanged archive gate has accepted this plan."""
    refs: dict[str, list[dict]] = {key: [] for key in ("changeset", *REGISTRIES)}
    snapshots: dict[str, bytes] = {}
    for record in records:
        source = Path(record.get("sourceRef") or record.get("source") or "")
        if not source.is_absolute():
            source = root / source
        data = source.read_bytes()
        ref = _reference(root, source, data)
        ref["id"] = record["id"]
        refs["changeset"].append(ref)
        snapshots[ref["path"]] = data
    # Gate validation already covers all version tasks. Reference the same
    # version scope explicitly, including shared and cross-module reviewers.
    task_root = root / "version" / args.version / "task"
    for task in sorted(task_root.iterdir()) if task_root.is_dir() else []:
        if not task.is_dir():
            continue
        for category, filename in REGISTRIES.items():
            source = task / filename
            if not source.is_file():
                continue  # e.g. no Review registry under a valid waiver
            data = source.read_bytes()
            ref = _reference(root, source, data)
            refs[category].append(ref)
            snapshots[ref["path"]] = data
    documents = _knowledge_sources(root, plan, args, pending)
    source_revisions = {
        "base": plan.get("baseRevisions", {}), "result": plan.get("resultRevisions", {}),
        "artifacts": plan.get("artifactMerges", []),
        "businessFlows": plan.get("businessFlowMerges", []),
    }
    identity = {"resultDigest": plan["resultDigest"], "archiveGate": plan["archiveGate"],
                "auditRefs": refs, "documents": documents, "sourceRevisions": source_revisions}
    archive_id = digest(encode(identity))
    directory = root / "archives" / args.version / args.module / archive_id
    verified_at = now()
    gate = {"schemaVersion": 1, "archiveId": archive_id, "status": "PASSED",
            "verifiedAt": verified_at, "resultDigest": plan["resultDigest"],
            "archiveGate": plan["archiveGate"]}
    gate_path = directory / "archive_gate_result.json"
    waived = {key: value.get("reason", "") for key, value in plan["archiveGate"]["gates"].items()
              if value.get("status") == "NOT_APPLICABLE"}
    manifest = {"schemaVersion": 1, "archiveId": archive_id, "archiveType": "KNOWLEDGE",
                "version": args.version, "module": args.module,
                "documents": list(documents), "documentDigests": documents,
                "sourceRevisions": source_revisions, "resultDigest": plan["resultDigest"],
                "auditScope": "VERSION", "auditRefs": refs,
                "gate": {"status": "PASSED", "verifiedAt": verified_at,
                         "ref": gate_path.relative_to(root).as_posix(), "waivers": waived}}
    validate_json_schema(manifest, "knowledge-archive.schema.json", "knowledge archive")
    validate_json_schema(gate, "archive-gate-result.schema.json", "archive gate result")
    payloads = {gate_path: encode(gate), directory / "archive_manifest.json": encode(manifest)}
    if getattr(args, "audit_bundle", False):
        payloads[directory / "audit_bundle.json"] = encode({
            "schemaVersion": 1, "archiveType": "AUDIT", "archiveId": archive_id,
            "manifest": manifest, "gate": gate,
            "indexes": {ref: data.decode("utf-8") for ref, data in snapshots.items()},
        })
    page = directory / "README.md"
    lines = [document_portal.GENERATED_MARKER, f"# {args.module} 知识归档", "", f"版本：{args.version}", "",
             f"Revision / 结果摘要：`{plan['resultDigest']}`", "", "归档资格校验已完成。", ""]
    if waived:
        lines += ["以下门禁不适用或经授权跳过，不代表验证通过：", ""]
        lines += [f"- {key}：{value}" for key, value in waived.items()]
        lines.append("")
    lines += [f"- [{Path(ref).name}]({os.path.relpath(root / ref, page.parent)})" for ref in documents]
    lines += ["", "[审计记录引用](archive_manifest.json) · [机器校验结果](archive_gate_result.json)", ""]
    payloads[page] = "\n".join(lines).encode("utf-8")
    document_portal.validate_payloads(
        root,
        {page: payloads[page]},
        pending={**pending, **payloads},
        versions=[str(args.version)],
    )
    return {"id": archive_id, "version": args.version, "module": args.module,
            "manifestRef": (directory / "archive_manifest.json").relative_to(root).as_posix(),
            "pageRef": page.relative_to(root).as_posix(), "resultDigest": plan["resultDigest"]}, payloads


def export_existing_audit(root: Path, args: Any, manifest: dict) -> dict[Path, bytes]:
    """Export the latest archived indexes only when their recorded digests still match."""
    matches = [item for item in manifest.get("archives", [])
               if item["version"] == args.version and item["module"] == args.module]
    if not matches:
        raise MergeError("no revision-bound archive exists for --audit-bundle; create a validated archive first")
    ref = root / matches[-1]["manifestRef"]
    archive = json.loads(ref.read_bytes())
    target = ref.parent / "audit_bundle.json"
    if target.is_file():
        return {}
    indexes = {}
    for group in archive["auditRefs"].values():
        for item in group:
            source = (root / item["path"]).resolve()
            data = source.read_bytes()
            if _reference(root, source, data)["digest"] != item["digest"]:
                raise MergeError(f"audit source changed since archive: {item['path']}; restore the archived revision before exporting")
            indexes[item["path"]] = data.decode("utf-8")
    gate = json.loads((ref.parent / "archive_gate_result.json").read_bytes())
    return {target: encode({"schemaVersion": 1, "archiveType": "AUDIT",
                           "archiveId": archive["archiveId"], "manifest": archive,
                           "gate": gate, "indexes": indexes})}
