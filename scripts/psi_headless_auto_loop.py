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
PROFILE_FIELDS = ["stage", "total_ms", "count", "avg_ms", "source"]
HOTSPOT_FIELDS = ["rank", "stage", "total_ms", "avg_ms", "count", "score", "notes"]


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
        ignore = shutil.ignore_patterns(".git", "build", "experiments", "headless_runs")
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
        rc, output = _run_external_patch_command(command, run_dir, workspace, source_root, candidate, iteration)
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
            "CANDIDATE_ID": candidate["candidate_id"],
            "CANDIDATE_LANE": candidate["lane"],
            "CANDIDATE_TARGET": candidate["target"],
            "CANDIDATE_TOUCHED_FILES": "|".join(candidate.get("touched_files", [])),
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
        "CANDIDATE_ID": candidate["candidate_id"],
        "CANDIDATE_LANE": candidate["lane"],
        "CANDIDATE_TARGET": candidate["target"],
        "CANDIDATE_TOUCHED_FILES": "|".join(candidate.get("touched_files", [])),
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
        if not args.runner:
            control_root = str(args.root or "/root/work/Code1/psi-trader-liangjunming")
            remote_env["RUNNER"] = _remote_default_runner(_remote_default_build_dir(control_root))
    elif args.root:
        remote_env["ROOT"] = str(args.root)
    if args.candidate_runner and not remote_ws:
        remote_env["CANDIDATE_RUNNER"] = str(args.candidate_runner)
    if args.build_dir and not remote_ws:
        remote_env["BUILD_DIR"] = str(args.build_dir)

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
        "paired_sample_count": str(batch_state.get("paired_sample_count", "")),
        "timing_verdict": batch_state.get("timing_verdict", batch_state.get("timing_status", "")),
        "sample_count": len(batch_state.get("candidate_samples_ms") or []),
        "samples_ms": ",".join(
            f"{v:.3f}" for v in (batch_state.get("candidate_samples_ms") or [])
        ),
        "control_median_ms": f"{batch_state.get('control_median_ms', 0) or 0:.3f}",
        "candidate_median_ms": f"{batch_state.get('candidate_median_ms', 0) or 0:.3f}",
        "delta_ms": f"{batch_state.get('delta_ms', 0) or 0:.3f}",
        "compare_result": batch_state.get("compare_status", ""),
        "noise_flag": batch_state.get("noise_flag", ""),
        "verdict": verdict,
        "retry_condition": retry_condition,
        "stop_reason": stop_reason,
        "recorded_at": utc_now(),
        "notes": notes,
    }
    append_tsv_row(run_dir / "attempts.tsv", row, ATTEMPTS_FIELDS)


def record_neutral_pool_entry(
    run_dir: Path,
    candidate: dict[str, Any],
    batch_state: dict[str, Any],
    retry_condition: str,
) -> None:
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
        "timing_summary": (
            f"sample_count={len(batch_state.get('candidate_samples_ms') or [])};"
            f" median_ms={batch_state.get('candidate_median_ms', 0):.3f};"
            f" delta_ms={batch_state.get('delta_ms', 0):.3f};"
            f" range_ms={(max(batch_state.get('candidate_samples_ms') or [0]) - min(batch_state.get('candidate_samples_ms') or [0])):.3f};"
            f" n={len(batch_state.get('candidate_samples_ms') or [])}"
        ),
        "semantic_risk": candidate.get("semantic_risk", ""),
        "stack_compatibility": candidate.get("stack_compatibility", "single"),
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
) -> None:
    """Write control + candidate rows into timing_history.tsv for this iteration."""

    control_samples = batch_state.get("control_samples_ms") or []
    candidate_samples = batch_state.get("candidate_samples_ms") or []
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
                "timing_verdict": batch_state.get("timing_status", "") if kind == "candidate" else "",
                "timing_verdict_reason": "",
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
                "verdict": batch_state.get("timing_status", "") if kind == "candidate" else "",
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

    noise_flag = (batch_state.get("noise_flag") or "").upper()
    timing = (batch_state.get("timing_verdict") or batch_state.get("timing_status") or "").upper()
    if noise_flag == "NOISY" or timing == "NOISY_PENDING":
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


