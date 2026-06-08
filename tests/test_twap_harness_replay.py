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

    def test_multi_subscriber_normal_regression_rejects_candidate(self) -> None:
        batch_state = {
            "compare_status": "pass",
            "decision": "screening_only",
            "reason": "completed",
            "lost_failure_count": 0,
            "twap_case_deltas": [
                {
                    "case": "100_i50_s4",
                    "control_p95_ms": "9.0",
                    "candidate_p95_ms": "13.4",
                    "p95_delta_ms": 4.4,
                    "control_lost": "0",
                    "candidate_lost": "0",
                }
            ],
            "twap_timing_samples": [
                {
                    "case": "100_i50_s4",
                    "role": "control",
                    "sent": "2000",
                    "received": "2000",
                    "lost": "0",
                    "p95_ms": "9.0",
                },
                {
                    "case": "100_i50_s4",
                    "role": "candidate",
                    "sent": "2000",
                    "received": "2000",
                    "lost": "0",
                    "p95_ms": "13.4",
                },
            ],
        }

        verdict, reason = loop.judge_verdict(batch_state)

        self.assertEqual(verdict, "rejected")
        self.assertEqual(reason, "TWAP normal-frequency p95 regression 4.400ms exceeds 1.000ms")

    def test_twap_normal_improvement_still_uses_legacy_remote_decision_path(self) -> None:
        batch_state = {
            "compare_status": "pass",
            "decision": "accepted",
            "reason": "remote TWAP gate accepted candidate",
            "timing_verdict": "accepted",
            "lost_failure_count": 0,
            "twap_case_deltas": [
                {
                    "case": "100_i50_s4",
                    "control_p95_ms": "12.0",
                    "candidate_p95_ms": "9.0",
                    "p95_delta_ms": -3.0,
                    "control_lost": "0",
                    "candidate_lost": "0",
                    "control_unknown_pushes": "0",
                    "candidate_unknown_pushes": "0",
                }
            ],
        }

        verdict, reason = loop.judge_verdict(batch_state)

        self.assertEqual(verdict, "accepted")
        self.assertEqual(reason, "")

    def test_twap_stress_regression_guard_remains_welded_for_now(self) -> None:
        batch_state = {
            "compare_status": "pass",
            "decision": "accepted",
            "reason": "remote TWAP gate accepted candidate",
            "lost_failure_count": 0,
            "twap_case_deltas": [
                {
                    "case": "500_i5_s4",
                    "control_p95_ms": "50.0",
                    "candidate_p95_ms": "55.5",
                    "p95_delta_ms": 5.5,
                    "control_lost": "0",
                    "candidate_lost": "0",
                    "control_unknown_pushes": "0",
                    "candidate_unknown_pushes": "0",
                }
            ],
        }

        verdict, reason = loop.judge_verdict(batch_state)

        self.assertEqual(verdict, "rejected")
        self.assertEqual(reason, "TWAP stress p95 regression 5.500ms exceeds 5.000ms")

    def test_multi_subscriber_unknown_pushes_reject_and_preserve_new_fields(self) -> None:
        summary = {
            "candidate_id": "twap_unknown_push_candidate",
            "build_status": "pass",
            "correctness_status": "pass",
            "timing_status": "pass",
            "decision": "rejected",
            "reason": "completed",
            "accepted": False,
            "lost_failure_count": 1,
            "case_deltas": [
                {
                    "case": "500_i20_s4",
                    "control_p95_ms": "8.0",
                    "candidate_p95_ms": "8.2",
                    "p95_delta_ms": 0.2,
                    "control_lost": "0",
                    "candidate_lost": "0",
                    "control_unknown_pushes": "0",
                    "candidate_unknown_pushes": "1",
                    "control_worst_subscriber_p95_ms": "8.5",
                    "candidate_worst_subscriber_p95_ms": "9.1",
                }
            ],
            "timing_samples": [
                {
                    "case": "500_i20_s4",
                    "role": "candidate",
                    "count": "500",
                    "publishes": "500",
                    "subscribers": "4",
                    "sent": "2000",
                    "received": "2000",
                    "lost": "0",
                    "unknown_pushes": "1",
                    "p50_ms": "7.0",
                    "p95_ms": "8.2",
                    "worst_subscriber_p95_ms": "9.1",
                    "status": "WARN_UNKNOWN_PUSH",
                }
            ],
        }

        with tempfile.TemporaryDirectory(prefix="twap_unknown_push_test_") as raw_dir:
            run_dir = Path(raw_dir)
            loop.ensure_run_dir(run_dir)
            candidate = {
                "candidate_id": "twap_unknown_push_candidate",
                "lane": "evidence",
                "target": "twap.push.test",
                "touched_files": ["PsiGrpcServer/twap_sale_service.cpp"],
                "semantic_risk": "low",
                "hypothesis": "fixture candidate",
            }
            batch_state = {"compare_status": "pass"}
            loop._merge_comparison_summary(batch_state, summary)

            verdict, reason = loop.judge_verdict(batch_state)
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

            self.assertEqual(verdict, "rejected")
            self.assertEqual(reason, "TWAP candidate produced unknown pushes: candidate_unknown_push_total=1")
            attempts = read_tsv(run_dir / "attempts.tsv")
            self.assertIn("unknown=1", attempts[0]["notes"])
            history = read_tsv(run_dir / "timing_history.tsv")
            self.assertEqual(history[0]["sample_count"], "2000")
            self.assertIn("subscribers=4", history[0]["notes"])
            self.assertIn("unknown_pushes=1", history[0]["notes"])
            self.assertIn("worst_subscriber_p95_ms=9.1", history[0]["notes"])

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
            self.assertEqual(reason, "TWAP control baseline unhealthy: control_lost_total=161, control_unknown_push_total=0")
            self.assertEqual(read_tsv(run_dir / "neutral_pool.tsv"), [])

            attempts = read_tsv(run_dir / "attempts.tsv")
            self.assertEqual(attempts[0]["verdict"], "infra_blocked")
            self.assertEqual(attempts[0]["timing_verdict"], "infra_blocked")
            self.assertEqual(attempts[0]["stop_reason"], "control_baseline_unhealthy")
            self.assertEqual(loop.count_verdict_rows(run_dir), (0, 0, 0, 0, 1, 0, 0, 0, 0, 0))

            history = read_tsv(run_dir / "timing_history.tsv")
            candidate_rows = [row for row in history if row["kind"] == "candidate"]
            self.assertEqual(len(candidate_rows), 1)
            self.assertEqual(candidate_rows[0]["verdict"], "infra_blocked")
            self.assertEqual(candidate_rows[0]["timing_verdict"], "infra_blocked")

    def test_twap_guard_rejects_rebuilding_cached_push_per_session(self) -> None:
        patch_text = """
diff --git a/PsiGrpcServer/twap_sale_service.cpp b/PsiGrpcServer/twap_sale_service.cpp
--- a/PsiGrpcServer/twap_sale_service.cpp
+++ b/PsiGrpcServer/twap_sale_service.cpp
@@ -1,12 +1,5 @@
-        TwapSalePushMessage stock_change_message;
-        bool has_stock_change_message = false;
-        if (!stock_code.empty()) {
-            stock_change_message = buildTwapSaleAggregationPushMessage(userId, stock_code, cmd);
-            has_stock_change_message = stock_change_message.success();
-        }
         for (const auto &target: target_sessions) {
             TwapSalePushMessage message = stock_code.empty()
                                           ? buildTwapSaleAggregationMessage(request)
-                                          : stock_change_message;
+                                          : buildTwapSaleAggregationPushMessage(userId, stock_code, cmd);
         }
"""

        violations = loop._validate_patch_semantic_guards(patch_text)

        self.assertTrue(any("fanout regression" in item for item in violations))


