#!/usr/bin/env bash
set -u

ROOT="${ROOT:-/root/work/Code1/psi-trader-liangjunming}"
ENV_FILE="${ENV_FILE:-/root/work/.toolchain/psi-env.sh}"
RUN_ID="${RUN_ID:-headless_psi_$(date +%Y%m%d_%H%M%S)}"
RUN_DIR="${RUN_DIR:-$ROOT/headless_runs/$RUN_ID}"
BUILD_DIR="${BUILD_DIR:-$ROOT/build/linux-relwithdebinfo-boost182}"
RUNNER="${RUNNER:-$BUILD_DIR/build_x64/RelWithDebInfo/bin/PsiTraderRunner/PsiTraderRunner}"
CONFIG="${CONFIG:-$ROOT/PsiTraderRunner/config.yaml}"
OUTPUT_DIR="${OUTPUT_DIR:-/root/work/Code1/dataset/output}"
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
REPORT_SCRIPT="${REPORT_SCRIPT:-}"
REPORT_ROOT="${REPORT_ROOT:-$RUN_DIR/reports}"
GENERATE_REPORT="${GENERATE_REPORT:-0}"
HEADLESS_CONTROL_DIR="$RUN_DIR"
MEASURE_RUNS="${MEASURE_RUNS:-5}"
CANDIDATE_RUNNER="${CANDIDATE_RUNNER:-}"

mkdir -p "$RUN_DIR"
mkdir -p "$RUN_DIR/logs" "$RUN_DIR/reports" "$RUN_DIR/patches"
printf "label\tmode\twarm_or_cold\telapsed_ms\telapsed_seconds\tcompat_seconds\trc\tlog_file\tpair_index\n" > "$RUN_DIR/timing_samples.tsv"

log() {
  echo "[$(date '+%F %T')] $*" | tee -a "$RUN_DIR/summary.txt"
}

sync_log_artifacts() {
  mkdir -p "$RUN_DIR/logs"
  local name
  for name in \
    summary.txt \
    build.log \
    build_tail.txt \
    current_no_compare.txt \
    current_compare.txt \
    report.log \
    perf_stat.txt \
    perf_report.txt \
    perf_report.err \
    perf_runner.log \
    perf_record_runner.log \
    hotspot_notes.txt \
    timing_samples.tsv
  do
    if [ -f "$RUN_DIR/$name" ]; then
      cp -f "$RUN_DIR/$name" "$RUN_DIR/logs/$name"
    fi
  done
}

count_runner_failure_markers() {
  # PsiRunner can return rc=0 even when benchmark input/compare files are missing.
  # Treat those log markers as hard gate failures before timing is interpreted.
  grep -E \
    "PsiRunner::run file:.*not exists|compareFile is not exists|compareFile error|basic_string|length_error|Aborted|Segmentation fault|terminate called" \
    "$@" 2>/dev/null | wc -l | awk '{print $1+0}'
}

stage_runtime_config() {
  local requested_config="$CONFIG"
  local runtime_config="$ROOT/PsiTraderRunner/config.yaml"
  if [ ! -f "$requested_config" ]; then
    log "ERROR CONFIG missing: $requested_config"
    write_failure_state "missing_config" "not_run" "not_run" "not_run"
    sync_log_artifacts
    exit 1
  fi
  if [ "$(readlink -f "$requested_config")" != "$(readlink -f "$runtime_config" 2>/dev/null || echo "$runtime_config")" ]; then
    cp -f "$requested_config" "$runtime_config"
    log "staged runtime config from $requested_config to $runtime_config"
  fi
  CONFIG="$runtime_config"
}

set_compare() {
  local value="$1"
  if grep -q "isCompareFile: true" "$CONFIG"; then
    sed -i "s/isCompareFile: true/isCompareFile: $value/" "$CONFIG"
  elif grep -q "isCompareFile: false" "$CONFIG"; then
    sed -i "s/isCompareFile: false/isCompareFile: $value/" "$CONFIG"
  else
    log "WARN missing isCompareFile in config"
  fi
}

now_ms() {
  local value seconds
  value=$(date +%s%3N 2>/dev/null || true)
  case "$value" in
    ""|*[!0-9]*)
      seconds=$(date +%s)
      echo $((seconds * 1000))
      ;;
    *)
      echo "$value"
      ;;
  esac
}

ms_to_seconds() {
  awk -v ms="$1" 'BEGIN { printf "%.3f", ms / 1000 }'
}

prepare_runtime_log_dir() {
  local today runner_log_dir
  today="$(date +%Y%m%d)"
  runner_log_dir="$ROOT/PsiTraderRunner/log/$today"
  if ! mkdir -p "$runner_log_dir"; then
    log "ERROR failed to prepare runtime log dir: $runner_log_dir"
    return 1
  fi
}

run_runner() {
  local label="$1"
  local log_file="$2"
  local mode="${3:-no_compare}"
  local warm_or_cold="${4:-measured}"
  local pair_index="${5:-}"
  local start_ms end_ms rc elapsed_ms elapsed_seconds compat_seconds
  start_ms=$(now_ms)
  (cd "$ROOT/PsiTraderRunner" && "$RUNNER" > "$log_file" 2>&1)
  rc=$?
  end_ms=$(now_ms)
  elapsed_ms=$((end_ms - start_ms))
  elapsed_seconds=$(ms_to_seconds "$elapsed_ms")
  compat_seconds=$(((end_ms / 1000) - (start_ms / 1000)))
  {
    echo "$label=$compat_seconds rc=$rc"
    echo "${label}_ms=$elapsed_ms"
    echo "${label}_seconds=$elapsed_seconds"
    echo "${label}_rc=$rc"
  } | tee -a "$RUN_DIR/$label.result"
  printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n" \
    "$label" "$mode" "$warm_or_cold" "$elapsed_ms" "$elapsed_seconds" "$compat_seconds" "$rc" "$log_file" "$pair_index" \
    >> "$RUN_DIR/timing_samples.tsv"
  return "$rc"
}

run_candidate_runner() {
  local label="$1"
  local log_file="$2"
  local mode="${3:-paired_candidate}"
  local warm_or_cold="${4:-measured}"
  local pair_index="${5:-}"
  local start_ms end_ms rc elapsed_ms elapsed_seconds compat_seconds
  start_ms=$(now_ms)
  (cd "$ROOT/PsiTraderRunner" && "$CANDIDATE_RUNNER" > "$log_file" 2>&1)
  rc=$?
  end_ms=$(now_ms)
  elapsed_ms=$((end_ms - start_ms))
  elapsed_seconds=$(ms_to_seconds "$elapsed_ms")
  compat_seconds=$(((end_ms / 1000) - (start_ms / 1000)))
  {
    echo "$label=$compat_seconds rc=$rc"
    echo "${label}_ms=$elapsed_ms"
    echo "${label}_seconds=$elapsed_seconds"
    echo "${label}_rc=$rc"
  } | tee -a "$RUN_DIR/$label.result"
  printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n" \
    "$label" "$mode" "$warm_or_cold" "$elapsed_ms" "$elapsed_seconds" "$compat_seconds" "$rc" "$log_file" "$pair_index" \
    >> "$RUN_DIR/timing_samples.tsv"
  return "$rc"
}