def iteration_step(
    args: argparse.Namespace,
    run_dir: Path,
    iteration: int,
    seen_candidate_ids: set[str],
    cooldown_targets: set[str],
    host_key: str,
) -> tuple[dict[str, Any] | None, str, str, dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Drive one iteration. Returns (candidate, verdict, stop_reason, lanes, batch_state)."""

    seed_profile_if_missing(run_dir)
    control_distribution = refresh_control_distribution(run_dir, host_key)
    update_heartbeat(run_dir, "generate", "building three-lane candidate queue")
    lanes = generate_candidates(run_dir)
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
        return None, "", "no_targets" if lanes_are_empty(lanes) else "", lanes, {}

    materialized, patch_meta, patch_reason = materialize_candidate_patch(args, run_dir, candidate, iteration)
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
        # Infrastructure failure; the remote script will have written failure_analysis.json.
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
    elif verdict == "NOISY_PENDING":
        record_retry_condition(
            run_dir,
            candidate,
            status="NOISY_PENDING",
            noise_flag="NOISY",
            required_condition=retry_condition,
            last_exit_reason="",
            notes="candidate-level pause; loop continues",
        )
        set_patch_status(run_dir, candidate["candidate_id"], "reverted", note="noisy; reverted pending rerun")
    elif verdict == "rejected":
        set_patch_status(run_dir, candidate["candidate_id"], "reverted", note="rejected; reverted")

    record_attempt(
        run_dir,
        iteration=iteration,
        candidate=candidate,
        batch_state=batch_state,
        verdict=verdict,
        retry_condition=retry_condition,
        stop_reason="",
        notes=candidate.get("expected_effect", ""),
    )
    upsert_timing_from_batch(run_dir, candidate, batch_state, host_key)

    return candidate, verdict, "", lanes, batch_state


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
    parser.add_argument("--first-accepted-stop", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--repeated-infra-failures", type=int, default=2)
    parser.add_argument("--stack-throttle", type=int, default=3, help="Try a neutral stack at most every N iterations")
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
    return parser


def resolve_stop_file(run_dir: Path, stop_file: str | None) -> Path:
    if stop_file:
        path = Path(stop_file)
        return path if path.is_absolute() else (run_dir / path)
    return run_dir / "STOP"


def count_verdict_rows(run_dir: Path) -> tuple[int, int, int, int]:
    counts = {"accepted": 0, "neutral": 0, "rejected": 0, "NOISY_PENDING": 0}
    for row in read_tsv(run_dir / "attempts.tsv"):
        verdict = (row.get("verdict") or "").strip()
        if verdict in counts:
            counts[verdict] += 1
    return counts["accepted"], counts["neutral"], counts["rejected"], counts["NOISY_PENDING"]


def main() -> int:
    args = build_parser().parse_args()
    if args.max_iterations < 1:
        raise SystemExit("--max-iterations must be >= 1")
    if args.max_hours <= 0:
        raise SystemExit("--max-hours must be > 0")

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
    )

    seen_ids: set[str] = set()
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

    for iteration in range(1, args.max_iterations + 1):
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

        accepted, neutral, rejected, noisy = count_verdict_rows(run_dir)
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
            accepted_count=accepted,
            neutral_count=neutral,
            rejected_count=rejected,
            noisy_pending_count=noisy,
        )
        update_heartbeat(
            run_dir,
            "iteration_complete",
            f"iter {iteration} verdict={verdict} candidate={candidate['candidate_id']}",
        )

        if verdict == "accepted" and args.first_accepted_stop:
            stop_reason = "accepted"
            stop_detail = f"accepted candidate {candidate['candidate_id']}; first-accepted-stop is enabled"
            break

    if not stop_reason:
        stop_reason = "budget_stop"
        stop_detail = "max-iterations exhausted without an explicit stop"

    accepted, neutral, rejected, noisy = count_verdict_rows(run_dir)
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
        accepted_count=accepted,
        neutral_count=neutral,
        rejected_count=rejected,
        noisy_pending_count=noisy,
    )
    update_heartbeat(run_dir, "stopped", stop_detail)

    print(f"run_dir={run_dir}")
    print(f"status=stopped")
    print(f"last_exit_reason={stop_reason}")
    print(f"iterations={candidates_tried}")
    print(f"accepted={accepted}")
    print(f"neutral={neutral}")
    print(f"rejected={rejected}")
    print(f"noisy_pending={noisy}")
    print(f"patch_manifest_path={run_dir / 'patches' / 'patch_manifest.json'}")
    return 0 if stop_reason in {"accepted", "budget_stop", "convergence_proven", "no_targets"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
