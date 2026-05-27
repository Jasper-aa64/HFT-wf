#!/usr/bin/env python3
"""Global validation lock for performance timing runs.

Manages a file-based lock on the remote devbox to prevent multiple
performance timing runs from running simultaneously.

Lock path on remote: ``/root/work/.perf_validation.lock``.

Lock file format (JSON):

.. code-block:: json

    {
      "run_id": "stack_skip_unused_row_fields_m24",
      "script_name": "psi_headless_remote.sh",
      "candidate_id": "stack_skip_unused_row_fields",
      "timestamp": "2026-05-27T12:00:00Z",
      "owner_command": "bash scripts/psi_headless_remote.sh",
      "pid": 12345,
      "hostname": "devbox"
    }

This module supports two usage modes:

1. **Local mode** -- direct filesystem operations (for scripts running on the
   devbox, e.g. ``psi_headless_remote.sh``).
2. **SSH mode** -- operations dispatched over SSH (for local Python drivers
   like ``psi_host_jitter_audit.py``).

CLI usage (local mode)::

    python3 scripts/perf_validation_lock.py acquire --run-id $RUN_ID --script-name $0 --candidate-id $CANDIDATE_ID
    python3 scripts/perf_validation_lock.py check
    python3 scripts/perf_validation_lock.py release --run-id $RUN_ID

Python API (local mode)::

    from perf_validation_lock import acquire_lock, release_lock, check_lock
    result = acquire_lock(run_id="m24", script_name="psi_headless_remote.sh")
    assert result["acquired"]
    ...
    release_lock(run_id="m24")

Python API (SSH mode)::

    from perf_validation_lock import acquire_lock_ssh, release_lock_ssh, check_lock_ssh
    result = acquire_lock_ssh("devbox", run_id="m24", script_name="psi_headless_remote.sh")
    assert result["acquired"]
    ...
    release_lock_ssh("devbox", run_id="m24")
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

DEFAULT_LOCK_PATH = "/root/work/.perf_validation.lock"
DEFAULT_REMOTE_HOST = "devbox"


def utc_now() -> str:
    """Return current UTC time as ISO-8601 string (Z suffix)."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def hostname() -> str:
    """Return the current hostname, or empty string on failure."""
    try:
        return socket.gethostname()
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# Local-mode (direct filesystem) operations
# ---------------------------------------------------------------------------


def _read_lock_file(lock_path: str) -> dict[str, Any] | None:
    """Read and parse the lock file.  Returns None when the file does not
    exist.  Returns a sentinel dict with ``_corrupt: true`` when the file
    exists but is not valid JSON, so callers can treat it as a held lock."""
    path = Path(lock_path)
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        # Cannot read the file -- treat as corrupt / held.
        return {"_corrupt": True, "run_id": "unreadable", "timestamp": utc_now()}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"_corrupt": True, "run_id": "corrupt", "timestamp": utc_now()}
    if not isinstance(data, dict):
        return {"_corrupt": True, "run_id": "non_dict", "timestamp": utc_now()}
    return data


