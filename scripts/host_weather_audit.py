#!/usr/bin/env python3
"""Host weather audit for performance promotion gates.

The audit is deliberately conservative:

- process / CPU / IO snapshots explain host conditions;
- control-only or baseline-vs-baseline timing samples decide whether the host
  is quiet enough for promotion;
- snapshot-only runs can be BORDERLINE at best, never QUIET.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import shlex
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


SNAPSHOT_FIELDS = [
    "sample_index",
    "recorded_at",
    "kind",
    "duration_ms",
    "loadavg_1m",
    "loadavg_5m",
    "load_per_cpu_1m",
    "cpu_idle_pct",
    "cpu_iowait_pct",
    "context_switches_per_sec",
    "run_queue",
    "procs_blocked",
    "active_runner_count",
    "cpu_governor",
    "cpu_freq_mhz_min",
    "cpu_freq_mhz_max",
    "disk_read_kb_per_sec",
    "disk_write_kb_per_sec",
    "metrics_available",
    "notes",
]

DEFAULT_PROCESS_NAMES = ["PsiTraderRunner"]


@dataclass(frozen=True)
class Thresholds:
    min_control_samples: int = 5
    quiet_control_cov: float = 0.015
    noisy_control_cov: float = 0.050
    quiet_control_range_ratio: float = 0.040
    noisy_control_range_ratio: float = 0.150
    quiet_paired_stdev_ms: float = 800.0
    noisy_paired_stdev_ms: float = 1500.0
    quiet_paired_range_ms: float = 2000.0
    noisy_paired_range_ms: float = 4000.0
    quiet_load_per_cpu: float = 0.35
    noisy_load_per_cpu: float = 0.75
    quiet_iowait_pct: float = 2.0
    noisy_iowait_pct: float = 10.0


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root is not an object: {path}")
    return payload


def write_tsv(path: Path, rows: list[dict[str, Any]], fieldnames: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fieldnames), delimiter="\t", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def read_tsv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def parse_float(raw: Any) -> float | None:
    if raw in (None, ""):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if math.isnan(value) or math.isinf(value):
        return None
    return value


def parse_csv_floats(raw: str | None) -> list[float]:
    if not raw:
        return []
    values: list[float] = []
    for part in re.split(r"[,;\s]+", raw.strip()):
        value = parse_float(part)
        if value is not None:
            values.append(value)
    return values


def stats(values: Sequence[float]) -> dict[str, Any]:
    clean = [float(value) for value in values]
    if not clean:
        return {
            "sample_count": 0,
            "median_ms": None,
            "mean_ms": None,
            "stdev_ms": None,
            "range_ms": None,
            "cov": None,
        }
    median = statistics.median(clean)
    stdev = statistics.stdev(clean) if len(clean) > 1 else 0.0
    range_ms = max(clean) - min(clean) if len(clean) > 1 else 0.0
    return {
        "sample_count": len(clean),
        "median_ms": median,
        "mean_ms": statistics.mean(clean),
        "stdev_ms": stdev,
        "range_ms": range_ms,
        "cov": stdev / median if median else None,
    }


def read_timing_history_control_samples(path: Path, host_key: str = "", limit_rows: int = 20) -> list[float]:
    rows = read_tsv(path)
    if host_key:
        rows = [row for row in rows if (row.get("host_key") or "").strip() == host_key]
    control_rows = [row for row in rows if (row.get("kind") or "").strip() == "control"]
    control_rows.sort(key=lambda row: row.get("recorded_at", ""))
    samples: list[float] = []
    for row in control_rows[-limit_rows:]:
        raw_samples = (row.get("samples_ms") or row.get("control_samples_ms") or "").strip()
        if raw_samples:
            samples.extend(parse_csv_floats(raw_samples))
            continue
        for key in ("median_ms", "control_median_ms", "mean_ms"):
            value = parse_float(row.get(key))
            if value is not None:
                samples.append(value)
                break
    return samples


def _read_loadavg(proc_root: Path) -> dict[str, Any]:
    raw = (proc_root / "loadavg").read_text(encoding="utf-8", errors="replace").strip().split()
    run_queue = 0
    if len(raw) >= 4 and "/" in raw[3]:
        run_queue = int(raw[3].split("/", 1)[0])
    return {
        "loadavg_1m": float(raw[0]) if raw else 0.0,
        "loadavg_5m": float(raw[1]) if len(raw) > 1 else 0.0,
        "run_queue": run_queue,
    }


def _read_proc_stat(proc_root: Path) -> dict[str, Any]:
    payload: dict[str, Any] = {"cpu": [], "ctxt": 0, "procs_running": 0, "procs_blocked": 0}
    for line in (proc_root / "stat").read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "cpu":
            payload["cpu"] = [int(value) for value in parts[1:]]
        elif parts[0] == "ctxt" and len(parts) > 1:
            payload["ctxt"] = int(parts[1])
        elif parts[0] == "procs_running" and len(parts) > 1:
            payload["procs_running"] = int(parts[1])
        elif parts[0] == "procs_blocked" and len(parts) > 1:
            payload["procs_blocked"] = int(parts[1])
    return payload


def _cpu_percentages(before: dict[str, Any], after: dict[str, Any]) -> dict[str, float]:
    first = [int(value) for value in before.get("cpu", [])]
    second = [int(value) for value in after.get("cpu", [])]
    if not first or not second or len(first) != len(second):
        return {"cpu_idle_pct": 0.0, "cpu_iowait_pct": 0.0}
    deltas = [max(0, b - a) for a, b in zip(first, second, strict=False)]
    total = sum(deltas)
    if total <= 0:
        return {"cpu_idle_pct": 0.0, "cpu_iowait_pct": 0.0}
    idle = deltas[3] if len(deltas) > 3 else 0
    iowait = deltas[4] if len(deltas) > 4 else 0
    return {
        "cpu_idle_pct": idle / total * 100.0,
        "cpu_iowait_pct": iowait / total * 100.0,
    }


def _read_diskstats(proc_root: Path) -> dict[str, int]:
    path = proc_root / "diskstats"
    if not path.exists():
        return {"read_sectors": 0, "write_sectors": 0}
    read_sectors = 0
    write_sectors = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split()
        if len(parts) < 14:
            continue
        name = parts[2]
        if name.startswith(("loop", "ram", "fd")):
            continue
        try:
            read_sectors += int(parts[5])
            write_sectors += int(parts[9])
        except ValueError:
            continue
    return {"read_sectors": read_sectors, "write_sectors": write_sectors}


def _disk_rates(before: dict[str, int], after: dict[str, int], elapsed_seconds: float) -> dict[str, float]:
    if elapsed_seconds <= 0:
        return {"disk_read_kb_per_sec": 0.0, "disk_write_kb_per_sec": 0.0}
    read_delta = max(0, after.get("read_sectors", 0) - before.get("read_sectors", 0))
    write_delta = max(0, after.get("write_sectors", 0) - before.get("write_sectors", 0))
    return {
        "disk_read_kb_per_sec": read_delta * 512.0 / 1024.0 / elapsed_seconds,
        "disk_write_kb_per_sec": write_delta * 512.0 / 1024.0 / elapsed_seconds,
    }


def _scan_processes(proc_root: Path, process_names: Sequence[str]) -> list[dict[str, str]]:
    names = {name.strip() for name in process_names if name.strip()}
    if not names:
        return []
    out: list[dict[str, str]] = []
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            comm = (entry / "comm").read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if comm not in names:
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", errors="replace").strip()
        except OSError:
            cmdline = comm
        out.append({"pid": entry.name, "name": comm, "cmdline": cmdline})
    return out


def _cpu_freq_and_governor() -> dict[str, Any]:
    root = Path("/sys/devices/system/cpu")
    freqs: list[float] = []
    governors: set[str] = set()
    if not root.exists():
        return {"cpu_freq_mhz_min": None, "cpu_freq_mhz_max": None, "cpu_governor": ""}
    for cpu_dir in root.glob("cpu[0-9]*"):
        cpufreq = cpu_dir / "cpufreq"
        if not cpufreq.exists():
            continue
        try:
            raw_freq = (cpufreq / "scaling_cur_freq").read_text(encoding="utf-8").strip()
            freqs.append(float(raw_freq) / 1000.0)
        except (OSError, ValueError):
            pass
        try:
            governors.add((cpufreq / "scaling_governor").read_text(encoding="utf-8").strip())
        except OSError:
            pass
    return {
        "cpu_freq_mhz_min": min(freqs) if freqs else None,
        "cpu_freq_mhz_max": max(freqs) if freqs else None,
        "cpu_governor": "|".join(sorted(governors)),
    }


def collect_local_snapshots(
    *,
    sample_count: int,
    interval_seconds: float,
    process_names: Sequence[str],
    proc_root: Path = Path("/proc"),
) -> list[dict[str, Any]]:
    if not proc_root.exists():
        return [
            {
                "sample_index": 1,
                "recorded_at": utc_now(),
                "kind": "snapshot",
                "duration_ms": 0.0,
                "metrics_available": False,
                "active_runner_count": 0,
                "blocking_processes": [],
                "notes": f"proc root not available: {proc_root}",
            }
        ]

    snapshots: list[dict[str, Any]] = []
    cpu_count = os.cpu_count() or 1
    for index in range(1, max(1, sample_count) + 1):
        started = time.monotonic()
        before_stat = _read_proc_stat(proc_root)
        before_disk = _read_diskstats(proc_root)
        time.sleep(max(0.0, interval_seconds))
        after_stat = _read_proc_stat(proc_root)
        after_disk = _read_diskstats(proc_root)
        elapsed = max(time.monotonic() - started, 0.000001)
        load = _read_loadavg(proc_root)
        cpu = _cpu_percentages(before_stat, after_stat)
        disk = _disk_rates(before_disk, after_disk, elapsed)
        active = _scan_processes(proc_root, process_names)
        freq = _cpu_freq_and_governor()
        snapshots.append(
            {
                "sample_index": index,
                "recorded_at": utc_now(),
                "kind": "snapshot",
                "duration_ms": elapsed * 1000.0,
                "loadavg_1m": load["loadavg_1m"],
                "loadavg_5m": load["loadavg_5m"],
                "load_per_cpu_1m": load["loadavg_1m"] / cpu_count,
                "cpu_idle_pct": cpu["cpu_idle_pct"],
                "cpu_iowait_pct": cpu["cpu_iowait_pct"],
                "context_switches_per_sec": max(0, int(after_stat.get("ctxt", 0)) - int(before_stat.get("ctxt", 0))) / elapsed,
                "run_queue": load["run_queue"],
                "procs_blocked": int(after_stat.get("procs_blocked", 0)),
                "active_runner_count": len(active),
                "blocking_processes": active,
                "metrics_available": True,
                "notes": "",
                **disk,
                **freq,
            }
        )
    return snapshots


REMOTE_PROBE = r'''
import json
import os
import pathlib
import sys
import time

config = json.loads(os.environ["HOST_WEATHER_PROBE_CONFIG"])
proc_root = pathlib.Path("/proc")

def utc_now():
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

def read_loadavg():
    raw = (proc_root / "loadavg").read_text(encoding="utf-8", errors="replace").strip().split()
    run_queue = 0
    if len(raw) >= 4 and "/" in raw[3]:
        run_queue = int(raw[3].split("/", 1)[0])
    return {"loadavg_1m": float(raw[0]) if raw else 0.0, "loadavg_5m": float(raw[1]) if len(raw) > 1 else 0.0, "run_queue": run_queue}

def read_stat():
    payload = {"cpu": [], "ctxt": 0, "procs_running": 0, "procs_blocked": 0}
    for line in (proc_root / "stat").read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "cpu":
            payload["cpu"] = [int(value) for value in parts[1:]]
        elif parts[0] == "ctxt" and len(parts) > 1:
            payload["ctxt"] = int(parts[1])
        elif parts[0] == "procs_running" and len(parts) > 1:
            payload["procs_running"] = int(parts[1])
        elif parts[0] == "procs_blocked" and len(parts) > 1:
            payload["procs_blocked"] = int(parts[1])
    return payload

def cpu_pct(before, after):
    first = before.get("cpu", [])
    second = after.get("cpu", [])
    if not first or not second or len(first) != len(second):
        return {"cpu_idle_pct": 0.0, "cpu_iowait_pct": 0.0}
    deltas = [max(0, b - a) for a, b in zip(first, second)]
    total = sum(deltas)
    if total <= 0:
        return {"cpu_idle_pct": 0.0, "cpu_iowait_pct": 0.0}
    idle = deltas[3] if len(deltas) > 3 else 0
    iowait = deltas[4] if len(deltas) > 4 else 0
    return {"cpu_idle_pct": idle / total * 100.0, "cpu_iowait_pct": iowait / total * 100.0}

def diskstats():
    path = proc_root / "diskstats"
    out = {"read_sectors": 0, "write_sectors": 0}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split()
        if len(parts) < 14:
            continue
        name = parts[2]
        if name.startswith(("loop", "ram", "fd")):
            continue
        try:
            out["read_sectors"] += int(parts[5])
            out["write_sectors"] += int(parts[9])
        except ValueError:
            pass
    return out

def scan_processes():
    names = {name for name in config.get("process_names", []) if name}
    rows = []
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            comm = (entry / "comm").read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if comm not in names:
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", errors="replace").strip()
        except OSError:
            cmdline = comm
        rows.append({"pid": entry.name, "name": comm, "cmdline": cmdline})
    return rows

def freq():
    root = pathlib.Path("/sys/devices/system/cpu")
    freqs = []
    governors = set()
    for cpu_dir in root.glob("cpu[0-9]*"):
        cpufreq = cpu_dir / "cpufreq"
        if not cpufreq.exists():
            continue
        try:
            freqs.append(float((cpufreq / "scaling_cur_freq").read_text(encoding="utf-8").strip()) / 1000.0)
        except Exception:
            pass
        try:
            governors.add((cpufreq / "scaling_governor").read_text(encoding="utf-8").strip())
        except Exception:
            pass
    return {"cpu_freq_mhz_min": min(freqs) if freqs else None, "cpu_freq_mhz_max": max(freqs) if freqs else None, "cpu_governor": "|".join(sorted(governors))}

if not proc_root.exists():
    print(json.dumps([{"sample_index": 1, "recorded_at": utc_now(), "kind": "snapshot", "duration_ms": 0.0, "metrics_available": False, "active_runner_count": 0, "blocking_processes": [], "notes": "/proc unavailable"}]))
    raise SystemExit(0)

snapshots = []
cpu_count = os.cpu_count() or 1
for index in range(1, int(config.get("sample_count", 1)) + 1):
    start = time.monotonic()
    stat0 = read_stat()
    disk0 = diskstats()
    time.sleep(max(0.0, float(config.get("interval_seconds", 1.0))))
    stat1 = read_stat()
    disk1 = diskstats()
    elapsed = max(time.monotonic() - start, 0.000001)
    load = read_loadavg()
    cpu = cpu_pct(stat0, stat1)
    active = scan_processes()
    snapshots.append({
        "sample_index": index,
        "recorded_at": utc_now(),
        "kind": "snapshot",
        "duration_ms": elapsed * 1000.0,
        "loadavg_1m": load["loadavg_1m"],
        "loadavg_5m": load["loadavg_5m"],
        "load_per_cpu_1m": load["loadavg_1m"] / cpu_count,
        "cpu_idle_pct": cpu["cpu_idle_pct"],
        "cpu_iowait_pct": cpu["cpu_iowait_pct"],
        "context_switches_per_sec": max(0, int(stat1.get("ctxt", 0)) - int(stat0.get("ctxt", 0))) / elapsed,
        "run_queue": load["run_queue"],
        "procs_blocked": int(stat1.get("procs_blocked", 0)),
        "active_runner_count": len(active),
        "blocking_processes": active,
        "metrics_available": True,
        "disk_read_kb_per_sec": max(0, disk1["read_sectors"] - disk0["read_sectors"]) * 512.0 / 1024.0 / elapsed,
        "disk_write_kb_per_sec": max(0, disk1["write_sectors"] - disk0["write_sectors"]) * 512.0 / 1024.0 / elapsed,
        "notes": "",
        **freq(),
    })
print(json.dumps(snapshots))
'''


def remote_quote(value: str) -> str:
    return shlex.quote(str(value))


def collect_remote_snapshots(
    *,
    remote_host: str,
    sample_count: int,
    interval_seconds: float,
    process_names: Sequence[str],
    timeout_seconds: int,
) -> list[dict[str, Any]]:
    config = {
        "sample_count": max(1, sample_count),
        "interval_seconds": max(0.0, interval_seconds),
        "process_names": list(process_names),
    }
    command = (
        f"HOST_WEATHER_PROBE_CONFIG={remote_quote(json.dumps(config, separators=(',', ':')))} "
        "python3 - <<'PY'\n"
        f"{REMOTE_PROBE}\n"
        "PY"
    )
    result = subprocess.run(
        ["ssh", remote_host, command],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
    )
    if result.returncode != 0:
        return [
            {
                "sample_index": 1,
                "recorded_at": utc_now(),
                "kind": "snapshot",
                "duration_ms": 0.0,
                "metrics_available": False,
                "active_runner_count": 0,
                "blocking_processes": [],
                "notes": f"remote probe failed rc={result.returncode}: {(result.stderr or result.stdout).strip()}",
            }
        ]
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return [
            {
                "sample_index": 1,
                "recorded_at": utc_now(),
                "kind": "snapshot",
                "duration_ms": 0.0,
                "metrics_available": False,
                "active_runner_count": 0,
                "blocking_processes": [],
                "notes": "remote probe returned invalid JSON",
            }
        ]
    if not isinstance(payload, list):
        raise ValueError("remote probe JSON root is not a list")
    return [row for row in payload if isinstance(row, dict)]


def _max_float(rows: Iterable[dict[str, Any]], key: str) -> float | None:
    values = [parse_float(row.get(key)) for row in rows]
    clean = [value for value in values if value is not None]
    return max(clean) if clean else None


def _latest_snapshot_readiness(
    snapshots: list[dict[str, Any]],
    *,
    thresholds: Thresholds,
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if not snapshots:
        return "BORDERLINE", ["no_snapshot_samples"]
    if any(not row.get("metrics_available", False) for row in snapshots):
        reasons.append("host_metrics_unavailable")
    active_count = max(int(row.get("active_runner_count") or 0) for row in snapshots)
    if active_count > 0:
        return "NOISY", [f"active_runner_seen:{active_count}"]
    max_load = _max_float(snapshots, "load_per_cpu_1m")
    if max_load is not None:
        if max_load >= thresholds.noisy_load_per_cpu:
            return "NOISY", [f"load_per_cpu_above_noisy_threshold:{max_load:.3f}"]
        if max_load >= thresholds.quiet_load_per_cpu:
            reasons.append(f"load_per_cpu_above_quiet_threshold:{max_load:.3f}")
    max_iowait = _max_float(snapshots, "cpu_iowait_pct")
    if max_iowait is not None:
        if max_iowait >= thresholds.noisy_iowait_pct:
            return "NOISY", [f"iowait_above_noisy_threshold:{max_iowait:.3f}"]
        if max_iowait >= thresholds.quiet_iowait_pct:
            reasons.append(f"iowait_above_quiet_threshold:{max_iowait:.3f}")
    if reasons:
        return "BORDERLINE", reasons
    return "QUIET", []


def classify_weather(
    snapshots: list[dict[str, Any]],
    *,
    control_samples_ms: Sequence[float],
    paired_deltas_ms: Sequence[float] = (),
    thresholds: Thresholds = Thresholds(),
) -> dict[str, Any]:
    snapshot_decision, snapshot_reasons = _latest_snapshot_readiness(snapshots, thresholds=thresholds)
    control = stats(control_samples_ms)
    paired = stats(paired_deltas_ms)
    reasons = list(snapshot_reasons)
    decision = snapshot_decision

    def set_decision(next_decision: str) -> None:
        nonlocal decision
        order = {"QUIET": 0, "BORDERLINE": 1, "NOISY": 2}
        if order[next_decision] > order[decision]:
            decision = next_decision

    if control["sample_count"] < thresholds.min_control_samples:
        set_decision("BORDERLINE")
        reasons.append(f"insufficient_control_samples:{control['sample_count']}<{thresholds.min_control_samples}")
    else:
        cov = control.get("cov")
        median = control.get("median_ms") or 0.0
        range_ms = control.get("range_ms") or 0.0
        range_ratio = range_ms / median if median else None
        if cov is not None:
            if cov >= thresholds.noisy_control_cov:
                set_decision("NOISY")
                reasons.append(f"control_cov_above_noisy_threshold:{cov:.6f}")
            elif cov >= thresholds.quiet_control_cov:
                set_decision("BORDERLINE")
                reasons.append(f"control_cov_above_quiet_threshold:{cov:.6f}")
        if range_ratio is not None:
            if range_ratio >= thresholds.noisy_control_range_ratio:
                set_decision("NOISY")
                reasons.append(f"control_range_ratio_above_noisy_threshold:{range_ratio:.6f}")
            elif range_ratio >= thresholds.quiet_control_range_ratio:
                set_decision("BORDERLINE")
                reasons.append(f"control_range_ratio_above_quiet_threshold:{range_ratio:.6f}")

    if paired["sample_count"] >= 2:
        paired_range = paired.get("range_ms") or 0.0
        paired_stdev = paired.get("stdev_ms") or 0.0
        if paired_range >= thresholds.noisy_paired_range_ms:
            set_decision("NOISY")
            reasons.append(f"paired_range_above_noisy_threshold:{paired_range:.3f}")
        elif paired_range >= thresholds.quiet_paired_range_ms:
            set_decision("BORDERLINE")
            reasons.append(f"paired_range_above_quiet_threshold:{paired_range:.3f}")
        if paired_stdev >= thresholds.noisy_paired_stdev_ms:
            set_decision("NOISY")
            reasons.append(f"paired_stdev_above_noisy_threshold:{paired_stdev:.3f}")
        elif paired_stdev >= thresholds.quiet_paired_stdev_ms:
            set_decision("BORDERLINE")
            reasons.append(f"paired_stdev_above_quiet_threshold:{paired_stdev:.3f}")

    if decision == "QUIET" and not reasons:
        reasons.append("host_snapshot_and_control_jitter_within_quiet_thresholds")

    return {
        "schema": "host_jitter_summary_v1",
        "recorded_at": utc_now(),
        "decision": decision,
        "snapshot_decision": snapshot_decision,
        "sample_count": control["sample_count"],
        "control_median_ms": control["median_ms"],
        "control_mean_ms": control["mean_ms"],
        "control_stdev_ms": control["stdev_ms"],
        "control_range_ms": control["range_ms"],
        "control_cov": control["cov"],
        "paired_sample_count": paired["sample_count"],
        "paired_range_ms": paired["range_ms"],
        "paired_stdev_ms": paired["stdev_ms"],
        "max_load_per_cpu_1m": _max_float(snapshots, "load_per_cpu_1m"),
        "max_loadavg_1m": _max_float(snapshots, "loadavg_1m"),
        "max_iowait_pct": _max_float(snapshots, "cpu_iowait_pct"),
        "max_context_switches_per_sec": _max_float(snapshots, "context_switches_per_sec"),
        "active_runner_seen": any(int(row.get("active_runner_count") or 0) > 0 for row in snapshots),
        "reasons": reasons,
        "thresholds": thresholds.__dict__,
    }


def build_readiness(
    snapshots: list[dict[str, Any]],
    summary: dict[str, Any],
    *,
    host_key: str,
    remote_host: str = "",
) -> dict[str, Any]:
    latest = dict(snapshots[-1]) if snapshots else {}
    blocking = []
    for row in snapshots:
        for proc in row.get("blocking_processes") or []:
            if isinstance(proc, dict):
                blocking.append(proc)
    return {
        "schema": "host_readiness_v1",
        "recorded_at": utc_now(),
        "host_key": host_key,
        "remote_host": remote_host,
        "decision": summary["decision"],
        "snapshot_decision": summary["snapshot_decision"],
        "active_runner_count": int(latest.get("active_runner_count") or 0),
        "loadavg_1m": latest.get("loadavg_1m"),
        "loadavg_5m": latest.get("loadavg_5m"),
        "load_per_cpu_1m": latest.get("load_per_cpu_1m"),
        "cpu_idle_pct": latest.get("cpu_idle_pct"),
        "cpu_iowait_pct": latest.get("cpu_iowait_pct"),
        "context_switches_per_sec": latest.get("context_switches_per_sec"),
        "run_queue": latest.get("run_queue"),
        "procs_blocked": latest.get("procs_blocked"),
        "cpu_governor": latest.get("cpu_governor", ""),
        "cpu_freq_mhz_min": latest.get("cpu_freq_mhz_min"),
        "cpu_freq_mhz_max": latest.get("cpu_freq_mhz_max"),
        "disk_read_kb_per_sec": latest.get("disk_read_kb_per_sec"),
        "disk_write_kb_per_sec": latest.get("disk_write_kb_per_sec"),
        "blocking_processes": blocking,
        "reasons": summary.get("reasons", []),
        "notes": latest.get("notes", ""),
    }


def load_fixture_snapshots(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(payload, dict):
            rows = payload.get("snapshots", [])
        else:
            rows = payload
        if not isinstance(rows, list):
            raise ValueError("snapshot JSON must be a list or object with snapshots list")
        return [row for row in rows if isinstance(row, dict)]
    rows = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if line.strip():
            payload = json.loads(line)
            if isinstance(payload, dict):
                rows.append(payload)
    return rows


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect host weather and jitter evidence for promotion gates.")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--host-key", default=os.environ.get("HOST_KEY") or os.environ.get("HARNESS_HOST_KEY") or "")
    parser.add_argument("--remote-host", default="")
    parser.add_argument("--sample-count", type=int, default=3)
    parser.add_argument("--sample-interval-seconds", type=float, default=1.0)
    parser.add_argument("--remote-timeout-seconds", type=int, default=120)
    parser.add_argument("--process-name", action="append", default=[], help="Blocking process name; repeatable.")
    parser.add_argument("--control-samples-ms", default="", help="Comma/space separated control-only timing samples.")
    parser.add_argument("--paired-deltas-ms", default="", help="Comma/space separated paired deltas, control-candidate.")
    parser.add_argument("--timing-history", type=Path, default=None, help="Optional timing_history.tsv to extract control samples from.")
    parser.add_argument("--timing-history-limit-rows", type=int, default=20)
    parser.add_argument("--snapshot-json", type=Path, default=None, help="Fixture JSON/JSONL snapshots for tests or replay.")
    parser.add_argument("--print-summary", action="store_true")
    parser.add_argument("--quiet-min-control-samples", type=int, default=Thresholds.min_control_samples)
    parser.add_argument("--quiet-control-cov", type=float, default=Thresholds.quiet_control_cov)
    parser.add_argument("--noisy-control-cov", type=float, default=Thresholds.noisy_control_cov)
    parser.add_argument("--quiet-control-range-ratio", type=float, default=Thresholds.quiet_control_range_ratio)
    parser.add_argument("--noisy-control-range-ratio", type=float, default=Thresholds.noisy_control_range_ratio)
    parser.add_argument("--quiet-load-per-cpu", type=float, default=Thresholds.quiet_load_per_cpu)
    parser.add_argument("--noisy-load-per-cpu", type=float, default=Thresholds.noisy_load_per_cpu)
    parser.add_argument("--quiet-iowait-pct", type=float, default=Thresholds.quiet_iowait_pct)
    parser.add_argument("--noisy-iowait-pct", type=float, default=Thresholds.noisy_iowait_pct)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    process_names = args.process_name or DEFAULT_PROCESS_NAMES
    if args.snapshot_json:
        snapshots = load_fixture_snapshots(args.snapshot_json)
    elif args.remote_host:
        snapshots = collect_remote_snapshots(
            remote_host=args.remote_host,
            sample_count=args.sample_count,
            interval_seconds=args.sample_interval_seconds,
            process_names=process_names,
            timeout_seconds=args.remote_timeout_seconds,
        )
    else:
        snapshots = collect_local_snapshots(
            sample_count=args.sample_count,
            interval_seconds=args.sample_interval_seconds,
            process_names=process_names,
        )

    control_samples = parse_csv_floats(args.control_samples_ms)
    if args.timing_history:
        control_samples.extend(
            read_timing_history_control_samples(
                args.timing_history,
                host_key=args.host_key,
                limit_rows=args.timing_history_limit_rows,
            )
        )
    paired_deltas = parse_csv_floats(args.paired_deltas_ms)
    thresholds = Thresholds(
        min_control_samples=args.quiet_min_control_samples,
        quiet_control_cov=args.quiet_control_cov,
        noisy_control_cov=args.noisy_control_cov,
        quiet_control_range_ratio=args.quiet_control_range_ratio,
        noisy_control_range_ratio=args.noisy_control_range_ratio,
        quiet_load_per_cpu=args.quiet_load_per_cpu,
        noisy_load_per_cpu=args.noisy_load_per_cpu,
        quiet_iowait_pct=args.quiet_iowait_pct,
        noisy_iowait_pct=args.noisy_iowait_pct,
    )
    summary = classify_weather(
        snapshots,
        control_samples_ms=control_samples,
        paired_deltas_ms=paired_deltas,
        thresholds=thresholds,
    )
    readiness = build_readiness(
        snapshots,
        summary,
        host_key=args.host_key or "unknown",
        remote_host=args.remote_host,
    )
    run_dir = args.run_dir.resolve()
    write_json(run_dir / "host_readiness.json", readiness)
    write_tsv(run_dir / "host_jitter_samples.tsv", snapshots, SNAPSHOT_FIELDS)
    write_json(run_dir / "host_jitter_summary.json", summary)
    if args.print_summary:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        print(f"decision={summary['decision']}")
        print(f"host_readiness={run_dir / 'host_readiness.json'}")
        print(f"host_jitter_summary={run_dir / 'host_jitter_summary.json'}")
        print(f"host_jitter_samples={run_dir / 'host_jitter_samples.tsv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
