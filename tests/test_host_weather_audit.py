from __future__ import annotations

import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import host_weather_audit as audit  # noqa: E402


def quiet_snapshot() -> dict[str, object]:
    return {
        "sample_index": 1,
        "recorded_at": "2026-05-26T00:00:00Z",
        "kind": "snapshot",
        "duration_ms": 1000.0,
        "loadavg_1m": 2.0,
        "loadavg_5m": 2.0,
        "load_per_cpu_1m": 0.05,
        "cpu_idle_pct": 96.0,
        "cpu_iowait_pct": 0.2,
        "context_switches_per_sec": 1000.0,
        "run_queue": 1,
        "procs_blocked": 0,
        "active_runner_count": 0,
        "blocking_processes": [],
        "metrics_available": True,
        "notes": "",
    }


class HostWeatherAuditTests(unittest.TestCase):
    def test_quiet_requires_stable_control_samples(self) -> None:
        summary = audit.classify_weather(
            [quiet_snapshot()],
            control_samples_ms=[1000.0, 1004.0, 998.0, 1002.0, 1001.0],
        )

        self.assertEqual(summary["decision"], "QUIET")
        self.assertLess(summary["control_cov"], 0.015)

    def test_snapshot_only_is_not_promotion_quiet(self) -> None:
        summary = audit.classify_weather([quiet_snapshot()], control_samples_ms=[])

        self.assertEqual(summary["decision"], "BORDERLINE")
        self.assertIn("insufficient_control_samples:0<5", summary["reasons"])

    def test_noisy_control_samples_block_promotion(self) -> None:
        summary = audit.classify_weather(
            [quiet_snapshot()],
            control_samples_ms=[1000.0, 1180.0, 860.0, 1230.0, 910.0],
        )

        self.assertEqual(summary["decision"], "NOISY")
        self.assertTrue(
            any(reason.startswith("control_cov_above_noisy_threshold") for reason in summary["reasons"])
        )

    def test_active_runner_blocks_even_with_stable_control(self) -> None:
        snapshot = quiet_snapshot()
        snapshot["active_runner_count"] = 1
        snapshot["blocking_processes"] = [
            {
                "pid": "1234",
                "name": "PsiTraderRunner",
                "cmdline": "/root/work/Code2/build/PsiTraderRunner",
            }
        ]

        summary = audit.classify_weather(
            [snapshot],
            control_samples_ms=[1000.0, 1004.0, 998.0, 1002.0, 1001.0],
        )

        self.assertEqual(summary["decision"], "NOISY")
        self.assertIn("active_runner_seen:1", summary["reasons"])

    def test_cli_writes_expected_artifacts(self) -> None:
        with tempfile.TemporaryDirectory(prefix="host_weather_test_") as raw_dir:
            run_dir = Path(raw_dir) / "run"
            fixture = Path(raw_dir) / "snapshots.json"
            fixture.write_text(json.dumps([quiet_snapshot()]), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS_DIR / "host_weather_audit.py"),
                    "--run-dir",
                    str(run_dir),
                    "--snapshot-json",
                    str(fixture),
                    "--control-samples-ms",
                    "1000,1004,998,1002,1001",
                    "--host-key",
                    "17062",
                ],
                cwd=str(REPO_ROOT),
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
            )

            self.assertEqual(result.returncode, 0, result.stdout)
            readiness = json.loads((run_dir / "host_readiness.json").read_text(encoding="utf-8"))
            summary = json.loads((run_dir / "host_jitter_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(readiness["decision"], "QUIET")
            self.assertEqual(summary["decision"], "QUIET")
            with (run_dir / "host_jitter_samples.tsv").open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle, delimiter="\t"))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["kind"], "snapshot")


if __name__ == "__main__":
    unittest.main()
