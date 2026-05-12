from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from psi_timing_analysis import summarize_paired_timing  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
