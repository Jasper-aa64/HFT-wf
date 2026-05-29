from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import sys


def load_auto_loop(repo_root: Path):
    module_path = repo_root / "scripts" / "psi_headless_auto_loop.py"
    if not module_path.exists():
        module_path = repo_root / "HFT-wf" / "scripts" / "psi_headless_auto_loop.py"
    scripts_dir = str(module_path.parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("psi_headless_auto_loop", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def build_candidate(ns: argparse.Namespace) -> dict[str, object]:
    return {
        "candidate_id": ns.candidate_id,
        "lane": ns.lane,
        "hypothesis": ns.hypothesis,
        "target": ns.target,
        "expected_effect": ns.expected_effect,
        "semantic_risk": ns.semantic_risk,
        "touched_files": [part.strip() for part in ns.touched_files.split("|") if part.strip()],
        "stack_members": [part.strip() for part in ns.stack_members.split("|") if part.strip()],
        "change_class": ns.change_class,
        "source_evidence": {
            "kind": "prepared_candidate",
            "scope": ns.target,
            "reason": ns.hypothesis,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--patch-command", required=True)
    parser.add_argument("--candidate-ledger", type=Path, required=True)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--hypothesis", required=True)
    parser.add_argument("--expected-effect", required=True)
    parser.add_argument("--semantic-risk", default="low")
    parser.add_argument("--touched-files", default="PsiFactorPipline/PsiReadWrite.cpp")
    parser.add_argument("--lane", default="evidence")
    parser.add_argument("--stack-members", default="")
    parser.add_argument("--remote-host", default="devbox")
    parser.add_argument("--remote-hft-root", default="/root/work/HFT-wf")
    parser.add_argument("--remote-control-root", default="")
    parser.add_argument("--change-class", default="class_b", choices=("class_a", "class_b"))
    parser.add_argument("--measure-runs", type=int, default=12)
    parser.add_argument(
        "--no-compare-runs",
        type=int,
        default=1,
        help="Measured no_compare smoke runs before paired A/B; prepared promotion retries default to one smoke run.",
    )
    parser.add_argument("--replication-history", type=Path, default=None, help="Prior independent timing_history.tsv used to verify replicated evidence.")
    parser.add_argument("--host-key", default="devbox", help="Host key used for timing history and replication-history filtering.")
    parser.add_argument("--replicated", action="store_true", default=False, help="Deprecated. Use --replication-history <prior timing_history.tsv> instead.")
    ns = parser.parse_args()
    if ns.replicated and ns.replication_history is None:
        raise SystemExit("--replicated is deprecated and no longer trusted by the harness; pass --replication-history <prior timing_history.tsv>.")

    auto = load_auto_loop(ns.repo_root.resolve())
    # Import validate_class_a from psi_timing_analysis for Class A hard guard.
    from psi_timing_analysis import validate_class_a  # noqa: E402
    run_dir = ns.run_dir.resolve()
    auto.ensure_run_dir(run_dir)
    host_key = ns.host_key
    started_at = auto.utc_now()

    auto.update_heartbeat(run_dir, "init", "prepared candidate manual driver initialized")
    auto.write_run_state(
        run_dir,
        status="running",
        iteration=0,
        started_at=started_at,
        stop_reason="",
        stop_detail="",
        first_accepted_stop=False,
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
        accepted_class_a_count=0,
    )

    args = auto.build_parser().parse_args(
        [
            "--run-dir",
            str(run_dir),
            "--source-root",
            str(ns.source_root.resolve()),
            "--patch-command",
            ns.patch_command,
            "--remote-host",
            ns.remote_host,
            "--remote-hft-root",
            ns.remote_hft_root,
            "--remote-batch-script",
            "scripts/psi_headless_remote.sh",
            "--remote-run-root",
            "/root/work/psi_experiments/runs",
            "--measure-runs",
            str(ns.measure_runs),
            "--no-compare-runs",
            str(ns.no_compare_runs),
            "--no-first-accepted-stop",
            "--candidate-ledger",
            str(ns.candidate_ledger.resolve()),
            "--host-key",
            ns.host_key,
        ]
    )
    if ns.replication_history is not None:
        args.replication_history = str(ns.replication_history.resolve())
    if ns.remote_control_root:
        args.root = ns.remote_control_root
        args.control_root = ns.remote_control_root
    args.candidate_ledger = str(Path(args.candidate_ledger).resolve())
    candidate = build_candidate(ns)
    iteration = 1

    # Hard Class A validation gate: if change_class is class_a, verify the
    # patch meets the whitelist before dispatching.
    if ns.change_class == "class_a":
        valid_a, reason_a = validate_class_a(
            hypothesis=ns.hypothesis,
            change_notes=ns.expected_effect,
            touched_files=candidate.get("touched_files", []),  # type: ignore[arg-type]
            candidate_id=ns.candidate_id,
        )
        if not valid_a:
            print(f"WARNING Class A validation failed: {reason_a}")
            print("Forcing change_class to class_b.")
            ns.change_class = "class_b"
            candidate["change_class"] = "class_b"
        else:
            print(f"Class A validation passed: {reason_a}")

    auto.update_heartbeat(run_dir, "materialize", f"materializing {candidate['candidate_id']}")
    ok, patch_meta, reason = auto.materialize_candidate_patch(args, run_dir, candidate, iteration)
    if not ok:
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
            "noise_flag": "not_run",
            "patch_materialization_reason": reason,
        }
        candidate["candidate_workspace"] = str(patch_meta.get("candidate_workspace", ""))
        candidate["patch_path"] = str(patch_meta.get("patch_path", ""))
        auto.record_attempt(
            run_dir,
            iteration=iteration,
            candidate=candidate,
            batch_state=batch_state,
            verdict="needs_patch",
            retry_condition=reason,
            stop_reason="",
            notes="manual prepared-candidate materialization failed",
        )
        auto.write_run_state(
            run_dir,
            status="stopped",
            iteration=1,
            started_at=started_at,
            stop_reason="budget_stop",
            stop_detail=f"materialization failed: {reason}",
            first_accepted_stop=False,
            infra_failures=0,
            lanes_snapshot={"evidence": [candidate], "insight": [], "combination": []},
            control_distribution={},
            latest_candidate=candidate,
            latest_verdict="needs_patch",
            accepted_count=0,
            neutral_count=0,
            rejected_count=0,
            noisy_pending_count=0,
            infra_blocked_count=0,
            accepted_class_a_count=0,
        )
        auto.update_heartbeat(run_dir, "stopped", f"materialization failed: {reason}")
        print(f"run_dir={run_dir}")
        print("verdict=needs_patch")
        print(f"reason={reason}")
        return 2

    auto.update_heartbeat(run_dir, "remote_batch", "running remote build/compare/paired timing")
    rc, _iter_dir, batch_state = auto.call_remote_batch(args, run_dir, candidate, iteration)
    if rc != 0:
        infra_mode = auto._infra_failure_mode(batch_state, rc)
        verdict = "infra_blocked" if infra_mode else "rejected"
        retry_condition = f"remote batch failed rc={rc}"
        auto.set_patch_status(run_dir, str(candidate["candidate_id"]), "failed", note=retry_condition)
        infra_failures = 1
    else:
        verdict, retry_condition = auto.judge_verdict(batch_state)
        infra_failures = 0
        if verdict == "NOISY_PENDING":
            auto.record_retry_condition(
                run_dir,
                candidate,
                status="NOISY_PENDING",
                noise_flag="NOISY",
                required_condition=retry_condition,
                last_exit_reason="",
                notes=(
                    f"candidate_id={candidate['candidate_id']};"
                    f" paired_sample_count={batch_state.get('paired_sample_count', '')};"
                    f" median_delta_ms={batch_state.get('median_delta_ms', '')};"
                    f" paired_range_ms={batch_state.get('paired_range_ms', '')};"
                    f" paired_stdev_ms={batch_state.get('paired_stdev_ms', '')};"
                    " manual prepared-candidate run"
                ),
            )
            auto.set_patch_status(run_dir, str(candidate["candidate_id"]), "reverted", note="noisy; reverted pending rerun")
        elif verdict == "accepted_noisy_replicated":
            auto.set_patch_status(run_dir, str(candidate["candidate_id"]), "applied", note="accepted with replicated evidence; shared-host promotion, artifact marked non-bare-metal")
        elif verdict == "accepted_noisy_single":
            auto.set_patch_status(run_dir, str(candidate["candidate_id"]), "reverted", note="accepted_noisy_single; queued for validation")
        elif verdict == "accepted_class_a":
            auto.set_patch_status(run_dir, str(candidate["candidate_id"]), "applied", note="accepted as Class A algorithmic change; correctness pass sufficient")
        elif verdict == "accepted":
            auto.set_patch_status(run_dir, str(candidate["candidate_id"]), "applied", note="accepted evidence; awaiting manual promotion")
        elif verdict == "neutral":
            auto.record_neutral_pool_entry(run_dir, candidate, batch_state, retry_condition)
            auto.set_patch_status(run_dir, str(candidate["candidate_id"]), "reverted", note="neutral; candidate reverted")
        else:
            auto.set_patch_status(run_dir, str(candidate["candidate_id"]), "reverted", note="rejected; reverted")

    auto.record_attempt(
        run_dir,
        iteration=iteration,
        candidate=candidate,
        batch_state=batch_state,
        verdict=verdict,
        retry_condition=retry_condition,
        stop_reason="",
        notes=str(candidate.get("expected_effect", "")),
    )
    auto.upsert_timing_from_batch(
        run_dir,
        candidate,
        batch_state,
        host_key,
        verdict=verdict,
        verdict_reason=retry_condition,
    )
    verdict_counts = auto.count_verdict_rows(run_dir)
    accepted_clean = verdict_counts[0]
    neutral = verdict_counts[1]
    rejected = verdict_counts[2]
    noisy = verdict_counts[3]
    infra_blocked = verdict_counts[4] if len(verdict_counts) > 4 else (1 if verdict == "infra_blocked" else 0)
    accepted_noisy = verdict_counts[5] if len(verdict_counts) > 5 else 0
    accepted_class_a_val = verdict_counts[6] if len(verdict_counts) > 6 else 0
    accepted_noisy_replicated = verdict_counts[7] if len(verdict_counts) > 7 else 0
    auto.write_run_state(
        run_dir,
        status="stopped",
        iteration=1,
        started_at=started_at,
        stop_reason="budget_stop" if rc == 0 else "remote_failed",
        stop_detail="manual prepared-candidate run complete" if rc == 0 else f"remote batch failed rc={rc}",
        first_accepted_stop=False,
        infra_failures=infra_failures,
        lanes_snapshot={"evidence": [candidate], "insight": [], "combination": []},
        control_distribution={},
        latest_candidate=candidate,
        latest_verdict=verdict,
        accepted_count=accepted_clean,
        neutral_count=neutral,
        rejected_count=rejected,
        noisy_pending_count=noisy,
        infra_blocked_count=infra_blocked,
        accepted_class_a_count=accepted_class_a_val,
        accepted_noisy_single_count=accepted_noisy - accepted_noisy_replicated,
        accepted_noisy_replicated_count=accepted_noisy_replicated,
        candidate_replication_detected=bool(batch_state.get("candidate_replication_detected")),
    )
    auto.update_heartbeat(run_dir, "stopped", "manual prepared-candidate run complete")
    print(f"run_dir={run_dir}")
    print(f"verdict={verdict}")
    print(f"candidate_replication_detected={bool(batch_state.get('candidate_replication_detected'))}")
    print(f"timing_verdict={batch_state.get('timing_verdict', batch_state.get('timing_status', ''))}")
    print(f"paired_sample_count={batch_state.get('paired_sample_count', '')}")
    print(f"remote_run_dir={batch_state.get('remote_run_dir', '')}")
    print(f"remote_candidate_workspace={batch_state.get('remote_candidate_workspace', '')}")
    return 0 if rc == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
