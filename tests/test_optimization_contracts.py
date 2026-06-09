import json
import re
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import psi_timing_analysis as timing  # noqa: E402


def load_contract(name: str) -> dict:
    path = ROOT / "contracts" / "optimization" / name
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


class OptimizationContractTests(unittest.TestCase):
    def test_psi_timing_contract_matches_confidence_tier_defaults(self) -> None:
        contract = load_contract("psi_timing_contract.json")

        self.assertEqual(contract["judge_kind"], "confidence_tier")
        self.assertEqual(contract["primary_metric"]["delta_min_control_median_ratio"], 0.005)
        self.assertEqual(contract["confidence"]["decisive_k"], timing.DEFAULT_DECISIVE_K)
        self.assertEqual(contract["confidence"]["sign_min"], timing.DEFAULT_SIGN_MIN)
        self.assertEqual(contract["sample_floors"]["promotion"], timing.DEFAULT_PROMOTION_SAMPLE_FLOOR)
        self.assertEqual(contract["sample_floors"]["bundle_audit"], timing.DEFAULT_BUNDLE_AUDIT_SAMPLE_FLOOR)

    def test_twap_push_contract_matches_python_and_shell_thresholds(self) -> None:
        contract = load_contract("twap_push_contract.json")
        thresholds = contract["thresholds"]

        self.assertEqual(contract["judge_kind"], "threshold_consistency")
        self.assertEqual(
            thresholds["min_normal_p95_improvement_ms"],
            timing.DEFAULT_TWAP_MIN_NORMAL_P95_IMPROVEMENT_MS,
        )
        self.assertEqual(
            thresholds["max_normal_p95_regression_ms"],
            timing.DEFAULT_TWAP_MAX_NORMAL_P95_REGRESSION_MS,
        )
        self.assertEqual(
            thresholds["max_stress_p95_regression_ms"],
            timing.DEFAULT_TWAP_MAX_STRESS_P95_REGRESSION_MS,
        )

        shell_text = (ROOT / "scripts" / "twap_headless_remote.sh").read_text(encoding="utf-8")
        self.assertEqual(
            self._shell_default(shell_text, "MIN_NORMAL_P95_IMPROVEMENT_MS"),
            thresholds["min_normal_p95_improvement_ms"],
        )
        self.assertEqual(
            self._shell_default(shell_text, "MAX_STRESS_P95_REGRESSION_MS"),
            thresholds["max_stress_p95_regression_ms"],
        )
        self.assertIn("max_normal_p95_regression_ms = 1.0", shell_text)

    def test_twap_push_contract_preserves_default_case_shape(self) -> None:
        contract = load_contract("twap_push_contract.json")

        self.assertEqual(
            contract["cases"]["default_measure_cases"],
            ["100:50:120", "500:20:180", "1000:20:240", "500:5:240"],
        )
        self.assertEqual(contract["cases"]["normal_interval_ms_min"], 20)
        self.assertEqual(contract["cases"]["stress_interval_ms_max"], 5)

    @staticmethod
    def _shell_default(shell_text: str, name: str) -> float:
        match = re.search(rf'^{name}="\$\{{{name}:-([^}}]+)\}}"', shell_text, re.MULTILINE)
        if not match:
            raise AssertionError(f"missing shell default for {name}")
        return float(match.group(1))


if __name__ == "__main__":
    unittest.main()