summarize_ms_samples() {
  local mode="$1"
  local warm_or_cold="$2"
  local prefix="$3"
  local out_file="$4"
  local tmp count samples_ms samples_seconds mean_ms mean_seconds median_ms median_seconds
  local stdev_ms stdev_seconds range_ms range_seconds median_index lower_index upper_index lower upper
  tmp="$RUN_DIR/${prefix}.samples_ms.tmp"
  awk -F '\t' -v mode="$mode" -v warm_or_cold="$warm_or_cold" \
    'NR > 1 && $2 == mode && $3 == warm_or_cold && $7 == "0" { print $4 }' \
    "$RUN_DIR/timing_samples.tsv" | sort -n > "$tmp"
  count=$(wc -l < "$tmp" | tr -d ' ')
  if [ "$count" -eq 0 ]; then
    rm -f "$tmp"
    return 0
  fi

  samples_ms=$(paste -sd, "$tmp")
  samples_seconds=$(awk '{ printf "%s%.3f", sep, $1 / 1000; sep="," } END { print "" }' "$tmp")
  mean_ms=$(awk -v count="$count" '{ sum += $1 } END { printf "%.3f", sum / count }' "$tmp")
  mean_seconds=$(ms_to_seconds "$mean_ms")

  if [ $((count % 2)) -eq 1 ]; then
    median_index=$((count / 2 + 1))
    median_ms=$(sed -n "${median_index}p" "$tmp")
  else
    lower_index=$((count / 2))
    upper_index=$((lower_index + 1))
    lower=$(sed -n "${lower_index}p" "$tmp")
    upper=$(sed -n "${upper_index}p" "$tmp")
    median_ms=$(awk -v lower="$lower" -v upper="$upper" 'BEGIN { printf "%.3f", (lower + upper) / 2 }')
  fi
  median_seconds=$(ms_to_seconds "$median_ms")

  stdev_ms=$(awk -v count="$count" -v mean="$mean_ms" \
    '{ diff = $1 - mean; sum += diff * diff } END { if (count > 1) printf "%.3f", sqrt(sum / (count - 1)); else printf "0.000" }' \
    "$tmp")
  stdev_seconds=$(ms_to_seconds "$stdev_ms")
  range_ms=$(awk 'NR == 1 { min = $1 } { max = $1 } END { printf "%.3f", max - min }' "$tmp")
  range_seconds=$(ms_to_seconds "$range_ms")

  {
    echo "${prefix}_sample_count=$count"
    echo "${prefix}_samples_ms=$samples_ms"
    echo "${prefix}_samples_seconds=$samples_seconds"
    echo "${prefix}_mean_ms=$mean_ms"
    echo "${prefix}_mean_seconds=$mean_seconds"
    echo "${prefix}_median_ms=$median_ms"
    echo "${prefix}_median_seconds=$median_seconds"
    echo "${prefix}_stdev_ms=$stdev_ms"
    echo "${prefix}_stdev_seconds=$stdev_seconds"
    echo "${prefix}_range_ms=$range_ms"
    echo "${prefix}_range_seconds=$range_seconds"
  } | tee -a "$out_file"

  rm -f "$tmp"
}

