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
    PsiTimingAdapter,
    TwapAdapter,
    confidence_tier,
    evidence_fields,
    independence_verified,
    judge_scorecard,
    naive_k1_counterfactual,
    replication_verified_from_audits,
    sample_escalation_decision,
    summarize_paired_timing,
    threshold_consistency,
    twap_case_interval_ms,
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

    def test_judge_verdict_does_not_downgrade_accepted_noisy_measurement(self) -> None:
        """Remote CI-native accepted evidence must not be re-downgraded by the legacy noise flag."""
        batch_state = {
            "compare_status": "pass",
            "control_source_kind": "synced_same_source",
            "remote_candidate_workspace": "/remote/candidate",
            "timing_verdict": "accepted",
            "noise_flag": "NOISY",
            "paired_sample_count": 8,
            "median_delta_ms": "7201.500",
            "bootstrap_ci_low_ms": "6525.000",
            "bootstrap_ci_high_ms": "8049.000",
        }
        verdict, reason = judge_verdict(batch_state)
        self.assertEqual(verdict, "accepted")
        self.assertEqual(reason, "")

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


class SampleEscalationDecisionTests(unittest.TestCase):
    def test_m4_gray_timestamp_shape_escalates_instead_of_parking(self) -> None:
        decision = sample_escalation_decision(
            verdict="NOISY_PENDING",
            correctness_pass=True,
            paired_sample_count=4,
            median_delta_ms=805.5,
            bootstrap_ci_low_ms=324.0,
            bootstrap_ci_high_ms=1392.0,
            delta_min_ms=290.0,
            decisive_k=1.0,
        )

        self.assertEqual(decision.action, "ESCALATE")
        self.assertEqual(decision.next_sample_count, 8)

    def test_obvious_noise_does_not_escalate(self) -> None:
        decision = sample_escalation_decision(
            verdict="NOISY_PENDING",
            correctness_pass=True,
            paired_sample_count=4,
            median_delta_ms=20.0,
            bootstrap_ci_low_ms=-300.0,
            bootstrap_ci_high_ms=400.0,
            delta_min_ms=290.0,
            decisive_k=1.0,
        )

        self.assertEqual(decision.action, "PARK")
        self.assertIsNone(decision.next_sample_count)

    def test_decisive_candidate_does_not_enter_escalation_path(self) -> None:
        decision = sample_escalation_decision(
            verdict="accepted",
            correctness_pass=True,
            paired_sample_count=8,
            median_delta_ms=7400.0,
            bootstrap_ci_low_ms=6800.0,
            bootstrap_ci_high_ms=7800.0,
            delta_min_ms=290.0,
            decisive_k=1.0,
        )

        self.assertEqual(decision.action, "NO_ACTION")
        self.assertIsNone(decision.next_sample_count)


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

    def test_evidence_fields_include_decision_constants_and_escalation_ladder(self) -> None:
        ev = summarize_paired_timing(
            [50000, 50020, 49980, 50010, 50030],
            [49000, 49010, 48990, 49020, 49040],
            required_pairs=5,
            bootstrap_resamples=200,
            permutation_resamples=200,
            delta_min_ms=123.0,
            decisive_k=1.25,
            sign_min=0.8,
        )

        fields = evidence_fields(ev)

        self.assertEqual(fields["delta_min_ms_used"], "123.000")
        self.assertEqual(fields["decisive_k"], "1.250")
        self.assertEqual(fields["sign_min"], "0.800")
        self.assertEqual(fields["escalation_steps"], "4,8,12")

    def test_evidence_fields_include_naive_k1_counterfactual(self) -> None:
        accepted_first = naive_k1_counterfactual([12.0, -50.0])
        rejected_first = naive_k1_counterfactual([-1.0, 500.0])
        self.assertEqual(accepted_first, (12.0, True))
        self.assertEqual(rejected_first, (-1.0, False))

        ev = summarize_paired_timing(
            [100.0, 100.0],
            [101.0, 50.0],
            required_pairs=2,
            bootstrap_resamples=50,
            permutation_resamples=50,
        )
        fields = evidence_fields(ev)

        self.assertEqual(fields["naive_k1_first_delta_ms"], "-1.000")
        self.assertEqual(fields["naive_k1_would_accept"], "false")

    def test_evidence_fields_include_env_fingerprint(self) -> None:
        ev = summarize_paired_timing(
            [1000.0, 1010.0, 990.0],
            [900.0, 905.0, 895.0],
            required_pairs=3,
            bootstrap_resamples=50,
            permutation_resamples=50,
        )

        fields = evidence_fields(
            ev,
            host_id="devbox-a",
            env_class="cloudish",
        )

        self.assertEqual(fields["host_id"], "devbox-a")
        self.assertEqual(fields["env_class"], "cloudish")
        self.assertEqual(fields["control_stdev_ms"], "5.000")
        self.assertEqual(fields["control_range_ms"], "10.000")

    def test_independence_verified_requires_time_gap_and_different_weather_bucket(self) -> None:
        base = {
            "recorded_at": "2026-06-10T00:00:00Z",
            "paired_stdev_ms": "50",
            "paired_range_ms": "120",
        }
        same_bucket_later = {
            "recorded_at": "2026-06-10T00:45:00Z",
            "paired_stdev_ms": "55",
            "paired_range_ms": "130",
        }
        different_bucket_later = {
            "recorded_at": "2026-06-10T00:45:00Z",
            "paired_stdev_ms": "900",
            "paired_range_ms": "2200",
        }
        too_soon_different_bucket = {
            "recorded_at": "2026-06-10T00:05:00Z",
            "paired_stdev_ms": "900",
            "paired_range_ms": "2200",
        }

        self.assertFalse(independence_verified(base, same_bucket_later))
        self.assertFalse(independence_verified(base, too_soon_different_bucket))
        self.assertTrue(independence_verified(base, different_bucket_later))
        self.assertFalse(independence_verified(base, {}))

    def test_replication_verified_from_audits_requires_assertion_and_complete_independent_audits(self) -> None:
        self.assertFalse(
            replication_verified_from_audits(
                False,
                prior_recorded_at="2026-06-10T00:00:00Z",
                prior_stdev_ms="50",
                prior_range_ms="120",
                current_recorded_at="2026-06-10T00:45:00Z",
                current_stdev_ms="900",
                current_range_ms="2200",
            )
        )
        self.assertFalse(
            replication_verified_from_audits(
                True,
                prior_recorded_at="2026-06-10T00:00:00Z",
                prior_stdev_ms="50",
                prior_range_ms="120",
                current_recorded_at="",
                current_stdev_ms="900",
                current_range_ms="2200",
            )
        )
        self.assertFalse(
            replication_verified_from_audits(
                True,
                prior_recorded_at="2026-06-10T00:00:00Z",
                prior_stdev_ms="50",
                prior_range_ms="120",
                current_recorded_at="2026-06-10T00:45:00Z",
                current_stdev_ms="55",
                current_range_ms="130",
            )
        )
        self.assertTrue(
            replication_verified_from_audits(
                True,
                prior_recorded_at="2026-06-10T00:00:00Z",
                prior_stdev_ms="50",
                prior_range_ms="120",
                current_recorded_at="2026-06-10T00:45:00Z",
                current_stdev_ms="900",
                current_range_ms="2200",
            )
        )