class RemoteBatchControlSourceKindTests(unittest.TestCase):
    """Nail tests: BOTH psi_headless_remote.sh AND twap_headless_remote.sh must
    emit control_source_kind in their run_state.json and comparison_summary.json
    so the shared judge_verdict same-source gate works for both adapters.

    These tests parse the shell scripts directly. Deleting the field from either
    script will turn the relevant test red without needing a real devbox run.
    The symmetry guard is the point: a fix to one script that is not mirrored in
    the other shows up immediately.
    """

    SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
    PSI_SCRIPT = SCRIPTS_DIR / "psi_headless_remote.sh"
    TWAP_SCRIPT = SCRIPTS_DIR / "twap_headless_remote.sh"

    def _text(self, script: Path) -> str:
        return script.read_text(encoding="utf-8")

    # ---- psi ----

    def test_psi_remote_emits_control_source_kind_in_both_payloads(self) -> None:
        """psi run_state and comparison_summary must both emit control_source_kind."""
        text = self._text(self.PSI_SCRIPT)
        count = text.count('"control_source_kind"')
        self.assertGreaterEqual(count, 2,
                                "psi_headless_remote.sh must emit control_source_kind "
                                "in both run_state and comparison_summary payloads")

    def test_psi_remote_control_source_kind_env_var_is_forwarded(self) -> None:
        """CONTROL_SOURCE_KIND must be forwarded into psi Python heredocs."""
        text = self._text(self.PSI_SCRIPT)
        count = text.count("CONTROL_SOURCE_KIND")
        self.assertGreaterEqual(count, 2,
                                "CONTROL_SOURCE_KIND must appear in psi_headless_remote.sh "
                                "at least in both Python payload blocks")

    def test_psi_remote_script_passes_bash_syntax_check(self) -> None:
        """bash -n must pass on psi_headless_remote.sh."""
        import subprocess as sp
        result = sp.run(
            ["bash", "-n", str(self.PSI_SCRIPT)],
            check=False, stdout=sp.PIPE, stderr=sp.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
        self.assertEqual(result.returncode, 0, f"bash -n psi failed:\n{result.stdout}")

    # ---- twap ----

    def test_twap_remote_emits_control_source_kind_in_both_payloads(self) -> None:
        """twap run_state and comparison_summary must both emit control_source_kind."""
        text = self._text(self.TWAP_SCRIPT)
        count = text.count('"control_source_kind"')
        self.assertGreaterEqual(count, 2,
                                "twap_headless_remote.sh must emit control_source_kind "
                                "in both run_state and comparison_summary payloads")

    def test_twap_remote_control_source_kind_env_var_is_forwarded(self) -> None:
        """CONTROL_SOURCE_KIND must be forwarded into twap Python heredocs."""
        text = self._text(self.TWAP_SCRIPT)
        count = text.count("CONTROL_SOURCE_KIND=")
        # At minimum: top-level init, write_state env block, write_summary env block.
        self.assertGreaterEqual(count, 3,
                                "CONTROL_SOURCE_KIND must be set/forwarded in at least 3 places "
                                "in twap_headless_remote.sh")

    def test_twap_remote_script_passes_bash_syntax_check(self) -> None:
        """bash -n must pass on twap_headless_remote.sh."""
        import subprocess as sp
        result = sp.run(
            ["bash", "-n", str(self.TWAP_SCRIPT)],
            check=False, stdout=sp.PIPE, stderr=sp.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
        self.assertEqual(result.returncode, 0, f"bash -n twap failed:\n{result.stdout}")


if __name__ == "__main__":
    unittest.main()
