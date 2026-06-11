from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import headless_auto_loop as auto_loop  # noqa: E402
from optimization_ledger import (  # noqa: E402
    LEDGER_FILENAME,
    build_ledger_row,
    constants_hash,
    read_ledger_rows_from_artifacts,
)
from attempts_schema import ATTEMPTS_FIELDNAMES, OPTIMIZATION_LEDGER_FIELDNAMES  # noqa: E402


class OptimizationLedgerTests(unittest.TestCase):
    def test_constants_hash_is_stable_and_sensitive_to_constants(self) -> None:
        base = constants_hash(
            delta_min_ms_used="290.000",
            decisive_k="1.000",
            sign_min="0.750",
            escalation_steps="4,8,12",
        )
        same = constants_hash(
            delta_min_ms_used="290.000",
            decisive_k="1.000",
            sign_min="0.750",
            escalation_steps="4,8,12",
        )
        changed = constants_hash(
            delta_min_ms_used="291.000",
            decisive_k="1.000",
            sign_min="0.750",
            escalation_steps="4,8,12",
        )

        self.assertEqual(base, same)
        self.assertNotEqual(base, changed)
        self.assertRegex(base, r"^[0-9a-f]{16}$")

    def test_build_ledger_row_reuses_evidence_field_names(self) -> None:
        candidate = {
            "candidate_id": "candidate_001",
            "lane": "evidence",
            "generator_model": "model-a",
            "generator_session": "session-a",
        }
        batch_state = {
            "timing_verdict_method": "paired_bootstrap_permutation_v1",
            "naive_k1_would_accept": "true",
            "naive_k1_first_delta_ms": "12.000",
            "delta_min_ms_used": "290.000",
            "decisive_k": "1.000",
            "sign_min": "0.750",
            "escalation_steps": "4,8,12",
            "host_id": "host-a",
            "env_class": "devbox",
            "control_stdev_ms": "123.000",
            "control_range_ms": "456.000",
            "replicated": "false",
            "recorded_at": "2026-06-11T00:00:00Z",
        }

        row = build_ledger_row(
            candidate=candidate,
            batch_state=batch_state,
            verdict="accepted_noisy_single",
            artifact_path="runs/iter_001/comparison_summary.json",
        )

        self.assertEqual(set(row), set(OPTIMIZATION_LEDGER_FIELDNAMES))
        self.assertEqual(row["candidate_id"], "candidate_001")
        self.assertEqual(row["judge_kind"], "confidence_tier")
        self.assertEqual(row["verdict"], "accepted_noisy_single")
        self.assertEqual(row["generator_model"], "model-a")
        self.assertEqual(row["generator_session"], "session-a")
        self.assertEqual(row["naive_k1_would_accept"], "true")
        self.assertEqual(row["artifact_path"], "runs/iter_001/comparison_summary.json")
        self.assertEqual(
            row["constants_hash"],
            constants_hash(
                delta_min_ms_used="290.000",
                decisive_k="1.000",
                sign_min="0.750",
                escalation_steps="4,8,12",
            ),
        )

    def test_replicated_field_does_not_infer_from_legacy_verdict_name(self) -> None:
        row = build_ledger_row(
            candidate={"candidate_id": "legacy", "lane": "evidence"},
            batch_state={},
            verdict="accepted_noisy_replicated",
            artifact_path="legacy/attempts.tsv",
        )

        self.assertEqual(row["verdict"], "accepted_noisy_replicated")
        self.assertEqual(row["replicated"], "false")

    def test_record_attempt_appends_optimization_ledger_row(self) -> None:
        with tempfile.TemporaryDirectory(prefix="optimization_ledger_") as raw_dir:
            run_dir = Path(raw_dir)
            candidate = {
                "candidate_id": "candidate_001",
                "lane": "evidence",
                "target": "handlerData.row_loop.stack",
                "touched_files": ["PsiFactorPipline/PsiReadWrite.cpp"],
                "generator_model": "model-a",
                "generator_session": "session-a",
            }
            batch_state = {
                "build_status": "pass",
                "compare_status": "pass",
                "timing_verdict": "accepted_noisy_single",
                "timing_verdict_method": "paired_bootstrap_permutation_v1",
                "candidate_samples_ms": [10.0, 9.0],
                "control_median_ms": 20.0,
                "candidate_median_ms": 10.0,
                "delta_ms": 10.0,
                "noise_flag": "NOISY",
                "paired_stdev_ms": "123.000",
                "paired_range_ms": "456.000",
                "naive_k1_would_accept": "true",
                "naive_k1_first_delta_ms": "12.000",
                "delta_min_ms_used": "290.000",
                "decisive_k": "1.000",
                "sign_min": "0.750",
                "escalation_steps": "4,8,12",
                "host_id": "host-a",
                "env_class": "devbox",
                "control_stdev_ms": "123.000",
                "control_range_ms": "456.000",
                "replicated": "false",
                "comparison_summary_path": str(run_dir / "iterations" / "iter_001" / "comparison_summary.json"),
            }

            auto_loop.record_attempt(
                run_dir,
                iteration=1,
                candidate=candidate,
                batch_state=batch_state,
                verdict="accepted_noisy_single",
                retry_condition="queued",
                stop_reason="",
            )

            ledger_rows = auto_loop.read_tsv(run_dir / LEDGER_FILENAME)
            self.assertEqual(len(ledger_rows), 1)
            self.assertEqual(ledger_rows[0]["candidate_id"], "candidate_001")
            self.assertEqual(ledger_rows[0]["verdict"], "accepted_noisy_single")
            self.assertEqual(ledger_rows[0]["generator_model"], "model-a")
            self.assertEqual(ledger_rows[0]["constants_hash"], constants_hash(
                delta_min_ms_used="290.000",
                decisive_k="1.000",
                sign_min="0.750",
                escalation_steps="4,8,12",
            ))

    def test_ledger_appender_failure_does_not_interrupt_attempt_recording(self) -> None:
        with tempfile.TemporaryDirectory(prefix="optimization_ledger_fail_") as raw_dir:
            run_dir = Path(raw_dir)
            candidate = {
                "candidate_id": "candidate_001",
                "lane": "evidence",
                "target": "handlerData.row_loop.stack",
                "touched_files": [],
            }
            batch_state = {
                "build_status": "pass",
                "compare_status": "pass",
                "candidate_samples_ms": [10.0],
                "control_median_ms": 20.0,
                "candidate_median_ms": 10.0,
                "delta_ms": 10.0,
            }

            with (
                mock.patch.object(auto_loop, "append_optimization_ledger_row", side_effect=OSError("disk full")),
                mock.patch("sys.stderr"),
            ):
                auto_loop.record_attempt(
                    run_dir,
                    iteration=1,
                    candidate=candidate,
                    batch_state=batch_state,
                    verdict="neutral",
                    retry_condition="",
                    stop_reason="",
                )

            attempts = auto_loop.read_tsv(run_dir / "attempts.tsv")
            self.assertEqual(len(attempts), 1)
            self.assertFalse((run_dir / LEDGER_FILENAME).exists())

    def test_backfill_reads_attempt_and_history_artifacts_without_executing_on_import(self) -> None:
        with tempfile.TemporaryDirectory(prefix="optimization_ledger_backfill_") as raw_dir:
            root = Path(raw_dir)
            run_dir = root / "run"
            run_dir.mkdir()
            auto_loop.write_tsv(
                run_dir / "attempts.tsv",
                [
                    {
                        "candidate_id": "",
                        "lane": "control",
                        "verdict": "DIAGNOSTIC_ONLY",
                    },
                    {
                        "candidate_id": "candidate_001",
                        "lane": "evidence",
                        "verdict": "accepted",
                        "timing_verdict_method": "paired_bootstrap_permutation_v1",
                        "paired_deltas_ms": "10,-5,20",
                        "delta_min_ms_used": "290.000",
                        "decisive_k": "1.000",
                        "sign_min": "0.750",
                        "escalation_steps": "4,8,12",
                        "recorded_at": "2026-06-11T00:00:00Z",
                    }
                ],
                ATTEMPTS_FIELDNAMES,
            )

            rows = list(read_ledger_rows_from_artifacts([root]))

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["candidate_id"], "candidate_001")
        self.assertEqual(rows[0]["naive_k1_first_delta_ms"], "10.000")
        self.assertEqual(rows[0]["naive_k1_would_accept"], "true")


if __name__ == "__main__":
    unittest.main()