write_failure_state() {
  local reason="$1"
  local build_status="${2:-unknown}"
  local compare_status="${3:-not_run}"
  local timing_status="${4:-not_run}"
  local failure_mode="${5:-stop}"
  if ! command -v python3 >/dev/null 2>&1; then
    return 0
  fi
  FAILURE_REASON="$reason" BUILD_STATUS="$build_status" COMPARE_STATUS="$compare_status" TIMING_STATUS="$timing_status" FAILURE_MODE="$failure_mode" \
    RUN_DIR="$RUN_DIR" RUN_ID="$RUN_ID" python3 - <<'PY'
from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path

run_dir = Path(os.environ["RUN_DIR"])
run_dir.mkdir(parents=True, exist_ok=True)
for child in ("logs", "reports", "patches"):
    (run_dir / child).mkdir(parents=True, exist_ok=True)
now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
reason = os.environ["FAILURE_REASON"]
continue_after = os.environ.get("FAILURE_MODE", "stop") == "continue"
global_stop_reason = "" if continue_after else "remote_failed"
sample_policy = {
    "screening_measured_samples": 3,
    "promotion_measured_samples": 5,
    "bundle_audit_measured_samples": 7,
    "screening_policy": "1 warmup + 3 measured is diagnostic screening only",
    "promotion_policy": "same-harness control and candidate timing with compare pass",
    "bundle_policy": "neutral stacks need bundle audit before promotion",
}

def ensure_tsv(name: str, fieldnames: list[str]) -> None:
    path = run_dir / name
    if path.exists():
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t", lineterminator="\n")
        writer.writeheader()

ensure_tsv("profile.tsv", ["stage", "total_ms", "count", "avg_ms", "source"])
ensure_tsv("hotspots.tsv", ["rank", "stage", "total_ms", "avg_ms", "count", "score", "notes"])
ensure_tsv("attempts.tsv", ["rank", "kind", "policy_bucket", "target", "sample_count", "noise_flag", "verdict", "notes"])
ensure_tsv("cooldown.tsv", ["target", "status", "cooldown_runs_remaining", "reason", "source_profile", "notes"])
ensure_tsv(
    "patch_queue.tsv",
    [
        "rank",
        "candidate_id",
        "target",
        "patch_path",
        "policy_bucket",
        "experiment_kind",
        "stack_members",
        "touched_files",
        "hypothesis",
        "compare_result",
        "timing_summary",
        "semantic_risk",
        "stack_compatibility",
        "queue_state",
        "build_status",
        "compare_status",
        "timing_status",
        "retry_condition",
        "notes",
    ],
)
ensure_tsv(
    "neutral_pool.tsv",
    [
        "candidate_id",
        "target",
        "lane",
        "patch_path",
        "touched_files",
        "hypothesis",
        "experiment_kind",
        "stack_members",
        "correctness",
        "compare_result",
        "sample_count",
        "promotion_sample_floor",
        "bundle_audit_sample_floor",
        "aggregate_gain_seconds",
        "timing_summary",
        "semantic_risk",
        "stack_compatibility",
        "validation_status",
        "retry_condition",
        "notes",
    ],
)
ensure_tsv("retry_conditions.tsv", ["target", "status", "noise_flag", "retry_after", "required_condition", "last_exit_reason", "notes"])
ensure_tsv("timing_history.tsv", ["history_key", "recorded_at", "run_id", "host_key", "control_head", "active_gate", "kind", "sample_count", "noise_flag", "verdict", "notes"])

failure_analysis = {
    "analysis_status": "recorded",
    "recorded_at": now,
    "run_id": os.environ.get("RUN_ID", "headless"),
    "reason": reason,
    "global_stop_reason": global_stop_reason,
    "analysis_phase": "compare" if reason == "compare_failed" else "run",
    "build_status": os.environ.get("BUILD_STATUS", "unknown"),
    "compare_status": os.environ.get("COMPARE_STATUS", "not_run"),
    "timing_status": os.environ.get("TIMING_STATUS", "not_run"),
    "batch_continuation": "continue_to_next_round" if continue_after else "stopped",
    "next_round_action": "continue" if continue_after else "stop",
    "summary": (
        "Compare failed; record analysis first, then continue to the next round."
        if continue_after and reason == "compare_failed"
        else f"Remote failure recorded and batch stopped: {reason}."
    ),
}
(run_dir / "failure_analysis.json").write_text(json.dumps(failure_analysis, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
with (run_dir / "logs" / "failure_analysis.log").open("w", encoding="utf-8") as handle:
    for key in ("analysis_status", "recorded_at", "run_id", "reason", "global_stop_reason", "analysis_phase", "build_status", "compare_status", "timing_status", "batch_continuation", "next_round_action", "summary"):
        handle.write(f"{key}={failure_analysis[key]}\n")

state = {
    "status": "running" if continue_after else "stopped",
    "mode": "headless",
    "run_id": os.environ.get("RUN_ID", "headless"),
    "bundle_id": os.environ.get("RUN_ID", "headless"),
    "started_at": now,
    "updated_at": now,
    "iteration": 0,
    "accepted_count": 0,
    "neutral_count": 0,
    "rejected_count": 0,
    "noise_status": "unknown",
    "last_exit_reason": global_stop_reason,
    "latest_report": "",
    "dry_run": False,
    "sample_policy": sample_policy,
    "build_status": os.environ.get("BUILD_STATUS", "unknown"),
    "compare_status": os.environ.get("COMPARE_STATUS", "not_run"),
    "timing_status": os.environ.get("TIMING_STATUS", "not_run"),
    "timing_history_path": str(run_dir / "timing_history.tsv"),
    "timing_history_run_copy": str(run_dir / "timing_history.tsv"),
    "patch_queue_path": str(run_dir / "patch_queue.tsv"),
    "neutral_pool_path": str(run_dir / "neutral_pool.tsv"),
    "retry_conditions_path": str(run_dir / "retry_conditions.tsv"),
    "failure_analysis_path": str(run_dir / "failure_analysis.json"),
    "failure_analysis_status": failure_analysis["analysis_status"],
    "failure_analysis_reason": reason,
    "batch_continuation": failure_analysis["batch_continuation"],
    "next_round_action": failure_analysis["next_round_action"],
}
(run_dir / "run_state.json").write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
heartbeat = {
    "updated_at": now,
    "phase": "analysis" if continue_after else "stopped",
    "current_step": f"{reason}; continue_to_next_round" if continue_after else f"remote_failed:{reason}",
    "pid_or_session": str(os.getpid()),
    "last_log": str((run_dir / "logs" / "failure_analysis.log").resolve()),
}
(run_dir / "heartbeat.json").write_text(json.dumps(heartbeat, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PY
}

write_headless_control_loop() {
  local control_head="$1"
  local control_median_ms="$2"
  local control_samples_ms="$3"
  local control_samples_seconds="$4"
  local control_noise="$5"
  local no_compare_count="$6"
  local compare_rc="$7"
  mkdir -p "$HEADLESS_CONTROL_DIR" "$HEADLESS_CONTROL_DIR/logs" "$HEADLESS_CONTROL_DIR/reports"
  export HEADLESS_CONTROL_DIR ROOT="$ROOT" CONTROL_HEAD="$control_head" CONTROL_MEDIAN_MS="$control_median_ms" \
    CONTROL_SAMPLES_MS="$control_samples_ms" CONTROL_SAMPLES_SECONDS="$control_samples_seconds" \
    CONTROL_NOISE="$control_noise" NO_COMPARE_COUNT="$no_compare_count" COMPARE_RC="$compare_rc" RUN_ID SCRIPT_DIR
  python3 - <<'PY'
from __future__ import annotations

import csv
import json
import os
import shutil
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

out_dir = Path(os.environ["HEADLESS_CONTROL_DIR"])
out_dir.mkdir(parents=True, exist_ok=True)
scripts_dir = Path(os.environ["SCRIPT_DIR"]).resolve()
sys.path.insert(0, str(scripts_dir))

from psi_timing_history import (  # noqa: E402
    default_host_key,
    history_rows_from_attempt_rows,
    per_run_history_path,
    shared_history_path_for_output,
    write_history_artifacts,
)

def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

def format_float(value: float) -> str:
    return f"{value:.3f}"

def parse_float_list(raw: str) -> list[float]:
    return [float(part) for part in raw.split(",") if part]

def percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (len(sorted_values) - 1) * (pct / 100.0)
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    fraction = position - lower
    return sorted_values[lower] * (1.0 - fraction) + sorted_values[upper] * fraction

def stats_from_ms(samples_ms: list[float], fallback_median_ms: float, noise_flag: str) -> dict[str, str]:
    if not samples_ms:
        median_ms = fallback_median_ms
        median_seconds = median_ms / 1000.0
        return {
            "sample_count": "0",
            "samples_ms": "",
            "samples": "",
            "mean_ms": format_float(median_ms),
            "mean_seconds": format_float(median_seconds),
            "median_ms": format_float(median_ms),
            "median_seconds": format_float(median_seconds),
            "mad_ms": "",
            "mad_seconds": "",
            "iqr_ms": "",
            "iqr_seconds": "",
            "stdev_ms": "",
            "stdev_seconds": "",
            "range_ms": "",
            "range_seconds": "",
            "noise_flag": noise_flag,
        }

    ordered = sorted(samples_ms)
    median_ms = statistics.median(ordered)
    mean_ms = statistics.mean(ordered)
    deviations = [abs(sample - median_ms) for sample in ordered]
    mad_ms = statistics.median(deviations)
    iqr_ms = percentile(ordered, 75.0) - percentile(ordered, 25.0)
    stdev_ms = statistics.stdev(ordered) if len(ordered) > 1 else 0.0
    range_ms = max(ordered) - min(ordered)
    return {
        "sample_count": str(len(ordered)),
        "samples_ms": ",".join(f"{sample:g}" for sample in ordered),
        "samples": ",".join(f"{sample / 1000.0:g}" for sample in ordered),
        "mean_ms": format_float(mean_ms),
        "mean_seconds": format_float(mean_ms / 1000.0),
        "median_ms": format_float(median_ms),
        "median_seconds": format_float(median_ms / 1000.0),
        "mad_ms": format_float(mad_ms),
        "mad_seconds": format_float(mad_ms / 1000.0),
        "iqr_ms": format_float(iqr_ms),
        "iqr_seconds": format_float(iqr_ms / 1000.0),
        "stdev_ms": format_float(stdev_ms),
        "stdev_seconds": format_float(stdev_ms / 1000.0),
        "range_ms": format_float(range_ms),
        "range_seconds": format_float(range_ms / 1000.0),
        "noise_flag": noise_flag,
    }

def merged_history_stats(rows: list[dict[str, str]], fallback_noise: str) -> dict[str, str]:
    if not rows:
        return {}
    merged_samples: list[float] = []
    for row in rows:
        merged_samples.extend(parse_float_list(str(row.get("samples_ms", ""))))
    fallback_median = float(rows[0].get("median_ms", "0") or 0)
    noise_flag = str(rows[0].get("noise_flag") or fallback_noise or "ok")
    return stats_from_ms(merged_samples, fallback_median, noise_flag)

def write_patch_manifest(
    out_dir: Path,
    root: Path,
    patch_queue_rows: list[dict[str, str]],
) -> dict[str, object]:
    patch_dir = out_dir / "patches"
    patch_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = patch_dir / "current_worktree.patch"
    manifest_path = patch_dir / "patch_manifest.json"

    tracked_result = subprocess.run(
        ["git", "-C", str(root), "diff", "--name-only", "HEAD", "--", "."],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    untracked_result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--others", "--exclude-standard"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    status_result = subprocess.run(
        ["git", "-C", str(root), "status", "--short", "--untracked-files=normal"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    tracked_changed_files = [line.strip() for line in tracked_result.stdout.splitlines() if line.strip()]
    untracked_files = [line.strip() for line in untracked_result.stdout.splitlines() if line.strip()]
    status_lines = [line.rstrip() for line in status_result.stdout.splitlines() if line.rstrip()]
    dirty_files = tracked_changed_files + untracked_files
    patch_queue_files = sorted(
        {
            str(row.get("touched_files") or "").strip()
            for row in patch_queue_rows
            if str(row.get("touched_files") or "").strip()
        }
    )

    snapshot_status = "captured"
    snapshot_rc = 0
    with snapshot_path.open("wb") as handle:
        snapshot_proc = subprocess.run(
            ["git", "-C", str(root), "-c", "core.quotePath=false", "diff", "--binary", "HEAD", "--", "."],
            check=False,
            stdout=handle,
            stderr=subprocess.PIPE,
        )
        snapshot_rc = snapshot_proc.returncode
    if snapshot_rc != 0:
        snapshot_path.write_text("git diff snapshot unavailable\n", encoding="utf-8")
        snapshot_status = "unavailable"

    dirty_risk = "dirty" if dirty_files or status_lines else "clean"
    if snapshot_status != "captured":
        dirty_risk = "evidence_risk"

    manifest: dict[str, object] = {
        "snapshot_status": snapshot_status,
        "snapshot_source": "git diff --binary HEAD -- .",
        "snapshot_rc": snapshot_rc,
        "dirty_risk": dirty_risk,
        "changed_files_count": len(dirty_files),
        "changed_files": dirty_files,
        "tracked_changed_files": tracked_changed_files,
        "untracked_files": untracked_files,
        "status_lines": status_lines,
        "patch_queue_touched_files": patch_queue_files,
        "patch_queue_touched_files_count": len(patch_queue_files),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    readme_path = patch_dir / "README.txt"
    readme_path.write_text(
        "Patch artifacts for the current headless batch.\n"
        "\n"
        "- `current_worktree.patch` snapshots the tracked business-repo diff used for this run.\n"
        "- Candidate patch files are copied from that snapshot according to `patch_queue.tsv` so queue entries have replayable paths.\n",
        encoding="utf-8",
    )

    return manifest

PROMOTION_SAMPLE_FLOOR = 5
BUNDLE_AUDIT_SAMPLE_FLOOR = 7
SCREENING_SAMPLE_FLOOR = 3
sample_policy = {
    "screening_measured_samples": SCREENING_SAMPLE_FLOOR,
    "promotion_measured_samples": PROMOTION_SAMPLE_FLOOR,
    "bundle_audit_measured_samples": BUNDLE_AUDIT_SAMPLE_FLOOR,
    "screening_policy": "1 warmup + 3 measured is diagnostic screening only",
    "promotion_policy": "same-harness control and candidate timing with compare pass",
    "bundle_policy": "neutral stacks need bundle audit before promotion",
}

control_head = os.environ["CONTROL_HEAD"]
control_median_ms = float(os.environ.get("CONTROL_MEDIAN_MS", "0") or 0)
control_samples_ms = [float(part) for part in os.environ.get("CONTROL_SAMPLES_MS", "").split(",") if part]
control_noise = os.environ.get("CONTROL_NOISE", "ok")
no_compare_count = os.environ.get("NO_COMPARE_COUNT", "0")
compare_rc = os.environ.get("COMPARE_RC", "0")
compare_pass = str(compare_rc) == "0"
run_id = os.environ.get("RUN_ID", "headless")
host_key = os.environ.get("PSI_TIMING_HOST_KEY") or os.environ.get("HOST_KEY") or default_host_key()
recorded_at = now()
control_stats = stats_from_ms(control_samples_ms, control_median_ms, control_noise)
control_median_ms_text = control_stats["median_ms"]
control_median_seconds = control_stats["median_seconds"]
noise_status = "NOISY" if str(control_noise).upper() == "NOISY" else "ok"
failure_analysis_path = out_dir / "failure_analysis.json"
failure_analysis: dict[str, object] = {}
if failure_analysis_path.exists():
    failure_analysis = json.loads(failure_analysis_path.read_text(encoding="utf-8-sig"))
last_exit_reason = str(failure_analysis.get("global_stop_reason") or "")
batch_continuation = str(failure_analysis.get("batch_continuation") or "continue_to_next_round")
next_round_action = str(failure_analysis.get("next_round_action") or "continue")
batch_status = "completed"
run_status = "stopped" if last_exit_reason else "running"

parsed_profile_rows: list[dict[str, object]] = []
try:
    from psi_log_profile import collect_events, summarize  # noqa: E402

    parsed_events, _parse_stats = collect_events(out_dir, 300000, False)
    parsed_profile_rows = summarize(parsed_events)
except Exception as exc:  # pragma: no cover - fallback for log parser regressions
    print(f"profile_parse_warning={exc}", file=sys.stderr)

profile_rows = parsed_profile_rows or [
    {
        "stage": "headless_no_compare",
        "total_ms": control_median_ms_text,
        "count": no_compare_count,
        "avg_ms": control_median_ms_text,
        "source": "psi_headless_remote.sh",
    }
]
with (out_dir / "profile.tsv").open("w", encoding="utf-8", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=["stage", "total_ms", "count", "avg_ms", "source"], delimiter="\t", lineterminator="\n")
    writer.writeheader()
    writer.writerows(profile_rows)

hotspot_rows = [
    {
        "rank": rank,
        "stage": row["stage"],
        "total_ms": row["total_ms"],
        "avg_ms": row["avg_ms"],
        "count": row["count"],
        "score": f"{float(row['total_ms']) / float(profile_rows[0]['total_ms']) if profile_rows and float(profile_rows[0]['total_ms']) else 0.0:.6f}",
        "notes": "relative_to_top_total" if row["stage"] != "headless_no_compare" else "screening_only",
    }
    for rank, row in enumerate(profile_rows, start=1)
]
with (out_dir / "hotspots.tsv").open("w", encoding="utf-8", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=["rank", "stage", "total_ms", "avg_ms", "count", "score", "notes"], delimiter="\t", lineterminator="\n")
    writer.writeheader()
    writer.writerows(hotspot_rows)

from psi_control_loop import build_attempts  # noqa: E402
from psi_timing_analysis import (  # noqa: E402
    PairedTimingEvidence,
    evidence_fields,
    summarize_paired_timing,
)

paired_control_rows: list[tuple[int, float]] = []
paired_candidate_rows: list[tuple[int, float]] = []
timing_samples_path = out_dir / "timing_samples.tsv"
if timing_samples_path.exists():
    with timing_samples_path.open("r", encoding="utf-8-sig", newline="") as _handle:
        _reader = csv.DictReader(_handle, delimiter="\t")
        for _row in _reader:
            _mode = (_row.get("mode") or "").strip()
            _rc = (_row.get("rc") or "").strip()
            _pair_index_raw = (_row.get("pair_index") or "").strip()
            _elapsed = (_row.get("elapsed_ms") or "").strip()
            if _rc != "0" or not _pair_index_raw or not _elapsed:
                continue
            try:
                _pair_index = int(_pair_index_raw)
                _elapsed_ms = float(_elapsed)
            except (TypeError, ValueError):
                continue
            if _mode == "paired_control":
                paired_control_rows.append((_pair_index, _elapsed_ms))
            elif _mode == "paired_candidate":
                paired_candidate_rows.append((_pair_index, _elapsed_ms))

paired_control_rows.sort(key=lambda item: item[0])
paired_candidate_rows.sort(key=lambda item: item[0])
_paired_pair_indexes = [pi for pi, _ in paired_control_rows]
_paired_control_values = [ms for _, ms in paired_control_rows]
_paired_candidate_values = [ms for _, ms in paired_candidate_rows]
paired_evidence: PairedTimingEvidence | None = None
candidate_runner = os.environ.get("CANDIDATE_RUNNER", "").strip()
paired_evidence_status = "present"
paired_evidence_reason = ""
if not candidate_runner:
    paired_evidence_status = "missing"
    paired_evidence_reason = "CANDIDATE_RUNNER was not provided; paired A/B evidence is required for acceptance"
elif not os.path.isfile(candidate_runner) or not os.access(candidate_runner, os.X_OK):
    paired_evidence_status = "missing"
    paired_evidence_reason = f"CANDIDATE_RUNNER is missing or not executable: {candidate_runner}"
elif not _paired_control_values or not _paired_candidate_values:
    paired_evidence_status = "missing"
    paired_evidence_reason = "no successful paired control/candidate timing rows were collected"
elif len(_paired_control_values) != len(_paired_candidate_values):
    paired_evidence_status = "missing"
    paired_evidence_reason = (
        f"paired row count mismatch: control={len(_paired_control_values)} "
        f"candidate={len(_paired_candidate_values)}"
    )
if (
    _paired_control_values
    and _paired_candidate_values
    and len(_paired_control_values) == len(_paired_candidate_values)
):
    paired_evidence = summarize_paired_timing(
        _paired_control_values,
        _paired_candidate_values,
        build_pass=True,
        compare_pass=compare_pass,
        verdict_context=run_id,
    )
    paired_evidence_status = "present"

paired_evidence_by_target: dict[str, PairedTimingEvidence] = {}
paired_target_label: str = ""
if paired_evidence is not None:
    # pre-compute attempt rows (without paired evidence) to discover the first
    # non-control target so we can key the paired evidence dict by it.
    _preview_rows = build_attempts(
        profile_rows,
        control_head,
        float(control_median_seconds),
        control_samples_ms,
        "ms",
        "compare pass; evaluate candidate against the run-specific control baseline recorded in attempts.tsv",
        recorded_at,
        3.0,
        1.0,
    )
    for _row in _preview_rows:
        if _row.get("kind") != "control":
            paired_target_label = str(_row.get("target") or "")
            break
    if paired_target_label:
        paired_evidence_by_target[paired_target_label] = paired_evidence

attempt_rows = build_attempts(
    profile_rows,
    control_head,
    float(control_median_seconds),
    control_samples_ms,
    "ms",
    "compare pass; evaluate candidate against the run-specific control baseline recorded in attempts.tsv",
    recorded_at,
    3.0,
    1.0,
    paired_evidence_by_target=paired_evidence_by_target or None,
)
if paired_evidence is not None and not paired_evidence_by_target:
    paired_fields = evidence_fields(paired_evidence)
    target = os.environ.get("CANDIDATE_TARGET", "").strip() or "candidate_runner"
    candidate_id = os.environ.get("CANDIDATE_ID", "").strip() or "candidate_runner"
    lane = os.environ.get("CANDIDATE_LANE", "").strip() or "screening"
    touched_files = os.environ.get("CANDIDATE_TOUCHED_FILES", "").strip() or target
    attempt_rows.append(
        {
            "rank": str(len(attempt_rows)),
            "kind": "single",
            "policy_bucket": lane,
            "experiment_kind": "single",
            "lane": lane,
            "target": target,
            "stack_members": target,
            "candidate_id": candidate_id,
            "patch_path": "patches/current_worktree.patch",
            "touched_files": touched_files,
            "hypothesis": "candidate runner paired A/B evidence",
            "compare_result": "pass" if compare_pass else "failed",
            "timing_summary": (
                f"paired_sample_count={paired_evidence.paired_sample_count}; "
                f"median_delta_ms={paired_fields.get('median_delta_ms', '')}; "
                f"noise_flag={paired_evidence.noise_flag}"
            ),
            "semantic_risk": "unknown",
            "stack_compatibility": "single",
            "retry_condition": paired_evidence.reason,
            "sample_unit": "ms",
            "warm_or_cold": "measured",
            "samples_ms": paired_fields.get("candidate_samples_ms", ""),
            "samples": ",".join(
                f"{float(value) / 1000.0:g}" for value in paired_evidence.candidate_samples_ms
            ),
            "sample_count": str(paired_evidence.candidate_sample_count),
            "mean_ms": f"{sum(paired_evidence.candidate_samples_ms) / paired_evidence.candidate_sample_count:.3f}"
            if paired_evidence.candidate_sample_count
            else "",
            "median_ms": "",
            "delta_ms": paired_fields.get("median_delta_ms", ""),
            "delta_seconds": paired_fields.get("median_delta_seconds", ""),
            "correctness": "pass" if compare_pass else "failed",
            "acceptance_policy": "compare pass plus paired bootstrap/permutation evidence",
            "evidence_status": "paired_present",
            "promotion_sample_floor": str(PROMOTION_SAMPLE_FLOOR),
            "bundle_audit_sample_floor": str(BUNDLE_AUDIT_SAMPLE_FLOOR),
            "notes": paired_evidence.reason,
            "stage": target,
            "recorded_at": recorded_at,
            "control_head": control_head,
            **paired_fields,
            "verdict": paired_evidence.verdict,
            "noise_flag": paired_evidence.noise_flag,
        }
    )
from psi_attempts_schema import ATTEMPTS_FIELDNAMES  # noqa: E402

attempt_fieldnames = ATTEMPTS_FIELDNAMES
with (out_dir / "attempts.tsv").open("w", encoding="utf-8", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=attempt_fieldnames, delimiter="\t", lineterminator="\n")
    writer.writeheader()
    writer.writerows(attempt_rows)

cooldown_rows = [
    {
        "target": "generateTable",
        "status": "cooldown",
        "cooldown_runs_remaining": "3",
        "reason": "cooldown until narrower evidence exists",
        "source_profile": "headless",
        "notes": "screening-only headless run",
    }
]
with (out_dir / "cooldown.tsv").open("w", encoding="utf-8", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=["target", "status", "cooldown_runs_remaining", "reason", "source_profile", "notes"], delimiter="\t", lineterminator="\n")
    writer.writeheader()
    writer.writerows(cooldown_rows)

patch_queue_rows = [
    {
        "rank": row["rank"],
        "candidate_id": f"{row['rank']}:{row['target']}",
        "target": row["target"],
        "patch_path": f"patches/{row['rank']}_{row['policy_bucket']}.patch",
        "policy_bucket": row["policy_bucket"],
        "experiment_kind": row["experiment_kind"],
        "stack_members": row["stack_members"],
        "touched_files": row["stack_members"] or row["target"],
        "hypothesis": row["notes"],
        "compare_result": "pass" if str(compare_rc) == "0" else "failed",
        "timing_summary": (
            "planned; candidate timing pending"
            if not row.get("sample_count")
            else f"sample_count={row['sample_count']}; median_ms={row['median_ms']}; delta_ms={row['delta_ms']}; noise_flag={row['noise_flag']}"
        ),
        "semantic_risk": "low" if row["policy_bucket"] != "reserve" else "medium",
        "stack_compatibility": "stackable" if row["experiment_kind"] == "neutral_stack" else "single",
        "queue_state": (
            "NOISY_PENDING"
            if noise_status == "NOISY"
            else "compare_failed"
            if not compare_pass
            else "bundle_audit_pending"
            if row["experiment_kind"] == "neutral_stack"
            else "candidate_planned"
        ),
        "build_status": "pass",
        "compare_status": "pass" if str(compare_rc) == "0" else "failed",
        "timing_status": "planned",
        "measured_samples": row["sample_count"] or "",
        "required_samples": str(BUNDLE_AUDIT_SAMPLE_FLOOR if row["experiment_kind"] == "neutral_stack" else PROMOTION_SAMPLE_FLOOR),
        "retry_condition": "rerun when same-host jitter is below threshold" if noise_status == "NOISY" else "run candidate patch after the current snapshot is replayed",
        "notes": row["notes"],
    }
    for row in attempt_rows
    if row["kind"] != "control"
]
current_patch = out_dir / "patches" / "current_worktree.patch"
for row in patch_queue_rows:
    patch_path = out_dir / row["patch_path"]
    patch_path.parent.mkdir(parents=True, exist_ok=True)
    if current_patch.exists():
        shutil.copy2(current_patch, patch_path)
    elif not patch_path.exists():
        patch_path.write_text("git diff snapshot unavailable\n", encoding="utf-8")
with (out_dir / "patch_queue.tsv").open("w", encoding="utf-8", newline="") as handle:
    fieldnames = [
        "rank",
        "candidate_id",
        "target",
        "patch_path",
        "policy_bucket",
        "experiment_kind",
        "stack_members",
        "touched_files",
        "hypothesis",
        "compare_result",
        "timing_summary",
        "semantic_risk",
        "stack_compatibility",
        "queue_state",
        "build_status",
        "compare_status",
        "timing_status",
        "measured_samples",
        "required_samples",
        "retry_condition",
        "notes",
    ]
    writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t", lineterminator="\n")
    writer.writeheader()
    writer.writerows(patch_queue_rows)

neutral_pool_rows = [
    {
        "candidate_id": f"{row['rank']}:{row['target']}",
        "target": row["target"],
        "lane": row["policy_bucket"],
        "patch_path": f"patches/{row['rank']}_{row['policy_bucket']}.patch",
        "touched_files": row["stack_members"] or row["target"],
        "hypothesis": row["notes"],
        "experiment_kind": row["experiment_kind"],
        "stack_members": row["stack_members"],
        "correctness": "pass" if str(compare_rc) == "0" else "failed",
        "compare_result": "pass" if str(compare_rc) == "0" else "failed",
        "sample_count": row["sample_count"] or "",
        "promotion_sample_floor": str(PROMOTION_SAMPLE_FLOOR),
        "bundle_audit_sample_floor": str(BUNDLE_AUDIT_SAMPLE_FLOOR),
        "aggregate_gain_seconds": row["expected_delta_seconds"],
        "timing_summary": (
            "planned; neutral stack timing pending"
            if not row.get("sample_count")
            else f"sample_count={row['sample_count']}; median_ms={row['median_ms']}; delta_ms={row['delta_ms']}; noise_flag={row['noise_flag']}"
        ),
        "semantic_risk": "low" if row["policy_bucket"] != "reserve" else "medium",
        "stack_compatibility": "stackable" if row["experiment_kind"] == "neutral_stack" else "single",
        "validation_status": "bundle_audit_pending",
        "retry_condition": "rerun when same-host jitter is below threshold" if noise_status == "NOISY" else "run the stack patch after the current snapshot is replayed",
        "notes": row["notes"],
    }
    for row in attempt_rows
    if compare_pass and (row["experiment_kind"] == "neutral_stack" or row["verdict"] == "neutral")
]
with (out_dir / "neutral_pool.tsv").open("w", encoding="utf-8", newline="") as handle:
    fieldnames = [
        "candidate_id",
        "target",
        "lane",
        "patch_path",
        "touched_files",
        "hypothesis",
        "experiment_kind",
        "stack_members",
        "correctness",
        "compare_result",
        "sample_count",
        "promotion_sample_floor",
        "bundle_audit_sample_floor",
        "aggregate_gain_seconds",
        "timing_summary",
        "semantic_risk",
        "stack_compatibility",
        "validation_status",
        "retry_condition",
        "notes",
    ]
    writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t", lineterminator="\n")
    writer.writeheader()
    writer.writerows(neutral_pool_rows)

retry_rows = [
    {
        "target": row["target"],
        "status": "NOISY_PENDING" if noise_status == "NOISY" else "ready_for_evidence",
        "noise_flag": noise_status,
        "retry_after": "next quiet same-host window" if noise_status == "NOISY" else "after candidate patch is prepared",
        "required_condition": f"collect >= {PROMOTION_SAMPLE_FLOOR} measured control and candidate samples; >= {BUNDLE_AUDIT_SAMPLE_FLOOR} for bundle audit",
        "last_exit_reason": last_exit_reason,
        "notes": "NOISY pauses judgment; compare pass alone is not promotion proof.",
    }
    for row in attempt_rows
    if row["kind"] != "control"
]
with (out_dir / "retry_conditions.tsv").open("w", encoding="utf-8", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=["target", "status", "noise_flag", "retry_after", "required_condition", "last_exit_reason", "notes"], delimiter="\t", lineterminator="\n")
    writer.writeheader()
    writer.writerows(retry_rows)

shared_history_out = shared_history_path_for_output(out_dir)
per_run_history_out = per_run_history_path(out_dir)
history_rows = history_rows_from_attempt_rows(
    attempt_rows,
    bundle_id=run_id,
    run_id=run_id,
    host_key=host_key,
    control_head=control_head,
    active_gate="headless remote batch",
    warm_or_cold="measured",
    sample_unit="ms",
    source_attempts_path=str(out_dir / "attempts.tsv"),
    recorded_at=recorded_at,
    default_noise_flag=control_stats["noise_flag"],
)
write_history_artifacts(shared_history_out, per_run_history_out, history_rows)
comparison_summary_path = out_dir / "comparison_summary.json"
accepted_attempt_rows = [row for row in attempt_rows if row.get("verdict") == "accepted"]
candidate_history_rows = [row for row in history_rows if row.get("kind") != "control"]
accepted_history_rows = [row for row in candidate_history_rows if row.get("verdict") == "accepted"]
comparison_candidate_stats = merged_history_stats(accepted_history_rows, control_stats["noise_flag"]) if accepted_history_rows else {}
comparison_updated_baseline_stats = comparison_candidate_stats if accepted_history_rows else {}
patch_manifest = write_patch_manifest(out_dir, Path(os.environ["ROOT"]), patch_queue_rows)
patch_manifest_path = out_dir / "patches" / "patch_manifest.json"

# Paired-timing evidence block injected into comparison_summary.json when
# interleaved A/B samples were collected in this run.
paired_block: dict[str, object] = {}
paired_samples_block: list[dict[str, object]] = []
paired_fields: dict[str, str] = {}
if paired_evidence is not None:
    paired_fields = evidence_fields(paired_evidence)
    for idx, pair_index in enumerate(_paired_pair_indexes):
        control_ms = _paired_control_values[idx]
        candidate_ms = _paired_candidate_values[idx]
        paired_samples_block.append(
            {
                "pair_index": pair_index,
                "control_ms": control_ms,
                "candidate_ms": candidate_ms,
                "delta_ms": control_ms - candidate_ms,
            }
        )
    paired_block = {
        "paired_evidence_status": paired_evidence_status,
        "paired_evidence_reason": paired_evidence_reason,
        "paired_sample_count": paired_evidence.paired_sample_count,
        "paired_deltas_ms": paired_fields.get("paired_deltas_ms", ""),
        "median_delta_ms": paired_fields.get("median_delta_ms", ""),
        "paired_stdev_ms": paired_fields.get("paired_stdev_ms", ""),
        "paired_range_ms": paired_fields.get("paired_range_ms", ""),
        "permutation_p_value": paired_fields.get("permutation_p_value", ""),
        "bootstrap_ci_low_ms": paired_fields.get("bootstrap_ci_low_ms", ""),
        "bootstrap_ci_high_ms": paired_fields.get("bootstrap_ci_high_ms", ""),
        "noise_flag": paired_evidence.noise_flag,
    }

comparison_summary = {
    "schema": "psi_headless_comparison_summary_v1",
    "run_id": run_id,
    "recorded_at": recorded_at,
    "host_key": host_key,
    "control_head": control_head,
    "active_gate": "headless remote batch",
    "control_role": "old_control",
    "candidate_role": "accepted_candidate" if accepted_attempt_rows else "screening_candidate",
    "updated_baseline_role": "updated_baseline" if accepted_attempt_rows else "pending",
    "compare_rc": int(compare_rc) if str(compare_rc).isdigit() else compare_rc,
    "compare_result": "pass" if compare_pass else "failed",
    "decision": "needs_paired_evidence" if paired_evidence_status == "missing" else "screening_only",
    "accepted": False,
    "paired_evidence_status": paired_evidence_status,
    "paired_evidence_reason": paired_evidence_reason,
    "timing_verdict": "needs_paired_evidence" if paired_evidence_status == "missing" else "screening_only",
    "timing_verdict_reason": paired_evidence_reason if paired_evidence_status == "missing" else "",
    "timing_verdict_method": "paired_ab_required",
    "accepted_attempt_count": len(accepted_attempt_rows),
    "candidate_history_count": len(candidate_history_rows),
    "accepted_history_count": len(accepted_history_rows),
    "control": {
        "sample_count": control_stats["sample_count"],
        "samples_ms": control_stats["samples_ms"],
        "median_ms": control_stats["median_ms"],
        "median_seconds": control_stats["median_seconds"],
        "stdev_ms": control_stats["stdev_ms"],
        "range_ms": control_stats["range_ms"],
        "noise_flag": control_stats["noise_flag"],
    },
    "candidate": comparison_candidate_stats,
    "updated_baseline": comparison_updated_baseline_stats,
    "artifact_paths": {
        "attempts": str(out_dir / "attempts.tsv"),
        "patch_queue": str(out_dir / "patch_queue.tsv"),
        "neutral_pool": str(out_dir / "neutral_pool.tsv"),
        "retry_conditions": str(out_dir / "retry_conditions.tsv"),
        "timing_history": str(shared_history_out),
        "timing_history_run_copy": str(per_run_history_out),
        "failure_analysis": str(failure_analysis_path) if failure_analysis else "",
        "patch_manifest": str(patch_manifest_path),
    },
    "notes": (
        "Accepted decisions require compare pass plus same-harness candidate timing history; "
        "old control, candidate, and updated baseline are recorded separately."
    ),
}
if paired_evidence is not None:
    comparison_summary["timing_verdict"] = paired_evidence.verdict
    comparison_summary["timing_verdict_reason"] = paired_evidence.reason
    comparison_summary["timing_verdict_method"] = paired_evidence.verdict_method
    comparison_summary["paired_sample_count"] = paired_evidence.paired_sample_count
    comparison_summary["paired_deltas_ms"] = paired_fields.get("paired_deltas_ms", "")
    comparison_summary["median_delta_ms"] = paired_fields.get("median_delta_ms", "")
    comparison_summary["bootstrap_ci_low_ms"] = paired_fields.get("bootstrap_ci_low_ms", "")
    comparison_summary["bootstrap_ci_high_ms"] = paired_fields.get("bootstrap_ci_high_ms", "")
    comparison_summary["permutation_p_value"] = paired_fields.get("permutation_p_value", "")
    comparison_summary["paired_samples"] = paired_samples_block
    comparison_summary["paired"] = paired_block
    paired_accepted = paired_evidence.verdict == "accepted" and compare_pass
    comparison_summary["decision"] = paired_evidence.verdict if paired_accepted else (
        "accepted" if accepted_attempt_rows else paired_evidence.verdict
    )
    comparison_summary["accepted"] = bool(paired_accepted)
else:
    comparison_summary["decision"] = "needs_paired_evidence" if paired_evidence_status == "missing" else "screening_only"
    comparison_summary["paired_sample_count"] = 0
    comparison_summary["paired_deltas_ms"] = ""
    comparison_summary["paired_samples"] = paired_samples_block
    comparison_summary["paired"] = {
        "paired_evidence_status": paired_evidence_status,
        "paired_evidence_reason": paired_evidence_reason,
        "paired_sample_count": 0,
    }

comparison_summary_path.write_text(json.dumps(comparison_summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

comparison_accepted = comparison_summary.get("accepted") is True
comparison_timing_verdict = str(comparison_summary.get("timing_verdict") or "")
comparison_decision = str(comparison_summary.get("decision") or "")
if comparison_accepted:
    run_state_timing_status = "accepted"
elif paired_evidence_status == "missing":
    run_state_timing_status = "needs_paired_evidence"
elif comparison_timing_verdict and comparison_timing_verdict != "accepted":
    run_state_timing_status = comparison_timing_verdict
else:
    run_state_timing_status = "screening_only"
if comparison_decision and (comparison_decision != "accepted" or comparison_accepted):
    run_state_comparison_decision = comparison_decision
else:
    run_state_comparison_decision = run_state_timing_status
run_state = {
    "status": run_status,
    "batch_status": batch_status,
    "mode": "headless",
    "run_id": run_id,
    "bundle_id": run_id,
    "host_key": host_key,
    "warm_or_cold": "measured",
    "started_at": recorded_at,
    "updated_at": recorded_at,
    "control_head": control_head,
    "active_gate": "headless remote batch",
    "iteration": int(no_compare_count),
    "accepted_count": 0,
    "neutral_count": 0,
    "rejected_count": 0,
    "consecutive_no_accepted": 1,
    "epsilon": None,
    "ucb95_expected_delta": None,
    "noise_status": noise_status,
    "last_exit_reason": last_exit_reason,
    "latest_report": "",
    "dry_run": False,
    "timing_history_path": str(shared_history_out),
    "timing_history_run_copy": str(per_run_history_out),
    "sample_policy": sample_policy,
    "build_status": "pass",
    "compare_status": "pass" if str(compare_rc) == "0" else "failed",
    "timing_status": run_state_timing_status,
    "comparison_decision": run_state_comparison_decision,
    "comparison_accepted": comparison_accepted,
    "paired_evidence_status": paired_evidence_status,
    "paired_evidence_reason": paired_evidence_reason,
    "timing_verdict": comparison_summary.get("timing_verdict", ""),
    "timing_verdict_reason": comparison_summary.get("timing_verdict_reason", ""),
    "comparison_summary_path": str(comparison_summary_path),
    "patch_queue_path": str(out_dir / "patch_queue.tsv"),
    "neutral_pool_path": str(out_dir / "neutral_pool.tsv"),
    "retry_conditions_path": str(out_dir / "retry_conditions.tsv"),
    "patch_manifest_path": str(out_dir / "patches" / "patch_manifest.json"),
    "patch_snapshot_status": patch_manifest.get("snapshot_status", ""),
    "patch_dirty_risk": patch_manifest.get("dirty_risk", ""),
    "patch_changed_files_count": patch_manifest.get("changed_files_count", 0),
    "patch_changed_files": patch_manifest.get("changed_files", []),
    "failure_analysis_path": str(failure_analysis_path) if failure_analysis else "",
    "failure_analysis_status": failure_analysis.get("analysis_status", ""),
    "failure_analysis_reason": failure_analysis.get("reason", ""),
    "batch_continuation": batch_continuation,
    "next_round_action": next_round_action,
}
with (out_dir / "run_state.json").open("w", encoding="utf-8") as handle:
    json.dump(run_state, handle, indent=2, ensure_ascii=False)
    handle.write("\n")

heartbeat = {
    "updated_at": recorded_at,
    "phase": "batch_complete" if not last_exit_reason else "stopped",
    "current_step": batch_continuation if not last_exit_reason else last_exit_reason,
    "pid_or_session": str(os.getpid()),
    "last_log": str(out_dir / "summary.txt"),
}
with (out_dir / "heartbeat.json").open("w", encoding="utf-8") as handle:
    json.dump(heartbeat, handle, indent=2, ensure_ascii=False)
    handle.write("\n")

PY
}

log "run_id=$RUN_ID"
log "root=$ROOT"

if [ -f "$ENV_FILE" ]; then
  # shellcheck disable=SC1090
  source "$ENV_FILE"
else
  log "ERROR missing env file: $ENV_FILE"
  write_failure_state "missing_env_file" "not_run" "not_run" "not_run"
  sync_log_artifacts
  exit 1
fi

log "waiting for existing PsiTraderRunner processes"
RUNNER_PROCESS_NAME="$(basename "$RUNNER")"
for _ in $(seq 1 60); do
  if pgrep -x "$RUNNER_PROCESS_NAME" >/dev/null 2>&1; then
    sleep 10
  else
    break
  fi
done

if pgrep -x "$RUNNER_PROCESS_NAME" >/dev/null 2>&1; then
  log "ERROR existing PsiTraderRunner still running after wait"
  write_failure_state "runner_busy" "not_run" "not_run" "not_run"
  sync_log_artifacts
  exit 1
fi

stage_runtime_config
set_compare false
grep "isCompareFile" "$CONFIG" | tee -a "$RUN_DIR/summary.txt"
if ! prepare_runtime_log_dir; then
  write_failure_state "runtime_log_dir_failed" "not_run" "not_run" "not_run"
  sync_log_artifacts
  exit 1
fi

log "building"
if [ ! -f "$BUILD_DIR/CMakeCache.txt" ]; then
  log "configuring build dir: $BUILD_DIR"
  mkdir -p "$BUILD_DIR"
  configure_status=0
  if [ -n "${CMAKE_CONFIGURE_FLAGS:-}" ]; then
    # Intentionally split extra CMake flags on shell words; callers must avoid
    # spaces inside individual -D values.
    # shellcheck disable=SC2086
    (cd "$ROOT" && cmake -S "$ROOT" -B "$BUILD_DIR" -DCMAKE_BUILD_TYPE="${CMAKE_BUILD_TYPE:-RelWithDebInfo}" -DCMAKE_EXPORT_COMPILE_COMMANDS=ON $CMAKE_CONFIGURE_FLAGS > "$RUN_DIR/configure.log" 2>&1) || configure_status=$?
  else
    (cd "$ROOT" && cmake -S "$ROOT" -B "$BUILD_DIR" -DCMAKE_BUILD_TYPE="${CMAKE_BUILD_TYPE:-RelWithDebInfo}" -DCMAKE_EXPORT_COMPILE_COMMANDS=ON > "$RUN_DIR/configure.log" 2>&1) || configure_status=$?
  fi
  if [ "$configure_status" -ne 0 ]; then
    log "ERROR configure failed"
    tail -80 "$RUN_DIR/configure.log" > "$RUN_DIR/configure_tail.txt"
    write_failure_state "configure_failed" "failed" "not_run" "not_run"
    sync_log_artifacts
    exit 1
  fi
  log "configure passed"
fi
if ! (cd "$ROOT" && cmake --build "$BUILD_DIR" -j2 > "$RUN_DIR/build.log" 2>&1); then
  log "ERROR build failed"
  tail -80 "$RUN_DIR/build.log" > "$RUN_DIR/build_tail.txt"
  write_failure_state "build_failed" "failed" "not_run" "not_run"
  sync_log_artifacts
  exit 1
fi
log "build passed"

log "running current-safe no_compare"
set_compare false
: > "$RUN_DIR/current_no_compare.txt"
measured_labels=()
for index in $(seq 1 "$MEASURE_RUNS"); do
  measured_labels+=("run$index")
done
for label in warmup "${measured_labels[@]}"; do
  warm_or_cold="measured"
  if [ "$label" = "warmup" ]; then
    warm_or_cold="warmup"
  fi
  if ! run_runner "$label" "$RUN_DIR/$label.no_compare.log" "no_compare" "$warm_or_cold"; then
    log "ERROR no_compare failed at $label"
    write_failure_state "timing_failed" "pass" "not_run" "failed"
    sync_log_artifacts
    exit 1
  fi
  cat "$RUN_DIR/$label.result" >> "$RUN_DIR/current_no_compare.txt"
done
no_compare_error_count=$(count_runner_failure_markers "$RUN_DIR"/*.no_compare.log)
if [ "$no_compare_error_count" -ne 0 ]; then
  echo "runner_error_grep_count=$no_compare_error_count" | tee -a "$RUN_DIR/current_no_compare.txt"
  log "ERROR no_compare logs contain runner failure markers; refusing timing verdict"
  write_failure_state "input_missing_or_runner_error" "pass" "not_run" "failed"
  sync_log_artifacts
  exit 1
fi
summarize_ms_samples "no_compare" "measured" "measured_no_compare" "$RUN_DIR/current_no_compare.txt"
no_compare_sample_count=$(awk 'BEGIN { c=0 } NR > 1 && $2=="no_compare" && $3=="measured" && $7=="0" { c++ } END { print c+0 }' "$RUN_DIR/timing_samples.tsv")
no_compare_median_ms=$(awk -F '\t' 'NR > 1 && $2=="no_compare" && $3=="measured" && $7=="0" { print $4 }' "$RUN_DIR/timing_samples.tsv" | sort -n | awk '{
  a[NR]=$1
} END {
  if (NR==0) { exit 0 }
  if (NR%2==1) { printf "%.3f", a[(NR+1)/2] }
  else { printf "%.3f", (a[NR/2]+a[NR/2+1])/2 }
}')
if [ -z "$no_compare_median_ms" ]; then
  no_compare_median_ms="0.000"
fi
find "$OUTPUT_DIR" -name "*.parquet" | wc -l | awk '{print "output_parquet_count="$1}' | tee -a "$RUN_DIR/current_no_compare.txt"

log "running candidate compare"
set_compare true
compare_runner_role="control"
compare_runner_path="$RUNNER"
if [ -n "$CANDIDATE_RUNNER" ] && [ -x "$CANDIDATE_RUNNER" ]; then
  compare_runner_role="candidate"
  compare_runner_path="$CANDIDATE_RUNNER"
fi
log "compare runner role=$compare_runner_role path=$compare_runner_path"
if [ "$compare_runner_role" = "candidate" ]; then
  if run_candidate_runner "compare" "$RUN_DIR/compare.log" "compare" "measured"; then
    compare_rc=0
  else
    compare_rc=$?
  fi
else
  if run_runner "compare" "$RUN_DIR/compare.log" "compare" "measured"; then
    compare_rc=0
  else
    compare_rc=$?
  fi
fi
set_compare false
compare_log_error_count=$(count_runner_failure_markers "$RUN_DIR/compare.log")
if [ "$compare_log_error_count" -ne 0 ]; then
  compare_rc=1
fi
{
  cat "$RUN_DIR/compare.result"
  echo "compare_runner_role=$compare_runner_role"
  echo "compare_runner_path=$compare_runner_path"
  find "$OUTPUT_DIR" -name "*.parquet" | wc -l | awk '{print "output_parquet_count="$1}'
  echo "compare_error_grep_count=$compare_log_error_count"
  echo "compare_rc=$compare_rc"
} | tee "$RUN_DIR/current_compare.txt"

if [ "$compare_rc" -ne 0 ]; then
  log "WARN compare failed; recording failure analysis and continuing to the next round"
  write_failure_state "compare_failed" "pass" "failed" "screening_only" "continue"
fi

if [ "$compare_rc" -ne 0 ]; then
  log "skipping paired A/B because candidate compare did not pass"
elif [ -n "$CANDIDATE_RUNNER" ]; then
  if [ -x "$CANDIDATE_RUNNER" ]; then
    log "running interleaved paired A/B (control=$RUNNER, candidate=$CANDIDATE_RUNNER)"
    set_compare false
    for pair_index in $(seq 1 "$MEASURE_RUNS"); do
      paired_ctrl_label="paired_ctrl_$pair_index"
      paired_cand_label="paired_cand_$pair_index"
      if ! run_runner "$paired_ctrl_label" "$RUN_DIR/$paired_ctrl_label.log" "paired_control" "measured" "$pair_index"; then
        log "WARN paired_control rc!=0 at pair_index=$pair_index"
      fi
      if ! run_candidate_runner "$paired_cand_label" "$RUN_DIR/$paired_cand_label.log" "paired_candidate" "measured" "$pair_index"; then
        log "WARN paired_candidate rc!=0 at pair_index=$pair_index"
      fi
    done
    paired_error_count=$(count_runner_failure_markers "$RUN_DIR"/paired_ctrl_*.log "$RUN_DIR"/paired_cand_*.log)
    if [ "$paired_error_count" -ne 0 ]; then
      echo "paired_error_grep_count=$paired_error_count" | tee -a "$RUN_DIR/current_compare.txt"
      log "ERROR paired A/B logs contain runner failure markers; refusing timing verdict"
      write_failure_state "timing_failed" "pass" "pass" "failed"
      sync_log_artifacts
      exit 1
    fi
  else
    log "WARN CANDIDATE_RUNNER set but not executable: $CANDIDATE_RUNNER; skipping paired A/B"
  fi
fi

control_head="${CONTROL_HEAD:-}"
if [ -z "$control_head" ]; then
  control_head=$(cd "$ROOT" && git rev-parse --short HEAD 2>/dev/null || echo "headless_psi_remote")
fi
control_samples_ms=$(awk -F '\t' 'NR > 1 && $2=="no_compare" && $3=="measured" && $7=="0" { printf "%s%s", sep, $4; sep="," } END { print "" }' "$RUN_DIR/timing_samples.tsv")
control_samples_seconds=$(awk -F '\t' 'NR > 1 && $2=="no_compare" && $3=="measured" && $7=="0" { printf "%s%s", sep, $5; sep="," } END { print "" }' "$RUN_DIR/timing_samples.tsv")
if ! write_headless_control_loop "$control_head" "$no_compare_median_ms" "$control_samples_ms" "$control_samples_seconds" "ok" "$no_compare_sample_count" "$compare_rc"; then
  log "WARN control-loop artifact generation failed"
fi

if [ "$GENERATE_REPORT" = "1" ]; then
  if [ -n "$REPORT_SCRIPT" ] && [ -f "$REPORT_SCRIPT" ] && command -v python3 >/dev/null 2>&1; then
    python3 "$REPORT_SCRIPT" \
      --date "$(date +%F)" \
      --control-loop-dir "$HEADLESS_CONTROL_DIR" \
      --report-root "$REPORT_ROOT" \
      --run-state "$HEADLESS_CONTROL_DIR/run_state.json" \
      --no-pdf \
      > "$RUN_DIR/report.log" 2>&1 || log "WARN report generation failed"
    latest_report_path=$(awk -F= '$1 == "markdown" { print substr($0, index($0, "=") + 1) }' "$RUN_DIR/report.log" | tail -1)
    if [ -n "$latest_report_path" ] && [ -f "$HEADLESS_CONTROL_DIR/run_state.json" ]; then
      LATEST_REPORT_PATH="$latest_report_path" python3 - <<'PY'
from __future__ import annotations

import json
import os
from pathlib import Path

state_path = Path(os.environ["HEADLESS_CONTROL_DIR"]) / "run_state.json"
state = json.loads(state_path.read_text(encoding="utf-8-sig"))
state["latest_report"] = os.environ["LATEST_REPORT_PATH"]
state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PY
      log "latest_report=$latest_report_path"
    fi
  else
    log "WARN report generation requested but REPORT_SCRIPT is missing or python3 is unavailable"
  fi
else
  log "report_generation=skipped set GENERATE_REPORT=1 REPORT_SCRIPT=/path/to/psi_daily_report.py to enable"
fi

log "running perf evidence"
set_compare false
if command -v perf >/dev/null 2>&1; then
  (cd "$ROOT/PsiTraderRunner" && perf stat -d -o "$RUN_DIR/perf_stat.txt" "$RUNNER" > "$RUN_DIR/perf_runner.log" 2>&1) || log "WARN perf stat failed"
  (cd "$ROOT/PsiTraderRunner" && perf record -F 99 -g -o "$RUN_DIR/perf.data" "$RUNNER" > "$RUN_DIR/perf_record_runner.log" 2>&1) || log "WARN perf record failed"
  perf report -i "$RUN_DIR/perf.data" --stdio > "$RUN_DIR/perf_report.txt" 2> "$RUN_DIR/perf_report.err" || log "WARN perf report failed"
else
  log "WARN perf command not available"
  touch "$RUN_DIR/perf_stat.txt" "$RUN_DIR/perf_report.txt"
fi

{
  echo "Hotspot notes"
  echo "Generated at $(date '+%F %T')"
  echo
  echo "Accepted baseline:"
  echo "7c1b842 perf: reuse psi bar day template, 72s -> 66s"
  echo "3ee9f21 perf: cache generated parquet row strings, 66s -> 60s"
  echo "195e50c perf: skip ordered tick sorting, 60s -> 56s"
  echo
  echo "Current-safe run evidence:"
  cat "$RUN_DIR/current_no_compare.txt"
  echo
  cat "$RUN_DIR/current_compare.txt"
  echo
  echo "Headless control loop artifacts:"
  echo "$HEADLESS_CONTROL_DIR"
  echo
  echo "Hold for review:"
  echo "skip_tick_strings measured around 55s but is semantically risky for factors that read tick thscode/exchange."
  echo "1 warmup + $MEASURE_RUNS measured is screening only; use evaluator or bundle audit for promotion evidence."
  echo
  echo "Next target selection should use perf_report.txt if it contains useful symbols."
  echo "Do not retry generateTable without a narrower profiler-backed hypothesis."
} > "$RUN_DIR/hotspot_notes.txt"

log "done"
log "run_dir=$RUN_DIR"
sync_log_artifacts
