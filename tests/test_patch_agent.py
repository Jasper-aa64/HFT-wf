from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import patch_agent  # noqa: E402


class PatchAgentCliTests(unittest.TestCase):
    def test_direct_edit_prompt_forbids_local_build_artifacts(self) -> None:
        prompt = patch_agent.build_direct_edit_prompt(
            {
                "target": "PsiBaseDataInfo.getKlineIndex",
                "hypothesis": "optimize lookup",
                "expected_effect": "faster lookup",
                "semantic_risk": "medium",
                "lane": "evidence",
                "source_evidence": {},
            },
            {"PsiData/PsiBaseDataInfo.cpp": "int x;\n"},
        )

        self.assertIn("Do not run builds", prompt)
        self.assertIn("create build directories", prompt)
        self.assertIn("generated artifacts", prompt)

    def test_claude_cli_uses_windows_cmd_fallback(self) -> None:
        with tempfile.TemporaryDirectory(prefix="patch_agent_") as raw_dir:
            workspace = Path(raw_dir)
            completed = subprocess.CompletedProcess(
                ["claude.cmd"],
                0,
                stdout="```patch_file:PsiFactorPipline/PsiReadWrite.cpp\ncontent\n```",
                stderr="",
            )
            with (
                mock.patch.object(
                    patch_agent.shutil,
                    "which",
                    side_effect=lambda name: "C:/Users/test/AppData/Roaming/npm/claude.cmd"
                    if name == "claude.cmd"
                    else None,
                ),
                mock.patch.object(patch_agent.subprocess, "run", return_value=completed) as run_mock,
            ):
                response = patch_agent.call_claude_cli("prompt", workspace)

            self.assertEqual(response, completed.stdout)
            command = run_mock.call_args.args[0]
            self.assertEqual(command[0], "C:/Users/test/AppData/Roaming/npm/claude.cmd")
            self.assertNotIn("--no-input", command)
            self.assertEqual(run_mock.call_args.kwargs["input"], "prompt")
            self.assertFalse((workspace / ".patch_prompt.txt").exists())

    def test_claude_cli_reports_missing_binary(self) -> None:
        with tempfile.TemporaryDirectory(prefix="patch_agent_") as raw_dir:
            workspace = Path(raw_dir)
            with mock.patch.object(patch_agent.shutil, "which", return_value=None):
                response = patch_agent.call_claude_cli("prompt", workspace)

            self.assertIsNone(response)
            self.assertFalse((workspace / ".patch_prompt.txt").exists())

    def test_claude_cli_empty_response_is_reported(self) -> None:
        with tempfile.TemporaryDirectory(prefix="patch_agent_") as raw_dir:
            workspace = Path(raw_dir)
            completed = subprocess.CompletedProcess(["claude.cmd"], 1, stdout="", stderr="API Error")
            with (
                mock.patch.object(patch_agent.shutil, "which", return_value="claude.cmd"),
                mock.patch.object(patch_agent.subprocess, "run", return_value=completed),
            ):
                response = patch_agent.call_claude_cli("prompt", workspace)

            self.assertIsNone(response)
            self.assertFalse((workspace / ".patch_prompt.txt").exists())

    def test_codex_cli_direct_edit_requires_workspace_changes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="patch_agent_") as raw_dir:
            root = Path(raw_dir)
            workspace = root / "workspace"
            run_dir = root / "run"
            workspace.mkdir()
            completed = subprocess.CompletedProcess(["codex.cmd"], 0, stdout="done", stderr="")
            with (
                mock.patch.object(patch_agent.shutil, "which", return_value="codex.cmd"),
                mock.patch.object(patch_agent.subprocess, "run", return_value=completed) as run_mock,
                mock.patch.object(patch_agent, "workspace_has_changes", return_value=True),
            ):
                ok = patch_agent.call_codex_cli("prompt", workspace, run_dir, "candidate")

            self.assertTrue(ok)
            command = run_mock.call_args.args[0]
            self.assertEqual(command[0], "codex.cmd")
            self.assertIn("--output-last-message", command)
            self.assertEqual(run_mock.call_args.kwargs["input"], "prompt")
            self.assertTrue((run_dir / "logs" / "codex_patch_prompt_candidate.md").exists())

    def test_codex_cli_direct_edit_fails_when_no_diff(self) -> None:
        with tempfile.TemporaryDirectory(prefix="patch_agent_") as raw_dir:
            root = Path(raw_dir)
            workspace = root / "workspace"
            run_dir = root / "run"
            workspace.mkdir()
            completed = subprocess.CompletedProcess(["codex.cmd"], 0, stdout="done", stderr="")
            with (
                mock.patch.object(patch_agent.shutil, "which", return_value="codex.cmd"),
                mock.patch.object(patch_agent.subprocess, "run", return_value=completed),
                mock.patch.object(patch_agent, "workspace_has_changes", return_value=False),
            ):
                ok = patch_agent.call_codex_cli("prompt", workspace, run_dir, "candidate")

            self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
