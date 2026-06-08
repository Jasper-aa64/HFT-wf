from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from psi_attempts_schema import ATTEMPTS_FIELDNAMES  # noqa: E402
from psi_headless_auto_loop import apply_change_class_policy, judge_verdict  # noqa: E402
from psi_timing_analysis import (  # noqa: E402
    ConfidenceTierResult,
    confidence_tier,
    evidence_fields,
    summarize_paired_timing,
    validate_class_a,
)


class PairedTimingAnalysisTests(unittest.TestCase):
    def test_clear_paired_slowdown_rejects_even_when_delta_jitter_is_noisy(self) -> None:
        evidence = summarize_paired_timing(
            [88, 83, 86, 86, 86],
            [98, 98, 92, 95, 97],
            bootstrap_resamples=400,
            permutation_resamples=400,
        )

        self.assertEqual(evidence.noise_flag, "NOISY")
        self.assertEqual(evidence.verdict, "rejected")
        self.assertEqual(evidence.paired_sample_count, 5)
        self.assertLessEqual(evidence.bootstrap_ci_high_ms or 0.0, 0.0)
        self.assertIn("non-improvement", evidence.reason)

    def test_accepted_noisy_single_when_replicated_false(self) -> None:
        """When replicated=False and tier is marginal + noisy -> accepted_noisy_single.

        Uses bimodal deltas (6 × 500ms + 6 × 15000ms): all positive so noise_flag=NOISY
        (range/control_median ≈ 29%), CI_low > 0 and p is tiny (bimodal permutation
        distribution can never reach the observed median), but decisiveness << 1.0
        because the CI is extremely wide.  This forces the marginal tier, which is
        the correct path for accepted_noisy_single under the B redesign.
        """
        # Bimodal deltas: 6×500ms + 6×15000ms → median=7750ms, range=14500ms.
        # range/control_median = 14500/50000 = 29% >> 2% → NOISY.
        # Bootstrap CI is extremely wide (~[500, 15000]), so decisiveness << 1.0 → marginal.
        control_6_6 = [50000] * 12
        candidate_6_6 = [49500, 49500, 49500, 49500, 49500, 49500,
                         35000, 35000, 35000, 35000, 35000, 35000]
        evidence = summarize_paired_timing(
            control_6_6,
            candidate_6_6,
            replicated=False,
            required_pairs=5,
            bootstrap_resamples=2000,
            permutation_resamples=2000,
        )

        self.assertEqual(evidence.noise_flag, "NOISY")
        self.assertEqual(evidence.confidence_tier_name, "marginal")
        self.assertEqual(evidence.verdict, "accepted_noisy_single")
        self.assertIn("queued for validation", evidence.reason)

    def test_accepted_noisy_replicated_when_replicated_true(self) -> None:
        """When replicated=True and tier is marginal + noisy -> accepted_noisy_replicated."""
        control_6_6 = [50000] * 12
        candidate_6_6 = [49500, 49500, 49500, 49500, 49500, 49500,
                         35000, 35000, 35000, 35000, 35000, 35000]
        evidence = summarize_paired_timing(
            control_6_6,
            candidate_6_6,
            replicated=True,
            required_pairs=5,
            bootstrap_resamples=2000,
            permutation_resamples=2000,
        )

        self.assertEqual(evidence.noise_flag, "NOISY")
        self.assertEqual(evidence.confidence_tier_name, "marginal")
        self.assertEqual(evidence.verdict, "accepted_noisy_replicated")
        self.assertIn("replicated evidence", evidence.reason)

    def test_judge_verdict_handles_lowercase_verdict_strings(self) -> None:
        """judge_verdict must handle lowercase timing_verdict values (Bug 1 fix)."""
        batch_state = {
            "compare_status": "pass",
            "timing_verdict": "accepted",
            "noise_flag": "ok",
        }
        verdict, _ = judge_verdict(batch_state)
        self.assertEqual(verdict, "accepted")

        batch_state_noisy_single = {
            "compare_status": "pass",
            "timing_verdict": "accepted_noisy_single",
            "noise_flag": "ok",
        }
        verdict, _ = judge_verdict(batch_state_noisy_single)
        self.assertEqual(verdict, "accepted_noisy_single")

        batch_state_noisy_replicated = {
            "compare_status": "pass",
            "timing_verdict": "accepted_noisy_replicated",
            "noise_flag": "ok",
        }
        verdict, _ = judge_verdict(batch_state_noisy_replicated)
        self.assertEqual(verdict, "accepted_noisy_replicated")

        batch_state_class_a = {
            "compare_status": "pass",
            "timing_verdict": "accepted_class_a",
            "noise_flag": "ok",
        }
        verdict, _ = judge_verdict(batch_state_class_a)
        self.assertEqual(verdict, "accepted_class_a")

    def test_judge_verdict_handles_uppercase_verdict_strings(self) -> None:
        """judge_verdict must also handle UPPERCASE timing_verdict (legacy format)."""
        batch_state = {
            "compare_status": "pass",
            "timing_verdict": "ACCEPTED",
            "noise_flag": "ok",
        }
        verdict, _ = judge_verdict(batch_state)
        self.assertEqual(verdict, "accepted")

        batch_state_noisy = {
            "compare_status": "pass",
            "timing_verdict": "ACCEPTED_NOISY_SINGLE",
            "noise_flag": "ok",
        }
        verdict, _ = judge_verdict(batch_state_noisy)
        self.assertEqual(verdict, "accepted_noisy_single")

    def test_validate_class_a_rejects_cache_pattern(self) -> None:
        """Class A must be rejected when the hypothesis mentions cache."""
        valid, reason = validate_class_a(
            hypothesis="add new cache using thread_local for faster lookup",
            change_notes="introduces static local cache",
        )
        self.assertFalse(valid)
        self.assertIn("forbidden", reason)

    def test_validate_class_a_rejects_branch_pattern(self) -> None:
        """Class A must be rejected when the change introduces a new branch."""
        valid, reason = validate_class_a(
            hypothesis="adds branch to skip computation when stock list is empty",
            change_notes="new conditional path",
        )
        self.assertFalse(valid)
        self.assertIn("forbidden", reason)

    def test_validate_class_a_rejects_container_swap(self) -> None:
        """Class A must be rejected when the change swaps container types."""
        valid, reason = validate_class_a(
            hypothesis="replace container type from vector to unordered_map for O(1) lookup",
        )
        self.assertFalse(valid)
        self.assertIn("forbidden", reason)

    def test_validate_class_a_accepts_dead_store_removal(self) -> None:
        """Class A must be accepted when removing dead store / unused assignment."""
        valid, reason = validate_class_a(
            hypothesis="removes unused assignment of market string that is never read back",
            change_notes="dead store elimination in row loop",
        )
        self.assertTrue(valid)
        self.assertIn("valid Class A", reason)

    def test_validate_class_a_accepts_unused_parameter_removal(self) -> None:
        """Class A must be accepted when removing unused parameter."""
        valid, reason = validate_class_a(
            hypothesis="removes unused parameter from handlerData construction",
            change_notes="the configKlineIndex parameter was never used",
        )
        self.assertTrue(valid)
        self.assertIn("valid Class A", reason)

    def test_validate_class_a_accepts_copy_replacement(self) -> None:
        """Class A must be accepted when the change replaces a copy with an existing value."""
        valid, reason = validate_class_a(
            hypothesis="replace copy with existing value already computed elsewhere",
            change_notes="uses the already-computed stock_code_str instead of constructing a new copy",
        )
        self.assertTrue(valid)
        self.assertIn("valid Class A", reason)

    def test_validate_class_a_rejects_without_any_allowed_pattern(self) -> None:
        """Class A must be rejected when no allowed pattern is found."""
        valid, reason = validate_class_a(
            hypothesis="adjust loop variable scope to reduce register pressure",
            change_notes="purely about scope and register allocation, no removal or deletion",
        )
        self.assertFalse(valid)
        self.assertIn("no allowed pattern matched", reason)

    def test_change_class_defaults_to_class_b_in_summarize_paired_timing(self) -> None:
        """When change_class is not explicitly passed, Class A path should NOT trigger."""
        evidence = summarize_paired_timing(
            [40000, 40100, 40050],
            [35000, 35100, 35050],
            build_pass=True,
            compare_pass=True,
        )
        # Should NOT be accepted_class_a since default is class_b
        self.assertNotEqual(evidence.verdict, "accepted_class_a")

    def test_evidence_fields_match_attempts_schema(self) -> None:
        """Remote attempts writer must accept every key returned by evidence_fields."""
        evidence = summarize_paired_timing(
            [50000, 50100, 49900, 50200, 49800, 50300],
            [37000, 43000, 39500, 41500, 40000, 44000],
            replicated=True,
            required_pairs=5,
            bootstrap_resamples=400,
            permutation_resamples=400,
        )
        fields = evidence_fields(evidence, change_class="class_b", replicated=True)

        self.assertFalse(set(fields) - set(ATTEMPTS_FIELDNAMES))
        self.assertIn("change_class", ATTEMPTS_FIELDNAMES)
        self.assertIn("replicated", ATTEMPTS_FIELDNAMES)

    def test_apply_change_class_policy_downgrades_forbidden_class_a(self) -> None:
        """Production auto-loop must not pass unsafe class_a requests through."""
        candidate = {
            "candidate_id": "cache_repeated_raw_timestamp",
            "hypothesis": "add new cache using thread_local for repeated timestamp conversion",
            "expected_effect": "new cache avoids conversion",
            "touched_files": ["PsiFactorPipline/PsiReadWrite.cpp"],
            "change_class": "class_a",
        }

        apply_change_class_policy(candidate)

        self.assertEqual(candidate["change_class"], "class_b")
        self.assertIn("forbidden", candidate["class_a_validation_reason"])

    def test_apply_change_class_policy_downgrades_text_valid_class_a_until_patch_validation_exists(self) -> None:
        """Text-only Class A evidence is advisory; headless mode still requires timing."""
        candidate = {
            "candidate_id": "drop_unused_timestamp_format_arg",
            "hypothesis": "removes unused parameter from timestamp conversion helper",
            "expected_effect": "pure removal of unused parameter",
            "touched_files": ["PsiFactorPipline/PsiReadWrite.cpp"],
            "change_class": "class_a",
        }

        apply_change_class_policy(candidate)

        self.assertEqual(candidate["change_class"], "class_b")
        self.assertIn("valid Class A", candidate["class_a_validation_reason"])
        self.assertIn("patch-structure validation", candidate["class_a_validation_reason"])


