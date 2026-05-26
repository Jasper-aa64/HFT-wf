#!/usr/bin/env python3
"""Psi-specific host jitter calibration driver.

This script does not materialize or apply candidate patches. It runs the
existing Psi headless remote batch in control-only mode, fetches its timing
artifacts, and feeds the measured control samples into ``host_weather_audit``.
The output is a promotion-gate weather decision, not a candidate verdict.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shlex
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import host_weather_audit


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_tsv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def run_command(
    args: Sequence[str],
    *,
    timeout: int,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(args),
        cwd=str(cwd) if cwd else None,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def remote_quote(value: str | Path) -> str:
    return shlex.quote(str(value))


def ssh(remote_host: str, command: str, *, timeout: int) -> subprocess.CompletedProcess:
    return run_command(["ssh", remote_host, command], timeout=timeout)


def blocking_runners(remote_host: str, *, timeout: int) -> list[str]:
    result = ssh(remote_host, "pgrep -a -x PsiTraderRunner || true", timeout=timeout)
    return [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]


def safe_extract_tar(archive_path: Path, destination: Path) -> None:
    destination = destination.resolve()
    with tarfile.open(archive_path, "r:gz") as handle:
        for member in handle.getmembers():
            target = (destination / member.name).resolve()
            if target != destination and destination not in target.parents:
                raise RuntimeError(f"remote archive contains unsafe path: {member.name}")
        try:
            handle.extractall(destination, filter="data")
        except TypeError:
            handle.extractall(destination)


def extract_control_samples(run_dir: Path) -> list[float]:
    samples = []
    for row in read_tsv(run_dir / "timing_samples.tsv"):
        if (row.get("mode") or "").strip() != "no_compare":
            continue
        if (row.get("warm_or_cold") or "").strip() != "measured":
            continue
        if (row.get("rc") or "").strip() != "0":
            continue
        value = host_weather_audit.parse_float(row.get("elapsed_ms"))
        if value is not None:
            samples.append(value)
    return samples


def extract_paired_deltas(run_dir: Path) -> list[float]:
    control: dict[int, float] = {}
    candidate: dict[int, float] = {}
    for row in read_tsv(run_dir / "timing_samples.tsv"):
        if (row.get("rc") or "").strip() != "0":
            continue
        pair_raw = (row.get("pair_index") or "").strip()
        if not pair_raw:
            continue
        try:
            pair_index = int(pair_raw)
        except ValueError:
            continue
        elapsed = host_weather_audit.parse_float(row.get("elapsed_ms"))
        if elapsed is None:
            continue
        mode = (row.get("mode") or "").strip()
        if mode == "paired_control":
            control[pair_index] = elapsed
        elif mode == "paired_candidate":
            candidate[pair_index] = elapsed
    return [control[index] - candidate[index] for index in sorted(control) if index in candidate]


def fetch_remote_dir(remote_host: str, remote_dir: str, local_dir: Path, *, timeout: int) -> None:
    local_dir.mkdir(parents=True, exist_ok=True)
    archive_name = f"/tmp/psi_host_jitter_{os.getpid()}_{utc_stamp()}.tgz"
    create = ssh(
        remote_host,
        f"tar -C {remote_quote(remote_dir)} -czf {remote_quote(archive_name)} .",
        timeout=timeout,
    )
    if create.returncode != 0:
        raise RuntimeError(f"remote tar failed: {create.stderr.strip() or create.stdout.strip()}")
    with tempfile.TemporaryDirectory(prefix="psi_host_jitter_fetch_") as raw_tmp:
        archive_path = Path(raw_tmp) / "remote.tgz"
        scp = run_command(
            ["scp", f"{remote_host}:{archive_name}", str(archive_path)],
            timeout=timeout,
        )
        ssh(remote_host, f"rm -f {remote_quote(archive_name)}", timeout=timeout)
        if scp.returncode != 0:
            raise RuntimeError(f"scp failed: {scp.stderr.strip() or scp.stdout.strip()}")
        safe_extract_tar(archive_path, local_dir)


def run_remote_control_batch(args: argparse.Namespace, remote_run_dir: str) -> subprocess.CompletedProcess:
    remote_env = {
        "RUN_ID": Path(remote_run_dir).name,
        "RUN_DIR": remote_run_dir,
        "HEADLESS_CONTROL_DIR": remote_run_dir,
        "GENERATE_REPORT": "0",
        "MEASURE_RUNS": str(args.measure_runs),
        "ROOT": args.root,
        "ENV_FILE": args.env_file,
        "CONFIG": args.config,
        "OUTPUT_DIR": args.output_dir,
        "HOST_KEY": args.host_key,
    }
    if args.build_dir:
        remote_env["BUILD_DIR"] = args.build_dir
    if args.runner:
        remote_env["RUNNER"] = args.runner
    env_prefix = " ".join(f"{key}={remote_quote(value)}" for key, value in remote_env.items() if value)
    command = (
        f"cd {remote_quote(args.remote_hft_root)} && "
        f"{env_prefix} {remote_quote(args.bash)} {remote_quote(args.remote_batch_script)}"
    )
    return ssh(args.remote_host, command, timeout=args.remote_timeout_seconds)


def build_summary(
    *,
    args: argparse.Namespace,
    run_dir: Path,
    remote_run_dir: str,
    remote_result: subprocess.CompletedProcess | None,
    control_samples: list[float],
    paired_deltas: list[float],
    weather_summary: dict[str, Any],
    weather_readiness: dict[str, Any],
    preflight_blocking: list[str],
    remote_artifacts_dir: Path | None,
) -> dict[str, Any]:
    return {
        "schema": "psi_host_jitter_audit_v1",
        "recorded_at": utc_now(),
        "run_dir": str(run_dir),
        "remote_host": args.remote_host,
        "remote_run_dir": remote_run_dir,
        "remote_rc": remote_result.returncode if remote_result is not None else None,
        "mode": "control_only_no_candidate",
        "measure_runs": args.measure_runs,
        "control_sample_count": len(control_samples),
        "control_samples_ms": ",".join(f"{value:.3f}" for value in control_samples),
        "paired_delta_count": len(paired_deltas),
        "paired_deltas_ms": ",".join(f"{value:.3f}" for value in paired_deltas),
        "host_readiness_path": str(run_dir / "host_readiness.json"),
        "host_jitter_summary_path": str(run_dir / "host_jitter_summary.json"),
        "host_jitter_samples_path": str(run_dir / "host_jitter_samples.tsv"),
        "weather_decision": weather_summary.get("decision", ""),
        "snapshot_decision": weather_summary.get("snapshot_decision", ""),
        "weather_reasons": weather_summary.get("reasons", []),
        "promotion_gate": "ready" if weather_summary.get("decision") == "QUIET" else "blocked_by_host_weather",
        "preflight_blocking_processes": preflight_blocking,
        "remote_artifacts_dir": str(remote_artifacts_dir or ""),
        "remote_run_state_path": str(remote_artifacts_dir / "run_state.json") if remote_artifacts_dir else "",
        "remote_comparison_summary_path": str(remote_artifacts_dir / "comparison_summary.json") if remote_artifacts_dir else "",
        "notes": (
            "This audit does not apply a candidate patch and cannot accept or reject optimization candidates."
        ),
        "readiness": weather_readiness,
    }


def force_noisy_for_preflight_block(
    weather_summary: dict[str, Any],
    weather_readiness: dict[str, Any],
    preflight_blocking: list[str],
) -> None:
    if not preflight_blocking:
        return
    reason = f"preflight_active_runner_seen:{len(preflight_blocking)}"
    for payload in (weather_summary, weather_readiness):
        payload["decision"] = "NOISY"
        payload["snapshot_decision"] = "NOISY"
        reasons = list(payload.get("reasons") or [])
        if reason not in reasons:
            reasons.insert(0, reason)
        payload["reasons"] = reasons


def run_audit(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = args.run_dir.resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    remote_run_dir = args.remote_run_dir or f"{args.remote_run_root.rstrip('/')}/{run_dir.name}"
    logs_dir = run_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    preflight = blocking_runners(args.remote_host, timeout=args.remote_timeout_seconds)
    if preflight and not args.allow_busy:
        snapshots = host_weather_audit.collect_remote_snapshots(
            remote_host=args.remote_host,
            sample_count=args.weather_sample_count,
            interval_seconds=args.weather_sample_interval_seconds,
            process_names=args.process_name or host_weather_audit.DEFAULT_PROCESS_NAMES,
            timeout_seconds=args.remote_timeout_seconds,
        )
        weather_summary = host_weather_audit.classify_weather(
            snapshots,
            control_samples_ms=[],
            paired_deltas_ms=[],
        )
        weather_readiness = host_weather_audit.build_readiness(
            snapshots,
            weather_summary,
            host_key=args.host_key,
            remote_host=args.remote_host,
        )
        force_noisy_for_preflight_block(weather_summary, weather_readiness, preflight)
        host_weather_audit.write_json(run_dir / "host_readiness.json", weather_readiness)
        host_weather_audit.write_tsv(run_dir / "host_jitter_samples.tsv", snapshots, host_weather_audit.SNAPSHOT_FIELDS)
        host_weather_audit.write_json(run_dir / "host_jitter_summary.json", weather_summary)
        summary = build_summary(
            args=args,
            run_dir=run_dir,
            remote_run_dir=remote_run_dir,
            remote_result=None,
            control_samples=[],
            paired_deltas=[],
            weather_summary=weather_summary,
            weather_readiness=weather_readiness,
            preflight_blocking=preflight,
            remote_artifacts_dir=None,
        )
        summary["promotion_gate"] = "blocked_by_preflight_runner"
        write_json(run_dir / "psi_host_jitter_audit_summary.json", summary)
        return summary

    remote_result = run_remote_control_batch(args, remote_run_dir)
    (logs_dir / "remote_stdout.log").write_text(remote_result.stdout or "", encoding="utf-8")
    (logs_dir / "remote_stderr.log").write_text(remote_result.stderr or "", encoding="utf-8")
    remote_artifacts_dir = run_dir / "remote_artifacts"
    if remote_result.returncode == 0 or args.fetch_on_failure:
        fetch_remote_dir(
            args.remote_host,
            remote_run_dir,
            remote_artifacts_dir,
            timeout=args.remote_timeout_seconds,
        )

    control_samples = extract_control_samples(remote_artifacts_dir)
    paired_deltas = extract_paired_deltas(remote_artifacts_dir)
    snapshots = host_weather_audit.collect_remote_snapshots(
        remote_host=args.remote_host,
        sample_count=args.weather_sample_count,
        interval_seconds=args.weather_sample_interval_seconds,
        process_names=args.process_name or host_weather_audit.DEFAULT_PROCESS_NAMES,
        timeout_seconds=args.remote_timeout_seconds,
    )
    weather_summary = host_weather_audit.classify_weather(
        snapshots,
        control_samples_ms=control_samples,
        paired_deltas_ms=paired_deltas,
    )
    weather_readiness = host_weather_audit.build_readiness(
        snapshots,
        weather_summary,
        host_key=args.host_key,
        remote_host=args.remote_host,
    )
    host_weather_audit.write_json(run_dir / "host_readiness.json", weather_readiness)
    host_weather_audit.write_tsv(run_dir / "host_jitter_samples.tsv", snapshots, host_weather_audit.SNAPSHOT_FIELDS)
    host_weather_audit.write_json(run_dir / "host_jitter_summary.json", weather_summary)
    summary = build_summary(
        args=args,
        run_dir=run_dir,
        remote_run_dir=remote_run_dir,
        remote_result=remote_result,
        control_samples=control_samples,
        paired_deltas=paired_deltas,
        weather_summary=weather_summary,
        weather_readiness=weather_readiness,
        preflight_blocking=preflight,
        remote_artifacts_dir=remote_artifacts_dir,
    )
    if remote_result.returncode != 0:
        summary["promotion_gate"] = "blocked_by_remote_failure"
        summary["weather_reasons"] = list(summary.get("weather_reasons") or []) + [
            f"remote_control_batch_failed:{remote_result.returncode}"
        ]
    write_json(run_dir / "psi_host_jitter_audit_summary.json", summary)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a Psi control-only host jitter calibration audit.")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--remote-host", default="devbox")
    parser.add_argument("--remote-hft-root", default="/root/work/HFT-wf")
    parser.add_argument("--remote-batch-script", default="scripts/psi_headless_remote.sh")
    parser.add_argument("--remote-run-root", default="/root/work/psi_experiments/host_jitter")
    parser.add_argument("--remote-run-dir", default="")
    parser.add_argument("--remote-timeout-seconds", type=int, default=7200)
    parser.add_argument("--bash", default="bash")
    parser.add_argument("--root", default="/root/work/Code1/psi-trader-liangjunming")
    parser.add_argument("--env-file", default="/root/work/.toolchain/psi-env.sh")
    parser.add_argument("--config", default="/root/work/Code1/psi-trader-liangjunming/PsiTraderRunner/config.yaml")
    parser.add_argument("--output-dir", default="/root/work/Code1/dataset/output")
    parser.add_argument("--build-dir", default="")
    parser.add_argument("--runner", default="")
    parser.add_argument("--host-key", default="17062")
    parser.add_argument("--measure-runs", type=int, default=5)
    parser.add_argument("--weather-sample-count", type=int, default=3)
    parser.add_argument("--weather-sample-interval-seconds", type=float, default=1.0)
    parser.add_argument("--process-name", action="append", default=[])
    parser.add_argument("--allow-busy", action="store_true")
    parser.add_argument("--fetch-on-failure", action="store_true")
    parser.add_argument("--print-summary", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = run_audit(args)
    if args.print_summary:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        print(f"decision={summary.get('weather_decision', '')}")
        print(f"promotion_gate={summary.get('promotion_gate', '')}")
        print(f"run_dir={summary.get('run_dir', '')}")
        print(f"remote_run_dir={summary.get('remote_run_dir', '')}")
        print(f"control_sample_count={summary.get('control_sample_count', 0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