class PsiScorecardCharacterizationTests(unittest.TestCase):
    """Golden tests for the current Psi verdict surface before Scorecard refactor."""

    def assertVerdictAndTier(
        self,
        control: list[float],
        candidate: list[float],
        *,
        verdict: str,
        tier: str,
        reason_contains: str = "",
        **kwargs: object,
    ) -> None:
        evidence = summarize_paired_timing(
            control,
            candidate,
            required_pairs=5,
            bootstrap_resamples=600,
            permutation_resamples=600,
            **kwargs,
        )
        self.assertEqual(evidence.verdict, verdict)
        self.assertEqual(evidence.confidence_tier_name, tier)
        if reason_contains:
            self.assertIn(reason_contains, evidence.reason)

    def test_current_decisive_positive_signal_is_accepted(self) -> None:
        self.assertVerdictAndTier(
            [58590, 58609, 58719, 58484, 57895, 58348, 58567, 59026],
            [51100, 51300, 52400, 51200, 50500, 51800, 51600, 50800],
            verdict="accepted",
            tier="decisive",
            reason_contains="CI-native decisive",
        )

    def test_current_clear_slowdown_is_rejected(self) -> None:
        self.assertVerdictAndTier(
            [50000, 50100, 49900, 50050, 50020],
            [51000, 51150, 50950, 51200, 51080],
            verdict="rejected",
            tier="weak",
            reason_contains="non-improvement",
        )

    def test_current_marginal_signal_is_accepted_noisy_single(self) -> None:
        self.assertVerdictAndTier(
            [50000] * 12,
            [49500, 49500, 49500, 49500, 49500, 49500,
             35000, 35000, 35000, 35000, 35000, 35000],
            verdict="accepted_noisy_single",
            tier="marginal",
            reason_contains="NOT applied",
        )

    def test_current_screening_sample_count_is_neutral(self) -> None:
        self.assertVerdictAndTier(
            [50000, 50100, 49900],
            [45000, 45100, 44900],
            verdict="neutral",
            tier="decisive",
            reason_contains="screening only",
        )

    def test_current_build_failure_is_rejected(self) -> None:
        self.assertVerdictAndTier(
            [50000, 50100, 49900, 50050, 50020],
            [45000, 45100, 44900, 45050, 45020],
            verdict="rejected",
            tier="decisive",
            reason_contains="build failed",
            build_pass=False,
        )

    def test_current_compare_failure_is_rejected(self) -> None:
        self.assertVerdictAndTier(
            [50000, 50100, 49900, 50050, 50020],
            [45000, 45100, 44900, 45050, 45020],
            verdict="rejected",
            tier="decisive",
            reason_contains="compare failed",
            compare_pass=False,
        )