class ConfidenceTierUnitTests(unittest.TestCase):
    """Unit tests for the confidence_tier() pure function (B redesign)."""

    def test_decisive_strong_consistent_signal(self) -> None:
        """Stack-skip m8 profile: 10σ CI, 8/8 positive → decisive."""
        # Mirrors smoke_m8 run_a: CI=[6840, 8200], control~58306, all positive.
        result = confidence_tier(
            bootstrap_ci_low_ms=6840.0,
            bootstrap_ci_high_ms=8200.0,
            median_delta_ms=7463.0,
            paired_deltas_ms=[7200.0, 7500.0, 7100.0, 7800.0, 7300.0, 7600.0, 7400.0, 7700.0],
            permutation_p_value_arg=0.070,  # n=8 power artifact; decisive trusts CI not p
            delta_min_ms=291.0,  # 0.5% of 58306ms
        )
        self.assertEqual(result.tier, "decisive")
        self.assertIsNotNone(result.margin)
        assert result.margin is not None
        self.assertGreater(result.margin, 0.0)
        self.assertIsNotNone(result.decisiveness)
        assert result.decisiveness is not None
        self.assertGreaterEqual(result.decisiveness, 1.0)
        self.assertIsNotNone(result.sign_consistency)
        assert result.sign_consistency is not None
        self.assertGreaterEqual(result.sign_consistency, 0.9)

    def test_marginal_bimodal_wide_ci(self) -> None:
        """Bimodal deltas: wide CI forces decisiveness << 1 → marginal despite p≈0."""
        result = confidence_tier(
            bootstrap_ci_low_ms=500.0,
            bootstrap_ci_high_ms=15000.0,
            median_delta_ms=7750.0,
            paired_deltas_ms=[500.0] * 6 + [15000.0] * 6,
            permutation_p_value_arg=0.0005,  # essentially 0 due to bimodal structure
            delta_min_ms=250.0,
        )
        self.assertEqual(result.tier, "marginal")
        self.assertIsNotNone(result.decisiveness)
        assert result.decisiveness is not None
        self.assertLess(result.decisiveness, 1.0)

    def test_weak_negative_margin(self) -> None:
        """FALSE candidate: CI_low negative → margin < 0 → weak."""
        result = confidence_tier(
            bootstrap_ci_low_ms=-926.0,
            bootstrap_ci_high_ms=200.0,
            median_delta_ms=-308.0,
            paired_deltas_ms=[-500.0] * 8 + [200.0] * 4,
            permutation_p_value_arg=0.188,
            delta_min_ms=250.0,
        )
        self.assertEqual(result.tier, "weak")
        self.assertIsNotNone(result.margin)
        assert result.margin is not None
        self.assertLess(result.margin, 0.0)

    def test_weak_no_ci(self) -> None:
        """No CI (None) → weak regardless of other inputs."""
        result = confidence_tier(
            bootstrap_ci_low_ms=None,
            bootstrap_ci_high_ms=None,
            median_delta_ms=1000.0,
            paired_deltas_ms=[1000.0] * 5,
            permutation_p_value_arg=0.01,
            delta_min_ms=0.0,
        )
        self.assertEqual(result.tier, "weak")

    def test_weak_high_p_not_marginal(self) -> None:
        """margin > 0 but p > 0.05 → NOT marginal → weak."""
        result = confidence_tier(
            bootstrap_ci_low_ms=300.0,
            bootstrap_ci_high_ms=2000.0,
            median_delta_ms=1000.0,
            paired_deltas_ms=[1000.0] * 8 + [-200.0] * 4,
            permutation_p_value_arg=0.195,  # r2 analog
            delta_min_ms=250.0,
        )
        self.assertEqual(result.tier, "weak")

    def test_degenerate_zero_ci_width_is_decisive(self) -> None:
        """Zero-width CI (single sample) with positive margin → decisive (∞ decisiveness)."""
        result = confidence_tier(
            bootstrap_ci_low_ms=5000.0,
            bootstrap_ci_high_ms=5000.0,
            median_delta_ms=5000.0,
            paired_deltas_ms=[5000.0],
            permutation_p_value_arg=0.5,
            delta_min_ms=250.0,
        )
        self.assertEqual(result.tier, "decisive")
        self.assertIsNone(result.decisiveness)  # sentinel for ∞


