from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import psi_headless_auto_loop as auto_loop  # noqa: E402

DRIVER_PATH = REPO_ROOT / "scripts" / "run_prepared_candidate.py"
SMOKE_DRIVER_PATH = DRIVER_PATH.with_name("run_prepared_replication_smoke.py")


def load_driver():
    spec = importlib.util.spec_from_file_location("run_prepared_candidate", DRIVER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load {DRIVER_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_smoke_driver():
    spec = importlib.util.spec_from_file_location("run_prepared_replication_smoke", SMOKE_DRIVER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load {SMOKE_DRIVER_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class RunPreparedCandidateDriverTests(unittest.TestCase):
    def test_replication_history_and_host_key_are_passed_to_auto_loop(self) -> None:
        driver = load_driver()
        captured: dict[str, object] = {}
        with tempfile.TemporaryDirectory(prefix="prepared_driver_") as raw_dir:
            root = Path(raw_dir)
            source = root / "source"
            source.mkdir()
            ledger = root / "candidate_ledger.json"
            ledger.write_text("{}", encoding="utf-8")
            history = root / "prior" / "timing_history.tsv"
            history.parent.mkdir()
            history.write_text("history_key\n", encoding="utf-8")
            run_dir = root / "run"
            fake_auto = SimpleNamespace()
            written_states: list[dict[str, object]] = []
            fake_auto.build_parser = lambda: SimpleNamespace(
                parse_args=lambda items: SimpleNamespace(
                    run_dir=Path(items[items.index("--run-dir") + 1]),
                    replication_history="",
                    host_key=items[items.index("--host-key") + 1] if "--host-key" in items else "",
                    root="",
                    control_root="",
                    candidate_ledger=items[items.index("--candidate-ledger") + 1],
                )
            )
            fake_auto.ensure_run_dir = lambda path: Path(path).mkdir(parents=True, exist_ok=True)
            fake_auto.utc_now = lambda: "2026-05-29T00:00:00Z"
            fake_auto.update_heartbeat = lambda *_args, **_kwargs: None
            def fake_write_run_state(run_dir, **kwargs):
                written_states.append(kwargs)
                (Path(run_dir) / "run_state.json").write_text(json.dumps(kwargs), encoding="utf-8")

            fake_auto.write_run_state = fake_write_run_state
            fake_auto.record_attempt = lambda *_args, **_kwargs: None
            fake_auto.upsert_timing_from_batch = lambda *_args, **_kwargs: None
            fake_auto.count_verdict_rows = lambda _run_dir: (0, 0, 0, 0, 0, 0, 0, 0, 0, 1)
            fake_auto.judge_verdict = lambda _batch_state: ("screening_only", "screening only")
            fake_auto.set_patch_status = lambda *_args, **_kwargs: None

            def fake_materialize(_args, _run_dir, candidate, _iteration):
                candidate["candidate_workspace"] = str(root / "candidate_ws")
                candidate["patch_path"] = str(root / "patches" / "candidate.patch")
                return True, {}, ""

            def fake_call_remote_batch(args, _run_dir, candidate, iteration):
                captured["replication_history"] = args.replication_history
                captured["host_key"] = args.host_key
                captured["candidate_has_replicated_key"] = "replicated" in candidate
                return (
                    0,
                    _run_dir / "iterations" / f"iter_{iteration:03d}",
                    {
                        "status": "stopped",
                        "build_status": "pass",
                        "compare_status": "pass",
                        "timing_status": "screening_only",
                        "timing_verdict": "screening_only",
                        "comparison_accepted": False,
                        "paired_sample_count": 0,
                        "candidate_replication_detected": True,
                    },
                )
            fake_auto.materialize_candidate_patch = fake_materialize
            fake_auto.call_remote_batch = fake_call_remote_batch

            argv = [
                "run_prepared_candidate.py",
                "--repo-root",
                str(REPO_ROOT),
                "--run-dir",
                str(run_dir),
                "--source-root",
                str(source),
                "--patch-command",
                "builtin:fake-nonempty",
                "--candidate-ledger",
                str(ledger),
                "--candidate-id",
                "candidate",
                "--target",
                "handlerData.row_loop.stack",
                "--hypothesis",
                "prepared replication smoke",
                "--expected-effect",
                "smoke",
                "--remote-host",
                "",
                "--replication-history",
                str(history),
                "--host-key",
                "devbox",
            ]

            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(driver, "load_auto_loop", return_value=fake_auto),
            ):
                rc = driver.main()

        self.assertEqual(rc, 0)
        self.assertEqual(captured["replication_history"], str(history.resolve()))
        self.assertEqual(captured["host_key"], "devbox")
        self.assertFalse(captured["candidate_has_replicated_key"])
        self.assertTrue(written_states[-1]["candidate_replication_detected"])

    def test_replicated_flag_without_history_is_rejected(self) -> None:
        driver = load_driver()
        with tempfile.TemporaryDirectory(prefix="prepared_driver_") as raw_dir:
            root = Path(raw_dir)
            source = root / "source"
            source.mkdir()
            ledger = root / "candidate_ledger.json"
            ledger.write_text("{}", encoding="utf-8")
            argv = [
                "run_prepared_candidate.py",
                "--repo-root",
                str(REPO_ROOT),
                "--run-dir",
                str(root / "run"),
                "--source-root",
                str(source),
                "--patch-command",
                "builtin:fake-nonempty",
                "--candidate-ledger",
                str(ledger),
                "--candidate-id",
                "candidate",
                "--target",
                "handlerData.row_loop.stack",
                "--hypothesis",
                "prepared replication smoke",
                "--expected-effect",
                "smoke",
                "--replicated",
            ]

            with mock.patch.object(sys, "argv", argv):
                with self.assertRaises(SystemExit) as raised:
                    driver.main()

        self.assertIn("--replication-history", str(raised.exception))

    def test_prepared_history_write_read_round_trip_preserves_verdict(self) -> None:
        with tempfile.TemporaryDirectory(prefix="prepared_history_") as raw_dir:
            root = Path(raw_dir)
            prior_run = root / "prior_run"
            prior_run.mkdir()
            candidate = {
                "candidate_id": "candidate",
                "lane": "combination",
                "target": "handlerData.row_loop.stack",
                "touched_files": ["PsiFactorPipline/PsiReadWrite.cpp"],
            }
            batch_state = {
                "control_samples_ms": [1000.0, 1010.0, 990.0],
                "candidate_samples_ms": [850.0, 860.0, 840.0],
                "delta_ms": 150.0,
                "bootstrap_ci_low_ms": 100.0,
                "bootstrap_ci_high_ms": 180.0,
                "permutation_p_value": 0.01,
                "paired_stdev_ms": 5.0,
                "paired_range_ms": 20.0,
                "paired_mean_ms": 150.0,
                "noise_flag": "NOISY",
            }

            auto_loop.upsert_timing_from_batch(
                prior_run,
                candidate,
                batch_state,
                "devbox",
                verdict="accepted_noisy_single",
                verdict_reason="single noisy positive run",
            )

            rows = auto_loop.read_tsv(prior_run / "timing_history.tsv")
            candidate_rows = [row for row in rows if row.get("kind") == "candidate"]
            self.assertEqual(candidate_rows[0]["verdict"], "accepted_noisy_single")
            self.assertEqual(candidate_rows[0]["timing_verdict"], "accepted_noisy_single")

            fresh_run = root / "fresh_replication_run"
            fresh_run.mkdir()
            self.assertTrue(
                auto_loop._candidate_has_prior_replication(
                    fresh_run,
                    candidate,
                    history_path=prior_run / "timing_history.tsv",
                    host_key="devbox",
                )
            )

    def test_replication_smoke_driver_dry_plan(self) -> None:
        smoke = load_smoke_driver()
        with tempfile.TemporaryDirectory(prefix="prepared_smoke_") as raw_dir:
            argv = [
                "run_prepared_replication_smoke.py",
                "--base-run-dir",
                str(Path(raw_dir) / "runs"),
                "--dry-plan",
            ]
            with mock.patch.object(sys, "argv", argv):
                rc = smoke.main()

        self.assertEqual(rc, 0)

    def test_replication_smoke_driver_accepts_clean_replicated_run(self) -> None:
        smoke = load_smoke_driver()
        with tempfile.TemporaryDirectory(prefix="prepared_smoke_") as raw_dir:
            root = Path(raw_dir)
            tools_dir = root / "tools"
            tools_dir.mkdir()
            driver = tools_dir / "run_prepared_candidate.py"
            patch = root / "patch.cmd"
            ledger = root / "candidate_ledger.json"
            driver.write_text("driver", encoding="utf-8")
            patch.write_text("patch", encoding="utf-8")
            ledger.write_text("{}", encoding="utf-8")
            base_run = root / "runs"

            def fake_run_prepared(**kwargs):
                run_dir = kwargs["run_dir"]
                run_dir.mkdir(parents=True, exist_ok=True)
                (run_dir / "timing_history.tsv").write_text("history_key\n", encoding="utf-8")
                (run_dir / "run_state.json").write_text(
                    json.dumps(
                        {
                            "latest_verdict": "accepted",
                            "candidate_replication_detected": bool(kwargs.get("replication_history")),
                        }
                    ),
                    encoding="utf-8",
                )
                summary_dir = run_dir / "iterations" / "iter_001_stack_skip_unused_row_fields"
                summary_dir.mkdir(parents=True)
                (summary_dir / "remote_comparison_summary.json").write_text(
                    json.dumps(
                        {
                            "control_source_kind": "synced_same_source",
                            "control_root": "/tmp/control",
                            "candidate_root": "/tmp/candidate",
                            "control_runner": "/tmp/control/runner",
                            "candidate_runner": "/tmp/candidate/runner",
                            "paired": {"noise_flag": "ok"},
                        }
                    ),
                    encoding="utf-8",
                )
                return 0, ""

            argv = [
                "run_prepared_replication_smoke.py",
                "--task-dir",
                str(root),
                "--base-run-dir",
                str(base_run),
                "--patch-command",
                str(patch),
                "--candidate-ledger",
                str(ledger),
                "--remote-host",
                "",
            ]
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(smoke, "run_prepared", side_effect=fake_run_prepared),
            ):
                rc = smoke.main()

        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
