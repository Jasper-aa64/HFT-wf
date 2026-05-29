from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


def run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    print("RUN " + " ".join(command), flush=True)
    return subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def remote_preflight(remote_host: str) -> None:
    if not remote_host:
        return
    command = (
        "busy=$(pgrep -af 'psi_headless_remote|twap_headless_remote|PsiTraderRunner' | grep -v grep || true); "
        "if [ -n \"$busy\" ]; then echo \"$busy\"; exit 42; fi; "
        "if [ -f /root/work/.perf_validation.lock ]; then echo validation_lock_present; cat /root/work/.perf_validation.lock; exit 43; fi; "
        "echo preflight_ok"
    )
    completed = run_command(["ssh", remote_host, command])
    if completed.returncode in {42, 43}:
        raise SystemExit("NOT_READY remote preflight blocked smoke:\n" + (completed.stdout or ""))
    require(completed.returncode == 0, f"remote preflight failed rc={completed.returncode}:\n{completed.stdout}")


def read_json(path: Path) -> dict[str, object]:
    if not path.exists():
        raise SystemExit(f"missing artifact: {path}")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def run_prepared(
    *,
    driver: Path,
    repo_root: Path,
    run_dir: Path,
    source_root: Path,
    patch_command: str,
    candidate_ledger: Path,
    candidate_id: str,
    target: str,
    hypothesis: str,
    expected_effect: str,
    semantic_risk: str,
    touched_files: str,
    lane: str,
    stack_members: str,
    remote_host: str,
    remote_hft_root: str,
    remote_control_root: str,
    change_class: str,
    measure_runs: int,
    no_compare_runs: int,
    host_key: str,
    replication_history: Path | None = None,
) -> tuple[int, str]:
    command = [
        sys.executable,
        str(driver),
        "--repo-root",
        str(repo_root),
        "--run-dir",
        str(run_dir),
        "--source-root",
        str(source_root),
        "--patch-command",
        patch_command,
        "--candidate-ledger",
        str(candidate_ledger),
        "--candidate-id",
        candidate_id,
        "--target",
        target,
        "--hypothesis",
        hypothesis,
        "--expected-effect",
        expected_effect,
        "--semantic-risk",
        semantic_risk,
        "--touched-files",
        touched_files,
        "--lane",
        lane,
        "--stack-members",
        stack_members,
        "--remote-host",
        remote_host,
        "--remote-hft-root",
        remote_hft_root,
        "--remote-control-root",
        remote_control_root,
        "--change-class",
        change_class,
        "--measure-runs",
        str(measure_runs),
        "--no-compare-runs",
        str(no_compare_runs),
        "--host-key",
        host_key,
    ]
    if replication_history is not None:
        command.extend(["--replication-history", str(replication_history)])
    started = time.monotonic()
    completed = run_command(command)
    elapsed = time.monotonic() - started
    log_path = run_dir / "prepared_driver_stdout.log"
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path.write_text(completed.stdout or "", encoding="utf-8")
    print(completed.stdout, end="", flush=True)
    print(f"elapsed_seconds={elapsed:.3f}", flush=True)
    return completed.returncode, completed.stdout or ""


def assert_same_source_summary(run_dir: Path, candidate_id: str) -> dict[str, object]:
    summary = read_json(run_dir / "iterations" / f"iter_001_{candidate_id}" / "remote_comparison_summary.json")
    require(summary.get("control_source_kind") == "synced_same_source", "control_source_kind was not synced_same_source")
    control_root = str(summary.get("control_root") or "")
    candidate_root = str(summary.get("candidate_root") or "")
    require(bool(control_root), "comparison_summary missing control_root")
    require(bool(candidate_root), "comparison_summary missing candidate_root")
    require(control_root != candidate_root, "control_root and candidate_root must differ")
    require(bool(summary.get("control_runner")), "comparison_summary missing control_runner")
    require(bool(summary.get("candidate_runner")), "comparison_summary missing candidate_runner")
    return summary


def assert_verdict(run_dir: Path, allowed: set[str], label: str) -> str:
    state = read_json(run_dir / "run_state.json")
    verdict = str(state.get("latest_verdict") or "")
    if verdict == "infra_blocked":
        raise SystemExit(f"NOT_READY {label} was infra_blocked; retry when devbox/runtime is free")
    require(verdict in allowed, f"{label} verdict {verdict!r} not in {sorted(allowed)}")
    history = run_dir / "timing_history.tsv"
    require(history.exists(), f"{label} missing timing_history.tsv")
    return verdict


def assert_replication_detected(run_dir: Path, label: str) -> None:
    state = read_json(run_dir / "run_state.json")
    require(
        bool(state.get("candidate_replication_detected")),
        f"{label} did not record candidate_replication_detected=true",
    )


