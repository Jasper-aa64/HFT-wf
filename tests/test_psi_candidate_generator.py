from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from psi_candidate_generator import flatten_for_tsv, generate_candidates  # noqa: E402


class PsiCandidateGeneratorProvenanceTests(unittest.TestCase):
    def test_candidates_and_patch_queue_rows_carry_generator_provenance(self) -> None:
        with tempfile.TemporaryDirectory(prefix="psi_generator_provenance_") as raw_dir:
            run_dir = Path(raw_dir)
            (run_dir / "profile.tsv").write_text(
                "stage\ttotal_ms\tcount\tavg_ms\tsource\ttouched_files\tsymbols\tnotes\n"
                "handlerData.row_loop\t1000\t10\t100\tprofile\tPsiFactorPipline/PsiReadWrite.cpp\t\tlow\n",
                encoding="utf-8",
            )
            (run_dir / "hotspots.tsv").write_text(
                "rank\tstage\ttotal_ms\tavg_ms\tcount\tscore\tnotes\ttouched_files\tsymbols\texpected_delta_seconds\n"
                "1\thandlerData.row_loop\t1000\t100\t10\t9\tlow\tPsiFactorPipline/PsiReadWrite.cpp\t\t0.1\n",
                encoding="utf-8",
            )
            for name in ("attempts.tsv", "cooldown.tsv", "neutral_pool.tsv", "retry_conditions.tsv"):
                (run_dir / name).write_text("", encoding="utf-8")

            with mock.patch.dict(
                "os.environ",
                {
                    "PSI_GENERATOR_MODEL": "codex-test-model",
                    "PSI_GENERATOR_SESSION": "session-123",
                },
                clear=False,
            ):
                lanes = generate_candidates(run_dir)
                rows = flatten_for_tsv(lanes)

        candidate = lanes["evidence"][0]
        self.assertEqual(candidate["generator_model"], "codex-test-model")
        self.assertEqual(candidate["generator_session"], "session-123")
        self.assertEqual(rows[0]["generator_model"], "codex-test-model")
        self.assertEqual(rows[0]["generator_session"], "session-123")


if __name__ == "__main__":
    unittest.main()
