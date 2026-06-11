from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import host_jitter_audit as audit  # noqa: E402


TIMING_FIELDS = [
    "label",
    "mode",
    "warm_or_cold",
    "elapsed_ms",
    "elapsed_seconds",
    "compat_seconds",
    "rc",
    "log_file",
    "pair_index",
]


def write_timing(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=TIMING_FIELDS, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


class HostJitterAuditTests(unittest.TestCase):
    def test_extracts_control_samples_from_no_compare_rows(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jitter_parse_") as raw_dir:
            run_dir = Path(raw_dir)
            write_timing(
                run_dir / "timing_samples.tsv",
                [
                    {"label": "warmup", "mode": "no_compare", "warm_or_cold": "warmup", "elapsed_ms": "900", "rc": "0"},
                    {"label": "run1", "mode": "no_compare", "warm_or_cold": "measured", "elapsed_ms": "1000", "rc": "0"},
                    {"label": "run2", "mode": "no_compare", "warm_or_cold": "measured", "elapsed_ms": "1010", "rc": "0"},
                    {"label": "run3", "mode": "no_compare", "warm_or_cold": "measured", "elapsed_ms": "bad", "rc": "0"},
                    {"label": "run4", "mode": "no_compare", "warm_or_cold": "measured", "elapsed_ms": "1500", "rc": "1"},
                ],
            )

            self.assertEqual(audit.extract_control_samples(run_dir), [1000.0, 1010.0])

    def test_extracts_paired_deltas_by_pair_index(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jitter_pair_") as raw_dir:
            run_dir = Path(raw_dir)
            write_timing(
                run_dir / "timing_samples.tsv",
                [
                    {"label": "ctrl1", "mode": "paired_control", "warm_or_cold": "measured", "elapsed_ms": "1000", "rc": "0", "pair_index": "1"},
                    {"label": "cand1", "mode": "paired_candidate", "warm_or_cold": "measured", "elapsed_ms": "950", "rc": "0", "pair_index": "1"},
                    {"label": "ctrl2", "mode": "paired_control", "warm_or_cold": "measured", "elapsed_ms": "1100", "rc": "0", "pair_index": "2"},
                    {"label": "cand2", "mode": "paired_candidate", "warm_or_cold": "measured", "elapsed_ms": "1200", "rc": "0", "pair_index": "2"},
                    {"label": "cand3", "mode": "paired_candidate", "warm_or_cold": "measured", "elapsed_ms": "1000", "rc": "0", "pair_index": "3"},
                ],
            )

            self.assertEqual(audit.extract_paired_deltas(run_dir), [50.0, -100.0])

    def test_busy_preflight_blocks_without_remote_batch(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jitter_busy_") as raw_dir:
            run_dir = Path(raw_dir)
            args = SimpleNamespace(
                run_dir=run_dir,
                remote_host="devbox",
                remote_run_root="/remote/jitter",
                remote_run_dir="",
                remote_timeout_seconds=30,
                weather_sample_count=1,
                weather_sample_interval_seconds=0.0,
                process_name=[],
                allow_busy=False,
                measure_runs=5,
                root="/root/work/Code1/psi-trader-liangjunming",
                env_file="/root/work/.toolchain/psi-env.sh",
                config="/root/work/Code1/psi-trader-liangjunming/PsiTraderRunner/config.yaml",
                output_dir="/root/work/Code1/dataset/output",
                host_key="17062",
            )
            snapshot = {
                "sample_index": 1,
                "recorded_at": "2026-05-26T00:00:00Z",
                "kind": "snapshot",
                "duration_ms": 1000.0,
                "load_per_cpu_1m": 0.0,
                "loadavg_1m": 0.0,
                "cpu_iowait_pct": 0.0,
                "active_runner_count": 1,
                "blocking_processes": [{"pid": "123", "name": "PsiTraderRunner"}],
                "metrics_available": True,
            }

            with mock.patch.object(audit, "blocking_runners", return_value=["123 PsiTraderRunner"]), mock.patch.object(
                audit.host_weather_audit,
                "collect_remote_snapshots",
                return_value=[snapshot],
            ), mock.patch.object(audit, "run_remote_control_batch") as remote_batch:
                summary = audit.run_audit(args)

            remote_batch.assert_not_called()
            self.assertEqual(summary["promotion_gate"], "blocked_by_preflight_runner")
            self.assertEqual(summary["weather_decision"], "NOISY")
            self.assertEqual(summary["control_sample_count"], 0)
            self.assertTrue((run_dir / "host_jitter_audit_summary.json").exists())
            saved = json.loads((run_dir / "host_jitter_audit_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["promotion_gate"], "blocked_by_preflight_runner")

    def test_busy_preflight_forces_noisy_when_snapshot_probe_fails(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jitter_probe_fail_") as raw_dir:
            run_dir = Path(raw_dir)
            args = SimpleNamespace(
                run_dir=run_dir,
                remote_host="devbox",
                remote_run_root="/remote/jitter",
                remote_run_dir="",
                remote_timeout_seconds=30,
                weather_sample_count=1,
                weather_sample_interval_seconds=0.0,
                process_name=[],
                allow_busy=False,
                measure_runs=5,
                root="/root/work/Code1/psi-trader-liangjunming",
                env_file="/root/work/.toolchain/psi-env.sh",
                config="/root/work/Code1/psi-trader-liangjunming/PsiTraderRunner/config.yaml",
                output_dir="/root/work/Code1/dataset/output",
                host_key="17062",
            )
            probe_failed_snapshot = {
                "sample_index": 1,
                "recorded_at": "2026-05-26T00:00:00Z",
                "kind": "snapshot",
                "duration_ms": 0.0,
                "metrics_available": False,
                "active_runner_count": 0,
                "blocking_processes": [],
                "notes": "remote probe failed",
            }

            with mock.patch.object(audit, "blocking_runners", return_value=["123 PsiTraderRunner"]), mock.patch.object(
                audit.host_weather_audit,
                "collect_remote_snapshots",
                return_value=[probe_failed_snapshot],
            ), mock.patch.object(audit, "run_remote_control_batch") as remote_batch:
                summary = audit.run_audit(args)

            remote_batch.assert_not_called()
            self.assertEqual(summary["promotion_gate"], "blocked_by_preflight_runner")
            self.assertEqual(summary["weather_decision"], "NOISY")
            readiness = json.loads((run_dir / "host_readiness.json").read_text(encoding="utf-8"))
            self.assertEqual(readiness["decision"], "NOISY")
            self.assertIn("preflight_active_runner_seen:1", readiness["reasons"])


if __name__ == "__main__":
    unittest.main()