def _write_lock_file(lock_path: str, payload: dict[str, Any]) -> None:
    """Atomically write the lock file."""
    path = Path(lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    # Write to a temp file and rename for atomicity on the same filesystem.
    tmp = path.with_suffix(path.suffix + ".tmp" + str(os.getpid()))
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def acquire_lock(
    lock_path: str = DEFAULT_LOCK_PATH,
    *,
    run_id: str,
    script_name: str,
    candidate_id: str = "",
    owner_command: str = "",
) -> dict[str, Any]:
    """Acquire the validation lock on the local filesystem.

    Returns a dict with fields:
        ``acquired`` (bool), ``reason`` (str), ``lock_data`` (dict or None).
    """
    existing = _read_lock_file(lock_path)
    if existing is not None:
        holder = existing.get("run_id", "unknown")
        return {
            "acquired": False,
            "reason": f"lock held by another run: {holder}",
            "lock_data": existing,
        }

    lock_data: dict[str, Any] = {
        "run_id": run_id,
        "script_name": script_name,
        "candidate_id": candidate_id,
        "timestamp": utc_now(),
        "owner_command": owner_command or " ".join(sys.argv),
        "pid": os.getpid(),
        "hostname": hostname(),
    }

    _write_lock_file(lock_path, lock_data)

    # Verify the write
    written = _read_lock_file(lock_path)
    if written and written.get("run_id") == run_id:
        return {
            "acquired": True,
            "reason": "lock acquired",
            "lock_data": lock_data,
        }

    return {
        "acquired": False,
        "reason": "lock verification failed after write",
        "lock_data": None,
    }


def release_lock(
    lock_path: str = DEFAULT_LOCK_PATH,
    *,
    run_id: str,
) -> dict[str, Any]:
    """Release the validation lock on the local filesystem.

    Only releases the lock when the stored ``run_id`` matches.
    Returns a dict with fields:
        ``released`` (bool), ``reason`` (str), ``lock_data`` (dict or None).
    """
    existing = _read_lock_file(lock_path)
    if existing is None:
        return {
            "released": False,
            "reason": "no lock file found",
            "lock_data": None,
        }

    holder = existing.get("run_id", "unknown")
    if holder != run_id:
        return {
            "released": False,
            "reason": f"lock run_id mismatch: expected {run_id}, got {holder}",
            "lock_data": existing,
        }

    try:
        Path(lock_path).unlink(missing_ok=True)
    except OSError as exc:
        return {
            "released": False,
            "reason": f"failed to remove lock file: {exc}",
            "lock_data": existing,
        }

    return {
        "released": True,
        "reason": "lock released",
        "lock_data": existing,
    }


def check_lock(lock_path: str = DEFAULT_LOCK_PATH) -> dict[str, Any]:
    """Check whether the validation lock is currently held.

    Returns a dict with fields:
        ``held`` (bool), ``reason`` (str), ``lock_data`` (dict or None).
    """
    existing = _read_lock_file(lock_path)
    if existing is None:
        return {
            "held": False,
            "reason": "no lock held",
            "lock_data": None,
        }
    return {
        "held": True,
        "reason": f"lock held by: {existing.get('run_id', 'unknown')}",
        "lock_data": existing,
    }


# ---------------------------------------------------------------------------
# SSH-mode (remote) operations
# ---------------------------------------------------------------------------


def _remote_quote(value: str) -> str:
    return shlex.quote(value)


def _ssh(remote_host: str, command: str, *, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["ssh", remote_host, command],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _remote_command_python(lock_path: str, operation: str, run_id: str = "", script_name: str = "", candidate_id: str = "", owner_command: str = "") -> str:
    """Build a remote Python command that uses this module's local functions."""
    flags = f"--lock-path {_remote_quote(lock_path)}"
    if run_id:
        flags += f" --run-id {_remote_quote(run_id)}"
    if script_name:
        flags += f" --script-name {_remote_quote(script_name)}"
    if candidate_id:
        flags += f" --candidate-id {_remote_quote(candidate_id)}"
    if owner_command:
        flags += f" --owner-command {_remote_quote(owner_command)}"
    return f"python3 scripts/perf_validation_lock.py {operation} {flags}"


def acquire_lock_ssh(
    remote_host: str,
    lock_path: str = DEFAULT_LOCK_PATH,
    *,
    run_id: str,
    script_name: str,
    candidate_id: str = "",
    owner_command: str = "",
    timeout: int = 30,
    remote_hft_root: str = "/root/work/HFT-wf",
) -> dict[str, Any]:
    """Acquire the validation lock on the remote host via SSH.

    This delegates to the remote ``perf_validation_lock.py`` module.

    Returns a dict with fields:
        ``acquired`` (bool), ``reason`` (str), ``lock_data`` (dict or None).
    """
    acquire_cmd = _remote_command_python(
        lock_path, "acquire",
        run_id=run_id, script_name=script_name,
        candidate_id=candidate_id, owner_command=owner_command,
    )
    command = f"cd {_remote_quote(remote_hft_root)} && {acquire_cmd}"
    result = _ssh(remote_host, command, timeout=timeout)

    if result.returncode != 0:
        err = result.stderr.strip() or result.stdout.strip()
        return {
            "acquired": False,
            "reason": f"remote acquire failed rc={result.returncode}: {err}",
            "lock_data": None,
        }

    try:
        return json.loads(result.stdout.strip().split("\n")[-1])
    except json.JSONDecodeError:
        return {
            "acquired": False,
            "reason": f"unexpected remote output: {result.stdout.strip()[:200]}",
            "lock_data": None,
        }


def release_lock_ssh(
    remote_host: str,
    lock_path: str = DEFAULT_LOCK_PATH,
    *,
    run_id: str,
    timeout: int = 30,
    remote_hft_root: str = "/root/work/HFT-wf",
) -> dict[str, Any]:
    """Release the validation lock on the remote host via SSH.

    Only releases the lock when the stored ``run_id`` matches.

    Returns a dict with fields:
        ``released`` (bool), ``reason`` (str), ``lock_data`` (dict or None).
    """
    release_cmd = _remote_command_python(lock_path, "release", run_id=run_id)
    command = f"cd {_remote_quote(remote_hft_root)} && {release_cmd}"
    result = _ssh(remote_host, command, timeout=timeout)

    if result.returncode != 0:
        err = result.stderr.strip() or result.stdout.strip()
        return {
            "released": False,
            "reason": f"remote release failed rc={result.returncode}: {err}",
            "lock_data": None,
        }

    try:
        return json.loads(result.stdout.strip().split("\n")[-1])
    except json.JSONDecodeError:
        return {
            "released": False,
            "reason": f"unexpected remote output: {result.stdout.strip()[:200]}",
            "lock_data": None,
        }


def check_lock_ssh(
    remote_host: str,
    lock_path: str = DEFAULT_LOCK_PATH,
    *,
    timeout: int = 30,
    remote_hft_root: str = "/root/work/HFT-wf",
) -> dict[str, Any]:
    """Check whether the validation lock is held on the remote host via SSH.

    Returns a dict with fields:
        ``held`` (bool), ``reason`` (str), ``lock_data`` (dict or None).
    """
    check_cmd = f"python3 scripts/perf_validation_lock.py check --lock-path {_remote_quote(lock_path)}"
    command = f"cd {_remote_quote(remote_hft_root)} && {check_cmd}"
    result = _ssh(remote_host, command, timeout=timeout)

    if result.returncode != 0:
        err = result.stderr.strip() or result.stdout.strip()
        return {
            "held": False,
            "reason": f"remote check failed rc={result.returncode}: {err}",
            "lock_data": None,
        }

    try:
        return json.loads(result.stdout.strip().split("\n")[-1])
    except json.JSONDecodeError:
        return {
            "held": False,
            "reason": f"unexpected remote output: {result.stdout.strip()[:200]}",
            "lock_data": None,
        }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage performance validation lock.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_acquire = sub.add_parser("acquire", help="Acquire the lock")
    p_acquire.add_argument("--lock-path", default=DEFAULT_LOCK_PATH)
    p_acquire.add_argument("--run-id", required=True)
    p_acquire.add_argument("--script-name", required=True)
    p_acquire.add_argument("--candidate-id", default="")
    p_acquire.add_argument("--owner-command", default="")

    p_release = sub.add_parser("release", help="Release the lock")
    p_release.add_argument("--lock-path", default=DEFAULT_LOCK_PATH)
    p_release.add_argument("--run-id", required=True)

    p_check = sub.add_parser("check", help="Check lock status")
    p_check.add_argument("--lock-path", default=DEFAULT_LOCK_PATH)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.command == "acquire":
        result = acquire_lock(
            lock_path=args.lock_path,
            run_id=args.run_id,
            script_name=args.script_name,
            candidate_id=args.candidate_id,
            owner_command=args.owner_command,
        )
    elif args.command == "release":
        result = release_lock(
            lock_path=args.lock_path,
            run_id=args.run_id,
        )
    elif args.command == "check":
        result = check_lock(lock_path=args.lock_path)
    else:
        print(f"unknown command: {args.command}", file=sys.stderr)
        return 2

    print(json.dumps(result, ensure_ascii=False))
    # Exit non-zero when acquire/release fails so bash can detect it.
    if args.command == "acquire":
        return 0 if result.get("acquired") else 1
    if args.command == "release":
        return 0 if result.get("released") else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
