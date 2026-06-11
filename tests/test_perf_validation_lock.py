from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import perf_validation_lock as lock_mod  # noqa: E402


class PerfValidationLockLocalTests(unittest.TestCase):
    """Tests for local filesystem lock operations."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="test_perf_lock_")
        self.tmp_dir = Path(self._tmp.name)
        self.lock_path = str(self.tmp_dir / ".perf_validation.lock")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # -- acquire --

    def test_acquire_when_empty(self) -> None:
        result = lock_mod.acquire_lock(
            self.lock_path,
            run_id="test_run_1",
            script_name="test_script.sh",
            candidate_id="candidate_a",
        )
        self.assertTrue(result["acquired"], f"expected acquired=True, got: {result}")
        self.assertEqual(result["lock_data"]["run_id"], "test_run_1")
        self.assertTrue(Path(self.lock_path).exists())

    def test_block_when_held(self) -> None:
        # Acquire first.
        first = lock_mod.acquire_lock(
            self.lock_path,
            run_id="test_run_1",
            script_name="test_script.sh",
        )
        self.assertTrue(first["acquired"])

        # Second acquire must be blocked.
        second = lock_mod.acquire_lock(
            self.lock_path,
            run_id="test_run_2",
            script_name="other_script.sh",
        )
        self.assertFalse(second["acquired"], f"expected acquired=False, got: {second}")
        self.assertIn("held by another run", second["reason"])
        self.assertIsNotNone(second["lock_data"])
        self.assertEqual(second["lock_data"]["run_id"], "test_run_1")

    # -- release --

    def test_release_own_lock(self) -> None:
        lock_mod.acquire_lock(
            self.lock_path,
            run_id="test_run_1",
            script_name="test_script.sh",
        )
        self.assertTrue(Path(self.lock_path).exists())

        result = lock_mod.release_lock(self.lock_path, run_id="test_run_1")
        self.assertTrue(result["released"], f"expected released=True, got: {result}")
        self.assertFalse(Path(self.lock_path).exists())

    def test_do_not_release_different_run_lock(self) -> None:
        lock_mod.acquire_lock(
            self.lock_path,
            run_id="test_run_1",
            script_name="test_script.sh",
        )

        # Try to release with wrong run_id.
        result = lock_mod.release_lock(self.lock_path, run_id="test_run_2")
        self.assertFalse(result["released"], f"expected released=False, got: {result}")
        self.assertIn("run_id mismatch", result["reason"])
        self.assertIsNotNone(result["lock_data"])
        self.assertEqual(result["lock_data"]["run_id"], "test_run_1")
        # Lock file must still exist.
        self.assertTrue(Path(self.lock_path).exists())

    def test_release_when_no_lock(self) -> None:
        result = lock_mod.release_lock(self.lock_path, run_id="nonexistent")
        self.assertFalse(result["released"])
        self.assertIn("no lock file found", result["reason"])

    # -- check --

    def test_check_when_empty(self) -> None:
        result = lock_mod.check_lock(self.lock_path)
        self.assertFalse(result["held"])
        self.assertIn("no lock held", result["reason"])

    def test_check_when_held(self) -> None:
        lock_mod.acquire_lock(
            self.lock_path,
            run_id="test_run_1",
            script_name="test_script.sh",
        )
        result = lock_mod.check_lock(self.lock_path)
        self.assertTrue(result["held"])
        self.assertEqual(result["lock_data"]["run_id"], "test_run_1")

    # -- lock metadata --

    def test_lock_metadata_fields(self) -> None:
        result = lock_mod.acquire_lock(
            self.lock_path,
            run_id="test_meta",
            script_name="headless_remote.sh",
            candidate_id="stack_skip_unused_row_fields",
            owner_command="bash scripts/headless_remote.sh",
        )
        self.assertTrue(result["acquired"])
        data = result["lock_data"]
        self.assertEqual(data["run_id"], "test_meta")
        self.assertEqual(data["script_name"], "headless_remote.sh")
        self.assertEqual(data["candidate_id"], "stack_skip_unused_row_fields")
        self.assertIn("timestamp", data)
        self.assertIn("pid", data)
        self.assertIn("hostname", data)
        self.assertEqual(data["owner_command"], "bash scripts/headless_remote.sh")

    # -- stale lock recovery --

    def test_stale_same_host_dead_pid_is_recovered(self) -> None:
        """A stale same-host lock with a dead pid should be removed and reacquired."""
        # Write a lock file by hand that looks stale.
        stale_data = {
            "run_id": "old_stale_run",
            "script_name": "test.sh",
            "candidate_id": "",
            "timestamp": "2025-01-01T00:00:00Z",
            "owner_command": "bash test.sh",
            "pid": 99999,
            "hostname": lock_mod.hostname(),
        }
        Path(self.lock_path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.lock_path).write_text(json.dumps(stale_data), encoding="utf-8")

        def fake_remove_stale(lock_path: str, _lock_data: dict, _reason: str) -> bool:
            Path(lock_path).unlink(missing_ok=True)
            return True

        with mock.patch.object(lock_mod, "_remove_stale_lock", side_effect=fake_remove_stale):
            result = lock_mod.acquire_lock(
                self.lock_path,
                run_id="new_run",
                script_name="headless_remote.sh",
            )
        self.assertTrue(result["acquired"])
        self.assertEqual(result["lock_data"]["run_id"], "new_run")
        self.assertTrue(Path(self.lock_path).exists())

    def test_old_same_host_live_pid_is_not_recovered_by_age(self) -> None:
        """A live same-host holder must not be stolen only because it is old."""
        stale_data = {
            "run_id": "old_live_run",
            "script_name": "test.sh",
            "candidate_id": "",
            "timestamp": "2025-01-01T00:00:00Z",
            "owner_command": "bash test.sh",
            "pid": 12345,
            "hostname": lock_mod.hostname(),
        }
        Path(self.lock_path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.lock_path).write_text(json.dumps(stale_data), encoding="utf-8")

        with mock.patch.object(lock_mod, "_pid_alive", return_value=True):
            result = lock_mod.acquire_lock(
                self.lock_path,
                run_id="new_run",
                script_name="headless_remote.sh",
            )

        self.assertFalse(result["acquired"])
        self.assertEqual(result["lock_data"]["run_id"], "old_live_run")

    # -- corrupt lock file --

    def test_corrupt_lock_file_is_treated_as_held(self) -> None:
        """A corrupt/unparseable lock file should block new acquires."""
        Path(self.lock_path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.lock_path).write_text("not valid json {{{", encoding="utf-8")

        result = lock_mod.acquire_lock(
            self.lock_path,
            run_id="new_run",
            script_name="test.sh",
        )
        # Should still block because _read_lock_file returns None for corrupt
        # files, and then we treat it as "lock held by another run" with
        # run_id="unknown".
        self.assertFalse(result["acquired"])

    # -- release idempotency --

    def test_double_release(self) -> None:
        lock_mod.acquire_lock(
            self.lock_path,
            run_id="test_run_1",
            script_name="test_script.sh",
        )
        self.assertTrue(Path(self.lock_path).exists())

        first = lock_mod.release_lock(self.lock_path, run_id="test_run_1")
        self.assertTrue(first["released"])

        second = lock_mod.release_lock(self.lock_path, run_id="test_run_1")
        self.assertFalse(second["released"])
        self.assertIn("no lock file found", second["reason"])

    # -- concurrent stress (same process, sequential simulation) --

    def test_sequential_acquire_release_cycle(self) -> None:
        for i in range(5):
            run_id = f"cycle_{i}"
            result = lock_mod.acquire_lock(
                self.lock_path,
                run_id=run_id,
                script_name="test.sh",
            )
            self.assertTrue(result["acquired"], f"cycle {i} acquire failed")
            self.assertEqual(result["lock_data"]["run_id"], run_id)
            released = lock_mod.release_lock(self.lock_path, run_id=run_id)
            self.assertTrue(released["released"], f"cycle {i} release failed")
            self.assertFalse(Path(self.lock_path).exists())


class PerfValidationLockCLITests(unittest.TestCase):
    """Tests for the CLI interface used by bash scripts."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="test_perf_lock_cli_")
        self.tmp_dir = Path(self._tmp.name)
        self.lock_path = str(self.tmp_dir / ".perf_validation.lock")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run_cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "perf_validation_lock.py")] + list(args),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )

    def test_cli_acquire_release_check(self) -> None:
        # Acquire
        result = self._run_cli(
            "acquire",
            "--lock-path", self.lock_path,
            "--run-id", "cli_test_run",
            "--script-name", "test.sh",
            "--candidate-id", "cand_1",
            "--owner-command", "bash test.sh",
        )
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout.strip().split("\n")[-1])
        self.assertTrue(data["acquired"], f"acquire failed: {data}")

        # Check
        result = self._run_cli("check", "--lock-path", self.lock_path)
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout.strip().split("\n")[-1])
        self.assertTrue(data["held"])

        # Release
        result = self._run_cli(
            "release",
            "--lock-path", self.lock_path,
            "--run-id", "cli_test_run",
        )
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout.strip().split("\n")[-1])
        self.assertTrue(data["released"], f"release failed: {data}")

    def test_cli_acquire_fails_when_held(self) -> None:
        self._run_cli(
            "acquire",
            "--lock-path", self.lock_path,
            "--run-id", "run_a",
            "--script-name", "test.sh",
        )
        result = self._run_cli(
            "acquire",
            "--lock-path", self.lock_path,
            "--run-id", "run_b",
            "--script-name", "other.sh",
        )
        self.assertNotEqual(result.returncode, 0)
        data = json.loads(result.stdout.strip().split("\n")[-1])
        self.assertFalse(data["acquired"])

    def test_cli_release_wrong_run_id_fails(self) -> None:
        self._run_cli(
            "acquire",
            "--lock-path", self.lock_path,
            "--run-id", "run_a",
            "--script-name", "test.sh",
        )
        result = self._run_cli(
            "release",
            "--lock-path", self.lock_path,
            "--run-id", "run_b",
        )
        self.assertNotEqual(result.returncode, 0)
        data = json.loads(result.stdout.strip().split("\n")[-1])
        self.assertFalse(data["released"])
        self.assertIn("run_id mismatch", data["reason"])


if __name__ == "__main__":
    unittest.main()
