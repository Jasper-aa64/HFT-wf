from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import psi_headless_auto_loop as auto_loop  # noqa: E402


class PsiHeadlessAutoLoopRemoteTests(unittest.TestCase):
    def test_global_stop_reasons_contract(self) -> None:
        self.assertEqual(
            auto_loop.STOP_REASONS_GLOBAL,
            {
                "accepted",
                "budget_stop",
                "convergence_proven",
                "no_targets",
                "remote_failed",
                "repeated_infra_failure",
                "control_baseline_unhealthy",
                "user_stopped",
            },
        )

    def test_remote_batch_passes_independent_no_compare_runs(self) -> None:
        with tempfile.TemporaryDirectory(prefix="psi_auto_loop_remote_") as raw_dir:
            run_dir = Path(raw_dir)
            (run_dir / "logs").mkdir()
            iteration_dir = run_dir / "iterations" / "iter_001_candidate"
            iteration_dir.mkdir(parents=True)
            args = SimpleNamespace(
                remote_host="devbox",
                remote_hft_root="/tmp/hftwf_verify_head",
                remote_batch_script="scripts/psi_headless_remote.sh",
                remote_run_root="/root/work/psi_experiments/runs",
                remote_run_dir="",
                remote_candidate_workspace_root="",
                bash="bash",
                measure_runs=24,
                no_compare_runs=1,
                remote_timeout_seconds=900,
                replication_history="",
                env_file="",
                runner="",
                config="",
                output_dir="",
                control_root="",
                root="/root/work/Code1/psi-trader-liangjunming",
                candidate_runner="",
                build_dir="",
                twap_endpoint="",
                twap_user_id="",
                twap_measure_cases="",
                twap_subscriber_counts="",
                twap_build_targets="",
                twap_correctness_mode="",
                twap_account_desc_check="",
            )
            candidate = {
                "candidate_id": "candidate",
                "lane": "combination",
                "target": "handlerData.row_loop.stack",
                "touched_files": ["PsiFactorPipline/PsiReadWrite.cpp"],
                "replicated": True,
                "change_class": "class_b",
            }
            auto_loop.append_tsv_row(
                run_dir / "attempts.tsv",
                {
                    "iteration": "0",
                    "candidate_id": "candidate",
                    "target": "handlerData.row_loop.stack",
                    "compare_status": "pass",
                    "verdict": "accepted_noisy_single",
                },
                auto_loop.ATTEMPTS_FIELDS,
            )
            calls: list[str] = []

            def fake_ssh(
                _host: str,
                command: str,
                *,
                text: bool = True,
                timeout: int | None = None,
            ) -> subprocess.CompletedProcess[str]:
                self.assertEqual(timeout, 900)
                calls.append(command)
                return subprocess.CompletedProcess(["ssh"], 0, stdout="", stderr="")

            with mock.patch.object(auto_loop, "_ssh", side_effect=fake_ssh):
                rc, _iter_dir, batch_state = auto_loop.call_ssh_remote_batch(args, run_dir, iteration_dir, candidate, 1)

            self.assertEqual(rc, 0)
            self.assertEqual(batch_state["remote_host"], "devbox")
            self.assertIn("cd /tmp/hftwf_verify_head", calls[0])
            self.assertIn("MEASURE_RUNS=24", calls[0])
            self.assertIn("NO_COMPARE_RUNS=1", calls[0])
            self.assertIn("CANDIDATE_REPLICATED=1", calls[0])

    def test_candidate_replicated_flag_is_not_trusted_without_prior_evidence(self) -> None:
        with tempfile.TemporaryDirectory(prefix="psi_auto_loop_remote_") as raw_dir:
            run_dir = Path(raw_dir)
            (run_dir / "logs").mkdir()
            iteration_dir = run_dir / "iterations" / "iter_001_candidate"
            iteration_dir.mkdir(parents=True)
            args = SimpleNamespace(
                remote_host="devbox",
                remote_hft_root="/tmp/hftwf_verify_head",
                remote_batch_script="scripts/psi_headless_remote.sh",
                remote_run_root="/root/work/psi_experiments/runs",
                remote_run_dir="",
                remote_candidate_workspace_root="",
                bash="bash",
                measure_runs=24,
                no_compare_runs=1,
                remote_timeout_seconds=900,
                replication_history="",
                env_file="",
                runner="",
                config="",
                output_dir="",
                control_root="",
                root="/root/work/Code1/psi-trader-liangjunming",
                candidate_runner="",
                build_dir="",
                twap_endpoint="",
                twap_user_id="",
                twap_measure_cases="",
                twap_subscriber_counts="",
                twap_build_targets="",
                twap_correctness_mode="",
                twap_account_desc_check="",
            )
            candidate = {
                "candidate_id": "candidate",
                "lane": "combination",
                "target": "handlerData.row_loop.stack",
                "touched_files": ["PsiFactorPipline/PsiReadWrite.cpp"],
                "replicated": True,
                "change_class": "class_b",
            }
            calls: list[str] = []

            def fake_ssh(
                _host: str,
                command: str,
                *,
                text: bool = True,
                timeout: int | None = None,
            ) -> subprocess.CompletedProcess[str]:
                calls.append(command)
                return subprocess.CompletedProcess(["ssh"], 0, stdout="", stderr="")

            with mock.patch.object(auto_loop, "_ssh", side_effect=fake_ssh):
                rc, _iter_dir, batch_state = auto_loop.call_ssh_remote_batch(args, run_dir, iteration_dir, candidate, 1)

            self.assertEqual(rc, 0)
            self.assertEqual(batch_state["remote_host"], "devbox")
            self.assertIn("CANDIDATE_REPLICATED=''", calls[0])

    def test_synced_candidate_uses_synced_same_source_control(self) -> None:
        with tempfile.TemporaryDirectory(prefix="psi_auto_loop_remote_") as raw_dir:
            run_dir = Path(raw_dir)
            (run_dir / "logs").mkdir()
            iteration_dir = run_dir / "iterations" / "iter_001_candidate"
            iteration_dir.mkdir(parents=True)
            source = run_dir / "source"
            source.mkdir()
            (source / "PsiTraderRunner").mkdir()
            (source / "PsiTraderRunner" / "config.yaml").write_text("isCompareFile: false\n", encoding="utf-8")
            candidate_ws = run_dir / "candidate_ws"
            candidate_ws.mkdir()
            (candidate_ws / "PsiTraderRunner").mkdir()
            (candidate_ws / "PsiTraderRunner" / "config.yaml").write_text("isCompareFile: false\n", encoding="utf-8")
            args = SimpleNamespace(
                remote_host="devbox",
                remote_hft_root="/tmp/hftwf_verify_head",
                remote_batch_script="scripts/psi_headless_remote.sh",
                remote_run_root="/root/work/psi_experiments/runs",
                remote_run_dir="",
                remote_candidate_workspace_root="",
                bash="bash",
                measure_runs=24,
                no_compare_runs=1,
                remote_timeout_seconds=900,
                replication_history="",
                env_file="",
                runner="",
                config="",
                output_dir="",
                control_root="",
                root=str(source),
                source_root=str(source),
                candidate_runner="",
                build_dir="",
                twap_endpoint="",
                twap_user_id="",
                twap_measure_cases="",
                twap_subscriber_counts="",
                twap_build_targets="",
                twap_correctness_mode="",
                twap_account_desc_check="",
            )
            candidate = {
                "candidate_id": "candidate",
                "lane": "combination",
                "target": "handlerData.row_loop.stack",
                "touched_files": ["PsiFactorPipline/PsiReadWrite.cpp"],
                "change_class": "class_b",
                "candidate_workspace": str(candidate_ws),
            }
            calls: list[str] = []

            def fake_ssh(
                _host: str,
                command: str,
                *,
                text: bool = True,
                timeout: int | None = None,
            ) -> subprocess.CompletedProcess[str]:
                calls.append(command)
                return subprocess.CompletedProcess(["ssh"], 0, stdout="", stderr="")

            def fake_sync_candidate(
                _args: object, _run_dir: object, _candidate: dict
            ) -> tuple[str, str]:
                # Return a fake remote workspace path without doing real scp.
                return "/remote/run/candidate_workspaces/candidate", ""

            def fake_sync_control(
                _args: object, _run_dir: object, _candidate: dict
            ) -> tuple[str, str]:
                # Return a fake remote control workspace path without doing real scp.
                return "/remote/run/candidate_workspaces/candidate_control", ""

            with (
                mock.patch.object(auto_loop, "_ssh", side_effect=fake_ssh),
                mock.patch.object(auto_loop, "_sync_candidate_workspace_to_remote", side_effect=fake_sync_candidate),
                mock.patch.object(auto_loop, "_sync_control_workspace_to_remote", side_effect=fake_sync_control),
            ):
                rc, _iter_dir, batch_state = auto_loop.call_ssh_remote_batch(args, run_dir, iteration_dir, candidate, 1)

            self.assertEqual(rc, 0)
            self.assertEqual(batch_state["remote_host"], "devbox")
            command = calls[-3]
            self.assertIn("CONTROL_SOURCE_KIND=synced_same_source", command)
            self.assertIn("CONTROL_ROOT=", command)
            self.assertIn("CONTROL_BUILD_DIR=", command)
            self.assertIn("_control", command)

    def test_synced_candidate_acceptance_requires_synced_control_metadata(self) -> None:
        verdict, reason = auto_loop.judge_verdict(
            {
                "compare_status": "pass",
                "timing_verdict": "accepted",
                "remote_candidate_workspace": "/tmp/candidate",
                "control_source_kind": "existing_runner",
            }
        )

        self.assertEqual(verdict, "needs_paired_evidence")
        self.assertIn("same-source control", reason)

    def test_candidate_replication_uses_cross_run_timing_history(self) -> None:
        with tempfile.TemporaryDirectory(prefix="psi_auto_loop_remote_") as raw_dir:
            run_dir = Path(raw_dir) / "fresh_replication_run"
            run_dir.mkdir()
            history_path = Path(raw_dir) / "prior_timing_history.tsv"
            auto_loop.write_tsv(
                history_path,
                [
                    {
                        "history_key": "prior|candidate",
                        "recorded_at": "2026-05-29T00:00:00Z",
                        "run_id": "prior_locked_run",
                        "host_key": "devbox",
                        "kind": "candidate",
                        "target": "handlerData.row_loop.stack",
                        "timing_verdict": "accepted_noisy_single",
                        "verdict": "accepted_noisy_single",
                    }
                ],
                auto_loop.HISTORY_FIELDNAMES,
            )
            candidate = {
                "candidate_id": "candidate",
                "lane": "combination",
                "target": "handlerData.row_loop.stack",
                "touched_files": ["PsiFactorPipline/PsiReadWrite.cpp"],
            }

            self.assertTrue(
                auto_loop._candidate_has_prior_replication(
                    run_dir,
                    candidate,
                    history_path=history_path,
                    host_key="devbox",
                )
            )

    def test_candidate_replication_ignores_current_run_history(self) -> None:
        with tempfile.TemporaryDirectory(prefix="psi_auto_loop_remote_") as raw_dir:
            run_dir = Path(raw_dir) / "current_run"
            run_dir.mkdir()
            history_path = run_dir / "timing_history.tsv"
            auto_loop.write_tsv(
                history_path,
                [
                    {
                        "history_key": "current|candidate",
                        "recorded_at": "2026-05-29T00:00:00Z",
                        "run_id": "current_run",
                        "host_key": "devbox",
                        "kind": "candidate",
                        "target": "handlerData.row_loop.stack",
                        "timing_verdict": "accepted_noisy_single",
                        "verdict": "accepted_noisy_single",
                    }
                ],
                auto_loop.HISTORY_FIELDNAMES,
            )
            candidate = {
                "candidate_id": "candidate",
                "lane": "combination",
                "target": "handlerData.row_loop.stack",
                "touched_files": ["PsiFactorPipline/PsiReadWrite.cpp"],
            }

            self.assertFalse(
                auto_loop._candidate_has_prior_replication(
                    run_dir,
                    candidate,
                    history_path=history_path,
                    host_key="devbox",
                )
            )

    def test_parser_defaults_no_compare_runs_to_one(self) -> None:
        parser = auto_loop.build_parser()
        args = parser.parse_args(["--run-dir", "dummy"])

        self.assertEqual(args.no_compare_runs, 1)
        self.assertEqual(args.remote_timeout_seconds, 14400)

    def test_infra_failure_mode_classifies_remote_failures(self) -> None:
        self.assertEqual(
            auto_loop._infra_failure_mode(
                {
                    "build_status": "not_run",
                    "compare_status": "not_run",
                    "timing_status": "validation_lock_blocked",
                    "reason": "validation_lock_blocked",
                },
                1,
            ),
            "validation_lock_blocked",
        )
        self.assertEqual(
            auto_loop._infra_failure_mode(
                {
                    "build_status": "failed",
                    "compare_status": "not_run",
                    "timing_status": "not_run",
                },
                1,
            ),
            "build_failed",
        )
        self.assertEqual(auto_loop._infra_failure_mode({}, 124), "remote_timeout")

    def test_missing_paired_evidence_does_not_become_neutral(self) -> None:
        verdict, reason = auto_loop.judge_verdict(
            {
                "compare_status": "pass",
                "timing_verdict": "needs_paired_evidence",
                "paired_evidence_reason": "CANDIDATE_RUNNER was not provided",
            }
        )

        self.assertEqual(verdict, "needs_paired_evidence")
        self.assertIn("CANDIDATE_RUNNER", reason)

    def test_external_patch_command_materializes_without_replication_args(self) -> None:
        with tempfile.TemporaryDirectory(prefix="psi_auto_loop_patch_") as raw_dir:
            run_dir = Path(raw_dir)
            source = run_dir / "source"
            source.mkdir()
            (source / "base.txt").write_text("base\n", encoding="utf-8")
            patch_cmd = run_dir / "patch.cmd"
            patch_cmd.write_text("@echo patched>> probe.txt\r\n", encoding="utf-8")
            candidate = {
                "candidate_id": "candidate",
                "lane": "evidence",
                "target": "handlerData.row_loop.stack",
                "touched_files": ["probe.txt"],
                "hypothesis": "probe",
                "expected_effect": "probe",
                "semantic_risk": "low",
            }
            args = SimpleNamespace(
                source_root=str(source),
                candidate_workspace="",
                reuse_candidate_workspace=False,
                patch_command=str(patch_cmd),
                candidate_ledger="",
            )

            ok, patch_meta, reason = auto_loop.materialize_candidate_patch(args, run_dir, candidate, 1)

            self.assertTrue(ok, reason)
            self.assertTrue(Path(patch_meta["patch_path"]).exists())

    def test_patch_boundary_rejects_build_artifacts(self) -> None:
        violations = auto_loop._validate_patch_boundaries(
            [
                "PsiData/PsiBaseDataInfo.cpp",
                "build/CMakeCache.txt",
                "build/PsiData/CMakeFiles/PsiFactorCompute.dir/PsiBaseDataInfo.cpp.obj",
            ]
        )

        self.assertEqual(len(violations), 2)
        self.assertTrue(all("fixed boundary path component: build" in item for item in violations))

    def test_iteration_step_records_remote_infra_as_failed_not_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="psi_auto_loop_infra_") as raw_dir:
            run_dir = Path(raw_dir)
            (run_dir / "logs").mkdir()
            candidate = {
                "candidate_id": "candidate",
                "lane": "evidence",
                "target": "handlerData.row_loop.stack",
                "touched_files": ["PsiFactorPipline/PsiReadWrite.cpp"],
                "hypothesis": "probe",
                "expected_effect": "probe",
                "semantic_risk": "low",
            }
            args = SimpleNamespace(
                remote_host="devbox",
                remote_batch_script="scripts/psi_headless_remote.sh",
                batch_script=REPO_ROOT / "scripts" / "psi_headless_remote.sh",
                dry_run=False,
                candidate_seed_file="",
                candidate_ledger="",
                patch_command="builtin:fake-nonempty",
                source_root=str(run_dir / "source"),
                candidate_workspace="",
                reuse_candidate_workspace=False,
                quiet_retry_min_control_samples=20,
                quiet_retry_control_stdev_ms=800.0,
                quiet_retry_control_range_ms=2000.0,
                remote_timeout_seconds=30,
                twap_endpoint="",
            )
            source = Path(args.source_root)
            source.mkdir()
            (source / "PsiFactorPipline").mkdir()
            (source / "PsiFactorPipline" / "PsiReadWrite.cpp").write_text("int x = 0;\n", encoding="utf-8")

            def fake_generate_candidates(*_args, **_kwargs):
                return {"evidence": [candidate], "insight": [], "combination": []}

            def fake_remote_batch(*_args, **_kwargs):
                return 1, run_dir / "iterations" / "iter_001_candidate", {
                    "status": "stopped",
                    "batch_status": "failed",
                    "build_status": "not_run",
                    "compare_status": "not_run",
                    "timing_status": "validation_lock_blocked",
                    "reason": "validation_lock_blocked",
                }

            with (
                mock.patch.object(auto_loop, "seed_profile_if_missing"),
                mock.patch.object(auto_loop, "generate_candidates", side_effect=fake_generate_candidates),
                mock.patch.object(auto_loop, "call_remote_batch", side_effect=fake_remote_batch),
            ):
                _candidate, verdict, iter_stop, _lanes, _batch_state = auto_loop.iteration_step(
                    args,
                    run_dir,
                    1,
                    set(),
                    set(),
                    "host",
                )

            self.assertEqual(verdict, "infra_blocked")
            self.assertEqual(iter_stop, "remote_failed")
            attempts = auto_loop.read_tsv(run_dir / "attempts.tsv")
            self.assertEqual(attempts[0]["verdict"], "infra_blocked")
            manifest = auto_loop.read_json(run_dir / "patches" / "patch_manifest.json")
            self.assertEqual(manifest["entries"][0]["status"], "failed")

    def test_iteration_step_escalates_gray_noisy_candidate_to_next_sample_depth(self) -> None:
        with tempfile.TemporaryDirectory(prefix="factor_auto_loop_escalate_") as raw_dir:
            run_dir = Path(raw_dir)
            candidate = {
                "candidate_id": "candidate",
                "lane": "evidence",
                "target": "handlerData.timestamp",
                "touched_files": ["PsiFactorPipline/PsiReadWrite.cpp"],
                "hypothesis": "probe",
                "expected_effect": "probe",
                "semantic_risk": "low",
            }
            args = SimpleNamespace(
                remote_host="devbox",
                remote_batch_script="scripts/psi_headless_remote.sh",
                batch_script=REPO_ROOT / "scripts" / "psi_headless_remote.sh",
                dry_run=False,
                candidate_seed_file="",
                candidate_ledger="",
                patch_command="builtin:fake-nonempty",
                source_root=str(run_dir / "source"),
                candidate_workspace="",
                reuse_candidate_workspace=False,
                quiet_retry_min_control_samples=20,
                quiet_retry_control_stdev_ms=800.0,
                quiet_retry_control_range_ms=2000.0,
                remote_timeout_seconds=30,
                twap_endpoint="",
                measure_runs=4,
            )
            source = Path(args.source_root)
            source.mkdir()
            (source / "PsiFactorPipline").mkdir()
            (source / "PsiFactorPipline" / "PsiReadWrite.cpp").write_text("int x = 0;\n", encoding="utf-8")

            def fake_generate_candidates(*_args, **_kwargs):
                return {"evidence": [candidate], "insight": [], "combination": []}

            def fake_remote_batch(*_args, **_kwargs):
                return 0, run_dir / "iterations" / "iter_001_candidate", {
                    "status": "stopped",
                    "batch_status": "completed",
                    "build_status": "pass",
                    "compare_status": "pass",
                    "timing_status": "NOISY_PENDING",
                    "timing_verdict": "NOISY_PENDING",
                    "noise_flag": "NOISY",
                    "paired_sample_count": 4,
                    "median_delta_ms": 805.5,
                    "bootstrap_ci_low_ms": 324.0,
                    "bootstrap_ci_high_ms": 1392.0,
                    "control_median_ms": 58000.0,
                    "paired_range_ms": 1068.0,
                    "paired_stdev_ms": 438.0,
                    "control_samples_ms": [58686, 57742, 57673, 57972],
                    "candidate_samples_ms": [57294, 56894, 56910, 57648],
                    "candidate_median_ms": 57102.0,
                    "compare_result": "pass",
                }

            with (
                mock.patch.object(auto_loop, "seed_profile_if_missing"),
                mock.patch.object(auto_loop, "generate_candidates", side_effect=fake_generate_candidates),
                mock.patch.object(auto_loop, "call_remote_batch", side_effect=fake_remote_batch),
            ):
                _candidate, verdict, _iter_stop, _lanes, _batch_state = auto_loop.iteration_step(
                    args,
                    run_dir,
                    1,
                    set(),
                    set(),
                    "host",
                )

            self.assertEqual(verdict, "NOISY_PENDING")
            retry_rows = auto_loop.read_tsv(run_dir / "retry_conditions.tsv")
            self.assertEqual(retry_rows[0]["status"], "ESCALATE")
            self.assertEqual(retry_rows[0]["next_measure_runs"], "8")
            self.assertIn("escalate to m8", retry_rows[0]["required_condition"])

    def test_measure_runs_override_deepens_retry_and_restores_args(self) -> None:
        with tempfile.TemporaryDirectory(prefix="factor_auto_loop_measure_override_") as raw_dir:
            run_dir = Path(raw_dir)
            args = SimpleNamespace(measure_runs=4)
            candidate = {
                "candidate_id": "retry_timestamp",
                "lane": "evidence",
                "target": "handlerData.timestamp",
                "measure_runs_override": 8,
            }

            def fake_remote_batch(call_args, _run_dir, _candidate, _iteration):
                self.assertEqual(call_args.measure_runs, 8)
                return 0, run_dir / "iterations" / "iter_002_retry_timestamp", {"timing_verdict": "neutral"}

            with mock.patch.object(auto_loop, "call_remote_batch", side_effect=fake_remote_batch):
                rc, _iter_dir, batch_state = auto_loop._call_remote_batch_with_measure_override(
                    args,
                    run_dir,
                    candidate,
                    2,
                )

            self.assertEqual(rc, 0)
            self.assertEqual(batch_state["timing_verdict"], "neutral")
            self.assertEqual(args.measure_runs, 4)

    def test_merge_comparison_summary_bridges_paired_samples_to_sample_lists(self) -> None:
        """_merge_comparison_summary must restore control_samples_ms / candidate_samples_ms
        from paired_samples so upsert_timing_from_batch does not early-return on empty lists.
        This is the bridge that was missing and caused timing_history.tsv to stay header-only
        after a real devbox paired run.
        """
        summary = {
            "compare_result": "pass",
            "timing_verdict": "accepted_noisy_single",
            "timing_verdict_reason": "statistically conclusive but noisy",
            "timing_verdict_method": "paired_bootstrap_permutation_v1",
            "control_source_kind": "synced_same_source",
            "paired": {
                "paired_sample_count": 3,
                "noise_flag": "NOISY",
                "median_delta_ms": "7268.000",
                "paired_deltas_ms": "7053,7564,6278",
                "bootstrap_ci_low_ms": "6937.000",
                "bootstrap_ci_high_ms": "7643.500",
                "permutation_p_value": "0.001000",
            },
            "paired_samples": [
                {"pair_index": 1, "control_ms": 58590.0, "candidate_ms": 51537.0, "delta_ms": 7053.0},
                {"pair_index": 2, "control_ms": 58609.0, "candidate_ms": 51045.0, "delta_ms": 7564.0},
                {"pair_index": 3, "control_ms": 58719.0, "candidate_ms": 52441.0, "delta_ms": 6278.0},
            ],
            "control": {"median_ms": "58639.333"},
        }
        batch_state: dict = {}
        auto_loop._merge_comparison_summary(batch_state, summary)

        self.assertIsInstance(batch_state.get("control_samples_ms"), list)
        self.assertIsInstance(batch_state.get("candidate_samples_ms"), list)
        self.assertEqual(batch_state["control_samples_ms"], [58590.0, 58609.0, 58719.0])
        self.assertEqual(batch_state["candidate_samples_ms"], [51537.0, 51045.0, 52441.0])
        self.assertEqual(batch_state["noise_flag"], "NOISY")
        self.assertEqual(batch_state["timing_verdict"], "accepted_noisy_single")
        # #4: delta_ms and candidate_median_ms must be back-filled from paired statistics
        self.assertAlmostEqual(batch_state.get("delta_ms", 0), 7268.0, places=1,
                               msg="delta_ms must be non-zero after bridge (was 0 before fix)")
        self.assertAlmostEqual(batch_state.get("candidate_median_ms", 0), 51537.0, places=1,
                               msg="candidate_median_ms must be set from candidate_ms list")

    def test_paired_samples_bridge_enables_timing_history_write(self) -> None:
        """End-to-end write-read: after the bridge fix, a summary with paired_samples must
        produce a non-empty timing_history.tsv with a candidate row carrying the verdict.
        This is the real-world path that was broken (history stayed header-only).
        """
        with tempfile.TemporaryDirectory(prefix="psi_bridge_e2e_") as raw_dir:
            run_dir = Path(raw_dir)
            candidate = {
                "candidate_id": "stack_skip_unused_row_fields",
                "lane": "combination",
                "target": "handlerData.row_loop.stack",
                "touched_files": ["PsiFactorPipline/PsiReadWrite.cpp"],
            }
            summary = {
                "compare_result": "pass",
                "timing_verdict": "accepted_noisy_single",
                "timing_verdict_reason": "statistically conclusive but noisy",
                "timing_verdict_method": "paired_bootstrap_permutation_v1",
                "control_source_kind": "synced_same_source",
                "paired": {
                    "paired_sample_count": 3,
                    "noise_flag": "NOISY",
                    "median_delta_ms": "7268.000",
                    "paired_deltas_ms": "7053,7564,6278",
                    "bootstrap_ci_low_ms": "6937.000",
                    "bootstrap_ci_high_ms": "7643.500",
                    "permutation_p_value": "0.001000",
                },
                "paired_samples": [
                    {"pair_index": 1, "control_ms": 58590.0, "candidate_ms": 51537.0, "delta_ms": 7053.0},
                    {"pair_index": 2, "control_ms": 58609.0, "candidate_ms": 51045.0, "delta_ms": 7564.0},
                    {"pair_index": 3, "control_ms": 58719.0, "candidate_ms": 52441.0, "delta_ms": 6278.0},
                ],
                "control": {"median_ms": "58639.333"},
            }
            batch_state: dict = {}
            auto_loop._merge_comparison_summary(batch_state, summary)

            # Use real upsert (not mocked) to verify the write path
            auto_loop.upsert_timing_from_batch(
                run_dir,
                candidate,
                batch_state,
                "devbox",
                verdict="accepted_noisy_single",
                verdict_reason="statistically conclusive but noisy",
            )

            rows = auto_loop.read_tsv(run_dir / "timing_history.tsv")
            candidate_rows = [r for r in rows if r.get("kind") == "candidate"]
            self.assertGreater(len(candidate_rows), 0, "timing_history.tsv must have at least one candidate row")
            self.assertEqual(candidate_rows[0]["verdict"], "accepted_noisy_single")
            self.assertEqual(candidate_rows[0]["timing_verdict"], "accepted_noisy_single")
            self.assertEqual(candidate_rows[0]["noise_flag"], "NOISY")
            # #4: delta_ms in timing_history row must be non-zero and consistent with
            # the median_delta_ms that was bridged from paired_samples.
            row_delta = float(candidate_rows[0].get("delta_ms") or 0)
            self.assertGreater(row_delta, 0,
                               "timing_history candidate delta_ms must be non-zero after bridge fix")
            self.assertAlmostEqual(row_delta, 7268.0, places=1,
                                   msg="timing_history delta_ms must match bridged median_delta_ms")

            # Verify replication detection can now read back the written history
            fresh_run = Path(raw_dir) / "fresh_replication_run"
            fresh_run.mkdir()
            self.assertTrue(
                auto_loop._candidate_has_prior_replication(
                    fresh_run,
                    candidate,
                    history_path=run_dir / "timing_history.tsv",
                    host_key="devbox",
                )
            )


if __name__ == "__main__":
    unittest.main()