class PsiScorecardStructureTests(unittest.TestCase):
    def test_psi_adapter_fills_domain_blind_scorecard(self) -> None:
        tier = ConfidenceTierResult(
            tier="decisive",
            margin=1000.0,
            ci_width=500.0,
            decisiveness=2.0,
            sign_consistency=1.0,
        )

        scorecard = PsiTimingAdapter.scorecard(
            deltas_ms=[1500.0, 1600.0, 1700.0, 1800.0, 1900.0],
            required_pairs=5,
            control_median_ms=50000.0,
            median_delta_ms=1700.0,
            bootstrap_ci_low_ms=1500.0,
            bootstrap_ci_high_ms=2000.0,
            permutation_p_value_arg=0.01,
            paired_stdev_ms=158.0,
            paired_range_ms=400.0,
            noise_flag="ok",
            confidence_tier_result=tier,
            build_pass=True,
            compare_pass=True,
            change_class="class_b",
        )

        self.assertEqual(scorecard.scenario_id, "psi_paired_timing")
        self.assertTrue(scorecard.correctness_pass)
        self.assertEqual(scorecard.primary.name, "paired_median_delta_ms")
        self.assertEqual(scorecard.primary.samples_ms, [1500.0, 1600.0, 1700.0, 1800.0, 1900.0])
        self.assertEqual(scorecard.regressions, ())

        verdict = judge_scorecard(scorecard)
        self.assertEqual(verdict.verdict, "accepted")


