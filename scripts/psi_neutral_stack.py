#!/usr/bin/env python3
"""Neutral stack builder for the Psi headless auto-loop.

Reads ``neutral_pool.tsv`` in a run root, selects compatible subsets (touched
files do not overlap, combined semantic risk stays below ``high``), and writes
a combined candidate row into ``patch_queue.tsv`` with a placeholder stack
patch under ``patches/``. The remote ``psi_headless_remote.sh`` is the one that
actually applies, builds, compares and times. This module only produces the
queue entry and metadata so the auto-loop can drive that pipeline end-to-end.

Design notes:

- A neutral stack is NEVER auto-accepted. It goes through the full
  build/compare/timing pipeline and the verdict is recorded for the whole
  stack, not the individual members.
- Throttling is the auto-loop's job. This module just proposes the next
  stack; the caller decides whether to enqueue it.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Any, Iterable

from psi_candidate_generator import (
    Candidate,
    _combination_lane,
    read_tsv,
)
from psi_patch_queue import register_candidate


def _write_tsv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def build_next_stack(
    run_dir: Path,
    *,
    max_members: int = 3,
    max_stacks: int = 1,
) -> list[Candidate]:
    """Return up to ``max_stacks`` compatible neutral stacks for ``run_dir``."""

    run_dir = Path(run_dir)
    neutral_rows = read_tsv(run_dir / "neutral_pool.tsv")
    cooldown_rows = read_tsv(run_dir / "cooldown.tsv")
    candidates_dicts = _combination_lane(
        neutral_rows,
        cooldown_rows,
        max_combinations=max_stacks,
        max_members=max_members,
    )
    # _combination_lane returns Candidate instances already
    return candidates_dicts


def enqueue_stack(
    run_dir: Path,
    stack: Candidate,
    *,
    base_commit: str = "",
) -> dict[str, Any]:
    """Record the proposed stack in ``patch_queue.tsv`` and patch manifest."""

    run_dir = Path(run_dir)
    manifest_entry = register_candidate(
        run_dir,
        candidate_id=stack.candidate_id,
        lane=stack.lane,
        hypothesis=stack.hypothesis,
        target=stack.target,
        touched_files=stack.touched_files,
        semantic_risk=stack.semantic_risk,
        stack_members=stack.stack_members,
        base_commit=base_commit,
        revert_method="remote bash helper reverts patch files listed in touched_files",
        status="pending",
    )

    queue_path = run_dir / "patch_queue.tsv"
    existing = read_tsv(queue_path)
    # avoid duplicate stack rows
    existing = [row for row in existing if (row.get("candidate_id") or "") != stack.candidate_id]
    new_row = {
        "rank": str(len(existing) + 1),
        "candidate_id": stack.candidate_id,
        "target": stack.target,
        "patch_path": f"patches/{stack.candidate_id}.patch",
        "policy_bucket": stack.lane,
        "experiment_kind": "neutral_stack",
        "stack_members": "|".join(stack.stack_members),
        "touched_files": "|".join(stack.touched_files),
        "hypothesis": stack.hypothesis,
        "compare_result": "pending",
        "timing_summary": "bundle_audit_pending",
        "semantic_risk": stack.semantic_risk,
        "stack_compatibility": stack.stack_compatibility,
        "queue_state": "bundle_audit_pending",
        "build_status": "pending",
        "compare_status": "pending",
        "timing_status": "pending",
        "retry_condition": "bundle audit required before stack promotion",
        "notes": stack.expected_effect,
    }
    existing.append(new_row)
    fieldnames = list(new_row.keys())
    _write_tsv(queue_path, existing, fieldnames)
    return {"queue_row": new_row, "manifest_entry": manifest_entry}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Propose neutral stacks for the Psi auto-loop.")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--max-members", type=int, default=3)
    parser.add_argument("--max-stacks", type=int, default=1)
    parser.add_argument("--enqueue", action="store_true", help="Write the proposed stack into patch_queue.tsv")
    parser.add_argument("--base-commit", default="")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    stacks = build_next_stack(
        args.run_dir,
        max_members=args.max_members,
        max_stacks=args.max_stacks,
    )
    if not stacks:
        print("neutral_stacks=0")
        return 0
    for stack in stacks:
        if args.enqueue:
            enqueue_stack(args.run_dir, stack, base_commit=args.base_commit)
        print(
            "stack "
            f"candidate_id={stack.candidate_id} "
            f"members={'|'.join(stack.stack_members)} "
            f"touched_files={'|'.join(stack.touched_files)} "
            f"semantic_risk={stack.semantic_risk}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
