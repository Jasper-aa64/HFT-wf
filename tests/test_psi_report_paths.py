from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sys
import subprocess


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from psi_auto_optimize import parse_report_paths  # noqa: E402
from psi_headless_longrun import sync_latest_artifacts  # noqa: E402
from psi_report_paths import reserve_report_paths, safe_report_text  # noqa: E402


class ReportPathTests(unittest.TestCase):
    def test_reserve_report_paths_uses_suffix_when_markdown_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report_root = Path(tmp)
            first_md, first_pdf = reserve_report_paths(report_root, "2026-05-11", "2026-05-11 Psi Report")
            first_md.write_text("first\n", encoding="utf-8")

            second_md, second_pdf = reserve_report_paths(report_root, "2026-05-11", "2026-05-11 Psi Report")

            self.assertEqual(first_md.name, "2026-05-11 Psi Report.md")
            self.assertEqual(first_pdf.name, "2026-05-11 Psi Report.pdf")
            self.assertEqual(second_md.name, "2026-05-11 Psi Report-2.md")
            self.assertEqual(second_pdf.name, "2026-05-11 Psi Report-2.pdf")

    def test_reserve_report_paths_reserves_markdown_before_content_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report_root = Path(tmp)
            first_md, _ = reserve_report_paths(report_root, "2026-05-11", "2026-05-11 Psi Report")
            second_md, _ = reserve_report_paths(report_root, "2026-05-11", "2026-05-11 Psi Report")

            self.assertTrue(first_md.exists())
            self.assertEqual(first_md.read_text(encoding="utf-8"), "")
            self.assertEqual(second_md.name, "2026-05-11 Psi Report-2.md")

    def test_reserve_report_paths_uses_suffix_when_only_pdf_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report_root = Path(tmp)
            report_dir = report_root / "2026-05-11"
            report_dir.mkdir()
            (report_dir / "2026-05-11 Psi Report.pdf").write_bytes(b"%PDF")

            md_path, pdf_path = reserve_report_paths(report_root, "2026-05-11", "2026-05-11 Psi Report")

            self.assertEqual(md_path.name, "2026-05-11 Psi Report-2.md")
            self.assertEqual(pdf_path.name, "2026-05-11 Psi Report-2.pdf")

    def test_safe_report_text_replaces_windows_invalid_path_characters(self) -> None:
        self.assertEqual(safe_report_text('a<b>c:d/e\\f|g?h*'), "a_b_c_d_e_f_g_h_")

    def test_parse_report_paths_keeps_reported_suffix_and_falls_back_to_pdf_suffix(self) -> None:
        md_path, pdf_path = parse_report_paths(
            "noise\nmarkdown=C:/runs/reports/2026-05-11/2026-05-11 Psi Report-2.md\n"
        )

        self.assertEqual(md_path.name, "2026-05-11 Psi Report-2.md")
        self.assertEqual(pdf_path.name, "2026-05-11 Psi Report-2.pdf")

    def test_longrun_sync_preserves_run_root_report_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "longrun"
            batch_dir = root / "batch"
            existing = run_dir / "reports" / "2026-05-11" / "2026-05-11 Psi Headless Long-Run Report.md"
            existing.parent.mkdir(parents=True)
            existing.write_text("long-run report\n", encoding="utf-8")
            batch_report = batch_dir / "reports" / "2026-05-11" / "2026-05-11 performance.md"
            batch_report.parent.mkdir(parents=True)
            batch_report.write_text("batch report\n", encoding="utf-8")

            sync_latest_artifacts(run_dir, batch_dir)

            self.assertTrue(existing.exists())
            self.assertEqual(existing.read_text(encoding="utf-8"), "long-run report\n")
            mirrored = run_dir / "reports" / "latest_batch" / "2026-05-11" / "2026-05-11 performance.md"
            self.assertTrue(mirrored.exists())
            self.assertEqual(mirrored.read_text(encoding="utf-8"), "batch report\n")

    def test_longrun_dry_run_writes_required_timing_samples_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "longrun"

            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS_DIR / "psi_headless_longrun.py"),
                    "--run-dir",
                    str(run_dir),
                    "--dry-run",
                    "--max-batches",
                    "1",
                ],
                cwd=REPO_ROOT,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )

            self.assertEqual(completed.returncode, 0, completed.stdout)
            batch_dirs = list((run_dir / "batches").iterdir())
            self.assertEqual(len(batch_dirs), 1)
            self.assertTrue((batch_dirs[0] / "timing_samples.tsv").exists())
            self.assertTrue((run_dir / "timing_samples.tsv").exists())
            self.assertIn("last_exit_reason=budget_stop", completed.stdout)


if __name__ == "__main__":
    unittest.main()