class ConfidenceTierIntegrationTests(unittest.TestCase):
    """Integration tests: summarize_paired_timing with B-redesign routing."""

    def _stack_skip_m8_analog_samples(self) -> tuple[list[float], list[float]]:
        """8-pair samples mimicking stack_skip smoke_m8: ~12.8% improvement, all positive.

        Delta spread must be >= 2% of control_median to trigger noise_flag=NOISY.
        Actual smoke_m8 had control_median≈58306ms, delta range=2589ms (~4.4%).
        We replicate that: 8 pairs with ~7300–9900ms deltas (range ≈ 2600ms,
        control_median ≈ 58400ms → 4.5%).
        """
        control = [58590, 58609, 58719, 58484, 57895, 58348, 58567, 59026]
        # Spread candidates to produce delta range of ~2600ms (all positive)
        candidate = [51100, 51300, 52400, 51200, 50500, 51800, 51600, 50800]
        # Deltas: 7490, 7309, 6319, 7284, 7395, 6548, 6967, 8226
        # range = 8226-6319 = 1907ms; control_median ≈ 58550ms → 3.3% → NOISY
        return control, candidate

    def test_decisive_large_effect_accepted_without_replication(self) -> None:
        """A decisive signal (strong m8 analog) must produce accepted, NOT accepted_noisy_single.

        This is the primary regression guard for the B redesign: before the fix,
        stack_skip m8 was forced to accepted_noisy_single due to noise_flag=NOISY
        even though the CI was 10σ above zero.
        """
        control, candidate = self._stack_skip_m8_analog_samples()
        evidence = summarize_paired_timing(
            control, candidate,
            replicated=False,
            required_pairs=5,
            bootstrap_resamples=2000,
            permutation_resamples=2000,
        )
        self.assertEqual(evidence.noise_flag, "NOISY")
        self.assertEqual(evidence.confidence_tier_name, "decisive")
        self.assertEqual(evidence.verdict, "accepted")
        self.assertNotIn("queued for validation", evidence.reason)

    def test_decisive_accepted_even_when_replicated_false(self) -> None:
        """decisive tier must produce 'accepted' regardless of replicated flag."""
        control, candidate = self._stack_skip_m8_analog_samples()
        ev_false = summarize_paired_timing(
            control, candidate, replicated=False,
            required_pairs=5, bootstrap_resamples=2000, permutation_resamples=2000,
        )
        ev_true = summarize_paired_timing(
            control, candidate, replicated=True,
            required_pairs=5, bootstrap_resamples=2000, permutation_resamples=2000,
        )
        # Both should be "accepted" — decisive does NOT tax on replication.
        self.assertEqual(ev_false.verdict, "accepted")
        self.assertEqual(ev_true.verdict, "accepted")

    def test_false_candidate_still_rejected(self) -> None:
        """FALSE candidates (negative or near-zero true effect) remain rejected/neutral."""
        # skip_sort_pass analog: median slightly positive but not significant
        control = [50000] * 12
        candidate = [49762] * 12  # +238ms — exactly the skip_sort_pass profile
        evidence = summarize_paired_timing(
            control, candidate,
            required_pairs=5,
            bootstrap_resamples=2000, permutation_resamples=2000,
        )
        # CI_low will be exactly 238 (no variance) but delta_min = 0.5% * 50000 = 250 → margin < 0
        # Or CI_low > 0 but p=1.0 (no variance at all, deterministic). Either way, not accepted.
        self.assertNotIn(evidence.verdict, ("accepted", "accepted_noisy_single", "accepted_noisy_replicated"))

    def test_confidence_fields_in_evidence(self) -> None:
        """PairedTimingEvidence must carry the five confidence tier fields."""
        control, candidate = self._stack_skip_m8_analog_samples()
        ev = summarize_paired_timing(control, candidate, required_pairs=5,
                                     bootstrap_resamples=400, permutation_resamples=400)
        self.assertIsNotNone(ev.confidence_tier_name)
        self.assertIn(ev.confidence_tier_name, ("decisive", "marginal", "weak"))
        self.assertIsNotNone(ev.confidence_margin_ms)
        self.assertIsNotNone(ev.confidence_ci_width_ms)
        self.assertIsNotNone(ev.confidence_sign_consistency)

    def test_evidence_fields_include_confidence_keys(self) -> None:
        """evidence_fields() must emit the five new confidence keys."""
        control, candidate = self._stack_skip_m8_analog_samples()
        ev = summarize_paired_timing(control, candidate, required_pairs=5,
                                     bootstrap_resamples=400, permutation_resamples=400)
        fields = evidence_fields(ev)
        for key in ("confidence_tier", "confidence_margin_ms", "confidence_ci_width_ms",
                    "confidence_decisiveness", "confidence_sign_consistency"):
            self.assertIn(key, fields, f"Missing key: {key}")


if __name__ == "__main__":
    unittest.main()
