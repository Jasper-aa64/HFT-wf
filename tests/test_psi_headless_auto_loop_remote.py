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

            def fake_ssh(_host: str, command: str, *, text: bool = True) -> subprocess.CompletedProcess[str]:
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


if __name__ == "__main__":
    unittest.main()