class TwapThresholdConsistencyTests(unittest.TestCase):
    def test_case_interval_parser_matches_shell_shape(self) -> None:
        self.assertEqual(twap_case_interval_ms("100_i50_s4"), 50)
        self.assertEqual(twap_case_interval_ms("500_i5"), 5)
        self.assertEqual(twap_case_interval_ms("100_i20_s16_extra"), 20)
        self.assertIsNone(twap_case_interval_ms("bad_case"))

    def test_all_normal_cases_improve_and_stress_ok_promotes(self) -> None:
        result = threshold_consistency(
            [
                {"case": "100_i50_s4", "p95_delta_ms": -1.2},
                {"case": "500_i20_s4", "p95_delta_ms": -2.0},
                {"case": "500_i5_s4", "p95_delta_ms": 4.5},
            ],
            build_status="pass",
            correctness_status="pass",
            timing_status="pass",
            has_control=True,
        )

        self.assertEqual(result.decision, "promotion_candidate")
        self.assertTrue(result.accepted)
        self.assertTrue(result.normal_frequency_p95_improved)
        self.assertTrue(result.stress_p95_regression_ok)

    def test_normal_case_without_minimum_improvement_is_screening_only(self) -> None:
        result = threshold_consistency(
            [
                {"case": "100_i50_s4", "p95_delta_ms": -0.4},
                {"case": "500_i20_s4", "p95_delta_ms": -2.0},
                {"case": "500_i5_s4", "p95_delta_ms": 4.5},
            ],
            build_status="pass",
            correctness_status="pass",
            timing_status="pass",
            has_control=True,
        )

        self.assertEqual(result.decision, "screening_only")
        self.assertFalse(result.accepted)
        self.assertFalse(result.normal_frequency_p95_improved)
        self.assertTrue(result.normal_frequency_p95_regression_ok)

    def test_stress_regression_rejects(self) -> None:
        result = threshold_consistency(
            [
                {"case": "100_i50_s4", "p95_delta_ms": -1.5},
                {"case": "500_i5_s4", "p95_delta_ms": 5.5},
            ],
            build_status="pass",
            correctness_status="pass",
            timing_status="pass",
            has_control=True,
        )

        self.assertEqual(result.decision, "rejected")
        self.assertFalse(result.stress_p95_regression_ok)

    def test_lost_unknown_or_non_pass_status_rejects(self) -> None:
        lost = threshold_consistency(
            [{"case": "100_i50_s4", "p95_delta_ms": -2.0, "candidate_lost": "1"}],
            build_status="pass",
            correctness_status="pass",
            timing_status="pass",
            has_control=True,
        )
        unknown = threshold_consistency(
            [{"case": "100_i50_s4", "p95_delta_ms": -2.0, "candidate_unknown_pushes": "1"}],
            build_status="pass",
            correctness_status="pass",
            timing_status="pass",
            has_control=True,
        )
        status = threshold_consistency(
            [{"case": "100_i50_s4", "p95_delta_ms": -2.0, "status": "WARN_UNKNOWN_PUSH"}],
            build_status="pass",
            correctness_status="pass",
            timing_status="pass",
            has_control=True,
        )

        self.assertEqual(lost.decision, "rejected")
        self.assertEqual(unknown.decision, "rejected")
        self.assertEqual(status.decision, "rejected")
        self.assertEqual(lost.lost_failure_count, 1)
        self.assertEqual(unknown.lost_failure_count, 1)
        self.assertEqual(status.lost_failure_count, 1)

    def test_build_or_correctness_fail_without_threshold_failure_screens_like_shell_auto(self) -> None:
        build_failed = threshold_consistency(
            [{"case": "100_i50_s4", "p95_delta_ms": -2.0}],
            build_status="failed",
            correctness_status="pass",
            timing_status="pass",
            has_control=True,
        )
        correctness_failed = threshold_consistency(
            [{"case": "100_i50_s4", "p95_delta_ms": -2.0}],
            build_status="pass",
            correctness_status="failed",
            timing_status="pass",
            has_control=True,
        )

        self.assertEqual(build_failed.decision, "screening_only")
        self.assertFalse(build_failed.accepted)
        self.assertEqual(correctness_failed.decision, "screening_only")
        self.assertFalse(correctness_failed.accepted)

    def test_twap_adapter_reads_comparison_summary_shape(self) -> None:
        result = TwapAdapter.threshold_result(
            {
                "build_status": "pass",
                "correctness_status": "pass",
                "timing_status": "pass",
                "has_control": True,
                "lost_failure_count": 0,
                "case_deltas": [
                    {"case": "100_i50_s4", "p95_delta_ms": -1.1},
                    {"case": "500_i5_s4", "p95_delta_ms": 0.0},
                ],
            }
        )

        self.assertEqual(result.decision, "promotion_candidate")
        self.assertEqual([case.case for case in result.normal_cases], ["100_i50_s4"])
        self.assertEqual([case.case for case in result.stress_cases], ["500_i5_s4"])

    def test_twap_adapter_trusts_shell_lost_failure_count_without_double_counting(self) -> None:
        result = TwapAdapter.threshold_result(
            {
                "build_status": "pass",
                "correctness_status": "pass",
                "timing_status": "pass",
                "has_control": True,
                "lost_failure_count": 1,
                "case_deltas": [
                    {"case": "100_i50_s4", "p95_delta_ms": -2.0, "candidate_lost": "1"},
                ],
            }
        )

        self.assertEqual(result.decision, "rejected")
        self.assertEqual(result.lost_failure_count, 1)


if __name__ == "__main__":
    unittest.main()
