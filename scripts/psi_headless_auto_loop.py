#!/usr/bin/env python3
"""Closed-loop Psi headless auto-loop controller.

This script wraps the existing single-batch ``psi_headless_remote.sh`` executor
with a true closed-loop candidate generator. Each iteration:

1. Refreshes the control distribution from ``timing_history.tsv`` (current host
   window) and optionally triggers a screening batch if not enough samples
   exist.
2. Calls the three-lane candidate generator (evidence / insight / combination).
3. Selects the next candidate by lane priority and cooldown state.
4. Registers the candidate patch body through ``psi_patch_queue``.
5. Invokes the remote batch script with the candidate id so the remote side
   applies the patch, builds, compares, and times.
6. Records the resulting attempt row, neutral pool membership, retry condition
   and timing history upsert.
7. Judges a verdict: accepted / neutral / rejected / NOISY_PENDING at the
   candidate level. A candidate-level NOISY_PENDING does NOT stop the loop.

Only these reasons can stop the whole run:

- ``accepted`` (with first-accepted-stop on)
- ``budget_stop`` (max-hours / max-iterations / max-candidates reached)
- ``no_targets`` (all three lanes are empty or blocked)
- ``convergence_proven``
- ``remote_failed`` (unrecoverable, e.g. missing env)
- ``repeated_infra_failure``
- user stop file

The script is Windows-friendly. Bash is only required when running against the
actual remote batch script; ``--dry-run`` skips the bash call entirely and
generates synthetic per-candidate evidence so the orchestration layer itself
can be validated locally.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import shlex
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

# Local modules - both live under HFT-wf/scripts/.
from psi_candidate_generator import generate_candidates
from psi_patch_queue import (
    register_candidate as register_patch,
    set_status as set_patch_status,
    snapshot_worktree,
)
from psi_timing_analysis import validate_class_a
from psi_timing_history import (
    HISTORY_FIELDNAMES,
    default_host_key,
    read_history_rows,
    upsert_history_rows,
)

STOP_REASONS_GLOBAL = {
    "accepted",
    "budget_stop",
    "convergence_proven",
    "no_targets",
    "remote_failed",
    "repeated_infra_failure",
    "control_baseline_unhealthy",
    "user_stopped",
}

LANE_PRIORITY = ("evidence", "insight", "combination")
INFRA_FAILURES = {"missing_env_file", "runner_busy", "build_failed"}
FORBIDDEN_PATCH_PATH_PARTS = {
    "baseline",
    "benchmark",
    "benchmarks",
    "compare",
    "compare_gate",
    "dataset",
    "datasets",
    "output_schema",
}

PAIRED_NUMERIC_FIELDS = (
    "paired_sample_count",
    "median_delta_ms",
    "bootstrap_ci_low_ms",
    "bootstrap_ci_high_ms",
    "permutation_p_value",
    "paired_stdev_ms",
    "paired_range_ms",
    "paired_mean_ms",
)


def _coerce_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _merge_comparison_summary(batch_state: dict[str, Any], summary: dict[str, Any]) -> None:
    """Carry the remote comparison decision into the local candidate verdict surface."""

    for field in (
        "compare_result",
        "decision",
        "accepted",
        "lost_failure_count",
        "paired_evidence_status",
        "paired_evidence_reason",
        "timing_verdict",
        "timing_verdict_reason",
        "timing_verdict_method",
    ):
        if field in summary:
            batch_state[field] = summary[field]
    if summary.get("compare_result") and not batch_state.get("compare_status"):
        batch_state["compare_status"] = summary["compare_result"]
    if summary.get("correctness_status") == "pass" and not batch_state.get("compare_status"):
        batch_state["compare_status"] = "pass"
    if summary.get("timing_status") and not batch_state.get("timing_status"):
        batch_state["timing_status"] = summary["timing_status"]
    if summary.get("decision") == "promotion_candidate" and summary.get("accepted") is True:
        batch_state["timing_verdict"] = "accepted"
    paired = summary.get("paired") if isinstance(summary.get("paired"), dict) else {}
    for field in PAIRED_NUMERIC_FIELDS:
        value = paired.get(field, summary.get(field))
        if value is None:
            continue
        batch_state[field] = value
    if paired.get("noise_flag"):
        batch_state["noise_flag"] = paired["noise_flag"]
    if paired.get("paired_deltas_ms"):
        batch_state["paired_deltas_ms"] = paired["paired_deltas_ms"]
    if summary.get("control") and isinstance(summary["control"], dict):
        control = summary["control"]
        median = _coerce_float(control.get("median_ms"))
        if median is not None:
            batch_state["control_median_ms"] = median
    if isinstance(summary.get("case_deltas"), list):
        batch_state["twap_case_deltas"] = summary["case_deltas"]
    if isinstance(summary.get("timing_samples"), list):
        batch_state["twap_timing_samples"] = summary["timing_samples"]
FORBIDDEN_PATCH_FILENAMES = {
    "config.yaml",
    "config.yml",
    "factor_set.json",
    "factor_set.yaml",
    "factor_set.yml",
    "factor_list.json",
    "factor_list.yaml",
    "factor_list.yml",
}
FORBIDDEN_PATCH_SUFFIXES = {
    ".arrow",
    ".csv",
    ".feather",
    ".parquet",
}


# ------------------------------- I/O helpers -------------------------------


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_tsv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def write_tsv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def append_tsv_row(path: Path, row: dict[str, Any], fieldnames: list[str]) -> None:
    rows = read_tsv(path)
    rows.append(row)
    write_tsv(path, rows, fieldnames)


# ------------------------------- schemas -------------------------------

ATTEMPTS_FIELDS = [
    "iteration",
    "candidate_id",
    "lane",
    "target",
    "touched_files",
    "candidate_workspace",
    "patch_path",
    "semantic_risk",
    "build_status",
    "compare_status",
    "paired_sample_count",
    "timing_verdict",
    "sample_count",
    "samples_ms",
    "control_median_ms",
    "candidate_median_ms",
    "delta_ms",
    "twap_case_deltas",
    "twap_max_normal_regression_ms",
    "twap_max_stress_regression_ms",
    "compare_result",
    "noise_flag",
    "verdict",
    "retry_condition",
    "stop_reason",
    "recorded_at",
    "notes",
]
NEUTRAL_POOL_FIELDS = [
    "candidate_id",
    "lane",
    "patch_path",
    "touched_files",
    "hypothesis",
    "compare_result",
    "timing_summary",
    "semantic_risk",
    "stack_compatibility",
    "retry_condition",
    "recorded_at",
]
RETRY_CONDITIONS_FIELDS = [
    "target",
    "status",
    "noise_flag",
    "retry_after",
    "required_condition",
    "last_exit_reason",
    "notes",
]
COOLDOWN_FIELDS = [
    "target",
    "status",
    "cooldown_runs_remaining",
    "reason",
    "source_profile",
    "notes",
]
PROFILE_FIELDS = ["stage", "total_ms", "count", "avg_ms", "source", "touched_files", "symbols", "notes"]
HOTSPOT_FIELDS = [
    "rank",
    "stage",
    "total_ms",
    "avg_ms",
    "count",
    "score",
    "notes",
    "touched_files",
    "symbols",
    "expected_delta_seconds",
]


# ------------------------------- run root setup -------------------------------


def ensure_run_dir(run_dir: Path) -> None:
    for child in ("logs", "reports", "patches", "charts", "iterations"):
        (run_dir / child).mkdir(parents=True, exist_ok=True)
    defaults: dict[str, tuple[list[dict[str, Any]], list[str]]] = {
        "attempts.tsv": ([], ATTEMPTS_FIELDS),
        "neutral_pool.tsv": ([], NEUTRAL_POOL_FIELDS),
        "retry_conditions.tsv": ([], RETRY_CONDITIONS_FIELDS),
        "cooldown.tsv": ([], COOLDOWN_FIELDS),
        "profile.tsv": ([], PROFILE_FIELDS),
        "hotspots.tsv": ([], HOTSPOT_FIELDS),
        "timing_history.tsv": ([], HISTORY_FIELDNAMES),
    }
    for name, (rows, fields) in defaults.items():
        path = run_dir / name
        if not path.exists():
            write_tsv(path, rows, fields)


def update_heartbeat(run_dir: Path, phase: str, step: str) -> None:
    write_json(
        run_dir / "heartbeat.json",
        {
            "updated_at": utc_now(),
            "phase": phase,
            "current_step": step,
            "pid_or_session": str(os.getpid()),
            "last_log": str((run_dir / "logs" / "auto_loop.log").resolve()),
        },
    )


# ------------------------------- control distribution -------------------------------


def refresh_control_distribution(run_dir: Path, host_key: str, window: int = 20) -> dict[str, Any]:
    """Read recent control rows for the current host from timing_history.tsv."""

    rows = read_tsv(run_dir / "timing_history.tsv")
    control_rows = [
        row
        for row in rows
        if (row.get("kind") or "").strip() == "control"
        and (row.get("host_key") or "").strip() == host_key
    ]
    control_rows.sort(key=lambda r: r.get("recorded_at", ""))
    recent = control_rows[-window:] if control_rows else []
    medians: list[float] = []
    samples_ms_all: list[float] = []
    for row in recent:
        try:
            medians.append(float(row.get("median_ms") or 0.0))
        except (TypeError, ValueError):
            pass
        raw = (row.get("samples_ms") or "").strip()
        if raw:
            for part in raw.split(","):
                part = part.strip()
                if not part:
                    continue
                try:
                    samples_ms_all.append(float(part))
                except ValueError:
                    continue

    distribution: dict[str, Any] = {
        "host_key": host_key,
        "window_size": len(recent),
        "median_of_medians_ms": statistics.median(medians) if medians else None,
        "stdev_of_medians_ms": statistics.stdev(medians) if len(medians) > 1 else 0.0,
        "median_of_samples_ms": statistics.median(samples_ms_all) if samples_ms_all else None,
        "sample_count": len(samples_ms_all),
        "range_ms": (max(samples_ms_all) - min(samples_ms_all)) if len(samples_ms_all) > 1 else 0.0,
    }
    distribution["trusted"] = (
        distribution["sample_count"] >= 5
        and (distribution["stdev_of_medians_ms"] or 0.0) < 3000.0
    )
    return distribution


# ------------------------------- quiet retry gate -------------------------------


def _remote_runner_idle(args: argparse.Namespace) -> tuple[bool, str]:
    if not args.remote_host:
        return True, "local/no-remote-host"
    result = _ssh(args.remote_host, "pgrep -a -x PsiTraderRunner || true")
    active = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
    if active:
        return False, "; ".join(active[:3])
    return True, "no PsiTraderRunner process"


def quiet_retry_ready_targets(
    args: argparse.Namespace,
    run_dir: Path,
    control_distribution: dict[str, Any],
) -> set[str]:
    retry_rows = [
        row
        for row in read_tsv(run_dir / "retry_conditions.tsv")
        if (row.get("status") or "").strip() == "NOISY_PENDING"
    ]
    gate: dict[str, Any] = {
        "updated_at": utc_now(),
        "remote_host": args.remote_host or "",
        "enabled": bool(retry_rows),
        "control_distribution": control_distribution,
        "thresholds": {
            "min_control_samples": args.quiet_retry_min_control_samples,
            "control_stdev_ms": args.quiet_retry_control_stdev_ms,
            "control_range_ms": args.quiet_retry_control_range_ms,
        },
        "remote_idle": False,
        "remote_idle_reason": "",
        "ready_targets": [],
        "blocked": [],
    }
    if not retry_rows:
        write_json(run_dir / "quiet_retry_gate.json", gate)
        return set()

    idle, idle_reason = _remote_runner_idle(args)
    gate["remote_idle"] = idle
    gate["remote_idle_reason"] = idle_reason

    sample_count = int(control_distribution.get("sample_count") or 0)
    stdev_ms = float(control_distribution.get("stdev_of_medians_ms") or 0.0)
    range_ms = float(control_distribution.get("range_ms") or 0.0)
    control_ok = (
        sample_count >= args.quiet_retry_min_control_samples
        and stdev_ms <= args.quiet_retry_control_stdev_ms
        and range_ms <= args.quiet_retry_control_range_ms
    )

    ready: set[str] = set()
    for row in retry_rows:
        target = (row.get("target") or "").strip()
        if not target:
            continue
        if idle and control_ok:
            ready.add(target)
            gate["ready_targets"].append(target)
        else:
            reasons = []
            if not idle:
                reasons.append(f"remote_busy={idle_reason}")
            if sample_count < args.quiet_retry_min_control_samples:
                reasons.append(f"control_samples={sample_count}<{args.quiet_retry_min_control_samples}")
            if stdev_ms > args.quiet_retry_control_stdev_ms:
                reasons.append(f"control_stdev_ms={stdev_ms:.3f}>{args.quiet_retry_control_stdev_ms:.3f}")
            if range_ms > args.quiet_retry_control_range_ms:
                reasons.append(f"control_range_ms={range_ms:.3f}>{args.quiet_retry_control_range_ms:.3f}")
            gate["blocked"].append({"target": target, "reasons": reasons})
    write_json(run_dir / "quiet_retry_gate.json", gate)
    return ready


# ------------------------------- candidate selection -------------------------------


def pick_next_candidate(
    lanes: dict[str, list[dict[str, Any]]],
    *,
    seen_candidate_ids: set[str],
    cooldown_targets: set[str],
) -> dict[str, Any] | None:
    for lane in LANE_PRIORITY:
        for candidate in lanes.get(lane, []):
            if candidate["candidate_id"] in seen_candidate_ids:
                continue
            if candidate["target"] in cooldown_targets:
                continue
            return candidate
    return None


def lanes_are_empty(lanes: dict[str, list[dict[str, Any]]]) -> bool:
    return not any(lanes.get(lane) for lane in LANE_PRIORITY)


def _normalize_seed_candidate(raw: dict[str, Any], lane: str, index: int) -> dict[str, Any]:
    candidate = dict(raw)
    candidate.setdefault("lane", lane)
    candidate.setdefault("candidate_id", f"{lane}_seed_{index + 1}")
    candidate.setdefault("target", candidate["candidate_id"])
    candidate.setdefault("hypothesis", candidate.get("target", candidate["candidate_id"]))
    candidate.setdefault("expected_effect", "seeded candidate")
    candidate.setdefault("semantic_risk", "medium")
    candidate.setdefault("touched_files", [])
    candidate.setdefault("source_evidence", {"kind": "seed_file"})
    candidate.setdefault("stack_members", [])
    candidate.setdefault("stack_compatibility", "single")
    candidate.setdefault("rank_score", 0.0)
    candidate.setdefault("change_class", "class_b")
    candidate.setdefault("replicated", False)

    missing = [
        field
        for field in ("candidate_id", "lane", "target", "hypothesis", "expected_effect", "semantic_risk")
        if not str(candidate.get(field) or "").strip()
    ]
    if missing:
        raise ValueError(f"seed candidate missing required fields {missing}: {raw!r}")
    if candidate["lane"] not in LANE_PRIORITY:
        raise ValueError(f"seed candidate has unsupported lane {candidate['lane']!r}: {candidate['candidate_id']}")
    if not isinstance(candidate.get("touched_files"), list):
        raise ValueError(f"seed candidate touched_files must be a list: {candidate['candidate_id']}")
    if not isinstance(candidate.get("source_evidence"), dict):
        candidate["source_evidence"] = {"kind": "seed_file", "raw": candidate["source_evidence"]}
    return candidate


def apply_change_class_policy(candidate: dict[str, Any]) -> None:
    requested = str(candidate.get("change_class") or "class_b").strip().lower()
    if requested != "class_a":
        candidate["change_class"] = "class_b"
        candidate.setdefault("class_a_validation_reason", "")
        return

    valid, reason = validate_class_a(
        hypothesis=str(candidate.get("hypothesis") or ""),
        change_notes=" ".join(
            str(candidate.get(key) or "")
            for key in ("expected_effect", "source_evidence", "class_a_notes")
        ),
        touched_files=[str(path) for path in candidate.get("touched_files", [])],
        candidate_id=str(candidate.get("candidate_id") or ""),
    )
    candidate["class_a_validation_reason"] = reason
    if valid:
        candidate["change_class"] = "class_a"
    else:
        candidate["change_class"] = "class_b"


def load_candidate_seed_file(path: Path) -> dict[str, list[dict[str, Any]]]:
    payload = read_json(path)
    lanes: dict[str, list[dict[str, Any]]] = {lane: [] for lane in LANE_PRIORITY}
    if not payload:
        return lanes

    if isinstance(payload.get("candidates"), list):
        for index, raw in enumerate(payload["candidates"]):
            if not isinstance(raw, dict):
                raise ValueError(f"seed candidate #{index + 1} is not an object")
            lane = str(raw.get("lane") or "evidence")
            candidate = _normalize_seed_candidate(raw, lane, index)
            lanes[candidate["lane"]].append(candidate)
        return lanes

    for lane in LANE_PRIORITY:
        items = payload.get(lane, [])
        if items is None:
            items = []
        if not isinstance(items, list):
            raise ValueError(f"seed lane {lane!r} must be a list")
        for index, raw in enumerate(items):
            if not isinstance(raw, dict):
                raise ValueError(f"seed lane {lane!r} item #{index + 1} is not an object")
            candidate = _normalize_seed_candidate(raw, lane, index)
            lanes[candidate["lane"]].append(candidate)
    return lanes


# ------------------------------- patch materialization -------------------------------


def _source_root(args: argparse.Namespace) -> Path:
    root = args.source_root or args.root or os.environ.get("ROOT") or repo_root()
    return Path(root).resolve()


def _candidate_workspace(args: argparse.Namespace, run_dir: Path, candidate: dict[str, Any]) -> Path:
    if args.candidate_workspace:
        base = Path(args.candidate_workspace)
        return (base if base.is_absolute() else run_dir / base).resolve()
    return (run_dir / "candidate_workspaces" / candidate["candidate_id"]).resolve()


def _git(workspace: Path, args: list[str], *, text: bool = True) -> subprocess.CompletedProcess:
    kwargs: dict[str, Any] = {
        "cwd": str(workspace),
        "check": False,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
    }
    if text:
        kwargs.update({"text": True, "encoding": "utf-8", "errors": "replace"})
    return subprocess.run(["git", *args], **kwargs)


def _git_head(root: Path) -> str:
    result = _git(root, ["rev-parse", "--short", "HEAD"])
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def _normalize_patch_path(path: str) -> str:
    return path.replace("\\", "/").strip("/")


def _boundary_violation(path: str) -> str:
    clean = _normalize_patch_path(path)
    if not clean:
        return "empty patch path"
    candidate_path = Path(clean)
    if candidate_path.is_absolute() or ".." in candidate_path.parts or ".git" in candidate_path.parts:
        return "patch path escapes the candidate workspace"
    lowered_parts = {part.lower() for part in candidate_path.parts}
    lowered_name = candidate_path.name.lower()
    lowered_suffix = candidate_path.suffix.lower()
    if lowered_name in FORBIDDEN_PATCH_FILENAMES:
        return "benchmark/config/factor-set boundary"
    if lowered_suffix in FORBIDDEN_PATCH_SUFFIXES:
        return "baseline data/output artifact boundary"
    blocked_parts = lowered_parts & FORBIDDEN_PATCH_PATH_PARTS
    if blocked_parts:
        return f"fixed boundary path component: {sorted(blocked_parts)[0]}"
    return ""


def _changed_files(workspace: Path) -> tuple[list[str], str]:
    result = _git(workspace, ["-c", "core.quotePath=false", "diff", "--cached", "--name-only", "-z", "HEAD"], text=False)
    if result.returncode != 0:
        return [], result.stderr.decode("utf-8", errors="replace").strip()
    raw_paths = result.stdout.decode("utf-8", errors="replace").split("\0")
    return [_normalize_patch_path(path) for path in raw_paths if path.strip()], ""


def _validate_patch_boundaries(changed_files: list[str]) -> list[str]:
    violations: list[str] = []
    for path in changed_files:
        reason = _boundary_violation(path)
        if reason:
            violations.append(f"{path}: {reason}")
    return violations


def _validate_patch_semantic_guards(patch_text: str) -> list[str]:
    """Reject known unsafe TWAP optimization shapes before remote timing.

    The performance harness is allowed to test small local optimizations, but
    it should not spend time on patches that change per-user push semantics.
    """

    added_lines = [
        line[1:].strip()
        for line in patch_text.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    ]
    removed_lines = [
        line[1:].strip()
        for line in patch_text.splitlines()
        if line.startswith("-") and not line.startswith("---")
    ]
    added_text = "\n".join(added_lines)
    removed_text = "\n".join(removed_lines)
    lower_added = added_text.lower()
    lower_removed = removed_text.lower()
    violations: list[str] = []

    reuses_push_message = (
        "buildtwapsaleaggregationpushmessage(userid, stock_code, cmd)" in lower_added
        and (
            "cached_push" in lower_added
            or "stock_push_message" in lower_added
            or "has_cached" in lower_added
            or "initialized" in lower_added
        )
        and re.search(r"\bTwapSalePushMessage\s+\w+", added_text) is not None
    )
    if reuses_push_message:
        violations.append(
            "unsafe TWAP push-message reuse: aggregation push payload is user/request dependent"
        )

    if "static TwapSalePushMessage" in added_text:
        violations.append("unsafe static TwapSalePushMessage cache in push path")

    removes_stock_change_cache = (
        "twapsalepushmessage stock_change_message" in lower_removed
        and "has_stock_change_message" in lower_removed
        and "buildtwapsaleaggregationpushmessage(userid, stock_code, cmd)" in lower_removed
    )
    rebuilds_push_message_per_session = (
        "buildtwapsaleaggregationpushmessage(userid, stock_code, cmd)" in lower_added
        and "stock_change_message" not in lower_added
    )
    if removes_stock_change_cache and rebuilds_push_message_per_session:
        violations.append(
            "unsafe TWAP fanout regression: keeps one-session timing by rebuilding stock push message inside the session loop"
        )

    return violations


def _remote_quote(value: str | Path) -> str:
    return shlex.quote(str(value))


def _remote_join(root: str, *parts: str) -> str:
    out = root.rstrip("/")
    for part in parts:
        clean = str(part).strip("/")
        if clean:
            out += "/" + clean
    return out


def _remote_run_root(args: argparse.Namespace, run_dir: Path) -> str:
    return args.remote_run_dir or _remote_join(args.remote_run_root, run_dir.name)


def _remote_iteration_dir(args: argparse.Namespace, run_dir: Path, candidate: dict[str, Any], iteration: int) -> str:
    return _remote_join(
        _remote_run_root(args, run_dir),
        "iterations",
        f"iter_{iteration:03d}_{candidate['candidate_id']}",
    )


def _remote_candidate_workspace(args: argparse.Namespace, run_dir: Path, candidate: dict[str, Any]) -> str:
    return _remote_join(
        args.remote_candidate_workspace_root or _remote_join(_remote_run_root(args, run_dir), "candidate_workspaces"),
        candidate["candidate_id"],
    )


def _remote_default_build_dir(root: str) -> str:
    return _remote_join(root, "build/linux-relwithdebinfo-boost182")


def _remote_default_runner(build_dir: str) -> str:
    return _remote_join(build_dir, "build_x64/RelWithDebInfo/bin/PsiTraderRunner/PsiTraderRunner")


def _ssh(remote_host: str, command: str, *, text: bool = True) -> subprocess.CompletedProcess:
    kwargs: dict[str, Any] = {
        "check": False,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
    }
    if text:
        kwargs.update({"text": True, "encoding": "utf-8", "errors": "replace"})
    return subprocess.run(["ssh", remote_host, command], **kwargs)


def _sync_candidate_workspace_to_remote(
    args: argparse.Namespace,
    run_dir: Path,
    candidate: dict[str, Any],
) -> tuple[str, str]:
    workspace_raw = candidate.get("candidate_workspace") or ""
    if not workspace_raw:
        return "", "candidate workspace is missing"
    workspace = Path(workspace_raw)
    if not workspace.exists():
        return "", f"candidate workspace does not exist: {workspace}"

    remote_ws = _remote_candidate_workspace(args, run_dir, candidate)
    with tempfile.TemporaryDirectory(prefix="psi_candidate_sync_") as tmp:
        archive_base = Path(tmp) / candidate["candidate_id"]
        archive_path = Path(shutil.make_archive(str(archive_base), "gztar", root_dir=workspace))
        remote_archive = f"/tmp/{archive_path.name}"
        scp = subprocess.run(
            ["scp", str(archive_path), f"{args.remote_host}:{remote_archive}"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if scp.returncode != 0:
            return "", f"scp candidate workspace failed: {scp.stderr.strip()}"

    parent = str(Path(remote_ws).parent).replace("\\", "/")
    unpack = (
        f"rm -rf {_remote_quote(remote_ws)} && "
        f"mkdir -p {_remote_quote(parent)} {_remote_quote(remote_ws)} && "
        f"tar -xzf {_remote_quote(remote_archive)} -C {_remote_quote(remote_ws)} && "
        f"rm -f {_remote_quote(remote_archive)}"
    )
    result = _ssh(args.remote_host, unpack)
    if result.returncode != 0:
        return "", f"remote candidate workspace unpack failed: {result.stderr.strip() or result.stdout.strip()}"
    return remote_ws, ""


def _prepare_workspace(source_root: Path, workspace: Path, *, refresh: bool) -> tuple[bool, str]:
    if workspace.exists():
        if not refresh:
            return True, ""
        shutil.rmtree(workspace)
    try:
        ignore = shutil.ignore_patterns(
            ".git",
            ".codex_build",
            ".gatekeeper_worktrees",
            ".trellis",
            ".trellis-backup*",
            "build",
            "gatekeeper_runs",
            "experiments",
            "headless_runs",
        )
        shutil.copytree(source_root, workspace, ignore=ignore)
    except Exception as exc:
        return False, f"candidate workspace copy failed: {exc}"
    init = _git(workspace, ["init"])
    if init.returncode != 0:
        return False, f"candidate workspace git init failed: {init.stderr.strip()}"
    subprocess.run(
        ["git", "config", "user.email", "psi-auto-loop@example.invalid"],
        cwd=str(workspace),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Psi Auto Loop"],
        cwd=str(workspace),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    add = _git(workspace, ["add", "-A"])
    if add.returncode != 0:
        return False, f"candidate workspace git add failed: {add.stderr.strip()}"
    commit = _git(workspace, ["commit", "-m", "psi candidate workspace base"])
    if commit.returncode != 0:
        return False, f"candidate workspace git commit failed: {commit.stderr.strip()}"
    return True, ""


def _run_builtin_patch_command(command: str, workspace: Path, candidate: dict[str, Any]) -> tuple[int, str]:
    if command in {"builtin:noop", "noop"}:
        return 0, "builtin noop produced no workspace changes"
    if command in {"builtin:fake-nonempty", "fake-nonempty"}:
        touched = candidate.get("touched_files") or ["psi_candidate_patch_probe.txt"]
        target = workspace / str(touched[0])
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(f"\n// psi-auto-loop materialized {candidate['candidate_id']}\n")
        return 0, f"builtin fake-nonempty touched {target.relative_to(workspace)}"
    return 127, f"unknown builtin patch command: {command}"


def _run_external_patch_command(
    command: str,
    run_dir: Path,
    workspace: Path,
    source_root: Path,
    candidate: dict[str, Any],
    iteration: int,
    candidate_ledger: str = "",
) -> tuple[int, str]:
    env = os.environ.copy()
    env.update(
        {
            "PSI_CANDIDATE_ID": candidate["candidate_id"],
            "PSI_CANDIDATE_LANE": candidate["lane"],
            "PSI_CANDIDATE_TARGET": candidate["target"],
            "PSI_CANDIDATE_TOUCHED_FILES": "|".join(candidate.get("touched_files", [])),
            "PSI_CANDIDATE_METADATA_JSON": json.dumps(candidate, ensure_ascii=False),
            "PSI_CANDIDATE_WORKSPACE": str(workspace),
            "PSI_SOURCE_ROOT": str(source_root),
            "PSI_RUN_DIR": str(run_dir),
            "PSI_ITERATION": str(iteration),
            "PSI_CANDIDATE_LEDGER": candidate_ledger,
        }
    )
    completed = subprocess.run(
        command,
        cwd=workspace,
        env=env,
        shell=True,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return completed.returncode, completed.stdout


def materialize_candidate_patch(
    args: argparse.Namespace,
    run_dir: Path,
    candidate: dict[str, Any],
    iteration: int,
) -> tuple[bool, dict[str, Any], str]:
    source_root = _source_root(args)
    workspace = _candidate_workspace(args, run_dir, candidate)
    touched_files = [str(path) for path in candidate.get("touched_files", []) if str(path).strip()]
    metadata = {
        "source_root": str(source_root),
        "candidate_workspace": str(workspace),
        "patch_path": str(run_dir / "patches" / f"{candidate['candidate_id']}.patch"),
        "touched_files": touched_files,
        "base_commit": _git_head(source_root),
        "patch_command": args.patch_command,
        "patch_command_rc": None,
    }

    def fail(reason: str, patch_body: bytes | str = b"") -> tuple[bool, dict[str, Any], str]:
        register_patch(
            run_dir,
            candidate_id=candidate["candidate_id"],
            lane=candidate["lane"],
            hypothesis=candidate["hypothesis"],
            target=candidate["target"],
            touched_files=touched_files,
            semantic_risk=candidate["semantic_risk"],
            stack_members=candidate.get("stack_members") or [],
            base_commit=str(metadata["base_commit"]),
            revert_method="not applied; materialization failed",
            patch_body=patch_body,
            status="failed",
            candidate_workspace=str(workspace),
            patch_command=str(metadata.get("patch_command") or ""),
            materialization_status="failed",
            materialization_reason=reason,
            patch_command_rc=metadata.get("patch_command_rc"),
            patch_source="candidate_workspace_git_diff",
        )
        return False, metadata, reason

    if not source_root.exists():
        return fail(f"source root does not exist: {source_root}")
    ok, reason = _prepare_workspace(source_root, workspace, refresh=not args.reuse_candidate_workspace)
    if not ok:
        return fail(reason)

    command = args.patch_command or os.environ.get("PSI_PATCH_COMMAND", "builtin:noop")
    metadata["patch_command"] = command
    if command.startswith("builtin:") or command in {"noop", "fake-nonempty"}:
        rc, output = _run_builtin_patch_command(command, workspace, candidate)
    else:
        rc, output = _run_external_patch_command(
            command,
            run_dir,
            workspace,
            source_root,
            candidate,
            iteration,
            str(args.candidate_ledger) if getattr(args, "candidate_ledger", "") else "",
        )
    metadata["patch_command_rc"] = rc
    log_path = run_dir / "logs" / f"iter_{iteration:03d}_{candidate['candidate_id']}_patch_command.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(output or "", encoding="utf-8")
    metadata["patch_command_log"] = str(log_path)
    if rc != 0:
        return fail(f"patch command failed rc={rc}")

    add = _git(workspace, ["add", "-A"], text=False)
    if add.returncode != 0:
        return fail(f"candidate workspace git add failed: {add.stderr.decode('utf-8', errors='replace').strip()}")
    actual_touched_files, changed_error = _changed_files(workspace)
    if changed_error:
        return fail(f"git changed-file scan failed: {changed_error}")
    if not actual_touched_files:
        return fail("patch materialization produced an empty diff")
    boundary_violations = _validate_patch_boundaries(actual_touched_files)
    if boundary_violations:
        return fail("patch violates fixed boundary: " + "; ".join(boundary_violations))
    diff_cmd = ["-c", "core.quotePath=false", "diff", "--cached", "--binary", "HEAD"]
    diff = _git(workspace, diff_cmd, text=False)
    if diff.returncode != 0:
        return fail(f"git diff failed: {diff.stderr.decode('utf-8', errors='replace').strip()}")
    patch_body = diff.stdout
    if not patch_body.strip():
        return fail("patch materialization produced an empty diff", patch_body)
    semantic_violations = _validate_patch_semantic_guards(
        patch_body.decode("utf-8", errors="replace")
    )
    if semantic_violations:
        return fail("patch violates semantic guard: " + "; ".join(semantic_violations), patch_body)

    register_patch(
        run_dir,
        candidate_id=candidate["candidate_id"],
        lane=candidate["lane"],
        hypothesis=candidate["hypothesis"],
        target=candidate["target"],
        touched_files=actual_touched_files,
        semantic_risk=candidate["semantic_risk"],
        stack_members=candidate.get("stack_members") or [],
        base_commit=str(metadata["base_commit"]),
        revert_method="git apply -R <patch_path>",
        patch_body=patch_body,
        status="pending",
        candidate_workspace=str(workspace),
        patch_command=command,
        materialization_status="materialized",
        materialization_reason="non-empty git diff captured from candidate workspace",
        patch_command_rc=rc,
        patch_source="candidate_workspace_git_diff",
    )
    candidate["touched_files"] = actual_touched_files
    candidate["candidate_workspace"] = str(workspace)
    candidate["patch_path"] = str((run_dir / "patches" / f"{candidate['candidate_id']}.patch").resolve())
    candidate["base_commit"] = str(metadata["base_commit"])
    return True, metadata, ""


# ------------------------------- remote batch invocation -------------------------------


def call_remote_batch(
    args: argparse.Namespace,
    run_dir: Path,
    candidate: dict[str, Any],
    iteration: int,
) -> tuple[int, Path, dict[str, Any]]:
    """Invoke ``psi_headless_remote.sh`` for one candidate.

    In dry-run mode, this simulates the remote pipeline by writing synthetic
    evidence into an iteration subdirectory. Otherwise it shells out with
    environment overrides matching the remote script's contract.
    """

    iteration_dir = run_dir / "iterations" / f"iter_{iteration:03d}_{candidate['candidate_id']}"
    iteration_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "logs" / f"iter_{iteration:03d}.log"

    if args.dry_run:
        return _dry_run_remote_batch(run_dir, iteration_dir, candidate, iteration)

    if args.remote_host:
        return call_ssh_remote_batch(args, run_dir, iteration_dir, candidate, iteration)

    env = os.environ.copy()
    env.update(
        {
            "RUN_ID": f"{run_dir.name}_iter_{iteration:03d}",
            "RUN_DIR": str(iteration_dir),
            "HEADLESS_CONTROL_DIR": str(iteration_dir),
            "GENERATE_REPORT": "0",
            "MEASURE_RUNS": str(args.measure_runs),
            "NO_COMPARE_RUNS": str(args.no_compare_runs),
            "CANDIDATE_ID": candidate["candidate_id"],
            "CANDIDATE_LANE": candidate["lane"],
            "CANDIDATE_TARGET": candidate["target"],
            "CANDIDATE_TOUCHED_FILES": "|".join(candidate.get("touched_files", [])),
            "CANDIDATE_REPLICATED": "1" if candidate.get("replicated") else "",
            "CHANGE_CLASS": str(candidate.get("change_class", "class_b")),
        }
    )
    for name in ("ROOT", "ENV_FILE", "BUILD_DIR", "RUNNER", "CANDIDATE_RUNNER", "CONFIG", "OUTPUT_DIR"):
        value = getattr(args, name.lower(), None)
        if value:
            env[name] = str(value)
    if args.candidate_runner:
        env["CANDIDATE_RUNNER"] = str(args.candidate_runner)

    # When a candidate workspace was materialized, override ROOT so the remote
    # builds from the patched source, not the original. Clear CANDIDATE_RUNNER
    # and BUILD_DIR so the remote rebuilds from the workspace.
    candidate_ws = candidate.get("candidate_workspace")
    if candidate_ws and Path(candidate_ws).exists():
        env["ROOT"] = str(candidate_ws)
        env.pop("BUILD_DIR", None)
        env.pop("CANDIDATE_RUNNER", None)

    script = args.batch_script.resolve()
    with log_path.open("w", encoding="utf-8") as handle:
        handle.write(f"iteration={iteration}\n")
        handle.write(f"candidate_id={candidate['candidate_id']}\n")
        handle.write(f"lane={candidate['lane']}\n")
        handle.flush()
        result = subprocess.run(
            [args.bash, str(script)],
            cwd=repo_root(),
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
        )
    batch_state = read_json(iteration_dir / "run_state.json")
    return result.returncode, iteration_dir, batch_state


def call_ssh_remote_batch(
    args: argparse.Namespace,
    run_dir: Path,
    iteration_dir: Path,
    candidate: dict[str, Any],
    iteration: int,
) -> tuple[int, Path, dict[str, Any]]:
    """Run the batch on a remote host after syncing the local candidate workspace."""

    log_path = run_dir / "logs" / f"iter_{iteration:03d}.log"
    remote_iter_dir = _remote_iteration_dir(args, run_dir, candidate, iteration)
    remote_ws = ""
    sync_reason = ""
    candidate_ws = candidate.get("candidate_workspace")
    if candidate_ws:
        remote_ws, sync_reason = _sync_candidate_workspace_to_remote(args, run_dir, candidate)
        if sync_reason:
            log_path.write_text(sync_reason + "\n", encoding="utf-8")
            return 1, iteration_dir, {
                "status": "stopped",
                "batch_status": "failed",
                "iteration": iteration,
                "candidate_id": candidate["candidate_id"],
                "lane": candidate["lane"],
                "target": candidate["target"],
                "build_status": "not_run",
                "compare_status": "not_run",
                "timing_status": "remote_sync_failed",
                "timing_verdict": "remote_sync_failed",
                "comparison_accepted": False,
                "paired_sample_count": 0,
                "patch_materialization_status": "materialized",
                "remote_sync_status": "failed",
                "remote_sync_reason": sync_reason,
            }
        candidate["remote_candidate_workspace"] = remote_ws

    remote_env = {
        "RUN_ID": f"{run_dir.name}_iter_{iteration:03d}",
        "RUN_DIR": remote_iter_dir,
        "HEADLESS_CONTROL_DIR": remote_iter_dir,
        "GENERATE_REPORT": "0",
        "MEASURE_RUNS": str(args.measure_runs),
        "NO_COMPARE_RUNS": str(args.no_compare_runs),
        "CANDIDATE_ID": candidate["candidate_id"],
        "CANDIDATE_LANE": candidate["lane"],
        "CANDIDATE_TARGET": candidate["target"],
        "CANDIDATE_TOUCHED_FILES": "|".join(candidate.get("touched_files", [])),
        "CANDIDATE_REPLICATED": "1" if candidate.get("replicated") else "",
        "CHANGE_CLASS": str(candidate.get("change_class", "class_b")),
    }
    for name in ("ENV_FILE", "RUNNER", "CONFIG", "OUTPUT_DIR"):
        value = getattr(args, name.lower(), None)
        if value:
            remote_env[name] = str(value)
    if remote_ws:
        remote_env["ROOT"] = remote_ws
        candidate_build_dir = _remote_default_build_dir(remote_ws)
        remote_env["BUILD_DIR"] = candidate_build_dir
        remote_env["CANDIDATE_RUNNER"] = _remote_default_runner(candidate_build_dir)
        if args.control_root or args.root:
            remote_env["CONTROL_ROOT"] = str(args.control_root or args.root)
        if not args.runner:
            control_root = str(args.root or "/root/work/Code1/psi-trader-liangjunming")
            remote_env["RUNNER"] = _remote_default_runner(_remote_default_build_dir(control_root))
    elif args.root:
        remote_env["ROOT"] = str(args.root)
    if args.candidate_runner and not remote_ws:
        remote_env["CANDIDATE_RUNNER"] = str(args.candidate_runner)
    if args.build_dir and not remote_ws:
        remote_env["BUILD_DIR"] = str(args.build_dir)
    if args.twap_endpoint:
        remote_env["ENDPOINT"] = str(args.twap_endpoint)
    if args.twap_user_id:
        remote_env["USER_ID"] = str(args.twap_user_id)
    if args.twap_measure_cases:
        remote_env["MEASURE_CASES"] = str(args.twap_measure_cases)
    if args.twap_subscriber_counts:
        remote_env["SUBSCRIBER_COUNTS"] = str(args.twap_subscriber_counts)
    if args.twap_build_targets:
        remote_env["BUILD_TARGETS"] = str(args.twap_build_targets)
    if args.twap_correctness_mode:
        remote_env["TWAP_CORRECTNESS_MODE"] = str(args.twap_correctness_mode)
    if args.twap_account_desc_check:
        remote_env["TWAP_ACCOUNT_DESC_CHECK"] = str(args.twap_account_desc_check)

    env_prefix = " ".join(f"{key}={_remote_quote(value)}" for key, value in remote_env.items())
    remote_batch_script = args.remote_batch_script or str(args.batch_script)
    command = (
        f"cd {_remote_quote(args.remote_hft_root)} && "
        f"{env_prefix} {_remote_quote(args.bash)} {_remote_quote(remote_batch_script)}"
    )
    with log_path.open("w", encoding="utf-8") as handle:
        handle.write(f"iteration={iteration}\n")
        handle.write(f"candidate_id={candidate['candidate_id']}\n")
        handle.write(f"lane={candidate['lane']}\n")
        handle.write(f"remote_host={args.remote_host}\n")
        handle.write(f"remote_run_dir={remote_iter_dir}\n")
        if remote_ws:
            handle.write(f"remote_candidate_workspace={remote_ws}\n")
        handle.flush()
        result = _ssh(args.remote_host, command)
        handle.write(result.stdout or "")
        handle.write(result.stderr or "")

    state_result = _ssh(args.remote_host, f"cat {_remote_quote(_remote_join(remote_iter_dir, 'run_state.json'))} 2>/dev/null || true")
    batch_state: dict[str, Any] = {}
    if state_result.stdout.strip():
        try:
            batch_state = json.loads(state_result.stdout)
            write_json(iteration_dir / "remote_run_state.json", batch_state)
        except json.JSONDecodeError:
            batch_state = {}
    summary_result = _ssh(
        args.remote_host,
        f"cat {_remote_quote(_remote_join(remote_iter_dir, 'comparison_summary.json'))} 2>/dev/null || true",
    )
    if summary_result.stdout.strip():
        try:
            comparison_summary = json.loads(summary_result.stdout)
            write_json(iteration_dir / "remote_comparison_summary.json", comparison_summary)
            _merge_comparison_summary(batch_state, comparison_summary)
        except json.JSONDecodeError:
            pass
    batch_state.setdefault("remote_host", args.remote_host)
    batch_state.setdefault("remote_run_dir", remote_iter_dir)
    if remote_ws:
        batch_state.setdefault("remote_candidate_workspace", remote_ws)
    return result.returncode, iteration_dir, batch_state


def _dry_run_remote_batch(
    run_dir: Path,
    iteration_dir: Path,
    candidate: dict[str, Any],
    iteration: int,
) -> tuple[int, Path, dict[str, Any]]:
    """Deterministic synthetic batch output for local smoke tests."""

    rng = random.Random(
        f"{run_dir.name}|{candidate['candidate_id']}|{iteration}".encode("utf-8")
    )
    base_ms = 48337.0
    control_samples = [base_ms + rng.uniform(-120.0, 180.0) for _ in range(5)]
    candidate_samples = [base_ms + rng.uniform(-240.0, 90.0) for _ in range(5)]
    median_control = statistics.median(control_samples)
    median_candidate = statistics.median(candidate_samples)
    delta_ms = median_control - median_candidate

    # pick a verdict for dry-run rotation:
    # iteration 1 accepts, iteration 2 neutrals, iteration 3 noisy, later rejects
    cycle = iteration % 4
    if cycle == 1:
        # accepted: candidate clearly faster
        candidate_samples = [value - 800.0 for value in candidate_samples]
        median_candidate = statistics.median(candidate_samples)
        delta_ms = median_control - median_candidate
        compare_result = "pass"
        noise_flag = "ok"
        verdict = "accepted"
    elif cycle == 2:
        # neutral: compare passes, small delta
        compare_result = "pass"
        noise_flag = "ok"
        verdict = "neutral"
    elif cycle == 3:
        # noisy: inject wide jitter
        candidate_samples = [value + rng.uniform(-3500.0, 3500.0) for value in candidate_samples]
        compare_result = "pass"
        noise_flag = "NOISY"
        verdict = "NOISY_PENDING"
        delta_ms = median_control - statistics.median(candidate_samples)
    else:
        # rejected: candidate slower
        candidate_samples = [value + 900.0 for value in candidate_samples]
        median_candidate = statistics.median(candidate_samples)
        delta_ms = median_control - median_candidate
        compare_result = "pass"
        noise_flag = "ok"
        verdict = "rejected"

    batch_state = {
        "status": "stopped",
        "batch_status": "completed",
        "iteration": iteration,
        "candidate_id": candidate["candidate_id"],
        "lane": candidate["lane"],
        "target": candidate["target"],
        "build_status": "pass",
        "compare_status": compare_result,
        "timing_status": verdict,
        "comparison_accepted": verdict == "accepted",
        "control_samples_ms": control_samples,
        "candidate_samples_ms": candidate_samples,
        "control_median_ms": median_control,
        "candidate_median_ms": median_candidate,
        "delta_ms": delta_ms,
        "noise_flag": noise_flag,
        "dry_run": True,
    }
    write_json(iteration_dir / "run_state.json", batch_state)
    (iteration_dir / "logs").mkdir(exist_ok=True)
    (iteration_dir / "logs" / "dry_run.log").write_text(
        f"dry-run iteration {iteration} candidate {candidate['candidate_id']}\n",
        encoding="utf-8",
    )
    (iteration_dir / "summary.txt").write_text(
        f"verdict={verdict}\ndelta_ms={delta_ms:.3f}\n", encoding="utf-8"
    )
    return 0, iteration_dir, batch_state


# ------------------------------- verdict application -------------------------------


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _twap_batch_stats(batch_state: dict[str, Any]) -> dict[str, Any]:
    case_deltas = batch_state.get("twap_case_deltas")
    if not isinstance(case_deltas, list):
        return {
            "case_deltas": [],
            "case_count": 0,
            "control_p95_ms": [],
            "candidate_p95_ms": [],
            "p95_benefit_ms": [],
            "case_delta_summary": "",
            "control_lost_total": 0,
            "candidate_lost_total": 0,
            "control_unknown_push_total": 0,
            "candidate_unknown_push_total": 0,
            "max_normal_regression_ms": None,
            "max_stress_regression_ms": None,
            "max_normal_regression_ms_text": "",
            "max_stress_regression_ms_text": "",
        }

    control_p95: list[float] = []
    candidate_p95: list[float] = []
    p95_benefit: list[float] = []
    normal_regressions: list[float] = []
    stress_regressions: list[float] = []
    control_lost_total = 0
    candidate_lost_total = 0
    control_unknown_push_total = 0
    candidate_unknown_push_total = 0
    rendered: list[str] = []

    def case_interval_ms(case_name: str) -> int | None:
        case_base = case_name.split("_s", 1)[0]
        for part in case_base.split("_"):
            if part.startswith("i"):
                try:
                    return int(part[1:])
                except ValueError:
                    return None
        return None

    for row in case_deltas:
        if not isinstance(row, dict):
            continue
        case = str(row.get("case") or "")
        ctrl = _float_or_none(row.get("control_p95_ms"))
        cand = _float_or_none(row.get("candidate_p95_ms"))
        raw_delta = _float_or_none(row.get("p95_delta_ms"))
        control_lost = _float_or_none(row.get("control_lost"))
        candidate_lost = _float_or_none(row.get("candidate_lost"))
        control_unknown_pushes = _float_or_none(row.get("control_unknown_pushes"))
        candidate_unknown_pushes = _float_or_none(row.get("candidate_unknown_pushes"))
        if control_lost is not None:
            control_lost_total += int(control_lost)
        if candidate_lost is not None:
            candidate_lost_total += int(candidate_lost)
        if control_unknown_pushes is not None:
            control_unknown_push_total += int(control_unknown_pushes)
        if candidate_unknown_pushes is not None:
            candidate_unknown_push_total += int(candidate_unknown_pushes)
        if ctrl is not None:
            control_p95.append(ctrl)
        if cand is not None:
            candidate_p95.append(cand)
        if raw_delta is not None:
            p95_benefit.append(-raw_delta)
            if raw_delta > 0:
                interval_ms = case_interval_ms(case)
                if interval_ms is not None and interval_ms >= 20:
                    normal_regressions.append(raw_delta)
                if interval_ms is not None and interval_ms <= 5:
                    stress_regressions.append(raw_delta)
        rendered.append(f"{case}:{row.get('p95_delta_ms', '')}")

    max_normal = max(normal_regressions) if normal_regressions else 0.0
    max_stress = max(stress_regressions) if stress_regressions else 0.0
    return {
        "case_deltas": case_deltas,
        "case_count": len([row for row in case_deltas if isinstance(row, dict)]),
        "control_p95_ms": control_p95,
        "candidate_p95_ms": candidate_p95,
        "p95_benefit_ms": p95_benefit,
        "case_delta_summary": ";".join(rendered),
        "control_lost_total": control_lost_total,
        "candidate_lost_total": candidate_lost_total,
        "control_unknown_push_total": control_unknown_push_total,
        "candidate_unknown_push_total": candidate_unknown_push_total,
        "max_normal_regression_ms": max_normal,
        "max_stress_regression_ms": max_stress,
        "max_normal_regression_ms_text": f"{max_normal:.3f}",
        "max_stress_regression_ms_text": f"{max_stress:.3f}",
    }


def record_attempt(
    run_dir: Path,
    *,
    iteration: int,
    candidate: dict[str, Any],
    batch_state: dict[str, Any],
    verdict: str,
    retry_condition: str,
    stop_reason: str,
    notes: str = "",
) -> None:
    twap_stats = _twap_batch_stats(batch_state)
    timing_notes = notes
    case_deltas = twap_stats.get("case_deltas", [])
    if isinstance(case_deltas, list) and case_deltas:
        rendered = []
        for delta in case_deltas:
            if not isinstance(delta, dict):
                continue
            rendered.append(
                f"{delta.get('case', '')}:p95_delta_ms={delta.get('p95_delta_ms', '')},lost={delta.get('candidate_lost', '')},unknown={delta.get('candidate_unknown_pushes', '')}"
            )
        if rendered:
            timing_notes = (timing_notes + "; " if timing_notes else "") + "twap_case_deltas[" + "; ".join(rendered) + "]"
    candidate_samples = batch_state.get("candidate_samples_ms") or twap_stats.get("candidate_p95_ms", [])
    control_median = batch_state.get("control_median_ms")
    if not control_median and twap_stats.get("control_p95_ms"):
        control_median = statistics.median(twap_stats["control_p95_ms"])
    candidate_median = batch_state.get("candidate_median_ms")
    if not candidate_median and twap_stats.get("candidate_p95_ms"):
        candidate_median = statistics.median(twap_stats["candidate_p95_ms"])
    delta_ms = batch_state.get("delta_ms")
    if not delta_ms and twap_stats.get("p95_benefit_ms"):
        delta_ms = statistics.median(twap_stats["p95_benefit_ms"])
    row = {
        "iteration": iteration,
        "candidate_id": candidate["candidate_id"],
        "lane": candidate["lane"],
        "target": candidate["target"],
        "touched_files": "|".join(candidate.get("touched_files", [])),
        "candidate_workspace": candidate.get("candidate_workspace", ""),
        "patch_path": candidate.get("patch_path", f"patches/{candidate['candidate_id']}.patch"),
        "semantic_risk": candidate.get("semantic_risk", ""),
        "build_status": batch_state.get("build_status", ""),
        "compare_status": batch_state.get("compare_status", ""),
        "paired_sample_count": str(batch_state.get("paired_sample_count", "") or twap_stats.get("case_count", "")),
        "timing_verdict": verdict,
        "sample_count": len(candidate_samples),
        "samples_ms": ",".join(
            f"{v:.3f}" for v in candidate_samples
        ),
        "control_median_ms": f"{control_median or 0:.3f}",
        "candidate_median_ms": f"{candidate_median or 0:.3f}",
        "delta_ms": f"{delta_ms or 0:.3f}",
        "twap_case_deltas": twap_stats.get("case_delta_summary", ""),
        "twap_max_normal_regression_ms": twap_stats.get("max_normal_regression_ms_text", ""),
        "twap_max_stress_regression_ms": twap_stats.get("max_stress_regression_ms_text", ""),
        "compare_result": batch_state.get("compare_status", ""),
        "noise_flag": batch_state.get("noise_flag", ""),
        "verdict": verdict,
        "retry_condition": retry_condition,
        "stop_reason": stop_reason,
        "recorded_at": utc_now(),
        "notes": timing_notes,
    }
    append_tsv_row(run_dir / "attempts.tsv", row, ATTEMPTS_FIELDS)


def record_neutral_pool_entry(
    run_dir: Path,
    candidate: dict[str, Any],
    batch_state: dict[str, Any],
    retry_condition: str,
) -> None:
    twap_stats = _twap_batch_stats(batch_state)
    candidate_samples = batch_state.get("candidate_samples_ms") or twap_stats.get("candidate_p95_ms", [])
    median_ms = batch_state.get("candidate_median_ms")
    if not median_ms and candidate_samples:
        median_ms = statistics.median(candidate_samples)
    delta_ms = batch_state.get("delta_ms")
    if not delta_ms and twap_stats.get("p95_benefit_ms"):
        delta_ms = statistics.median(twap_stats["p95_benefit_ms"])
    range_ms = (max(candidate_samples) - min(candidate_samples)) if len(candidate_samples) > 1 else 0.0
    timing_summary = (
        f"sample_count={len(candidate_samples)};"
        f" median_ms={median_ms or 0:.3f};"
        f" delta_ms={delta_ms or 0:.3f};"
        f" range_ms={range_ms:.3f};"
        f" n={len(candidate_samples)}"
    )
    if twap_stats.get("case_delta_summary"):
        timing_summary += (
            f"; twap_case_deltas={twap_stats['case_delta_summary']};"
            f" max_normal_regression_ms={twap_stats['max_normal_regression_ms_text']};"
            f" max_stress_regression_ms={twap_stats['max_stress_regression_ms_text']}"
        )
    existing = read_tsv(run_dir / "neutral_pool.tsv")
    existing = [
        row for row in existing if (row.get("candidate_id") or "") != candidate["candidate_id"]
    ]
    row = {
        "candidate_id": candidate["candidate_id"],
        "lane": candidate["lane"],
        "patch_path": f"patches/{candidate['candidate_id']}.patch",
        "touched_files": "|".join(candidate.get("touched_files", [])),
        "hypothesis": candidate.get("hypothesis", ""),
        "compare_result": batch_state.get("compare_status", ""),
        "timing_summary": timing_summary,
        "semantic_risk": candidate.get("semantic_risk", ""),
        "stack_compatibility": (
            candidate.get("stack_compatibility")
            if candidate.get("stack_compatibility") and candidate.get("stack_compatibility") != "single"
            else ("stackable" if (candidate.get("semantic_risk") or "").lower() == "low" else "single")
        ),
        "retry_condition": retry_condition,
        "recorded_at": utc_now(),
    }
    existing.append(row)
    write_tsv(run_dir / "neutral_pool.tsv", existing, NEUTRAL_POOL_FIELDS)


def record_retry_condition(
    run_dir: Path,
    candidate: dict[str, Any],
    *,
    status: str,
    noise_flag: str,
    required_condition: str,
    last_exit_reason: str,
    notes: str,
) -> None:
    existing = read_tsv(run_dir / "retry_conditions.tsv")
    existing = [
        row for row in existing if (row.get("target") or "") != candidate["target"]
    ]
    row = {
        "target": candidate["target"],
        "status": status,
        "noise_flag": noise_flag,
        "retry_after": "next quiet same-host window" if status == "NOISY_PENDING" else "when new evidence appears",
        "required_condition": required_condition,
        "last_exit_reason": last_exit_reason,
        "notes": notes,
    }
    existing.append(row)
    write_tsv(run_dir / "retry_conditions.tsv", existing, RETRY_CONDITIONS_FIELDS)


def upsert_timing_from_batch(
    run_dir: Path,
    candidate: dict[str, Any],
    batch_state: dict[str, Any],
    host_key: str,
    *,
    verdict: str = "",
    verdict_reason: str = "",
) -> None:
    """Write control + candidate rows into timing_history.tsv for this iteration."""

    control_samples = batch_state.get("control_samples_ms") or []
    candidate_samples = batch_state.get("candidate_samples_ms") or []
    twap_timing_samples = batch_state.get("twap_timing_samples") or []
    twap_case_deltas = {
        str(row.get("case") or ""): row
        for row in (batch_state.get("twap_case_deltas") or [])
        if isinstance(row, dict)
    }
    if isinstance(twap_timing_samples, list) and twap_timing_samples:
        recorded_at = utc_now()
        rows: list[dict[str, str]] = []
        for sample in twap_timing_samples:
            if not isinstance(sample, dict):
                continue
            case = str(sample.get("case") or "")
            role = str(sample.get("role") or "")
            if role not in {"control", "candidate"} or not case:
                continue
            delta_row = twap_case_deltas.get(case, {})
            raw_p95_delta = _float_or_none(delta_row.get("p95_delta_ms"))
            benefit_ms = -raw_p95_delta if raw_p95_delta is not None else None
            received = str(sample.get("received") or sample.get("count") or "")
            rows.append(
                {
                    "history_key": f"{run_dir.name}|{candidate['candidate_id']}|{case}|{role}",
                    "recorded_at": recorded_at,
                    "time_window": recorded_at[:10],
                    "bundle_id": run_dir.name,
                    "run_id": run_dir.name,
                    "source_attempts_path": str(run_dir / "attempts.tsv"),
                    "host_key": host_key,
                    "control_head": os.environ.get("PSI_CONTROL_HEAD", "auto_loop"),
                    "active_gate": "twap headless remote",
                    "compatibility_group": "twap_position_push",
                    "compatibility_tag": case,
                    "warm_or_cold": "measured",
                    "sample_unit": "ms",
                    "kind": role,
                    "policy_bucket": candidate["lane"] if role == "candidate" else "control",
                    "experiment_kind": "neutral_stack" if candidate["lane"] == "combination" else "single",
                    "target": candidate["target"] if role == "candidate" else "control baseline",
                    "stage": f"{candidate['target']}|{case}",
                    "sample_count": received,
                    "samples_ms": "",
                    "samples": "",
                    "mean_ms": str(sample.get("avg_ms") or ""),
                    "mean_seconds": "",
                    "median_ms": str(sample.get("p50_ms") or ""),
                    "median_seconds": "",
                    "mad_ms": "",
                    "mad_seconds": "",
                    "iqr_ms": "",
                    "iqr_seconds": "",
                    "stdev_ms": "",
                    "stdev_seconds": "",
                    "range_ms": "",
                    "range_seconds": "",
                    "delta_ms": f"{benefit_ms:.3f}" if role == "candidate" and benefit_ms is not None else "",
                    "delta_seconds": f"{benefit_ms / 1000.0:.6f}" if role == "candidate" and benefit_ms is not None else "",
                    "timing_verdict": verdict if role == "candidate" else "",
                    "timing_verdict_reason": verdict_reason if role == "candidate" else "",
                    "timing_verdict_method": "twap_headless_remote",
                    "control_sample_count": received if role == "control" else "",
                    "candidate_sample_count": received if role == "candidate" else "",
                    "paired_sample_count": "1" if role == "candidate" and case in twap_case_deltas else "",
                    "control_median_ms": str(delta_row.get("control_p95_ms") or "") if role == "candidate" else "",
                    "control_median_seconds": "",
                    "control_samples_ms": str(delta_row.get("control_p95_ms") or "") if role == "candidate" else "",
                    "candidate_samples_ms": str(delta_row.get("candidate_p95_ms") or "") if role == "candidate" else "",
                    "paired_deltas_ms": f"{benefit_ms:.3f}" if role == "candidate" and benefit_ms is not None else "",
                    "paired_deltas_seconds": f"{benefit_ms / 1000.0:.6f}" if role == "candidate" and benefit_ms is not None else "",
                    "median_delta_ms": f"{benefit_ms:.3f}" if role == "candidate" and benefit_ms is not None else "",
                    "median_delta_seconds": f"{benefit_ms / 1000.0:.6f}" if role == "candidate" and benefit_ms is not None else "",
                    "bootstrap_ci_low_ms": "",
                    "bootstrap_ci_high_ms": "",
                    "bootstrap_ci_low_seconds": "",
                    "bootstrap_ci_high_seconds": "",
                    "permutation_p_value": "",
                    "paired_stdev_ms": "",
                    "paired_range_ms": "",
                    "paired_mean_ms": "",
                    "noise_flag": batch_state.get("noise_flag", "ok"),
                    "verdict": verdict if role == "candidate" else "",
                    "notes": (
                        f"case={case}; sent={sample.get('sent', '')}; received={sample.get('received', '')}; "
                        f"lost={sample.get('lost', '')}; unknown_pushes={sample.get('unknown_pushes', '')}; "
                        f"subscribers={sample.get('subscribers', '')}; p95_ms={sample.get('p95_ms', '')}; "
                        f"worst_subscriber_p95_ms={sample.get('worst_subscriber_p95_ms', '')}; "
                        f"p99_ms={sample.get('p99_ms', '')}; max_ms={sample.get('max_ms', '')}; "
                        f"p95_delta_candidate_minus_control={delta_row.get('p95_delta_ms', '')}"
                    ),
                }
            )
        if rows:
            upsert_history_rows(run_dir / "timing_history.tsv", rows)
            return
    if not control_samples and not candidate_samples:
        return
    recorded_at = utc_now()
    rows: list[dict[str, str]] = []

    def _stats(samples: list[float]) -> dict[str, Any]:
        if not samples:
            return {}
        return {
            "sample_count": str(len(samples)),
            "samples_ms": ",".join(f"{v:.3f}" for v in samples),
            "median_ms": f"{statistics.median(samples):.3f}",
            "mean_ms": f"{statistics.mean(samples):.3f}",
            "stdev_ms": f"{statistics.stdev(samples):.3f}" if len(samples) > 1 else "0.000",
            "range_ms": f"{max(samples) - min(samples):.3f}" if len(samples) > 1 else "0.000",
        }

    for kind, samples in (("control", control_samples), ("candidate", candidate_samples)):
        if not samples:
            continue
        stats = _stats(samples)
        rows.append(
            {
                "history_key": f"{run_dir.name}|{candidate['candidate_id']}|{kind}",
                "recorded_at": recorded_at,
                "time_window": recorded_at[:10],
                "bundle_id": run_dir.name,
                "run_id": run_dir.name,
                "source_attempts_path": str(run_dir / "attempts.tsv"),
                "host_key": host_key,
                "control_head": os.environ.get("PSI_CONTROL_HEAD", "auto_loop"),
                "active_gate": "headless auto-loop",
                "compatibility_group": "",
                "compatibility_tag": "",
                "warm_or_cold": "measured",
                "sample_unit": "ms",
                "kind": kind,
                "policy_bucket": candidate["lane"] if kind == "candidate" else "control",
                "experiment_kind": "neutral_stack" if candidate["lane"] == "combination" else "single",
                "target": candidate["target"] if kind == "candidate" else "control baseline",
                "stage": candidate["target"],
                "sample_count": stats.get("sample_count", ""),
                "samples_ms": stats.get("samples_ms", ""),
                "samples": "",
                "mean_ms": stats.get("mean_ms", ""),
                "mean_seconds": "",
                "median_ms": stats.get("median_ms", ""),
                "median_seconds": "",
                "mad_ms": "",
                "mad_seconds": "",
                "iqr_ms": "",
                "iqr_seconds": "",
                "stdev_ms": stats.get("stdev_ms", ""),
                "stdev_seconds": "",
                "range_ms": stats.get("range_ms", ""),
                "range_seconds": "",
                "delta_ms": f"{batch_state.get('delta_ms', 0):.3f}" if kind == "candidate" else "",
                "delta_seconds": "",
                "timing_verdict": verdict if kind == "candidate" else "",
                "timing_verdict_reason": verdict_reason if kind == "candidate" else "",
                "timing_verdict_method": "auto_loop_dry_run" if batch_state.get("dry_run") else "psi_headless_remote",
                "control_sample_count": str(len(control_samples)),
                "candidate_sample_count": str(len(candidate_samples)),
                "paired_sample_count": str(min(len(control_samples), len(candidate_samples))),
                "control_samples_ms": ",".join(f"{v:.3f}" for v in control_samples),
                "candidate_samples_ms": ",".join(f"{v:.3f}" for v in candidate_samples),
                "paired_deltas_ms": ",".join(
                    f"{c - b:.3f}" for c, b in zip(control_samples, candidate_samples, strict=False)
                ),
                "paired_deltas_seconds": ",".join(
                    f"{(c - b) / 1000.0:.3f}" for c, b in zip(control_samples, candidate_samples, strict=False)
                ),
                "median_delta_ms": f"{batch_state.get('delta_ms', 0):.3f}",
                "median_delta_seconds": f"{float(batch_state.get('delta_ms', 0) or 0.0) / 1000.0:.3f}",
                "bootstrap_ci_low_ms": f"{batch_state.get('bootstrap_ci_low_ms', '')}",
                "bootstrap_ci_high_ms": f"{batch_state.get('bootstrap_ci_high_ms', '')}",
                "bootstrap_ci_low_seconds": "",
                "bootstrap_ci_high_seconds": "",
                "permutation_p_value": f"{batch_state.get('permutation_p_value', '')}",
                "paired_stdev_ms": f"{batch_state.get('paired_stdev_ms', '')}",
                "paired_range_ms": f"{batch_state.get('paired_range_ms', '')}",
                "paired_mean_ms": f"{batch_state.get('paired_mean_ms', '')}",
                "noise_flag": batch_state.get("noise_flag", "ok"),
                "verdict": verdict if kind == "candidate" else "",
                "notes": f"iteration={batch_state.get('iteration', '')}; dry_run={batch_state.get('dry_run', False)}",
            }
        )
    if rows:
        upsert_history_rows(run_dir / "timing_history.tsv", rows)


# ------------------------------- verdict judging -------------------------------


def judge_verdict(batch_state: dict[str, Any]) -> tuple[str, str]:
    """Return (verdict, retry_condition)."""

    compare = (batch_state.get("compare_status") or "").lower()
    if compare not in {"pass", "ok"}:
        return "rejected", "compare gate failed; fix patch before retry"

    twap_stats = _twap_batch_stats(batch_state)
    if twap_stats.get("case_count"):
        control_lost_total = int(twap_stats.get("control_lost_total") or 0)
        control_unknown_push_total = int(twap_stats.get("control_unknown_push_total") or 0)
        if control_lost_total > 0 or control_unknown_push_total > 0:
            return "infra_blocked", f"TWAP control baseline unhealthy: control_lost_total={control_lost_total}, control_unknown_push_total={control_unknown_push_total}"
        candidate_unknown_push_total = int(twap_stats.get("candidate_unknown_push_total") or 0)
        if candidate_unknown_push_total > 0:
            return "rejected", f"TWAP candidate produced unknown pushes: candidate_unknown_push_total={candidate_unknown_push_total}"
        lost_failure_count = int(batch_state.get("lost_failure_count") or 0)
        if lost_failure_count > 0:
            return "rejected", f"TWAP push timing lost messages: lost_failure_count={lost_failure_count}"
        max_normal = float(twap_stats.get("max_normal_regression_ms") or 0.0)
        max_stress = float(twap_stats.get("max_stress_regression_ms") or 0.0)
        if max_stress > 5.0:
            return "rejected", f"TWAP stress p95 regression {max_stress:.3f}ms exceeds 5.000ms"
        if max_normal > 1.0:
            return "rejected", f"TWAP normal-frequency p95 regression {max_normal:.3f}ms exceeds 1.000ms"

    decision = (batch_state.get("decision") or "").lower()
    if decision == "rejected":
        return "rejected", batch_state.get("reason", "") or "remote TWAP gate rejected candidate"

    noise_flag = (batch_state.get("noise_flag") or "").upper()
    timing = (batch_state.get("timing_verdict") or batch_state.get("timing_status") or "").lower()
    if timing == "accepted_noisy_replicated":
        return "accepted_noisy_replicated", "statistically conclusive with replicated evidence; shared-host promotion, artifact marked non-bare-metal"
    if timing == "accepted_noisy_single":
        return "accepted_noisy_single", "statistically conclusive but measurement environment was noisy; single-run evidence only, queued for validation"
    if timing == "accepted_class_a":
        return "accepted_class_a", "Class A algorithmic change: correctness pass is sufficient; perf recorded but not gated."
    if noise_flag == "NOISY" or timing == "noisy_pending":
        return (
            "NOISY_PENDING",
            "retry when same-host control stdev_ms < 1500 or paired range_ms < 1500",
        )

    if timing.lower() == "accepted":
        return "accepted", ""
    delta = batch_state.get("delta_ms") or 0.0
    if delta <= -100.0:
        return "rejected", "candidate is slower than control under same-host timing"
    return "neutral", "keep for neutral stack; needs bundle audit before promotion"


# ------------------------------- run_state orchestration -------------------------------


def write_run_state(
    run_dir: Path,
    *,
    status: str,
    iteration: int,
    started_at: str,
    stop_reason: str,
    stop_detail: str,
    first_accepted_stop: bool,
    infra_failures: int,
    lanes_snapshot: dict[str, list[dict[str, Any]]],
    control_distribution: dict[str, Any],
    latest_candidate: dict[str, Any] | None,
    latest_verdict: str,
    accepted_count: int,
    neutral_count: int,
    rejected_count: int,
    noisy_pending_count: int,
    infra_blocked_count: int,
    accepted_class_a_count: int = 0,
    accepted_noisy_single_count: int = 0,
    accepted_noisy_replicated_count: int = 0,
) -> None:
    lane_counts = {lane: len(lanes_snapshot.get(lane, [])) for lane in LANE_PRIORITY}
    state = {
        "status": status,
        "mode": "headless_auto_loop",
        "run_id": run_dir.name,
        "started_at": started_at,
        "updated_at": utc_now(),
        "iteration": iteration,
        "lane_counts": lane_counts,
        "lanes_empty": lanes_are_empty(lanes_snapshot),
        "control_distribution": control_distribution,
        "latest_candidate_id": latest_candidate["candidate_id"] if latest_candidate else "",
        "latest_lane": latest_candidate["lane"] if latest_candidate else "",
        "latest_verdict": latest_verdict,
        "accepted_count": accepted_count,
        "neutral_count": neutral_count,
        "rejected_count": rejected_count,
        "noisy_pending_count": noisy_pending_count,
        "infra_blocked_count": infra_blocked_count,
        "accepted_class_a_count": accepted_class_a_count,
        "accepted_noisy_single_count": accepted_noisy_single_count,
        "accepted_noisy_replicated_count": accepted_noisy_replicated_count,
        "first_accepted_stop": first_accepted_stop,
        "infra_failure_count": infra_failures,
        "last_exit_reason": stop_reason,
        "last_exit_detail": stop_detail,
        "supported_stop_reasons": sorted(STOP_REASONS_GLOBAL),
        "patch_manifest_path": str(run_dir / "patches" / "patch_manifest.json"),
        "attempts_path": str(run_dir / "attempts.tsv"),
        "neutral_pool_path": str(run_dir / "neutral_pool.tsv"),
        "retry_conditions_path": str(run_dir / "retry_conditions.tsv"),
        "timing_history_path": str(run_dir / "timing_history.tsv"),
        "patches_dir": str(run_dir / "patches"),
    }
    write_json(run_dir / "run_state.json", state)


# ------------------------------- iteration step -------------------------------


def seed_profile_if_missing(run_dir: Path) -> None:
    """If a profile/hotspots snapshot is not yet present, write a small seed so
    the candidate generator always has at least one row to work with.
    """

    profile_path = run_dir / "profile.tsv"
    if read_tsv(profile_path):
        return
    seed_profile = [
        {"stage": "handlerData.row_loop", "total_ms": "65200", "count": "8", "avg_ms": "8150.000", "source": "seed"},
        {"stage": "write.generate_table", "total_ms": "28400", "count": "8", "avg_ms": "3550.000", "source": "seed"},
        {"stage": "handlerData.timestamp", "total_ms": "18700", "count": "8", "avg_ms": "2337.500", "source": "seed"},
        {"stage": "compute.factor_on_tick", "total_ms": "9100", "count": "8", "avg_ms": "1137.500", "source": "seed"},
    ]
    write_tsv(profile_path, seed_profile, PROFILE_FIELDS)
    hotspots = [
        {
            "rank": str(idx + 1),
            "stage": row["stage"],
            "total_ms": row["total_ms"],
            "avg_ms": row["avg_ms"],
            "count": row["count"],
            "score": f"{float(row['total_ms']) / float(seed_profile[0]['total_ms']):.6f}",
            "notes": "seeded",
        }
        for idx, row in enumerate(seed_profile)
    ]
    write_tsv(run_dir / "hotspots.tsv", hotspots, HOTSPOT_FIELDS)


def is_twap_run(args: argparse.Namespace) -> bool:
    marker = " ".join(
        [
            str(args.remote_batch_script or ""),
            str(args.batch_script or ""),
            str(args.patch_command or ""),
            str(args.twap_endpoint or ""),
        ]
    ).lower()
    return "twap" in marker


def seed_twap_profile_if_missing(args: argparse.Namespace, run_dir: Path) -> None:
    """Create TWAP profile/hotspot context for adapter runs.

    TWAP cannot use the Psi synthetic profile. When the run has no profile yet,
    call the TWAP source scanner so the candidate generator receives real
    source-root/touched-file context rather than hand-authored seed candidates.
    """

    if read_tsv(run_dir / "profile.tsv") and read_tsv(run_dir / "hotspots.tsv"):
        return
    source_root = _source_root(args)
    script = repo_root() / "scripts" / "twap_profile_hotspots.py"
    if not script.exists():
        raise SystemExit(f"TWAP profile generator is missing: {script}")
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--source-root",
            str(source_root),
            "--run-dir",
            str(run_dir),
        ],
        cwd=str(repo_root()),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    log_path = run_dir / "logs" / "twap_profile_hotspots.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(result.stdout or "", encoding="utf-8")
    if result.returncode != 0:
        raise SystemExit(f"TWAP profile generator failed rc={result.returncode}; see {log_path}")


def iteration_step(
    args: argparse.Namespace,
    run_dir: Path,
    iteration: int,
    seen_candidate_ids: set[str],
    cooldown_targets: set[str],
    host_key: str,
) -> tuple[dict[str, Any] | None, str, str, dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Drive one iteration. Returns (candidate, verdict, stop_reason, lanes, batch_state)."""

    if is_twap_run(args) and not args.candidate_seed_file:
        seed_twap_profile_if_missing(args, run_dir)
    else:
        seed_profile_if_missing(run_dir)
    control_distribution = refresh_control_distribution(run_dir, host_key)
    retry_ready_targets = quiet_retry_ready_targets(args, run_dir, control_distribution)
    update_heartbeat(run_dir, "generate", "building three-lane candidate queue")
    if args.candidate_seed_file:
        lanes = load_candidate_seed_file(Path(args.candidate_seed_file))
    else:
        lanes = generate_candidates(
            run_dir,
            retry_ready_targets=retry_ready_targets,
            candidate_ledger_path=Path(args.candidate_ledger) if args.candidate_ledger else None,
        )
    # Persist a snapshot of the lane queue for this iteration.
    iteration_dir = run_dir / "iterations" / f"iter_{iteration:03d}_plan"
    iteration_dir.mkdir(parents=True, exist_ok=True)
    write_json(iteration_dir / "candidate_lanes.json", lanes)

    candidate = pick_next_candidate(
        lanes,
        seen_candidate_ids=seen_candidate_ids,
        cooldown_targets=cooldown_targets,
    )
    if candidate is None:
        return None, "", "no_targets", lanes, {}

    materialized, patch_meta, patch_reason = materialize_candidate_patch(args, run_dir, candidate, iteration)
    apply_change_class_policy(candidate)
    if not materialized:
        candidate["candidate_workspace"] = str(patch_meta.get("candidate_workspace", ""))
        candidate["patch_path"] = str(patch_meta.get("patch_path", ""))
        candidate["base_commit"] = str(patch_meta.get("base_commit", ""))
        batch_state = {
            "status": "stopped",
            "batch_status": "completed",
            "iteration": iteration,
            "candidate_id": candidate["candidate_id"],
            "lane": candidate["lane"],
            "target": candidate["target"],
            "build_status": "not_run",
            "compare_status": "not_run",
            "timing_status": "needs_patch",
            "timing_verdict": "needs_patch",
            "comparison_accepted": False,
            "paired_sample_count": 0,
            "control_samples_ms": [],
            "candidate_samples_ms": [],
            "control_median_ms": 0.0,
            "candidate_median_ms": 0.0,
            "delta_ms": 0.0,
            "noise_flag": "not_run",
            "patch_materialization_status": "failed",
            "patch_materialization_reason": patch_reason,
        }
        record_attempt(
            run_dir,
            iteration=iteration,
            candidate=candidate,
            batch_state=batch_state,
            verdict="needs_patch",
            retry_condition=patch_reason,
            stop_reason="",
            notes="patch materialization failed before remote/build/timing",
        )
        set_patch_status(run_dir, candidate["candidate_id"], "failed", note=patch_reason)
        return candidate, "needs_patch", "", lanes, batch_state

    update_heartbeat(run_dir, "remote_batch", f"running iteration {iteration} candidate {candidate['candidate_id']}")
    rc, _iter_dir, batch_state = call_remote_batch(args, run_dir, candidate, iteration)
    if rc != 0 and not args.dry_run:
        if batch_state:
            retry_condition = str(batch_state.get("reason") or batch_state.get("timing_status") or f"remote rc={rc}")
            if batch_state.get("build_status") == "pass" and not batch_state.get("compare_status"):
                batch_state["compare_status"] = "pass"
            if not batch_state.get("timing_verdict"):
                batch_state["timing_verdict"] = "rejected"
            record_attempt(
                run_dir,
                iteration=iteration,
                candidate=candidate,
                batch_state=batch_state,
                verdict="rejected",
                retry_condition=retry_condition,
                stop_reason="",
                notes="remote gate rejected candidate before timing completed",
            )
            set_patch_status(run_dir, candidate["candidate_id"], "reverted", note=retry_condition)
            return candidate, "rejected", "", lanes, batch_state
        # No remote state was recoverable; treat this as infrastructure.
        set_patch_status(run_dir, candidate["candidate_id"], "failed", note=f"remote rc={rc}")
        return candidate, "rejected", "remote_failed", lanes, batch_state

    verdict, retry_condition = judge_verdict(batch_state)
    if verdict == "neutral":
        record_neutral_pool_entry(run_dir, candidate, batch_state, retry_condition)
        set_patch_status(run_dir, candidate["candidate_id"], "reverted", note="neutral; candidate reverted")
    elif verdict == "accepted":
        if not candidate.get("candidate_workspace") or not candidate.get("patch_path"):
            verdict = "needs_patch"
            retry_condition = "accepted verdict blocked because patch/workspace audit fields are missing"
            set_patch_status(run_dir, candidate["candidate_id"], "failed", note=retry_condition)
        else:
            set_patch_status(run_dir, candidate["candidate_id"], "applied", note="accepted as new baseline")
    elif verdict == "accepted_class_a":
        set_patch_status(run_dir, candidate["candidate_id"], "applied", note="accepted as Class A algorithmic change; correctness pass sufficient")
    elif verdict == "accepted_noisy_replicated":
        set_patch_status(
            run_dir,
            candidate["candidate_id"],
            "applied",
            note="accepted with replicated evidence; shared-host promotion, artifact marked non-bare-metal",
        )
    elif verdict == "accepted_noisy_single":
        set_patch_status(
            run_dir,
            candidate["candidate_id"],
            "reverted",
            note="accepted_noisy_single; queued for validation",
        )
    elif verdict == "NOISY_PENDING":
        noisy_notes = (
            f"candidate_id={candidate['candidate_id']};"
            f" paired_sample_count={batch_state.get('paired_sample_count', '')};"
            f" median_delta_ms={batch_state.get('median_delta_ms', '')};"
            f" paired_range_ms={batch_state.get('paired_range_ms', '')};"
            f" paired_stdev_ms={batch_state.get('paired_stdev_ms', '')};"
            " candidate-level pause; loop continues"
        )
        record_retry_condition(
            run_dir,
            candidate,
            status="NOISY_PENDING",
            noise_flag="NOISY",
            required_condition=retry_condition,
            last_exit_reason="",
            notes=noisy_notes,
        )
        set_patch_status(run_dir, candidate["candidate_id"], "reverted", note="noisy; reverted pending rerun")
    elif verdict == "rejected":
        set_patch_status(run_dir, candidate["candidate_id"], "reverted", note="rejected; reverted")
    elif verdict == "infra_blocked":
        set_patch_status(run_dir, candidate["candidate_id"], "reverted", note=f"infra blocked; {retry_condition}")

    attempt_stop_reason = "control_baseline_unhealthy" if verdict == "infra_blocked" else ""
    record_attempt(
        run_dir,
        iteration=iteration,
        candidate=candidate,
        batch_state=batch_state,
        verdict=verdict,
        retry_condition=retry_condition,
        stop_reason=attempt_stop_reason,
        notes=candidate.get("expected_effect", ""),
    )
    upsert_timing_from_batch(
        run_dir,
        candidate,
        batch_state,
        host_key,
        verdict=verdict,
        verdict_reason=retry_condition,
    )

    iter_stop = "control_baseline_unhealthy" if verdict == "infra_blocked" else ""
    return candidate, verdict, iter_stop, lanes, batch_state


