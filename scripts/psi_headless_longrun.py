#!/usr/bin/env python3
"""Bounded long-run controller for Psi headless optimization batches.

This script is the control layer around ``psi_headless_remote.sh``. It keeps the
single-batch evidence contract intact, then publishes a durable long-run status
surface at the run root after each batch.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from psi_timing_history import HISTORY_FIELDNAMES, read_history_rows, upsert_history_rows


STOP_REASONS = {
    "accepted",
    "budget_stop",
    "convergence_proven",
    "no_targets",
    "remote_failed",
    "repeated_infra_failure",
    "user_stopped",
}
CONTINUE_ACTIONS = {"", "continue", "continue_to_next_round", "refresh_candidate_selection"}
INFRA_FAILURE_REASONS = {"missing_env_file", "runner_busy", "build_failed", "timing_failed"}
REQUIRED_BATCH_ARTIFACTS = (
    "run_state.json",
    "heartbeat.json",
    "attempts.tsv",
    "timing_history.tsv",
    "reports",
    "patches",
)
LATEST_BATCH_MIRRORS = (
    "run_state.json",
    "heartbeat.json",
    "reports",
    "patches",
)
LATEST_BATCH_TABLES = (
    "profile.tsv",
    "hotspots.tsv",
    "cooldown.tsv",
    "patch_queue.tsv",
    "neutral_pool.tsv",
    "retry_conditions.tsv",
)
TOP_LEVEL_TSVS = (
    "attempts.tsv",
    "profile.tsv",
    "hotspots.tsv",
    "cooldown.tsv",
    "patch_queue.tsv",
    "neutral_pool.tsv",
    "retry_conditions.tsv",
    "timing_history.tsv",
)
DEFAULT_TSV_HEADERS = {
    "attempts.tsv": ["batch_index", "batch_id", "rank", "kind", "target", "verdict"],
    "profile.tsv": ["stage", "total_ms", "count", "avg_ms", "source"],
    "hotspots.tsv": ["rank", "stage", "total_ms", "avg_ms", "count", "score", "notes"],
    "cooldown.tsv": ["target", "status", "cooldown_runs_remaining", "reason", "source_profile", "notes"],
    "patch_queue.tsv": ["rank", "candidate_id", "target", "patch_path", "queue_state", "retry_condition", "notes"],
    "neutral_pool.tsv": ["candidate_id", "target", "lane", "patch_path", "validation_status", "retry_condition", "notes"],
    "retry_conditions.tsv": ["target", "status", "noise_flag", "retry_after", "required_condition", "last_exit_reason", "notes"],
    "timing_history.tsv": HISTORY_FIELDNAMES,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def parse_bool(raw: object) -> bool:
    if raw is True:
        return True
    if raw is False or raw in (None, ""):
        return False
    if isinstance(raw, str):
        text = raw.strip().lower()
        if text in {"1", "true", "yes", "y", "on"}:
            return True
        if text in {"0", "false", "no", "n", "off"}:
            return False
    return bool(raw)


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


def ensure_artifact_tree(run_dir: Path) -> None:
    for child in ("batches", "logs", "reports", "patches"):
        (run_dir / child).mkdir(parents=True, exist_ok=True)
    for name in TOP_LEVEL_TSVS:
        path = run_dir / name
        if not path.exists():
            write_tsv(path, [], DEFAULT_TSV_HEADERS[name])


def copy_if_exists(source: Path, destination: Path) -> str:
    if not source.exists():
        return ""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(source, destination)
    else:
        shutil.copy2(source, destination)
    return str(destination)


def sync_latest_artifacts(run_dir: Path, batch_dir: Path) -> dict[str, str]:
    copied: dict[str, str] = {}
    for name in LATEST_BATCH_MIRRORS:
        destination = run_dir / name
        copied[name] = copy_if_exists(batch_dir / name, destination)
    copied["logs"] = copy_if_exists(batch_dir / "logs", run_dir / "logs" / "latest_batch")
    for name in LATEST_BATCH_TABLES:
        copied[name] = copy_if_exists(batch_dir / name, run_dir / name)
    for optional in ("comparison_summary.json", "failure_analysis.json"):
        copied[optional] = copy_if_exists(batch_dir / optional, run_dir / optional)
    patch_manifest = batch_dir / "patches" / "patch_manifest.json"
    copied["patch_manifest.json"] = copy_if_exists(patch_manifest, run_dir / "patches" / "patch_manifest.json")
    return copied


def append_prefixed_tsv(target: Path, source: Path, batch_id: str, batch_index: int) -> None:
    rows = read_tsv(source)
    if not rows:
        return
    source_fieldnames = list(rows[0].keys())
    fieldnames = ["batch_index", "batch_id", *source_fieldnames]
    existing = read_tsv(target)
    combined: list[dict[str, Any]] = list(existing)
    for row in rows:
        combined.append({"batch_index": batch_index, "batch_id": batch_id, **row})
    write_tsv(target, combined, fieldnames)


def append_attempt_rows(target: Path, source: Path, batch_id: str, batch_index: int) -> None:
    append_prefixed_tsv(target, source, batch_id, batch_index)


def upsert_timing_history(target: Path, source: Path, batch_id: str) -> None:
    rows = read_history_rows([source])
    if not rows:
        return
    for row in rows:
        notes = row.get("notes", "")
        row["notes"] = f"{notes}; batch_id={batch_id}" if notes else f"batch_id={batch_id}"
    upsert_history_rows(target, rows)


def aggregate_batch_tsvs(run_dir: Path, batch_dir: Path, batch_id: str, batch_index: int) -> None:
    append_attempt_rows(run_dir / "attempts.tsv", batch_dir / "attempts.tsv", batch_id, batch_index)
    upsert_timing_history(run_dir / "timing_history.tsv", batch_dir / "timing_history.tsv", batch_id)


def count_verdicts(attempt_rows: list[dict[str, str]]) -> dict[str, int]:
    counts = {"accepted": 0, "neutral": 0, "rejected": 0}
    for row in attempt_rows:
        verdict = (row.get("verdict") or "").strip()
        if verdict in counts:
            counts[verdict] += 1
    return counts


def count_noisy(run_dir: Path) -> int:
    retry_rows = read_tsv(run_dir / "retry_conditions.tsv")
    queue_rows = read_tsv(run_dir / "patch_queue.tsv")
    targets = {
        (row.get("target") or "").strip()
        for row in retry_rows
        if (row.get("status") or "").strip() == "NOISY_PENDING"
    }
    targets.update(
        (row.get("target") or "").strip()
        for row in queue_rows
        if (row.get("queue_state") or "").strip() == "NOISY_PENDING"
    )
    targets.discard("")
    return len(targets)


def artifact_presence(batch_dir: Path) -> dict[str, bool]:
    return {name: (batch_dir / name).exists() for name in REQUIRED_BATCH_ARTIFACTS}


def update_heartbeat(run_dir: Path, phase: str, current_step: str, last_log: Path | None = None) -> None:
    write_json(
        run_dir / "heartbeat.json",
        {
            "updated_at": utc_now(),
            "phase": phase,
            "current_step": current_step,
            "pid_or_session": str(os.getpid()),
            "last_log": str((last_log or (run_dir / "logs" / "longrun.log")).resolve()),
        },
    )


def latest_report_path(state: dict[str, Any], batch_dir: Path) -> str:
    report = str(state.get("latest_report") or "")
    if report:
        return report
    reports = sorted((batch_dir / "reports").glob("**/*.md")) if (batch_dir / "reports").exists() else []
    return str(reports[-1]) if reports else ""


def infer_stop_reason(
    *,
    returncode: int,
    batch_state: dict[str, Any],
    batch_dir: Path,
    batch_index: int,
    max_batches: int,
    started_monotonic: float,
    max_hours: float,
    infra_failures: int,
    repeated_infra_failures: int,
    first_accepted_stop: bool,
) -> tuple[str, str]:
    last_exit_reason = str(batch_state.get("last_exit_reason") or "").strip()
    next_round_action = str(batch_state.get("next_round_action") or "").strip()
    comparison_accepted = parse_bool(batch_state.get("comparison_accepted"))

    if returncode != 0 and last_exit_reason:
        return "remote_failed", f"batch exited rc={returncode}; batch reason={last_exit_reason}"
    if returncode != 0:
        return "remote_failed", f"batch exited rc={returncode}"
    if infra_failures >= repeated_infra_failures:
        return "repeated_infra_failure", f"{infra_failures} infrastructure failures reached the limit"
    if last_exit_reason == "accepted" and first_accepted_stop:
        return "accepted", "accepted comparison recorded and first-accepted-stop is enabled"
    if comparison_accepted and first_accepted_stop:
        return "accepted", "accepted comparison recorded and first-accepted-stop is enabled"
    if last_exit_reason == "accepted" or comparison_accepted:
        return "", "accepted comparison recorded; continuing because first-accepted-stop is disabled"
    if last_exit_reason in STOP_REASONS and last_exit_reason:
        return last_exit_reason, f"batch requested stop reason {last_exit_reason}"
    if next_round_action and next_round_action not in CONTINUE_ACTIONS:
        return "no_targets" if next_round_action == "stop_no_targets" else "remote_failed", f"batch next_round_action={next_round_action}"
    elapsed_hours = (time.monotonic() - started_monotonic) / 3600.0
    if elapsed_hours >= max_hours:
        return "budget_stop", f"max-hours reached ({elapsed_hours:.3f} >= {max_hours:.3f})"
    if batch_index >= max_batches:
        return "budget_stop", f"max-batches reached ({batch_index} >= {max_batches})"
    missing = [name for name, present in artifact_presence(batch_dir).items() if not present]
    if missing:
        return "remote_failed", f"batch missing required artifacts: {','.join(missing)}"
    return "", ""


def write_longrun_state(
    run_dir: Path,
    *,
    status: str,
    batch_index: int,
    max_batches: int,
    batch_dir: Path | None,
    batch_state: dict[str, Any],
    started_at: str,
    stop_reason: str,
    stop_detail: str,
    first_accepted_stop: bool,
    infra_failures: int,
    batch_rc: int | None,
) -> None:
    attempts = read_tsv(run_dir / "attempts.tsv")
    counts = count_verdicts(attempts)
    latest_report = latest_report_path(batch_state, batch_dir) if batch_dir else ""
    state = {
        "status": status,
        "mode": "headless_longrun",
        "run_id": run_dir.name,
        "started_at": started_at,
        "updated_at": utc_now(),
        "iteration": batch_index,
        "current_batch_index": batch_index,
        "max_batches": max_batches,
        "latest_batch_dir": str(batch_dir) if batch_dir else "",
        "latest_batch_rc": "" if batch_rc is None else batch_rc,
        "latest_batch_status": batch_state.get("batch_status", ""),
        "accepted_count": counts["accepted"],
        "neutral_count": counts["neutral"],
        "rejected_count": counts["rejected"],
        "noise_status": "NOISY" if count_noisy(run_dir) else str(batch_state.get("noise_status") or "ok"),
        "noisy_candidate_count": count_noisy(run_dir),
        "last_exit_reason": stop_reason,
        "last_exit_detail": stop_detail,
        "first_accepted_stop": first_accepted_stop,
        "infra_failure_count": infra_failures,
        "supported_stop_reasons": sorted(STOP_REASONS),
        "latest_report": latest_report,
        "build_status": batch_state.get("build_status", ""),
        "compare_status": batch_state.get("compare_status", ""),
        "timing_status": batch_state.get("timing_status", ""),
        "timing_verdict": batch_state.get("timing_verdict", batch_state.get("timing_status", "")),
        "timing_verdict_reason": batch_state.get("timing_verdict_reason", ""),
        "comparison_decision": batch_state.get("comparison_decision", ""),
        "comparison_accepted": parse_bool(batch_state.get("comparison_accepted")),
        "paired_evidence_status": batch_state.get("paired_evidence_status", ""),
        "paired_evidence_reason": batch_state.get("paired_evidence_reason", ""),
        "sample_policy": batch_state.get("sample_policy", {}),
        "batch_continuation": batch_state.get("batch_continuation", ""),
        "next_round_action": batch_state.get("next_round_action", ""),
        "comparison_summary_path": str(run_dir / "comparison_summary.json") if (run_dir / "comparison_summary.json").exists() else "",
        "patch_manifest_path": str(run_dir / "patches" / "patch_manifest.json") if (run_dir / "patches" / "patch_manifest.json").exists() else "",
        "patch_queue_path": str(run_dir / "patch_queue.tsv"),
        "neutral_pool_path": str(run_dir / "neutral_pool.tsv"),
        "retry_conditions_path": str(run_dir / "retry_conditions.tsv"),
        "timing_history_path": str(run_dir / "timing_history.tsv"),
        "batch_artifact_presence": artifact_presence(batch_dir) if batch_dir else {},
        "batch_state": batch_state,
    }
    write_json(run_dir / "run_state.json", state)


def write_batch_report(run_dir: Path, rows: list[dict[str, Any]]) -> Path:
    report_dir = run_dir / "reports" / date.today().isoformat()
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"{date.today().isoformat()} psi headless longrun report.md"
    lines = [
        "# Psi Headless Long-Run Report",
        "",
        f"- run_dir: `{run_dir}`",
        f"- updated_at: `{utc_now()}`",
        "",
        "| batch | status | rc | stop_reason | continuation | report |",
        "|---:|---|---:|---|---|---|",
    ]
    for row in rows:
        lines.append(
            "| {batch_index} | {status} | {rc} | {stop_reason} | {continuation} | {report} |".format(
                batch_index=row.get("batch_index", ""),
                status=row.get("status", ""),
                rc=row.get("rc", ""),
                stop_reason=row.get("stop_reason", ""),
                continuation=row.get("continuation", ""),
                report=row.get("report", ""),
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def run_batch(args: argparse.Namespace, run_dir: Path, batch_index: int) -> tuple[int, Path]:
    batch_id = f"{run_dir.name}_batch_{batch_index:03d}"
    batch_dir = run_dir / "batches" / batch_id
    env = os.environ.copy()
    env.update(
        {
            "RUN_ID": batch_id,
            "RUN_DIR": str(batch_dir),
            "HEADLESS_CONTROL_DIR": str(batch_dir),
            "GENERATE_REPORT": "1" if args.generate_report else env.get("GENERATE_REPORT", "0"),
            "REPORT_SCRIPT": str((repo_root() / "scripts" / "psi_daily_report.py").resolve()),
            "REPORT_ROOT": str(batch_dir / "reports"),
            "MEASURE_RUNS": str(args.measure_runs),
        }
    )
    for name in ("ROOT", "ENV_FILE", "BUILD_DIR", "RUNNER", "CANDIDATE_RUNNER", "CONFIG", "OUTPUT_DIR"):
        value = getattr(args, name.lower())
        if value:
            env[name] = str(value)
    if args.candidate_runner:
        env["CANDIDATE_RUNNER"] = str(args.candidate_runner)

    script = args.batch_script.resolve()
    log_path = run_dir / "logs" / f"batch_{batch_index:03d}.log"
    update_heartbeat(run_dir, "batch_start", f"starting {batch_id}", log_path)
    with log_path.open("w", encoding="utf-8") as log_handle:
        log_handle.write(f"batch_id={batch_id}\n")
        log_handle.write(f"batch_dir={batch_dir}\n")
        log_handle.flush()
        result = subprocess.run([args.bash, str(script)], cwd=repo_root(), env=env, stdout=log_handle, stderr=subprocess.STDOUT)
    return result.returncode, batch_dir


def resolve_stop_file(run_dir: Path, stop_file: str | None) -> Path:
    if stop_file:
        path = Path(stop_file)
        return path if path.is_absolute() else (run_dir / path)
    return run_dir / "STOP"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run bounded Psi headless batches with a durable long-run status surface.")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--batch-script", type=Path, default=repo_root() / "scripts" / "psi_headless_remote.sh")
    parser.add_argument("--bash", default="bash")
    parser.add_argument("--max-batches", type=int, default=3)
    parser.add_argument("--max-hours", type=float, default=8.0)
    parser.add_argument("--measure-runs", type=int, default=5)
    parser.add_argument("--first-accepted-stop", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--repeated-infra-failures", type=int, default=2)
    parser.add_argument("--generate-report", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--root")
    parser.add_argument("--env-file")
    parser.add_argument("--build-dir")
    parser.add_argument("--runner")
    parser.add_argument("--candidate-runner")
    parser.add_argument("--config")
    parser.add_argument("--output-dir")
    parser.add_argument("--stop-file", help="Stop before launching the next batch when this file exists. Defaults to <run-dir>/STOP.")
    parser.add_argument("--dry-run", action="store_true", help="Write the long-run surface without executing the remote batch script.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.max_batches < 1:
        raise SystemExit("--max-batches must be >= 1")
    if args.max_hours <= 0:
        raise SystemExit("--max-hours must be > 0")
    if args.measure_runs < 1:
        raise SystemExit("--measure-runs must be >= 1")

    run_dir = args.run_dir.resolve()
    ensure_artifact_tree(run_dir)
    started_at = utc_now()
    started_monotonic = time.monotonic()
    batch_rows: list[dict[str, Any]] = []
    infra_failures = 0
    stop_reason = ""
    stop_detail = ""
    batch_state: dict[str, Any] = {}
    batch_dir: Path | None = None
    batch_rc: int | None = None
    stop_file = resolve_stop_file(run_dir, args.stop_file)

    write_longrun_state(
        run_dir,
        status="running",
        batch_index=0,
        max_batches=args.max_batches,
        batch_dir=None,
        batch_state={},
        started_at=started_at,
        stop_reason="",
        stop_detail="",
        first_accepted_stop=args.first_accepted_stop,
        infra_failures=0,
        batch_rc=None,
    )
    update_heartbeat(run_dir, "init", "long-run controller initialized")

    for batch_index in range(1, args.max_batches + 1):
        if stop_file.exists():
            stop_reason = "user_stopped"
            stop_detail = f"stop file exists: {stop_file}"
            write_longrun_state(
                run_dir,
                status="stopped",
                batch_index=batch_index - 1,
                max_batches=args.max_batches,
                batch_dir=batch_dir,
                batch_state=batch_state,
                started_at=started_at,
                stop_reason=stop_reason,
                stop_detail=stop_detail,
                first_accepted_stop=args.first_accepted_stop,
                infra_failures=infra_failures,
                batch_rc=batch_rc,
            )
            update_heartbeat(run_dir, "stopped", stop_detail)
            break

        if args.dry_run:
            batch_id = f"{run_dir.name}_batch_{batch_index:03d}"
            batch_dir = run_dir / "batches" / batch_id
            batch_dir.mkdir(parents=True, exist_ok=True)
            batch_state = {
                "status": "running",
                "batch_status": "completed",
                "batch_continuation": "continue_to_next_round",
                "next_round_action": "continue",
                "sample_policy": {"screening_measured_samples": args.measure_runs},
                "dry_run": True,
            }
            write_json(batch_dir / "run_state.json", batch_state)
            write_json(batch_dir / "heartbeat.json", {"updated_at": utc_now(), "phase": "batch_complete", "current_step": "dry_run"})
            for dirname in ("logs", "reports", "patches"):
                (batch_dir / dirname).mkdir(exist_ok=True)
            write_tsv(batch_dir / "attempts.tsv", [], ["rank", "kind", "target", "verdict"])
            write_tsv(batch_dir / "timing_history.tsv", [], ["history_key", "run_id", "kind", "verdict"])
            batch_rc = 0
        else:
            batch_rc, batch_dir = run_batch(args, run_dir, batch_index)
            batch_state = read_json(batch_dir / "run_state.json")

        sync_latest_artifacts(run_dir, batch_dir)
        aggregate_batch_tsvs(run_dir, batch_dir, batch_dir.name, batch_index)
        failure_reason = str(batch_state.get("failure_analysis_reason") or batch_state.get("last_exit_reason") or "")
        if failure_reason in INFRA_FAILURE_REASONS:
            infra_failures += 1

        stop_reason, stop_detail = infer_stop_reason(
            returncode=batch_rc,
            batch_state=batch_state,
            batch_dir=batch_dir,
            batch_index=batch_index,
            max_batches=args.max_batches,
            started_monotonic=started_monotonic,
            max_hours=args.max_hours,
            infra_failures=infra_failures,
            repeated_infra_failures=args.repeated_infra_failures,
            first_accepted_stop=args.first_accepted_stop,
        )
        if parse_bool(batch_state.get("comparison_accepted")) and not args.first_accepted_stop and not stop_reason:
            update_heartbeat(run_dir, "accepted_audit", "accepted artifacts recorded; refreshing candidate selection")

        batch_rows.append(
            {
                "batch_index": batch_index,
                "status": batch_state.get("status", ""),
                "rc": batch_rc,
                "stop_reason": stop_reason or batch_state.get("last_exit_reason", ""),
                "continuation": batch_state.get("batch_continuation", ""),
                "report": latest_report_path(batch_state, batch_dir),
            }
        )
        report_path = write_batch_report(run_dir, batch_rows)
        write_longrun_state(
            run_dir,
            status="stopped" if stop_reason else "running",
            batch_index=batch_index,
            max_batches=args.max_batches,
            batch_dir=batch_dir,
            batch_state=batch_state,
            started_at=started_at,
            stop_reason=stop_reason,
            stop_detail=stop_detail,
            first_accepted_stop=args.first_accepted_stop,
            infra_failures=infra_failures,
            batch_rc=batch_rc,
        )
        state = read_json(run_dir / "run_state.json")
        state["latest_report"] = str(report_path)
        write_json(run_dir / "run_state.json", state)
        update_heartbeat(
            run_dir,
            "stopped" if stop_reason else "batch_complete",
            stop_detail or f"batch {batch_index} complete; continuing",
            run_dir / "logs" / f"batch_{batch_index:03d}.log",
        )
        if stop_reason:
            break

    if not stop_reason:
        stop_reason = "budget_stop"
        stop_detail = "batch loop exited without another explicit stop; max-batches exhausted"
        write_longrun_state(
            run_dir,
            status="stopped",
            batch_index=len(batch_rows),
            max_batches=args.max_batches,
            batch_dir=batch_dir,
            batch_state=batch_state,
            started_at=started_at,
            stop_reason=stop_reason,
            stop_detail=stop_detail,
            first_accepted_stop=args.first_accepted_stop,
            infra_failures=infra_failures,
            batch_rc=batch_rc,
        )
        update_heartbeat(run_dir, "stopped", stop_detail)

    print(f"run_dir={run_dir}")
    print("status=stopped")
    print(f"last_exit_reason={stop_reason}")
    print(f"last_exit_detail={stop_detail}")
    print(f"batches={len(batch_rows)}")
    print(f"latest_report_path={read_json(run_dir / 'run_state.json').get('latest_report', '')}")
    return 0 if stop_reason in {"accepted", "budget_stop", "convergence_proven", "no_targets"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
