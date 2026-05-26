import csv
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import psi_headless_auto_loop as loop  # noqa: E402


def read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


class TwapHarnessReplayTests(unittest.TestCase):
    def test_stress_regression_replays_as_rejected_everywhere(self) -> None:
        summary = {
            "candidate_id": "twap_bad_stress_candidate",
            "build_status": "pass",
            "correctness_status": "pass",
            "timing_status": "pass",
            "decision": "screening_only",
            "reason": "completed",
            "accepted": False,
            "lost_failure_count": 0,
            "case_deltas": [
                {
                    "case": "500_i20",
                    "control_p95_ms": "52.0",
                    "candidate_p95_ms": "52.4",
                    "p95_delta_ms": 0.4,
                    "control_lost": "0",
                    "candidate_lost": "0",
                },
                {
                    "case": "1000_i20",
                    "control_p95_ms": "53.0",
                    "candidate_p95_ms": "53.2",
                    "p95_delta_ms": 0.2,
                    "control_lost": "0",
                    "candidate_lost": "0",
                },
                {
                    "case": "500_i5",
                    "control_p95_ms": "55.0",
                    "candidate_p95_ms": "71.0",
                    "p95_delta_ms": 16.0,
                    "control_lost": "0",
                    "candidate_lost": "0",
                },
            ],
            "timing_samples": [
                {
                    "case": "500_i20",
                    "role": "control",
                    "count": "500",
                    "sent": "500",
                    "received": "500",
                    "lost": "0",
                    "avg_ms": "30.0",
                    "p50_ms": "30.0",
                    "p95_ms": "52.0",
                    "p99_ms": "60.0",
                    "max_ms": "80.0",
                    "status": "PASS",
                },
                {
                    "case": "500_i20",
                    "role": "candidate",
                    "count": "500",
                    "sent": "500",
                    "received": "500",
                    "lost": "0",
                    "avg_ms": "30.2",
                    "p50_ms": "30.1",
                    "p95_ms": "52.4",
                    "p99_ms": "61.0",
                    "max_ms": "82.0",
                    "status": "PASS",
                },
                {
                    "case": "1000_i20",
                    "role": "control",
                    "count": "1000",
                    "sent": "1000",
                    "received": "1000",
                    "lost": "0",
                    "avg_ms": "30.1",
                    "p50_ms": "30.0",
                    "p95_ms": "53.0",
                    "p99_ms": "62.0",
                    "max_ms": "90.0",
                    "status": "PASS",
                },
                {
                    "case": "1000_i20",
                    "role": "candidate",
                    "count": "1000",
                    "sent": "1000",
                    "received": "1000",
                    "lost": "0",
                    "avg_ms": "30.2",
                    "p50_ms": "30.1",
                    "p95_ms": "53.2",
                    "p99_ms": "63.0",
                    "max_ms": "91.0",
                    "status": "PASS",
                },
                {
                    "case": "500_i5",
                    "role": "control",
                    "count": "500",
                    "sent": "500",
                    "received": "500",
                    "lost": "0",
                    "avg_ms": "32.0",
                    "p50_ms": "32.0",
                    "p95_ms": "55.0",
                    "p99_ms": "70.0",
                    "max_ms": "99.0",
                    "status": "PASS",
                },
                {
                    "case": "500_i5",
                    "role": "candidate",
                    "count": "500",
                    "sent": "500",
                    "received": "500",
                    "lost": "0",
                    "avg_ms": "40.0",
                    "p50_ms": "39.0",
                    "p95_ms": "71.0",
                    "p99_ms": "88.0",
                    "max_ms": "110.0",
                    "status": "PASS",
                },
            ],
        }

        with tempfile.TemporaryDirectory(prefix="twap_replay_test_") as raw_dir:
            run_dir = Path(raw_dir)
            loop.ensure_run_dir(run_dir)
            candidate = {
                "candidate_id": "twap_bad_stress_candidate",
                "lane": "evidence",
                "target": "twap.push.test",
                "touched_files": ["PsiGrpcServer/twap_sale_service.cpp"],
                "semantic_risk": "low",
                "hypothesis": "fixture candidate",
            }
            batch_state = {"compare_status": "pass"}
            loop._merge_comparison_summary(batch_state, summary)

            verdict, reason = loop.judge_verdict(batch_state)
            if verdict == "neutral":
                loop.record_neutral_pool_entry(run_dir, candidate, batch_state, reason)
            loop.record_attempt(
                run_dir,
                iteration=1,
                candidate=candidate,
                batch_state=batch_state,
                verdict=verdict,
                retry_condition=reason,
                stop_reason="",
                notes="fixture replay",
            )
            loop.upsert_timing_from_batch(
                run_dir,
                candidate,
                batch_state,
                "17062",
                verdict=verdict,
                verdict_reason=reason,
            )

            attempts = read_tsv(run_dir / "attempts.tsv")
            self.assertEqual(attempts[0]["verdict"], "rejected")
            self.assertEqual(attempts[0]["timing_verdict"], "rejected")
            self.assertEqual(attempts[0]["sample_count"], "3")
            self.assertEqual(attempts[0]["twap_max_stress_regression_ms"], "16.000")

            self.assertEqual(read_tsv(run_dir / "neutral_pool.tsv"), [])

            history = read_tsv(run_dir / "timing_history.tsv")
            candidate_rows = [row for row in history if row["kind"] == "candidate"]
            self.assertEqual(len(candidate_rows), 3)
            self.assertTrue(all(row["timing_verdict"] == "rejected" for row in candidate_rows))
            self.assertTrue(all(row["verdict"] == "rejected" for row in candidate_rows))
            self.assertFalse(any(row["timing_verdict"] == "pass" for row in candidate_rows))
            self.assertFalse(any(row["verdict"] == "pass" for row in candidate_rows))

    def test_remote_rejected_completed_still_reports_twap_loss_reason(self) -> None:
        batch_state = {
            "compare_status": "pass",
            "decision": "rejected",
            "reason": "completed",
            "lost_failure_count": 2,
            "twap_case_deltas": [
                {
                    "case": "500_i5",
                    "control_p95_ms": "250.0",
                    "candidate_p95_ms": "312.0",
                    "p95_delta_ms": 62.0,
                    "control_lost": "0",
                    "candidate_lost": "102",
                }
            ],
        }

        verdict, reason = loop.judge_verdict(batch_state)

        self.assertEqual(verdict, "rejected")
        self.assertEqual(reason, "TWAP push timing lost messages: lost_failure_count=2")

    def test_control_lost_pushes_blocks_as_infra_not_candidate_verdict(self) -> None:
        batch_state = {
            "compare_status": "pass",
            "decision": "screening_only",
            "reason": "completed",
            "lost_failure_count": 1,
            "twap_case_deltas": [
                {
                    "case": "500_i20",
                    "control_p95_ms": "1085.0",
                    "candidate_p95_ms": "53.0",
                    "p95_delta_ms": "",
                    "control_lost": "161",
                    "candidate_lost": "0",
                }
            ],
            "twap_timing_samples": [
                {
                    "case": "500_i20",
                    "role": "control",
                    "sent": "500",
                    "received": "339",
                    "lost": "161",
                    "p95_ms": "1085.0",
                },
                {
                    "case": "500_i20",
                    "role": "candidate",
                    "sent": "500",
                    "received": "500",
                    "lost": "0",
                    "p95_ms": "53.0",
                },
            ],
        }

        with tempfile.TemporaryDirectory(prefix="twap_control_lost_test_") as raw_dir:
            run_dir = Path(raw_dir)
            loop.ensure_run_dir(run_dir)
            candidate = {
                "candidate_id": "twap_control_unhealthy",
                "lane": "evidence",
                "target": "twap.push.test",
                "touched_files": ["PsiGrpcServer/twap_sale_service.cpp"],
                "semantic_risk": "low",
                "hypothesis": "fixture candidate",
            }

            verdict, reason = loop.judge_verdict(batch_state)
            if verdict == "neutral":
                loop.record_neutral_pool_entry(run_dir, candidate, batch_state, reason)
            loop.record_attempt(
                run_dir,
                iteration=1,
                candidate=candidate,
                batch_state=batch_state,
                verdict=verdict,
                retry_condition=reason,
                stop_reason="control_baseline_unhealthy",
                notes="fixture replay",
            )
            loop.upsert_timing_from_batch(
                run_dir,
                candidate,
                batch_state,
                "17062",
                verdict=verdict,
                verdict_reason=reason,
            )

            self.assertEqual(verdict, "infra_blocked")
            self.assertEqual(reason, "TWAP control baseline lost pushes: control_lost_total=161")
            self.assertEqual(read_tsv(run_dir / "neutral_pool.tsv"), [])

            attempts = read_tsv(run_dir / "attempts.tsv")
            self.assertEqual(attempts[0]["verdict"], "infra_blocked")
            self.assertEqual(attempts[0]["timing_verdict"], "infra_blocked")
            self.assertEqual(attempts[0]["stop_reason"], "control_baseline_unhealthy")
            self.assertEqual(loop.count_verdict_rows(run_dir), (0, 0, 0, 0, 1))

            history = read_tsv(run_dir / "timing_history.tsv")
            candidate_rows = [row for row in history if row["kind"] == "candidate"]
            self.assertEqual(len(candidate_rows), 1)
            self.assertEqual(candidate_rows[0]["verdict"], "infra_blocked")
            self.assertEqual(candidate_rows[0]["timing_verdict"], "infra_blocked")


if __name__ == "__main__":
    unittest.main()
