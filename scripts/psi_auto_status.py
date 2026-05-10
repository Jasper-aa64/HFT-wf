#!/usr/bin/env python3
"""Print the observable status surface for a Psi contract-v1 run."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def read_json(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_tsv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def count_verdicts(attempts: list[dict[str, str]]) -> dict[str, int]:
    counts = {"accepted": 0, "neutral": 0, "rejected": 0, "control": 0}
    for row in attempts:
        kind = (row.get("kind") or "").strip()
        verdict = (row.get("verdict") or "").strip()
        if kind == "control":
            counts["control"] += 1
        elif verdict in counts:
            counts[verdict] += 1
    return counts


def latest_candidate(attempts: list[dict[str, str]]) -> str:
    candidates = [
        row
        for row in attempts
        if (row.get("kind") or "").strip() != "control"
    ]
    if not candidates:
        return ""
    return candidates[-1].get("target", "")


def latest_timing(attempts: list[dict[str, str]]) -> dict[str, str]:
    for row in reversed(attempts):
        median_ms = row.get("median_ms", "")
        median_seconds = row.get("median_seconds", "")
        samples_ms = row.get("samples_ms", "")
        samples = row.get("samples", "")
        delta_ms = row.get("delta_ms", "")
        delta_seconds = row.get("delta_seconds", "")
        if median_ms or median_seconds or samples_ms or samples or delta_ms or delta_seconds:
            return {
                "median_ms": median_ms,
                "median_seconds": median_seconds,
                "samples_ms": samples_ms,
                "samples": samples,
                "delta_ms": delta_ms,
                "delta_seconds": delta_seconds,
            }
    return {"median_ms": "", "median_seconds": "", "samples_ms": "", "samples": "", "delta_ms": "", "delta_seconds": ""}


def count_rows(rows: list[dict[str, str]], column: str, value: str) -> int:
    return sum(1 for row in rows if (row.get(column) or "").strip() == value)


def latest_queue_state(rows: list[dict[str, str]]) -> str:
    if not rows:
        return ""
    return rows[-1].get("queue_state", "")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Show Psi automatic optimization run status.")
    parser.add_argument("--run-dir", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    run_dir = args.run_dir.resolve()
    state = read_json(run_dir / "run_state.json")
    heartbeat = read_json(run_dir / "heartbeat.json")
    failure_analysis = read_json(run_dir / "failure_analysis.json")
    attempts = read_tsv(run_dir / "attempts.tsv")
    patch_queue = read_tsv(run_dir / "patch_queue.tsv")
    neutral_pool = read_tsv(run_dir / "neutral_pool.tsv")
    retry_conditions = read_tsv(run_dir / "retry_conditions.tsv")
    counts = count_verdicts(attempts)

    latest_report = state.get("latest_report") or ""
    current_candidate = latest_candidate(attempts)
    timing = latest_timing(attempts)
    current_iteration = state.get("iteration", len([row for row in attempts if row.get("kind") != "control"]))
    last_gate = ""
    if attempts:
        last_gate = attempts[-1].get("correctness", "") or attempts[-1].get("verdict", "")

    print(f"run_dir={run_dir}")
    print(f"status={state.get('status', 'unknown')}")
    print(f"current_iteration={current_iteration}")
    print(f"iteration={current_iteration}")
    print(f"current_candidate={current_candidate}")
    print(f"last_heartbeat={heartbeat.get('updated_at', '')}")
    print(f"last_heartbeat_phase={heartbeat.get('phase', '')}")
    print(f"last_heartbeat_step={heartbeat.get('current_step', '')}")
    print(f"last_gate={last_gate}")
    print(f"latest_median_ms={timing['median_ms']}")
    print(f"latest_median_seconds={timing['median_seconds']}")
    print(f"latest_samples_ms={timing['samples_ms']}")
    print(f"latest_samples={timing['samples']}")
    print("delta_convention=control_minus_candidate_positive_is_faster")
    print(f"latest_delta_ms={timing['delta_ms']}")
    print(f"latest_delta_seconds={timing['delta_seconds']}")
    print(f"build_status={state.get('build_status', 'unknown')}")
    print(f"compare_status={state.get('compare_status', 'unknown')}")
    print(f"timing_status={state.get('timing_status', 'unknown')}")
    print(f"accepted={state.get('accepted_count', counts['accepted'])}")
    print(f"neutral={state.get('neutral_count', counts['neutral'])}")
    print(f"rejected={state.get('rejected_count', counts['rejected'])}")
    print(f"noise_status={state.get('noise_status', 'unknown')}")
    print(f"last_exit_reason={state.get('last_exit_reason', '')}")
    print(f"failure_analysis_path={state.get('failure_analysis_path', '')}")
    print(f"failure_analysis_status={state.get('failure_analysis_status', failure_analysis.get('analysis_status', ''))}")
    print(f"failure_analysis_reason={state.get('failure_analysis_reason', failure_analysis.get('reason', ''))}")
    print(f"batch_continuation={state.get('batch_continuation', failure_analysis.get('batch_continuation', ''))}")
    print(f"next_round_action={state.get('next_round_action', failure_analysis.get('next_round_action', ''))}")
    sample_policy = state.get("sample_policy") if isinstance(state.get("sample_policy"), dict) else {}
    print(f"screening_measured_samples={sample_policy.get('screening_measured_samples', '')}")
    print(f"promotion_measured_samples={sample_policy.get('promotion_measured_samples', '')}")
    print(f"bundle_audit_measured_samples={sample_policy.get('bundle_audit_measured_samples', '')}")
    print(f"patch_queue_count={len(patch_queue)}")
    print(f"patch_queue_latest_state={latest_queue_state(patch_queue)}")
    print(f"neutral_pool_count={len(neutral_pool)}")
    print(f"neutral_stack_pending={count_rows(neutral_pool, 'validation_status', 'bundle_audit_pending')}")
    print(f"retry_conditions_count={len(retry_conditions)}")
    print(f"noisy_pending_count={count_rows(retry_conditions, 'status', 'NOISY_PENDING') + count_rows(patch_queue, 'queue_state', 'NOISY_PENDING')}")
    print(f"latest_report_path={latest_report}")
    print(f"latest_report={latest_report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
