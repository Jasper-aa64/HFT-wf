from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from psi_attempts_schema import ATTEMPTS_FIELDNAMES  # noqa: E402
from psi_headless_auto_loop import apply_change_class_policy, judge_verdict  # noqa: E402
from psi_timing_analysis import evidence_fields, summarize_paired_timing, validate_class_a  # noqa: E402


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
        """When replicated=False and stats conclusive + noisy -> accepted_noisy_single."""
        # Use 12 samples with high deltas variance to trigger NOISY, and strong
        # positive signal so statistical evidence is conclusive.
        evidence = summarize_paired_timing(
            [50000, 50100, 49900, 50200, 49800, 50300, 49700, 50400, 49600, 50500, 50050, 50250],
            [37000, 43000, 39500, 41500, 40000, 44000, 40500, 38500, 43500, 39500, 42000, 39000],
            replicated=False,
            required_pairs=5,
            bootstrap_resamples=2000,
            permutation_resamples=2000,
        )

        self.assertEqual(evidence.noise_flag, "NOISY")
        self.assertEqual(evidence.verdict, "accepted_noisy_single")
        self.assertIn("queued for validation", evidence.reason)

    def test_accepted_noisy_replicated_when_replicated_true(self) -> None:
        """When replicated=True and stats conclusive + noisy -> accepted_noisy_replicated."""
        evidence = summarize_paired_timing(
            [50000, 50100, 49900, 50200, 49800, 50300, 49700, 50400, 49600, 50500, 50050, 50250],
            [37000, 43000, 39500, 41500, 40000, 44000, 40500, 38500, 43500, 39500, 42000, 39000],
            replicated=True,
            required_pairs=5,
            bootstrap_resamples=2000,
            permutation_resamples=2000,
        )

        self.assertEqual(evidence.noise_flag, "NOISY")
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

    def test_apply_change_class_policy_preserves_valid_class_a(self) -> None:
        """Production auto-loop keeps class_a only for whitelisted pure removals."""
        candidate = {
            "candidate_id": "drop_unused_timestamp_format_arg",
            "hypothesis": "removes unused parameter from timestamp conversion helper",
            "expected_effect": "pure removal of unused parameter",
            "touched_files": ["PsiFactorPipline/PsiReadWrite.cpp"],
            "change_class": "class_a",
        }

        apply_change_class_policy(candidate)

        self.assertEqual(candidate["change_class"], "class_a")
        self.assertIn("valid Class A", candidate["class_a_validation_reason"])


if __name__ == "__main__":
    unittest.main()
