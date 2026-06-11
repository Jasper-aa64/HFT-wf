#!/usr/bin/env python3
"""Patch queue helpers for the headless auto-loop.

This module owns the filesystem surface for candidate patches. It does two
things:

1. Stores patch bodies as files under ``<run_dir>/patches/<candidate_id>.patch``
   so they can be replayed or audited after a run.
2. Maintains ``<run_dir>/patches/patch_manifest.json`` with per-candidate
   metadata: ``candidate_id``, ``path``, ``base_commit``, ``applied_at``,
   ``status`` (``pending|applied|reverted|failed``), ``touched_files``,
   materialization audit fields, and ``revert_method`` text (how to undo if a
   remote job needs to).

The helpers here do not apply patches on disk. Remote apply/revert is done by
``headless_remote.sh``. The auto-loop uses this module to record intent and
status so the run root is the single machine-readable surface.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


MANIFEST_FILE = "patch_manifest.json"
VALID_STATUS = {"pending", "applied", "reverted", "failed", "skipped"}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def patches_dir(run_dir: Path) -> Path:
    path = Path(run_dir) / "patches"
    path.mkdir(parents=True, exist_ok=True)
    return path


def manifest_path(run_dir: Path) -> Path:
    return patches_dir(run_dir) / MANIFEST_FILE


def load_manifest(run_dir: Path) -> dict[str, Any]:
    path = manifest_path(run_dir)
    if not path.exists():
        return {"schema": "patch_manifest_v1", "entries": []}
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if "entries" not in data:
        data["entries"] = []
    return data


def save_manifest(run_dir: Path, manifest: dict[str, Any]) -> Path:
    path = manifest_path(run_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def _find_entry(manifest: dict[str, Any], candidate_id: str) -> dict[str, Any] | None:
    for entry in manifest.get("entries", []):
        if entry.get("candidate_id") == candidate_id:
            return entry
    return None


def _normalize_touched(files: Iterable[str] | str | None) -> list[str]:
    if not files:
        return []
    if isinstance(files, str):
        parts = [part.strip() for part in files.split("|") if part.strip()]
        return parts
    return [str(item).strip() for item in files if str(item).strip()]


def register_candidate(
    run_dir: Path,
    *,
    candidate_id: str,
    lane: str,
    hypothesis: str = "",
    target: str = "",
    touched_files: Iterable[str] | str | None = None,
    semantic_risk: str = "",
    stack_members: Iterable[str] | str | None = None,
    base_commit: str = "",
    revert_method: str = "git -C <repo> checkout -- <touched_files>",
    patch_body: str | bytes | None = None,
    status: str = "pending",
    candidate_workspace: str = "",
    patch_command: str = "",
    materialization_status: str = "",
    materialization_reason: str = "",
    patch_command_rc: int | None = None,
    patch_source: str = "",
) -> dict[str, Any]:
    """Register a candidate patch. Writes the patch file and updates the manifest.

    The patch body can be empty for planned candidates; in that case a stub file
    is written that records the candidate metadata for later replay.
    """

    if status not in VALID_STATUS:
        raise ValueError(f"invalid patch status: {status}")

    run_dir = Path(run_dir)
    patch_dir = patches_dir(run_dir)
    patch_path = patch_dir / f"{candidate_id}.patch"

    patch_bytes: bytes
    if patch_body is None:
        patch_body_text = (
            f"# candidate_id={candidate_id}\n"
            f"# lane={lane}\n"
            f"# target={target}\n"
            f"# touched_files={'|'.join(_normalize_touched(touched_files))}\n"
            f"# status={status}\n"
            "# no patch body prepared yet; replay requires a worktree diff snapshot\n"
        )
        patch_bytes = patch_body_text.encode("utf-8")
        patch_path.write_bytes(patch_bytes)
    elif isinstance(patch_body, bytes):
        patch_bytes = patch_body
        patch_path.write_bytes(patch_bytes)
    else:
        patch_bytes = patch_body.encode("utf-8")
        patch_path.write_bytes(patch_bytes)

    manifest = load_manifest(run_dir)
    entry = _find_entry(manifest, candidate_id)
    new_entry = {
        "candidate_id": candidate_id,
        "lane": lane,
        "target": target,
        "hypothesis": hypothesis,
        "path": str(patch_path.relative_to(run_dir)).replace("\\", "/"),
        "absolute_path": str(patch_path),
        "base_commit": base_commit,
        "touched_files": _normalize_touched(touched_files),
        "stack_members": _normalize_touched(stack_members),
        "semantic_risk": semantic_risk,
        "status": status,
        "applied_at": "" if status != "applied" else utc_now(),
        "revert_method": revert_method,
        "candidate_workspace": candidate_workspace,
        "patch_command": patch_command,
        "patch_command_rc": patch_command_rc,
        "patch_source": patch_source,
        "materialization_status": materialization_status,
        "materialization_reason": materialization_reason,
        "patch_sha256": hashlib.sha256(patch_bytes).hexdigest(),
        "patch_bytes": len(patch_bytes),
        "recorded_at": utc_now(),
        "updated_at": utc_now(),
    }
    if entry is None:
        manifest.setdefault("entries", []).append(new_entry)
    else:
        # preserve applied_at if already applied
        if entry.get("status") == "applied" and status != "applied":
            new_entry["applied_at"] = entry.get("applied_at", "")
        entry.update(new_entry)
        new_entry = entry
    save_manifest(run_dir, manifest)
    return new_entry


def set_status(
    run_dir: Path,
    candidate_id: str,
    status: str,
    *,
    note: str = "",
) -> dict[str, Any] | None:
    """Update the status of an existing candidate patch entry."""

    if status not in VALID_STATUS:
        raise ValueError(f"invalid patch status: {status}")
    manifest = load_manifest(run_dir)
    entry = _find_entry(manifest, candidate_id)
    if entry is None:
        return None
    entry["status"] = status
    entry["updated_at"] = utc_now()
    if status == "applied" and not entry.get("applied_at"):
        entry["applied_at"] = utc_now()
    if note:
        entry.setdefault("notes", []).append({"at": utc_now(), "note": note})
    save_manifest(run_dir, manifest)
    return entry


def mark_reverted(run_dir: Path, candidate_id: str, *, note: str = "") -> dict[str, Any] | None:
    return set_status(run_dir, candidate_id, "reverted", note=note or "reverted by auto-loop")


def mark_failed(run_dir: Path, candidate_id: str, *, note: str = "") -> dict[str, Any] | None:
    return set_status(run_dir, candidate_id, "failed", note=note)


def snapshot_worktree(
    run_dir: Path,
    *,
    snapshot_name: str = "current_worktree.patch",
    content: str | bytes = "",
) -> Path:
    """Write the raw worktree diff snapshot under patches/ for replay audits."""

    patch_dir = patches_dir(run_dir)
    path = patch_dir / snapshot_name
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content or "# empty worktree snapshot\n", encoding="utf-8")
    return path


def list_entries(run_dir: Path) -> list[dict[str, Any]]:
    return list(load_manifest(run_dir).get("entries", []))