def summary_noise_flag(summary: dict[str, object]) -> str:
    paired = summary.get("paired")
    if isinstance(paired, dict):
        value = paired.get("noise_flag")
        if value:
            return str(value)
    return str(summary.get("noise_flag") or "")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--task-dir",
        type=Path,
        default=Path(r"C:\Users\liangjunming\Desktop\work\.trellis\tasks\05-12-psi_next_candidate_experiment_launch_v2"),
    )
    parser.add_argument("--base-run-dir", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, default=Path(r"C:\Users\liangjunming\Desktop\work\Code1\psi-trader-liangjunming"))
    parser.add_argument("--patch-command", default="")
    parser.add_argument("--candidate-ledger", type=Path, default=None)
    parser.add_argument("--remote-host", default="devbox")
    parser.add_argument("--remote-hft-root", default="/tmp/hftwf_verify_head")
    parser.add_argument("--remote-control-root", default="/root/work/Code1/psi-trader-liangjunming")
    parser.add_argument("--host-key", default="devbox")
    parser.add_argument("--measure-runs", type=int, default=24)
    parser.add_argument("--no-compare-runs", type=int, default=1)
    parser.add_argument("--candidate-id", default="stack_skip_unused_row_fields")
    parser.add_argument("--target", default="handlerData.row_loop.stack")
    parser.add_argument("--hypothesis", default="replicate_stack_skip_unused_row_fields_under_validation_lock")
    parser.add_argument("--expected-effect", default="skip_unused_row_field_materialization_stack")
    parser.add_argument("--semantic-risk", default="low")
    parser.add_argument("--touched-files", default="PsiFactorPipline/PsiReadWrite.cpp")
    parser.add_argument("--lane", default="combination")
    parser.add_argument(
        "--stack-members",
        default="skip_unused_market_strings|skip_unused_book_volume_assignment|skip_unused_amount_assignment|skip_unused_preclose_assignment",
    )
    parser.add_argument("--change-class", default="class_b", choices=("class_a", "class_b"))
    parser.add_argument("--allow-first-verdict", default="accepted_noisy_single,accepted")
    parser.add_argument("--allow-second-verdict", default="accepted_noisy_replicated,accepted")
    parser.add_argument("--dry-plan", action="store_true", help="Print the planned run directories and exit without running.")
    args = parser.parse_args()

    task_dir = args.task_dir.resolve()
    driver = args.repo_root.resolve() / "scripts" / "run_prepared_candidate.py"
    patch_command = args.patch_command or str(task_dir / "patch_agents" / "stack_skip_unused_row_fields.cmd")
    candidate_ledger = args.candidate_ledger or (task_dir / "candidate_ledger.json")
    run_a = args.base_run_dir.resolve() / "run_a_first"
    run_b = args.base_run_dir.resolve() / "run_b_replicated"
    print(f"run_a={run_a}")
    print(f"run_b={run_b}")

    require(driver.exists(), f"missing prepared driver: {driver}")
    require(Path(patch_command).exists(), f"missing patch command: {patch_command}")
    require(candidate_ledger.exists(), f"missing candidate ledger: {candidate_ledger}")
    if args.dry_plan:
        return 0

    remote_preflight(args.remote_host)

    rc_a, _out_a = run_prepared(
        driver=driver,
        repo_root=args.repo_root.resolve(),
        run_dir=run_a,
        source_root=args.source_root.resolve(),
        patch_command=patch_command,
        candidate_ledger=candidate_ledger.resolve(),
        candidate_id=args.candidate_id,
        target=args.target,
        hypothesis=args.hypothesis,
        expected_effect=args.expected_effect,
        semantic_risk=args.semantic_risk,
        touched_files=args.touched_files,
        lane=args.lane,
        stack_members=args.stack_members,
        remote_host=args.remote_host,
        remote_hft_root=args.remote_hft_root,
        remote_control_root=args.remote_control_root,
        change_class=args.change_class,
        measure_runs=args.measure_runs,
        no_compare_runs=args.no_compare_runs,
        host_key=args.host_key,
    )
    require(rc_a == 0, f"run A failed rc={rc_a}")
    first_verdict = assert_verdict(run_a, set(args.allow_first_verdict.split(",")), "run A")
    assert_same_source_summary(run_a, args.candidate_id)
    print(f"run_a_verdict={first_verdict}")

    rc_b, _out_b = run_prepared(
        driver=driver,
        repo_root=args.repo_root.resolve(),
        run_dir=run_b,
        source_root=args.source_root.resolve(),
        patch_command=patch_command,
        candidate_ledger=candidate_ledger.resolve(),
        candidate_id=args.candidate_id,
        target=args.target,
        hypothesis=args.hypothesis,
        expected_effect=args.expected_effect,
        semantic_risk=args.semantic_risk,
        touched_files=args.touched_files,
        lane=args.lane,
        stack_members=args.stack_members,
        remote_host=args.remote_host,
        remote_hft_root=args.remote_hft_root,
        remote_control_root=args.remote_control_root,
        change_class=args.change_class,
        measure_runs=args.measure_runs,
        no_compare_runs=args.no_compare_runs,
        host_key=args.host_key,
        replication_history=run_a / "timing_history.tsv",
    )
    require(rc_b == 0, f"run B failed rc={rc_b}")
    second_verdict = assert_verdict(run_b, set(args.allow_second_verdict.split(",")), "run B")
    summary_b = assert_same_source_summary(run_b, args.candidate_id)
    assert_replication_detected(run_b, "run B")
    noise_flag = summary_noise_flag(summary_b).upper()
    if noise_flag == "NOISY":
        require(
            second_verdict == "accepted_noisy_replicated",
            "run B was noisy and should have upgraded to accepted_noisy_replicated",
        )
        require(
            summary_b.get("evidence_tier") == "shared_host_replicated",
            "run B did not record shared_host_replicated evidence tier",
        )
    else:
        require(second_verdict == "accepted", "quiet run B should finish as accepted")
    print(f"run_b_verdict={second_verdict}")
    print("run_b_candidate_replication_detected=True")
    print("prepared_replication_smoke=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