# ------------------------------- main loop -------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Psi headless auto-loop (closed-loop optimization).")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--batch-script", type=Path, default=repo_root() / "scripts" / "psi_headless_remote.sh")
    parser.add_argument("--bash", default="bash")
    parser.add_argument("--remote-host", default="", help="SSH host for remote build/compare/timing. Patch generation still runs locally.")
    parser.add_argument("--remote-hft-root", default="/root/work/HFT-wf", help="Remote HFT-wf root containing the batch script.")
    parser.add_argument("--remote-batch-script", default="scripts/psi_headless_remote.sh", help="Batch script path on the remote host, relative to --remote-hft-root unless absolute.")
    parser.add_argument("--remote-run-root", default="/root/work/psi_experiments/runs", help="Remote parent directory for per-run artifacts.")
    parser.add_argument("--remote-run-dir", default="", help="Exact remote run root override. Defaults to <remote-run-root>/<local-run-dir-name>.")
    parser.add_argument("--remote-candidate-workspace-root", default="", help="Remote parent directory for synced local candidate workspaces.")
    parser.add_argument("--max-iterations", type=int, default=6)
    parser.add_argument("--max-hours", type=float, default=8.0)
    parser.add_argument("--max-candidates", type=int, default=12)
    parser.add_argument("--measure-runs", type=int, default=5)
    parser.add_argument(
        "--no-compare-runs",
        type=int,
        default=0,
        help="Measured no_compare smoke runs before compare; 0 preserves legacy behavior by using --measure-runs.",
    )
    parser.add_argument("--first-accepted-stop", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--repeated-infra-failures", type=int, default=2)
    parser.add_argument("--stack-throttle", type=int, default=3, help="Try a neutral stack at most every N iterations")
    parser.add_argument("--quiet-retry-min-control-samples", type=int, default=20, help="Minimum same-host control samples required before retrying a NOISY_PENDING candidate.")
    parser.add_argument("--quiet-retry-control-stdev-ms", type=float, default=800.0, help="Maximum same-host control median stdev before retrying a NOISY_PENDING candidate.")
    parser.add_argument("--quiet-retry-control-range-ms", type=float, default=2000.0, help="Maximum same-host control sample range before retrying a NOISY_PENDING candidate.")
    parser.add_argument("--host-key", default="")
    parser.add_argument("--root")
    parser.add_argument("--env-file")
    parser.add_argument("--build-dir")
    parser.add_argument("--runner")
    parser.add_argument("--candidate-runner")
    parser.add_argument("--config")
    parser.add_argument("--output-dir")
    parser.add_argument("--stop-file", help="Stop before the next iteration when this file exists. Defaults to <run-dir>/STOP.")
    parser.add_argument("--dry-run", action="store_true", help="Skip SSH / bash and generate synthetic per-iteration evidence.")
    parser.add_argument("--patch-command", default="", help="External command to generate candidate patch in workspace. Receives PSI_* env vars. Use 'builtin:noop' or 'builtin:fake-nonempty' for testing.")
    parser.add_argument("--source-root", default="", help="Root of the Psi source tree to copy into candidate workspaces.")
    parser.add_argument("--candidate-workspace", default="", help="Override candidate workspace base path (default: <run-dir>/candidate_workspaces/<id>).")
    parser.add_argument("--reuse-candidate-workspace", action="store_true", help="Skip workspace refresh if it already exists.")
    parser.add_argument("--candidate-ledger", default="", help="Optional JSON ledger of blocked, retry-only, and non-retry Psi candidates/classes.")
    parser.add_argument("--candidate-seed-file", default="", help="Optional JSON file containing explicit candidate lanes for non-Psi adapters such as TWAP.")
    parser.add_argument("--control-root", default="", help="Optional control source root for non-Psi batch scripts such as TWAP.")
    parser.add_argument("--twap-endpoint", default="", help="TWAP gRPC endpoint passed to twap_headless_remote.sh.")
    parser.add_argument("--twap-user-id", default="", help="TWAP userId passed to twap_headless_remote.sh.")
    parser.add_argument("--twap-measure-cases", default="", help="TWAP timing cases, e.g. '100:50:120 500:20:180'.")
    parser.add_argument("--twap-subscriber-counts", default="", help="TWAP subscriber fanout counts passed to twap_headless_remote.sh, e.g. '1 4'.")
    parser.add_argument("--twap-build-targets", default="", help="TWAP build targets passed to twap_headless_remote.sh.")
    parser.add_argument("--twap-correctness-mode", default="", choices=("", "push_only", "skip"), help="TWAP correctness mode passed to twap_headless_remote.sh.")
    parser.add_argument("--twap-account-desc-check", default="", choices=("", "required", "optional"), help="TWAP accountDesc correctness strictness passed to twap_headless_remote.sh.")
    return parser


def resolve_stop_file(run_dir: Path, stop_file: str | None) -> Path:
    if stop_file:
        path = Path(stop_file)
        return path if path.is_absolute() else (run_dir / path)
    return run_dir / "STOP"


def count_verdict_rows(run_dir: Path) -> tuple[int, int, int, int, int, int, int, int]:
    counts = {"accepted": 0, "accepted_noisy_single": 0, "accepted_noisy_replicated": 0, "accepted_class_a": 0, "neutral": 0, "rejected": 0, "NOISY_PENDING": 0, "infra_blocked": 0}
    for row in read_tsv(run_dir / "attempts.tsv"):
        verdict = (row.get("verdict") or "").strip()
        if verdict in counts:
            counts[verdict] += 1
    accepted_clean = counts["accepted"] + counts["accepted_class_a"]
    accepted_noisy = counts["accepted_noisy_single"] + counts["accepted_noisy_replicated"]
    return (
        accepted_clean,
        counts["neutral"],
        counts["rejected"],
        counts["NOISY_PENDING"],
        counts["infra_blocked"],
        accepted_noisy,
        counts["accepted_class_a"],
        counts["accepted_noisy_replicated"],
    )


def main() -> int:
    args = build_parser().parse_args()
    if args.max_iterations < 1:
        raise SystemExit("--max-iterations must be >= 1")
    if args.max_hours <= 0:
        raise SystemExit("--max-hours must be > 0")
    if args.no_compare_runs <= 0:
        args.no_compare_runs = args.measure_runs
    if args.candidate_ledger:
        args.candidate_ledger = str(Path(args.candidate_ledger).resolve())
    if args.candidate_seed_file:
        args.candidate_seed_file = str(Path(args.candidate_seed_file).resolve())
        if not Path(args.candidate_seed_file).exists():
            raise SystemExit(f"--candidate-seed-file does not exist: {args.candidate_seed_file}")

    run_dir = args.run_dir.resolve()
    ensure_run_dir(run_dir)
    host_key = args.host_key or default_host_key()
    started_at = utc_now()
    started_monotonic = time.monotonic()
    stop_file = resolve_stop_file(run_dir, args.stop_file)
    update_heartbeat(run_dir, "init", "auto-loop controller initialized")

    # Seed an empty worktree snapshot for audit trail.
    snapshot_worktree(run_dir, content=f"# auto-loop started at {started_at}\n")

    write_run_state(
        run_dir,
        status="running",
        iteration=0,
        started_at=started_at,
        stop_reason="",
        stop_detail="",
        first_accepted_stop=args.first_accepted_stop,
        infra_failures=0,
        lanes_snapshot={"evidence": [], "insight": [], "combination": []},
        control_distribution={},
        latest_candidate=None,
        latest_verdict="",
        accepted_count=0,
        neutral_count=0,
        rejected_count=0,
        noisy_pending_count=0,
        infra_blocked_count=0,
    )

    prior_attempts = read_tsv(run_dir / "attempts.tsv")
    seen_ids: set[str] = {
        (row.get("candidate_id") or "").strip()
        for row in prior_attempts
        if (row.get("candidate_id") or "").strip()
    }
    cooldown_targets = {(row.get("target") or "").strip() for row in read_tsv(run_dir / "cooldown.tsv")}
    cooldown_targets.discard("")
    infra_failures = 0
    stop_reason = ""
    stop_detail = ""
    lanes_snapshot: dict[str, list[dict[str, Any]]] = {lane: [] for lane in LANE_PRIORITY}
    latest_candidate: dict[str, Any] | None = None
    latest_verdict = ""
    control_distribution: dict[str, Any] = {}
    candidates_tried = 0

    start_iteration = len(prior_attempts) + 1
    end_iteration = start_iteration + args.max_iterations
    for iteration in range(start_iteration, end_iteration):
        if stop_file.exists():
            stop_reason = "user_stopped"
            stop_detail = f"stop file present: {stop_file}"
            break
        if candidates_tried >= args.max_candidates:
            stop_reason = "budget_stop"
            stop_detail = f"max-candidates reached ({candidates_tried} >= {args.max_candidates})"
            break
        elapsed_hours = (time.monotonic() - started_monotonic) / 3600.0
        if elapsed_hours >= args.max_hours:
            stop_reason = "budget_stop"
            stop_detail = f"max-hours reached ({elapsed_hours:.3f} >= {args.max_hours:.3f})"
            break

        control_distribution = refresh_control_distribution(run_dir, host_key)
        candidate, verdict, iter_stop, lanes, batch_state = iteration_step(
            args,
            run_dir,
            iteration,
            seen_ids,
            cooldown_targets,
            host_key,
        )
        lanes_snapshot = lanes

        if iter_stop == "no_targets":
            stop_reason = "no_targets"
            stop_detail = "all three lanes are empty or blocked"
            break
        if candidate is None:
            # no candidate available this iteration; keep going until budget stops us
            continue

        latest_candidate = candidate
        latest_verdict = verdict
        seen_ids.add(candidate["candidate_id"])
        candidates_tried += 1

        if iter_stop == "remote_failed":
            infra_failures += 1
            if infra_failures >= args.repeated_infra_failures:
                stop_reason = "repeated_infra_failure"
                stop_detail = f"{infra_failures} consecutive infrastructure failures"
                break
            # candidate-level skip; continue
            continue
        if iter_stop == "control_baseline_unhealthy":
            stop_reason = "control_baseline_unhealthy"
            stop_detail = f"candidate {candidate['candidate_id']} stopped because TWAP control baseline lost pushes"
            break

        accepted_clean, neutral, rejected, noisy, infra_blocked, accepted_noisy, accepted_class_a, accepted_noisy_replicated = count_verdict_rows(run_dir)
        write_run_state(
            run_dir,
            status="running",
            iteration=iteration,
            started_at=started_at,
            stop_reason="",
            stop_detail="",
            first_accepted_stop=args.first_accepted_stop,
            infra_failures=infra_failures,
            lanes_snapshot=lanes_snapshot,
            control_distribution=control_distribution,
            latest_candidate=latest_candidate,
            latest_verdict=latest_verdict,
            accepted_count=accepted_clean,
            neutral_count=neutral,
            rejected_count=rejected,
            noisy_pending_count=noisy,
            infra_blocked_count=infra_blocked,
            accepted_class_a_count=accepted_class_a,
            accepted_noisy_single_count=accepted_noisy - accepted_noisy_replicated,
            accepted_noisy_replicated_count=accepted_noisy_replicated,
        )
        update_heartbeat(
            run_dir,
            "iteration_complete",
            f"iter {iteration} verdict={verdict} candidate={candidate['candidate_id']}",
        )

        if verdict in ("accepted", "accepted_class_a", "accepted_noisy_replicated") and args.first_accepted_stop:
            stop_reason = "accepted"
            stop_detail = f"accepted candidate {candidate['candidate_id']}; first-accepted-stop is enabled"
            break

    if not stop_reason:
        stop_reason = "budget_stop"
        stop_detail = "max-iterations exhausted without an explicit stop"

    accepted_clean, neutral, rejected, noisy, infra_blocked, accepted_noisy, accepted_class_a, accepted_noisy_replicated = count_verdict_rows(run_dir)
    write_run_state(
        run_dir,
        status="stopped",
        iteration=candidates_tried,
        started_at=started_at,
        stop_reason=stop_reason,
        stop_detail=stop_detail,
        first_accepted_stop=args.first_accepted_stop,
        infra_failures=infra_failures,
        lanes_snapshot=lanes_snapshot,
        control_distribution=control_distribution,
        latest_candidate=latest_candidate,
        latest_verdict=latest_verdict,
        accepted_count=accepted_clean,
        neutral_count=neutral,
        rejected_count=rejected,
        noisy_pending_count=noisy,
        infra_blocked_count=infra_blocked,
        accepted_class_a_count=accepted_class_a,
        accepted_noisy_single_count=accepted_noisy - accepted_noisy_replicated,
        accepted_noisy_replicated_count=accepted_noisy_replicated,
    )
    update_heartbeat(run_dir, "stopped", stop_detail)

    print(f"run_dir={run_dir}")
    print(f"status=stopped")
    print(f"last_exit_reason={stop_reason}")
    print(f"iterations={candidates_tried}")
    print(f"accepted={accepted_clean}")
    print(f"neutral={neutral}")
    print(f"rejected={rejected}")
    print(f"noisy_pending={noisy}")
    print(f"infra_blocked={infra_blocked}")
    print(f"accepted_noisy={accepted_noisy}")
    print(f"accepted_noisy_replicated={accepted_noisy_replicated}")
    print(f"patch_manifest_path={run_dir / 'patches' / 'patch_manifest.json'}")
    return 0 if stop_reason in {"accepted", "budget_stop", "convergence_proven", "no_targets"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
