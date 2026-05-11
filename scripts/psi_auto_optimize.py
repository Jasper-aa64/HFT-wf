#!/usr/bin/env python3
"""Local contract-v1 harness for Psi automatic optimization runs.

The implementation in this file is intentionally local-only for ``--dry-run``.
It writes the v1 state surface, synthetic attempts, convergence charts, and the
dated performance report without touching the Psi business repository or any
remote host.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import statistics
import subprocess
import sys
import time
import zlib
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from struct import pack

from psi_timing_history import (
    bundle_id_for_path,
    default_host_key,
    history_rows_from_attempt_rows,
    per_run_history_path,
    sample_statistics_from_ms,
    shared_history_path_for_output,
    write_history_artifacts,
)
from psi_timing_analysis import evidence_fields, summarize_paired_timing
from psi_attempts_schema import ATTEMPTS_FIELDNAMES


TITLE_SUFFIX = "\u6027\u80fd\u4f18\u5316\u62a5\u544a"
EPSILON_ABS_SECONDS = 1.0
EPSILON_SIGMA_MULTIPLIER = 2.0
# Attempt-level deltas required before the loop may claim convergence_proven.
# Per-candidate promotion still needs its own repeated timing samples.
MIN_CONVERGENCE_SAMPLES = 5
PROMOTION_SAMPLE_FLOOR = 5
BUNDLE_AUDIT_SAMPLE_FLOOR = 7
SCREENING_SAMPLE_FLOOR = 3
NOISE_RANGE_THRESHOLD = 5.0
NOISE_STDEV_THRESHOLD = 1.5
NOISE_CV_THRESHOLD = 0.03
STOP_REASONS = {
    "accepted",
    "convergence_proven",
    "budget_stop",
    "no_targets",
    "remote_failed",
    "user_stopped",
}
SAMPLE_POLICY = {
    "screening_measured_samples": SCREENING_SAMPLE_FLOOR,
    "promotion_measured_samples": PROMOTION_SAMPLE_FLOOR,
    "bundle_audit_measured_samples": BUNDLE_AUDIT_SAMPLE_FLOOR,
    "screening_policy": "1 warmup + 3 measured is diagnostic screening only",
    "promotion_policy": "same-harness control and candidate evidence with compare pass",
    "bundle_policy": "neutral stacks need bundle audit before promotion",
}


def safe_token(text: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in text.strip())
    return cleaned.strip("_") or "candidate"


@dataclass(frozen=True)
class Attempt:
    rank: int
    lane: str
    target: str
    verdict: str
    control_samples: list[float]
    candidate_samples: list[float]
    correctness: str
    stop_reason: str
    notes: str


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_tsv(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def sample_stats(samples: list[float]) -> dict[str, object]:
    if not samples:
        return {
            "sample_count": 0,
            "samples_ms": "",
            "samples": "",
            "mean_ms": "",
            "mean_seconds": 0.0,
            "median_ms": "",
            "median_seconds": 0.0,
            "mad_ms": "",
            "mad_seconds": "",
            "iqr_ms": "",
            "iqr_seconds": "",
            "stdev_ms": "",
            "stdev_seconds": 0.0,
            "range_ms": "",
            "range_seconds": 0.0,
            "noise_flag": "ok",
        }
    samples_ms = [sample * 1000.0 for sample in samples]
    stats_ms = sample_statistics_from_ms(samples_ms)
    return {
        "sample_count": float(len(samples)),
        "samples_ms": stats_ms["samples_ms"],
        "samples": stats_ms["samples"],
        "mean_ms": stats_ms["mean_ms"],
        "mean_seconds": float(stats_ms["mean_seconds"]),
        "median_ms": stats_ms["median_ms"],
        "median_seconds": float(stats_ms["median_seconds"]),
        "mad_ms": stats_ms["mad_ms"],
        "mad_seconds": float(stats_ms["mad_seconds"]),
        "iqr_ms": stats_ms["iqr_ms"],
        "iqr_seconds": float(stats_ms["iqr_seconds"]),
        "stdev_ms": stats_ms["stdev_ms"],
        "stdev_seconds": float(stats_ms["stdev_seconds"]),
        "range_ms": stats_ms["range_ms"],
        "range_seconds": float(stats_ms["range_seconds"]),
        "noise_flag": "ok",
    }


def delta_i(control_median: float, candidate_median: float) -> float:
    return control_median - candidate_median


def epsilon_for(control_samples: list[float], epsilon_abs: float = EPSILON_ABS_SECONDS, k: float = EPSILON_SIGMA_MULTIPLIER) -> float:
    sigma_control = statistics.stdev(control_samples) if len(control_samples) > 1 else 0.0
    return max(epsilon_abs, k * sigma_control)


def ucb95_expected_delta(deltas: list[float]) -> float | None:
    if not deltas:
        return None
    mean_delta = statistics.mean(deltas)
    if len(deltas) == 1:
        return mean_delta
    stdev_delta = statistics.stdev(deltas)
    return mean_delta + 1.96 * stdev_delta / math.sqrt(len(deltas))


def is_noisy(control_samples: list[float], candidate_samples: list[float]) -> bool:
    samples = control_samples + candidate_samples
    if not samples:
        return False
    stdev_value = statistics.stdev(samples) if len(samples) > 1 else 0.0
    range_value = max(samples) - min(samples)
    mean_value = statistics.mean(samples)
    cv = stdev_value / mean_value if mean_value else 0.0
    return (
        range_value >= NOISE_RANGE_THRESHOLD
        or stdev_value >= NOISE_STDEV_THRESHOLD
        or cv >= NOISE_CV_THRESHOLD
    )


def classify_stop(
    attempts: list[Attempt],
    stall_limit: int,
    max_iterations: int,
    min_samples: int = MIN_CONVERGENCE_SAMPLES,
) -> dict[str, object]:
    evidence_by_rank = {
        attempt.rank: attempt_timing_evidence(
            attempt,
            BUNDLE_AUDIT_SAMPLE_FLOOR if attempt.lane == "combination" else max(min_samples, PROMOTION_SAMPLE_FLOOR),
        )
        for attempt in attempts
    }
    relevant = [attempt for attempt in attempts if evidence_by_rank[attempt.rank].verdict in {"accepted", "neutral", "rejected", "NOISY_PENDING"}]
    judged_attempts: list[Attempt] = []
    noisy_candidate_count = 0
    for attempt in relevant:
        evidence = evidence_by_rank[attempt.rank]
        if evidence.verdict == "NOISY_PENDING":
            noisy_candidate_count += 1
        else:
            judged_attempts.append(attempt)

    all_control = [sample for attempt in judged_attempts for sample in attempt.control_samples]
    deltas = [
        float(evidence_by_rank[attempt.rank].median_delta_ms) / 1000.0
        for attempt in judged_attempts
        if evidence_by_rank[attempt.rank].median_delta_ms is not None
    ]
    epsilon = epsilon_for(all_control) if all_control else EPSILON_ABS_SECONDS
    ucb95 = ucb95_expected_delta(deltas)
    consecutive_no_accepted = 0
    for attempt in reversed(judged_attempts):
        if evidence_by_rank[attempt.rank].verdict == "accepted":
            break
        consecutive_no_accepted += 1

    noise_status = "NOISY" if noisy_candidate_count else "ok"
    if len(deltas) >= min_samples and ucb95 is not None and ucb95 <= epsilon:
        reason = "convergence_proven"
    elif consecutive_no_accepted >= stall_limit or len(relevant) >= max_iterations:
        reason = "budget_stop"
    else:
        reason = None

    if reason is not None and reason not in STOP_REASONS:
        raise ValueError(f"unsupported stop reason: {reason}")

    return {
        "epsilon": round(epsilon, 6),
        "ucb95_expected_delta": None if ucb95 is None else round(ucb95, 6),
        "deltas": [round(value, 6) for value in deltas],
        "consecutive_no_accepted": consecutive_no_accepted,
        "noisy_candidate_count": noisy_candidate_count,
        "last_exit_reason": reason,
        "noise_status": noise_status,
        "supported_stop_reasons": sorted(STOP_REASONS),
    }


def synthetic_attempts(max_iterations: int) -> list[Attempt]:
    planned = [
        Attempt(
            rank=1,
            lane="evidence",
            target="handlerData.row_loop",
            verdict="neutral",
            control_samples=[118.4, 118.9, 118.6],
            candidate_samples=[118.1, 118.7, 118.5],
            correctness="pass",
            stop_reason="",
            notes="Synthetic evidence lane candidate; tiny delta remains below epsilon.",
        ),
        Attempt(
            rank=2,
            lane="insight",
            target="timestamp cache locality",
            verdict="neutral",
            control_samples=[118.4, 118.9, 118.6],
            candidate_samples=[118.0, 118.8, 118.6],
            correctness="pass",
            stop_reason="",
            notes="Synthetic Class A/cache-locality candidate, kept independent from evidence rank.",
        ),
        Attempt(
            rank=3,
            lane="combination",
            target="low-risk neutral stack",
            verdict="neutral",
            control_samples=[118.4, 118.9, 118.6],
            candidate_samples=[118.2, 118.8, 118.7],
            correctness="pass",
            stop_reason="",
            notes="Synthetic neutral stack member with compare pass but no material gain.",
        ),
        Attempt(
            rank=4,
            lane="evidence",
            target="write.generate_table",
            verdict="neutral",
            control_samples=[118.4, 118.9, 118.6],
            candidate_samples=[118.3, 118.8, 118.6],
            correctness="pass",
            stop_reason="",
            notes="Additional synthetic sample to exercise convergence metrics before budget stop.",
        ),
    ]
    return planned[: max(0, max_iterations)]


def init_artifact_tree(run_dir: Path) -> None:
    for child in ("logs", "patches", "charts", "reports"):
        (run_dir / child).mkdir(parents=True, exist_ok=True)


def write_profile(run_dir: Path) -> None:
    profile_rows = [
        {"stage": "handlerData.row_loop", "total_ms": 65200, "count": 8, "avg_ms": "8150.000", "source": "synthetic_dry_run"},
        {"stage": "write.generate_table", "total_ms": 28400, "count": 8, "avg_ms": "3550.000", "source": "synthetic_dry_run"},
        {"stage": "handlerData.timestamp", "total_ms": 18700, "count": 8, "avg_ms": "2337.500", "source": "synthetic_dry_run"},
        {"stage": "compute.5447", "total_ms": 9100, "count": 8, "avg_ms": "1137.500", "source": "synthetic_dry_run"},
    ]
    write_tsv(run_dir / "profile.tsv", profile_rows, ["stage", "total_ms", "count", "avg_ms", "source"])


def evidence_score(total_ms: int, p_owned: float, p_safe: float, p_gate: float, p_local: float, uncertainty: float) -> float:
    expected_delta_seconds = total_ms / 1000.0 * 0.03
    cost_attempt_seconds = 1800.0
    return expected_delta_seconds * p_owned * p_safe * p_gate * p_local / cost_attempt_seconds + 0.05 * uncertainty


def timing_summary_text(sample_count: object, median_ms: object, delta_ms: object, noise_flag: str) -> str:
    return f"sample_count={sample_count}; median_ms={median_ms}; delta_ms={delta_ms}; noise_flag={noise_flag}"


def attempt_timing_evidence(attempt: Attempt, required_pairs: int) -> object:
    return summarize_paired_timing(
        [sample * 1000.0 for sample in attempt.control_samples],
        [sample * 1000.0 for sample in attempt.candidate_samples],
        build_pass=attempt.correctness == "pass",
        compare_pass=attempt.correctness == "pass",
        required_pairs=required_pairs,
        verdict_context=attempt.target,
    )


def write_hotspots(run_dir: Path) -> None:
    row_loop_score = f"{evidence_score(65200, 0.9, 0.84, 0.92, 1.0, 0.1):.6f}"
    write_table_score = f"{evidence_score(28400, 0.82, 0.96, 0.92, 0.98, 0.1):.6f}"
    rows = [
        {
            "rank": 1,
            "stage": "handlerData.row_loop",
            "total_ms": 65200,
            "avg_ms": "8150.000",
            "count": 8,
            "expected_delta_seconds": "1.956",
            "p_owned": "0.900",
            "p_safe": "0.840",
            "p_gate": "0.920",
            "p_local": "1.000",
            "cost_attempt_seconds": "1800.0",
            "uncertainty": "0.100",
            "lambda": "0.050",
            "score_evidence": row_loop_score,
            "score": row_loop_score,
            "notes": "synthetic expected-value evidence lane row",
        },
        {
            "rank": 2,
            "stage": "write.generate_table",
            "total_ms": 28400,
            "avg_ms": "3550.000",
            "count": 8,
            "expected_delta_seconds": "0.852",
            "p_owned": "0.820",
            "p_safe": "0.960",
            "p_gate": "0.920",
            "p_local": "0.980",
            "cost_attempt_seconds": "1800.0",
            "uncertainty": "0.100",
            "lambda": "0.050",
            "score_evidence": write_table_score,
            "score": write_table_score,
            "notes": "synthetic expected-value evidence lane row",
        },
    ]
    write_tsv(
        run_dir / "hotspots.tsv",
        rows,
        [
            "rank",
            "stage",
            "total_ms",
            "avg_ms",
            "count",
            "expected_delta_seconds",
            "p_owned",
            "p_safe",
            "p_gate",
            "p_local",
            "cost_attempt_seconds",
            "uncertainty",
            "lambda",
            "score_evidence",
            "score",
            "notes",
        ],
    )


def write_attempts(run_dir: Path, attempts: list[Attempt], control_head: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    control_samples = attempts[0].control_samples if attempts else [118.4, 118.9, 118.6]
    control = sample_stats(control_samples)
    rows.append(
        {
            "rank": 0,
            "kind": "control",
            "policy_bucket": "control",
            "experiment_kind": "control_bundle",
            "lane": "control",
            "target": f"{control_head} control baseline",
            "stack_members": "",
            "candidate_id": "control",
            "patch_path": "",
            "touched_files": "",
            "hypothesis": "diagnostic baseline for the current run",
            "compare_result": "control",
            "timing_summary": timing_summary_text(control["sample_count"], control["median_ms"], "0.000", "ok"),
            "semantic_risk": "none",
            "stack_compatibility": "baseline",
            "retry_condition": "",
            "sample_unit": "seconds_compat",
            "warm_or_cold": "measured",
            "samples_ms": control["samples_ms"],
            "samples": control["samples"],
            "sample_count": int(control["sample_count"]),
            "mean_ms": control["mean_ms"],
            "mean_seconds": f"{control['mean_seconds']:.3f}",
            "median_ms": control["median_ms"],
            "median_seconds": f"{control['median_seconds']:.3f}",
            "mad_ms": control["mad_ms"],
            "mad_seconds": f"{control['mad_seconds']:.3f}",
            "iqr_ms": control["iqr_ms"],
            "iqr_seconds": f"{control['iqr_seconds']:.3f}",
            "stdev_ms": control["stdev_ms"],
            "stdev_seconds": f"{control['stdev_seconds']:.3f}",
            "range_ms": control["range_ms"],
            "range_seconds": f"{control['range_seconds']:.3f}",
            "delta_ms": "0.000",
            "delta_seconds": "0.000",
            "verdict": "DIAGNOSTIC_ONLY",
            "correctness": "pass",
            "noise_flag": "ok",
            "stop_reason": "",
            "acceptance_policy": "diagnostic baseline; not promotion proof",
            "evidence_status": "screening_only",
            "promotion_sample_floor": PROMOTION_SAMPLE_FLOOR,
            "bundle_audit_sample_floor": BUNDLE_AUDIT_SAMPLE_FLOOR,
            "notes": "Synthetic local control baseline; not performance authority.",
        }
    )
    for attempt in attempts:
        stats = sample_stats(attempt.candidate_samples)
        control_median = statistics.median(attempt.control_samples)
        candidate_median = statistics.median(attempt.candidate_samples)
        required_pairs = BUNDLE_AUDIT_SAMPLE_FLOOR if attempt.lane == "combination" else PROMOTION_SAMPLE_FLOOR
        evidence = attempt_timing_evidence(attempt, required_pairs)
        timing_fields = evidence_fields(evidence)
        noisy = evidence.verdict == "NOISY_PENDING"
        compare_result = "pass" if attempt.correctness == "pass" else "failed"
        candidate_id = f"{attempt.rank:02d}_{safe_token(attempt.target)}"
        patch_path = f"patches/{attempt.rank:02d}_{safe_token(attempt.lane)}.patch"
        timing_summary = timing_summary_text(
            stats["sample_count"],
            stats["median_ms"],
            f"{delta_i(control_median, candidate_median) * 1000.0:.3f}",
            timing_fields["noise_flag"],
        )
        row = {
            "rank": attempt.rank,
            "kind": "candidate",
            "policy_bucket": attempt.lane,
            "experiment_kind": "neutral_stack" if attempt.lane == "combination" else "single",
            "lane": attempt.lane,
            "target": attempt.target,
            "stack_members": attempt.target if attempt.lane != "combination" else "handlerData.row_loop|timestamp cache locality",
            "candidate_id": candidate_id,
            "patch_path": patch_path,
            "touched_files": attempt.target if attempt.lane != "combination" else "handlerData.row_loop|timestamp cache locality",
            "hypothesis": attempt.notes,
            "compare_result": compare_result,
            "timing_summary": (
                f"{timing_summary}; timing_verdict={evidence.verdict}; "
                f"permutation_p={timing_fields['permutation_p_value']}"
            ),
            "semantic_risk": "low" if attempt.verdict != "rejected" else "medium",
            "stack_compatibility": "stackable" if attempt.lane == "combination" else "single",
            "retry_condition": "rerun when interleaved paired jitter is below threshold" if noisy else "bundle audit required" if attempt.lane == "combination" else "eligible for stronger paired evidence run",
            "sample_unit": "seconds_compat",
            "warm_or_cold": "measured",
            "samples_ms": stats["samples_ms"],
            "samples": stats["samples"],
            "sample_count": int(stats["sample_count"]),
            "mean_ms": stats["mean_ms"],
            "mean_seconds": f"{stats['mean_seconds']:.3f}",
            "median_ms": stats["median_ms"],
            "median_seconds": f"{stats['median_seconds']:.3f}",
            "mad_ms": stats["mad_ms"],
            "mad_seconds": f"{stats['mad_seconds']:.3f}",
            "iqr_ms": stats["iqr_ms"],
            "iqr_seconds": f"{stats['iqr_seconds']:.3f}",
            "stdev_ms": stats["stdev_ms"],
            "stdev_seconds": f"{stats['stdev_seconds']:.3f}",
            "range_ms": stats["range_ms"],
            "range_seconds": f"{stats['range_seconds']:.3f}",
            "delta_ms": f"{delta_i(control_median, candidate_median) * 1000.0:.3f}",
            "delta_seconds": f"{delta_i(control_median, candidate_median):.3f}",
            "verdict": attempt.verdict,
            "correctness": attempt.correctness,
            "noise_flag": timing_fields["noise_flag"],
            "stop_reason": attempt.stop_reason,
            "acceptance_policy": "compare pass plus interleaved same-harness paired timing",
            "evidence_status": evidence.verdict if evidence.verdict == "NOISY_PENDING" else "neutral_pool_candidate" if attempt.verdict == "neutral" else attempt.verdict,
            "promotion_sample_floor": PROMOTION_SAMPLE_FLOOR,
            "bundle_audit_sample_floor": BUNDLE_AUDIT_SAMPLE_FLOOR,
            "notes": attempt.notes,
        }
        row.update(timing_fields)
        rows.append(row)
    write_tsv(
        run_dir / "attempts.tsv",
        rows,
        ATTEMPTS_FIELDNAMES,
    )
    return rows


def write_cooldown(run_dir: Path) -> None:
    rows = [
        {
            "target": "generateTable broad rewrite",
            "status": "cooldown",
            "cooldown_runs_remaining": 3,
            "reason": "broad target needs narrower evidence",
            "notes": "synthetic dry-run cooldown row",
        },
        {
            "target": "AvgVolCmp::afterCompute",
            "status": "blocked",
            "cooldown_runs_remaining": 99,
            "reason": "active gate does not cover this factor",
            "notes": "synthetic dry-run blocked row",
        },
        {
            "target": "evidence lane",
            "status": "cooldown",
            "cooldown_runs_remaining": 1,
            "reason": "lane_cooldown_budget",
            "notes": "Beta(1,1) budget model says continue only with new evidence.",
        },
    ]
    write_tsv(run_dir / "cooldown.tsv", rows, ["target", "status", "cooldown_runs_remaining", "reason", "notes"])


def write_patch_queue(run_dir: Path, attempt_rows: list[dict[str, object]]) -> None:
    rows: list[dict[str, object]] = []
    for row in attempt_rows:
        if row.get("kind") == "control":
            continue
        sample_count = int(row.get("sample_count") or 0)
        verdict = str(row.get("verdict") or "")
        timing_verdict = str(row.get("timing_verdict") or verdict)
        noisy = str(row.get("noise_flag") or "").upper() == "NOISY"
        experiment_kind = str(row.get("experiment_kind") or "single")
        required_samples = BUNDLE_AUDIT_SAMPLE_FLOOR if experiment_kind == "neutral_stack" else PROMOTION_SAMPLE_FLOOR
        rows.append(
            {
                "rank": row.get("rank", ""),
                "candidate_id": row.get("candidate_id", ""),
                "target": row.get("target", ""),
                "patch_path": row.get("patch_path", ""),
                "policy_bucket": row.get("policy_bucket", ""),
                "experiment_kind": experiment_kind,
                "stack_members": row.get("stack_members", ""),
                "touched_files": row.get("touched_files", ""),
                "hypothesis": row.get("hypothesis", ""),
                "compare_result": row.get("compare_result", ""),
                "timing_summary": row.get("timing_summary", ""),
                "semantic_risk": row.get("semantic_risk", ""),
                "stack_compatibility": row.get("stack_compatibility", ""),
                "queue_state": (
                    "NOISY_PENDING"
                    if noisy or timing_verdict == "NOISY_PENDING"
                    else "bundle_audit_pending"
                    if experiment_kind == "neutral_stack"
                    else "neutral_pool"
                    if verdict == "neutral" or timing_verdict == "neutral"
                    else "review"
                ),
                "build_status": "dry_run",
                "compare_status": "pass" if row.get("compare_result") == "pass" else "failed",
                "timing_status": timing_verdict,
                "timing_verdict_reason": row.get("timing_verdict_reason", ""),
                "measured_samples": sample_count,
                "required_samples": required_samples,
                "retry_condition": row.get(
                    "retry_condition",
                    (
                        "rerun when interleaved paired jitter is below threshold"
                        if noisy or timing_verdict == "NOISY_PENDING"
                        else "bundle audit required"
                        if experiment_kind == "neutral_stack"
                        else "eligible for stronger paired evidence run"
                    ),
                ),
                "cooldown_status": "hold" if noisy or timing_verdict == "NOISY_PENDING" else "open",
                "notes": row.get("notes", ""),
            }
        )
    write_tsv(
        run_dir / "patch_queue.tsv",
        rows,
        [
            "rank",
            "candidate_id",
            "target",
            "patch_path",
            "policy_bucket",
            "experiment_kind",
            "stack_members",
            "touched_files",
            "hypothesis",
            "compare_result",
            "timing_summary",
            "semantic_risk",
            "stack_compatibility",
            "queue_state",
            "build_status",
            "compare_status",
            "timing_status",
            "timing_verdict_reason",
            "measured_samples",
            "required_samples",
            "retry_condition",
            "cooldown_status",
            "notes",
        ],
    )


def write_neutral_pool(run_dir: Path, attempts: list[Attempt]) -> None:
    rows = [
        {
            "candidate_id": f"{attempt.rank:02d}_{safe_token(attempt.target)}",
            "target": attempt.target,
            "lane": attempt.lane,
            "patch_path": f"patches/{attempt.rank:02d}_{safe_token(attempt.lane)}.patch",
            "touched_files": attempt.target if attempt.lane != "combination" else "handlerData.row_loop|timestamp cache locality",
            "hypothesis": attempt.notes,
            "experiment_kind": "neutral_stack" if attempt.lane == "combination" else "single",
            "stack_members": attempt.target if attempt.lane != "combination" else "handlerData.row_loop|timestamp cache locality",
            "correctness": attempt.correctness,
            "compare_result": attempt.correctness,
            "sample_count": len(attempt.candidate_samples),
            "promotion_sample_floor": PROMOTION_SAMPLE_FLOOR,
            "bundle_audit_sample_floor": BUNDLE_AUDIT_SAMPLE_FLOOR,
            "aggregate_gain_seconds": f"{delta_i(statistics.median(attempt.control_samples), statistics.median(attempt.candidate_samples)):.3f}",
            "timing_summary": timing_summary_text(
                len(attempt.candidate_samples),
                f"{statistics.median(attempt.candidate_samples) * 1000.0:.3f}",
                f"{delta_i(statistics.median(attempt.control_samples), statistics.median(attempt.candidate_samples)) * 1000.0:.3f}",
                "ok",
            ),
            **evidence_fields(
                attempt_timing_evidence(
                    attempt,
                    BUNDLE_AUDIT_SAMPLE_FLOOR if attempt.lane == "combination" else PROMOTION_SAMPLE_FLOOR,
                )
            ),
            "semantic_risk": "low" if attempt.verdict != "rejected" else "medium",
            "stack_compatibility": "stackable" if attempt.lane == "combination" else "single",
            "validation_status": "bundle_audit_pending" if attempt.lane == "combination" else "stackable",
            "retry_condition": "bundle audit required" if attempt.lane == "combination" else "eligible for stronger paired evidence run",
            "notes": "safe neutral evidence retained; promotion still requires stronger same-harness samples",
        }
        for attempt in attempts
        if attempt.verdict == "neutral"
    ]
    write_tsv(
        run_dir / "neutral_pool.tsv",
        rows,
        [
            "candidate_id",
            "target",
            "lane",
            "patch_path",
            "touched_files",
            "hypothesis",
            "experiment_kind",
            "stack_members",
            "correctness",
            "compare_result",
            "sample_count",
            "promotion_sample_floor",
            "bundle_audit_sample_floor",
            "aggregate_gain_seconds",
            "timing_summary",
            "timing_verdict",
            "timing_verdict_reason",
            "timing_verdict_method",
            "control_sample_count",
            "candidate_sample_count",
            "paired_sample_count",
            "control_samples_ms",
            "candidate_samples_ms",
            "paired_deltas_ms",
            "median_delta_ms",
            "bootstrap_ci_low_ms",
            "bootstrap_ci_high_ms",
            "permutation_p_value",
            "semantic_risk",
            "stack_compatibility",
            "validation_status",
            "retry_condition",
            "notes",
        ],
    )


def write_retry_conditions(run_dir: Path, attempts: list[Attempt], metrics: dict[str, object] | None = None) -> None:
    rows: list[dict[str, object]] = []
    for attempt in attempts:
        evidence = attempt_timing_evidence(
            attempt,
            BUNDLE_AUDIT_SAMPLE_FLOOR if attempt.lane == "combination" else PROMOTION_SAMPLE_FLOOR,
        )
        noisy = evidence.verdict == "NOISY_PENDING"
        rows.append(
            {
                "target": attempt.target,
                "status": "NOISY_PENDING" if noisy else "ready_for_evidence",
                "noise_flag": evidence.noise_flag,
                "retry_after": "next quiet same-host window" if noisy else "after candidate patch is prepared",
                "required_condition": (
                    f"collect >= {PROMOTION_SAMPLE_FLOOR} measured control and candidate samples; "
                    f">= {BUNDLE_AUDIT_SAMPLE_FLOOR} for neutral-stack bundle audit"
                ),
                "last_exit_reason": "" if metrics is None else metrics.get("last_exit_reason", ""),
                "notes": evidence.reason if noisy else "paired evidence remains eligible for review.",
            }
        )
    write_tsv(
        run_dir / "retry_conditions.tsv",
        rows,
        ["target", "status", "noise_flag", "retry_after", "required_condition", "last_exit_reason", "notes"],
    )


def parse_float_list(raw: object) -> list[float]:
    if raw in (None, ""):
        return []
    if isinstance(raw, list):
        return [float(value) for value in raw]
    return [float(part.strip()) for part in str(raw).split(",") if part.strip()]


def summary_stats_from_ms_text(raw: object) -> dict[str, str]:
    return sample_statistics_from_ms(parse_float_list(raw))


def write_comparison_summary(
    run_dir: Path,
    run_id: str,
    recorded_at: str,
    attempt_rows: list[dict[str, object]],
) -> tuple[Path, dict[str, object]]:
    candidates = [
        row
        for row in attempt_rows
        if row.get("kind") == "candidate" and row.get("timing_verdict")
    ]
    selected = next((row for row in candidates if row.get("timing_verdict") == "accepted"), candidates[0] if candidates else {})
    control_samples_ms = parse_float_list(selected.get("control_samples_ms"))
    candidate_samples_ms = parse_float_list(selected.get("candidate_samples_ms"))
    paired_deltas_ms = parse_float_list(selected.get("paired_deltas_ms"))
    pair_count = min(len(control_samples_ms), len(candidate_samples_ms), len(paired_deltas_ms))
    paired_samples = [
        {
            "pair_index": index + 1,
            "control_ms": f"{control_samples_ms[index]:.3f}",
            "candidate_ms": f"{candidate_samples_ms[index]:.3f}",
            "delta_ms": f"{paired_deltas_ms[index]:.3f}",
        }
        for index in range(pair_count)
    ]
    timing_verdict = str(selected.get("timing_verdict") or "neutral")
    summary = {
        "schema": "psi_headless_comparison_summary_v2",
        "run_id": run_id,
        "recorded_at": recorded_at,
        "control_role": "control",
        "candidate_role": "candidate",
        "updated_baseline_role": "updated_baseline" if timing_verdict == "accepted" else "pending",
        "build_result": "dry_run",
        "compare_result": "simulated_pass",
        "decision": timing_verdict,
        "timing_verdict": timing_verdict,
        "timing_verdict_reason": str(selected.get("timing_verdict_reason") or ""),
        "timing_verdict_method": str(selected.get("timing_verdict_method") or ""),
        "accepted": timing_verdict == "accepted",
        "control": summary_stats_from_ms_text(selected.get("control_samples_ms")),
        "candidate": summary_stats_from_ms_text(selected.get("candidate_samples_ms")),
        "updated_baseline": summary_stats_from_ms_text(selected.get("candidate_samples_ms")) if timing_verdict == "accepted" else {},
        "paired": {
            "paired_sample_count": str(selected.get("paired_sample_count") or pair_count),
            "paired_deltas_ms": str(selected.get("paired_deltas_ms") or ""),
            "median_delta_ms": str(selected.get("median_delta_ms") or ""),
            "median_delta_seconds": str(selected.get("median_delta_seconds") or ""),
            "bootstrap_ci_low_ms": str(selected.get("bootstrap_ci_low_ms") or ""),
            "bootstrap_ci_high_ms": str(selected.get("bootstrap_ci_high_ms") or ""),
            "bootstrap_ci_low_seconds": str(selected.get("bootstrap_ci_low_seconds") or ""),
            "bootstrap_ci_high_seconds": str(selected.get("bootstrap_ci_high_seconds") or ""),
            "permutation_p_value": str(selected.get("permutation_p_value") or ""),
            "paired_stdev_ms": str(selected.get("paired_stdev_ms") or ""),
            "paired_range_ms": str(selected.get("paired_range_ms") or ""),
            "paired_mean_ms": str(selected.get("paired_mean_ms") or ""),
            "noise_flag": str(selected.get("noise_flag") or ""),
        },
        "paired_samples": paired_samples,
        "artifact_paths": {
            "attempts": str(run_dir / "attempts.tsv"),
            "timing_history_run_copy": str(per_run_history_path(run_dir)),
            "retry_conditions": str(run_dir / "retry_conditions.tsv"),
        },
        "notes": "Paired delta is control_ms - candidate_ms; positive means the candidate is faster.",
    }
    path = run_dir / "comparison_summary.json"
    write_json(path, summary)
    return path, summary


def write_logs_and_patches(run_dir: Path) -> None:
    (run_dir / "logs" / "profile.log").write_text("synthetic profile refresh completed\n", encoding="utf-8")
    (run_dir / "logs" / "current.log").write_text("synthetic contract-v1 dry-run completed\n", encoding="utf-8")
    (run_dir / "logs" / "control.build.log").write_text("dry-run: remote build not executed\n", encoding="utf-8")
    (run_dir / "logs" / "control.compare.log").write_text("dry-run: compare gate simulated as pass\n", encoding="utf-8")
    (run_dir / "logs" / "control.timing.log").write_text("dry-run control samples: 118.4,118.9,118.6\n", encoding="utf-8")
    (run_dir / "patches" / "README.txt").write_text("dry-run: no business repository patch generated\n", encoding="utf-8")


def png_chunk(tag: bytes, data: bytes) -> bytes:
    return pack(">I", len(data)) + tag + data + pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def write_png(path: Path, width: int, height: int, pixels: list[list[tuple[int, int, int]]]) -> None:
    raw = b"".join(b"\x00" + b"".join(bytes(pixel) for pixel in row) for row in pixels)
    data = (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + png_chunk(b"IDAT", zlib.compress(raw, 9))
        + png_chunk(b"IEND", b"")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def blank_canvas(width: int = 900, height: int = 520) -> list[list[tuple[int, int, int]]]:
    return [[(255, 255, 255) for _ in range(width)] for _ in range(height)]


def draw_line(pixels: list[list[tuple[int, int, int]]], x1: int, y1: int, x2: int, y2: int, color: tuple[int, int, int]) -> None:
    dx = abs(x2 - x1)
    dy = -abs(y2 - y1)
    sx = 1 if x1 < x2 else -1
    sy = 1 if y1 < y2 else -1
    err = dx + dy
    x, y = x1, y1
    while True:
        if 0 <= y < len(pixels) and 0 <= x < len(pixels[0]):
            pixels[y][x] = color
        if x == x2 and y == y2:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x += sx
        if e2 <= dx:
            err += dx
            y += sy


def draw_rect(pixels: list[list[tuple[int, int, int]]], x: int, y: int, w: int, h: int, color: tuple[int, int, int]) -> None:
    for yy in range(max(0, y), min(len(pixels), y + h)):
        for xx in range(max(0, x), min(len(pixels[0]), x + w)):
            pixels[yy][xx] = color


def draw_chart(path: Path, values: list[float], reference: float | None = None) -> None:
    width, height = 900, 520
    left, right, top, bottom = 70, 40, 35, 70
    pixels = blank_canvas(width, height)
    axis = (34, 45, 57)
    grid = (226, 232, 240)
    blue = (37, 99, 235)
    red = (220, 38, 38)
    green = (22, 163, 74)
    for i in range(6):
        y = top + int((height - top - bottom) * i / 5)
        draw_line(pixels, left, y, width - right, y, grid)
    draw_line(pixels, left, top, left, height - bottom, axis)
    draw_line(pixels, left, height - bottom, width - right, height - bottom, axis)

    if not values:
        values = [0.0]
    all_values = values + ([] if reference is None else [reference])
    min_value = min(all_values)
    max_value = max(all_values)
    if math.isclose(min_value, max_value):
        min_value -= 1.0
        max_value += 1.0

    def x_at(index: int) -> int:
        if len(values) == 1:
            return left
        return left + int((width - left - right) * index / (len(values) - 1))

    def y_at(value: float) -> int:
        return height - bottom - int((height - top - bottom) * (value - min_value) / (max_value - min_value))

    points = [(x_at(index), y_at(value)) for index, value in enumerate(values)]
    for first, second in zip(points, points[1:]):
        draw_line(pixels, first[0], first[1], second[0], second[1], blue)
    for x, y in points:
        draw_rect(pixels, x - 4, y - 4, 9, 9, green)
    if reference is not None:
        y = y_at(reference)
        draw_line(pixels, left, y, width - right, y, red)
    write_png(path, width, height, pixels)


def write_charts(run_dir: Path, attempts: list[Attempt], metrics: dict[str, object]) -> None:
    medians = [statistics.median(attempt.candidate_samples) for attempt in attempts if attempt.candidate_samples]
    deltas = [float(value) for value in metrics.get("deltas", [])]
    epsilon = metrics.get("epsilon")
    control_reference = statistics.median(attempts[0].control_samples) if attempts and attempts[0].control_samples else None
    draw_chart(run_dir / "charts" / "runtime_convergence.png", medians, control_reference)
    draw_chart(run_dir / "charts" / "convergence_decision.png", deltas, float(epsilon) if epsilon is not None else None)


def update_heartbeat(run_dir: Path, phase: str, current_step: str) -> None:
    write_json(
        run_dir / "heartbeat.json",
        {
            "updated_at": utc_now(),
            "phase": phase,
            "current_step": current_step,
            "pid_or_session": str(os.getpid()),
            "last_log": str((run_dir / "logs" / "current.log").resolve()),
        },
    )


def parse_report_paths(stdout: str) -> tuple[Path, Path]:
    md_path: Path | None = None
    pdf_path: Path | None = None
    for line in stdout.splitlines():
        if line.startswith("markdown="):
            md_path = Path(line.split("=", 1)[1].strip())
        elif line.startswith("pdf="):
            pdf_path = Path(line.split("=", 1)[1].strip())
    if md_path is None:
        raise RuntimeError("psi_daily_report.py did not print a markdown= path")
    if pdf_path is None:
        pdf_path = md_path.with_suffix(".pdf")
    return md_path, pdf_path


def generate_report(run_dir: Path, report_date: str) -> tuple[Path, Path]:
    script = repo_root() / "scripts" / "psi_daily_report.py"
    command = [
        sys.executable,
        str(script),
        "--date",
        report_date,
        "--control-loop-dir",
        str(run_dir),
        "--run-state",
        str(run_dir / "run_state.json"),
        "--image",
        str(run_dir / "charts" / "runtime_convergence.png"),
        "--image",
        str(run_dir / "charts" / "convergence_decision.png"),
    ]
    completed = subprocess.run(
        command,
        cwd=repo_root(),
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    md_path, pdf_path = parse_report_paths(completed.stdout)
    return md_path, pdf_path


def run_dry_contract_v1(args: argparse.Namespace) -> int:
    run_dir = args.run_dir.resolve()
    report_date = args.date or date.today().isoformat()
    run_id = run_dir.name
    started_at = utc_now()
    init_artifact_tree(run_dir)

    state: dict[str, object] = {
        "status": "running",
        "mode": args.mode,
        "run_id": run_id,
        "bundle_id": bundle_id_for_path(run_dir),
        "host_key": default_host_key(),
        "warm_or_cold": "measured",
        "timing_history_path": str(shared_history_path_for_output(run_dir)),
        "timing_history_run_copy": str(per_run_history_path(run_dir)),
        "started_at": started_at,
        "updated_at": started_at,
        "control_head": "synthetic-local-control",
        "active_gate": "synthetic factor ids 5447-5450",
        "iteration": 0,
        "accepted_count": 0,
        "neutral_count": 0,
        "rejected_count": 0,
        "consecutive_no_accepted": 0,
        "epsilon": None,
        "ucb95_expected_delta": None,
        "noise_status": "ok",
        "noisy_candidate_count": 0,
        "last_exit_reason": None,
        "latest_report": None,
        "dry_run": True,
        "build_status": "dry_run",
        "compare_status": "simulated_pass",
        "timing_status": "screening_only",
        "sample_policy": SAMPLE_POLICY,
        "comparison_summary_path": str(run_dir / "comparison_summary.json"),
        "patch_queue_path": str(run_dir / "patch_queue.tsv"),
        "neutral_pool_path": str(run_dir / "neutral_pool.tsv"),
        "retry_conditions_path": str(run_dir / "retry_conditions.tsv"),
    }
    write_json(run_dir / "run_state.json", state)
    update_heartbeat(run_dir, "init", "artifact tree initialized")

    write_profile(run_dir)
    write_hotspots(run_dir)
    update_heartbeat(run_dir, "profile", "synthetic profile and hotspots written")

    attempts = synthetic_attempts(args.max_iterations)
    if not attempts:
        attempts = synthetic_attempts(1)
    if args.stall_limit <= len([attempt for attempt in attempts if attempt.verdict != "accepted"]):
        final_reason = "budget_stop"
        attempts = [
            Attempt(
                rank=attempt.rank,
                lane=attempt.lane,
                target=attempt.target,
                verdict=attempt.verdict,
                control_samples=attempt.control_samples,
                candidate_samples=attempt.candidate_samples,
                correctness=attempt.correctness,
                stop_reason=final_reason if index == len(attempts) - 1 else attempt.stop_reason,
                notes=attempt.notes,
            )
            for index, attempt in enumerate(attempts)
        ]

    attempt_rows = write_attempts(run_dir, attempts, "synthetic-local-control")
    write_patch_queue(run_dir, attempt_rows)
    write_cooldown(run_dir)
    write_neutral_pool(run_dir, attempts)
    write_logs_and_patches(run_dir)
    update_heartbeat(run_dir, "batch", "synthetic contract-v1 attempts written")

    timing_history_rows = history_rows_from_attempt_rows(
        attempt_rows,
        bundle_id=str(state["bundle_id"]),
        run_id=run_id,
        host_key=str(state["host_key"]),
        control_head=str(state["control_head"]),
        active_gate=str(state["active_gate"]),
        warm_or_cold=str(state["warm_or_cold"]),
        sample_unit="seconds_compat",
        source_attempts_path=str(run_dir / "attempts.tsv"),
        recorded_at=started_at,
        default_noise_flag="ok",
    )
    shared_history_path = shared_history_path_for_output(run_dir)
    per_run_history_out = per_run_history_path(run_dir)
    write_history_artifacts(shared_history_path, per_run_history_out, timing_history_rows)

    metrics = classify_stop(attempts, stall_limit=args.stall_limit, max_iterations=args.max_iterations)
    write_retry_conditions(run_dir, attempts, metrics)
    comparison_summary_path, comparison_summary = write_comparison_summary(run_dir, run_id, started_at, attempt_rows)
    write_charts(run_dir, attempts, metrics)
    update_heartbeat(run_dir, "charts", "runtime_convergence and convergence_decision written")

    state.update(
        {
            "status": "stopped",
            "updated_at": utc_now(),
            "iteration": len(attempts),
            "accepted_count": sum(1 for attempt in attempts if attempt.verdict == "accepted"),
            "neutral_count": sum(1 for attempt in attempts if attempt.verdict == "neutral"),
            "rejected_count": sum(1 for attempt in attempts if attempt.verdict == "rejected"),
            "consecutive_no_accepted": metrics["consecutive_no_accepted"],
            "epsilon": metrics["epsilon"],
            "ucb95_expected_delta": metrics["ucb95_expected_delta"],
            "noise_status": metrics["noise_status"],
            "noisy_candidate_count": metrics["noisy_candidate_count"],
            "last_exit_reason": metrics["last_exit_reason"],
            "supported_stop_reasons": metrics["supported_stop_reasons"],
            "timing_history_path": str(shared_history_path),
            "timing_history_run_copy": str(per_run_history_out),
            "build_status": "dry_run",
            "compare_status": "simulated_pass",
            "timing_status": comparison_summary.get("timing_verdict", "neutral"),
            "timing_verdict": comparison_summary.get("timing_verdict", "neutral"),
            "timing_verdict_reason": comparison_summary.get("timing_verdict_reason", ""),
            "comparison_decision": comparison_summary.get("decision", ""),
            "comparison_accepted": comparison_summary.get("accepted", False),
            "comparison_summary_path": str(comparison_summary_path),
            "sample_policy": SAMPLE_POLICY,
            "patch_queue_path": str(run_dir / "patch_queue.tsv"),
            "neutral_pool_path": str(run_dir / "neutral_pool.tsv"),
            "retry_conditions_path": str(run_dir / "retry_conditions.tsv"),
        }
    )
    write_json(run_dir / "run_state.json", state)

    md_path, pdf_path = generate_report(run_dir, report_date)
    state = read_json(run_dir / "run_state.json")
    state["updated_at"] = utc_now()
    state["latest_report"] = str(md_path)
    write_json(run_dir / "run_state.json", state)
    update_heartbeat(run_dir, "report", "performance optimization report written")

    print(f"run_dir={run_dir}")
    print(f"status={state['status']}")
    print(f"last_exit_reason={state['last_exit_reason']}")
    print(f"runtime_convergence={run_dir / 'charts' / 'runtime_convergence.png'}")
    print(f"convergence_decision={run_dir / 'charts' / 'convergence_decision.png'}")
    print(f"markdown={md_path}")
    print(f"pdf={pdf_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Psi contract-v1 automatic optimization harness.")
    parser.add_argument("--mode", choices=["headless", "interactive"], required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--batch-size", choices=["contract-v1"], required=True)
    parser.add_argument("--max-iterations", type=int, default=4)
    parser.add_argument("--max-hours", type=float, default=8.0)
    parser.add_argument("--stall-limit", type=int, default=3)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--date", default=date.today().isoformat())
    return parser


def auto_loop(args: argparse.Namespace) -> int:
    if not args.dry_run:
        raise SystemExit("Only --dry-run is implemented for local contract-v1 validation; remote execution is out of scope.")
    start = time.monotonic()
    result = run_dry_contract_v1(args)
    if (time.monotonic() - start) / 3600.0 > args.max_hours:
        state_path = args.run_dir / "run_state.json"
        state = read_json(state_path)
        state["status"] = "stopped"
        state["last_exit_reason"] = "budget_stop"
        state["updated_at"] = utc_now()
        write_json(state_path, state)
    return result


def main() -> int:
    args = build_parser().parse_args()
    return auto_loop(args)


if __name__ == "__main__":
    raise SystemExit(main())
