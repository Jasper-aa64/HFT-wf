#!/usr/bin/env bash
set -u

ROOT="/root/work/Code1/psi-trader-liangjunming"
ENV_FILE="/root/work/.toolchain/psi-env.sh"
RUN_ID="headless_psi_$(date +%Y%m%d_%H%M%S)"
RUN_DIR="$ROOT/headless_runs/$RUN_ID"
BUILD_DIR="$ROOT/build/linux-relwithdebinfo-boost182"
RUNNER="$BUILD_DIR/build_x64/RelWithDebInfo/bin/PsiTraderRunner/PsiTraderRunner"
CONFIG="$ROOT/PsiTraderRunner/config.yaml"
OUTPUT_DIR="/root/work/Code1/dataset/output"
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
REPORT_SCRIPT="${REPORT_SCRIPT:-}"
REPORT_ROOT="${REPORT_ROOT:-$RUN_DIR/reports}"
GENERATE_REPORT="${GENERATE_REPORT:-0}"
HEADLESS_CONTROL_DIR="$RUN_DIR"
MEASURE_RUNS="${MEASURE_RUNS:-5}"

mkdir -p "$RUN_DIR"
printf "label\tmode\twarm_or_cold\telapsed_ms\telapsed_seconds\tcompat_seconds\trc\tlog_file\n" > "$RUN_DIR/timing_samples.tsv"

log() {
  echo "[$(date '+%F %T')] $*" | tee -a "$RUN_DIR/summary.txt"
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

run_runner() {
  local label="$1"
  local log_file="$2"
  local mode="${3:-no_compare}"
  local warm_or_cold="${4:-measured}"
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
  printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n" \
    "$label" "$mode" "$warm_or_cold" "$elapsed_ms" "$elapsed_seconds" "$compat_seconds" "$rc" "$log_file" \
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
  if ! command -v python3 >/dev/null 2>&1; then
    return 0
  fi
  FAILURE_REASON="$reason" BUILD_STATUS="$build_status" COMPARE_STATUS="$compare_status" TIMING_STATUS="$timing_status" \
    RUN_DIR="$RUN_DIR" RUN_ID="$RUN_ID" python3 - <<'PY'
from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path

run_dir = Path(os.environ["RUN_DIR"])
run_dir.mkdir(parents=True, exist_ok=True)
now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
reason = os.environ["FAILURE_REASON"]
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

state = {
    "status": "stopped",
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
    "last_exit_reason": reason,
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
}
(run_dir / "run_state.json").write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
heartbeat = {
    "updated_at": now,
    "phase": "stopped",
    "current_step": reason,
    "pid_or_session": str(os.getpid()),
    "last_log": str(run_dir / "summary.txt"),
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
  mkdir -p "$HEADLESS_CONTROL_DIR"
  export HEADLESS_CONTROL_DIR CONTROL_HEAD="$control_head" CONTROL_MEDIAN_MS="$control_median_ms" \
    CONTROL_SAMPLES_MS="$control_samples_ms" CONTROL_SAMPLES_SECONDS="$control_samples_seconds" \
    CONTROL_NOISE="$control_noise" NO_COMPARE_COUNT="$no_compare_count" COMPARE_RC="$compare_rc" RUN_ID SCRIPT_DIR
  python3 - <<'PY'
from __future__ import annotations

import csv
import json
import os
import statistics
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
run_id = os.environ.get("RUN_ID", "headless")
host_key = os.environ.get("PSI_TIMING_HOST_KEY") or os.environ.get("HOST_KEY") or default_host_key()
recorded_at = now()
control_stats = stats_from_ms(control_samples_ms, control_median_ms, control_noise)
control_median_ms_text = control_stats["median_ms"]
control_median_seconds = control_stats["median_seconds"]
last_exit_reason = "compare_pass" if str(compare_rc) == "0" else "compare_failed"
noise_status = "NOISY" if str(control_noise).upper() == "NOISY" else "ok"

profile_rows = [
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
        "rank": 1,
        "stage": "headless_no_compare",
        "total_ms": control_median_ms_text,
        "avg_ms": control_median_ms_text,
        "count": no_compare_count,
        "score": "1.000000" if int(float(no_compare_count or 0)) else "0.000000",
        "notes": "screening_only",
    }
]
with (out_dir / "hotspots.tsv").open("w", encoding="utf-8", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=["rank", "stage", "total_ms", "avg_ms", "count", "score", "notes"], delimiter="\t", lineterminator="\n")
    writer.writeheader()
    writer.writerows(hotspot_rows)

attempt_rows = [
    {
        "rank": 0,
        "kind": "control",
        "policy_bucket": "control",
        "experiment_kind": "control_bundle",
        "target": f"{control_head} control baseline",
        "stack_members": "",
        "stage": "baseline",
        "observed_cost_ms": control_median_ms_text,
        "expected_delta_seconds": "0.000",
        "p_owned": "1.000",
        "p_safe": "1.000",
        "p_gate": "1.000",
        "p_local": "1.000",
        "cost_attempt_seconds": "0.0",
        "uncertainty": "0.000",
        "lambda": "0.050",
        "score_evidence": "0.000000",
        "ownership_confidence": "1.000",
        "correctness_safety": "1.000",
        "locality": "1.000",
        "legacy_corl_score": "",
        "score": "0.000000",
        "recorded_at": recorded_at,
        "sample_count": control_stats["sample_count"] or no_compare_count,
        "samples_ms": control_stats["samples_ms"],
        "samples": control_stats["samples"],
        "mean_ms": control_stats["mean_ms"],
        "mean_seconds": control_stats["mean_seconds"],
        "median_ms": control_stats["median_ms"],
        "median_seconds": control_stats["median_seconds"],
        "mad_ms": control_stats["mad_ms"],
        "mad_seconds": control_stats["mad_seconds"],
        "iqr_ms": control_stats["iqr_ms"],
        "iqr_seconds": control_stats["iqr_seconds"],
        "stdev_ms": control_stats["stdev_ms"],
        "stdev_seconds": control_stats["stdev_seconds"],
        "range_ms": control_stats["range_ms"],
        "range_seconds": control_stats["range_seconds"],
        "noise_flag": control_stats["noise_flag"],
        "verdict": "DIAGNOSTIC_ONLY",
        "control_head": control_head,
        "control_median_ms": control_median_ms_text,
        "control_median_seconds": control_median_seconds,
        "delta_ms": "0.000",
        "delta_seconds": "0.000",
        "acceptance_policy": "compare pass; evaluate candidate against the run-specific control baseline recorded in attempts.tsv",
        "evidence_status": "screening_only",
        "promotion_sample_floor": str(PROMOTION_SAMPLE_FLOOR),
        "bundle_audit_sample_floor": str(BUNDLE_AUDIT_SAMPLE_FLOOR),
        "notes": f"Headless screening baseline; 1 warmup + {no_compare_count} measured is diagnostic only.",
    },
    {
        "rank": 1,
        "kind": "diagnostic",
        "policy_bucket": "diagnostic",
        "experiment_kind": "compare_gate",
        "target": "compare gate baseline",
        "stack_members": "compare gate baseline",
        "stage": "headless_compare",
        "observed_cost_ms": "",
        "expected_delta_seconds": "0.000",
        "p_owned": "0.900",
        "p_safe": "0.840",
        "p_gate": "0.920",
        "p_local": "1.000",
        "cost_attempt_seconds": "1800.0",
        "uncertainty": "0.100",
        "lambda": "0.050",
        "score_evidence": "0.000000",
        "ownership_confidence": "0.900",
        "correctness_safety": "0.840",
        "locality": "1.000",
        "legacy_corl_score": "",
        "score": "0.000000",
        "recorded_at": recorded_at,
        "sample_count": "",
        "samples_ms": "",
        "samples": "",
        "mean_ms": "",
        "mean_seconds": "",
        "median_ms": "",
        "median_seconds": "",
        "mad_ms": "",
        "mad_seconds": "",
        "iqr_ms": "",
        "iqr_seconds": "",
        "stdev_ms": "",
        "stdev_seconds": "",
        "range_ms": "",
        "range_seconds": "",
        "noise_flag": "ok",
        "verdict": "DIAGNOSTIC_ONLY",
        "control_head": control_head,
        "control_median_ms": control_median_ms_text,
        "control_median_seconds": control_median_seconds,
        "delta_ms": "",
        "delta_seconds": "",
        "acceptance_policy": "compare pass; evaluate candidate against the run-specific control baseline recorded in attempts.tsv",
        "evidence_status": "compare_pass" if str(compare_rc) == "0" else "compare_failed",
        "promotion_sample_floor": str(PROMOTION_SAMPLE_FLOOR),
        "bundle_audit_sample_floor": str(BUNDLE_AUDIT_SAMPLE_FLOOR),
        "notes": f"compare_rc={compare_rc}; correctness gate only, not candidate timing evidence",
    },
    {
        "rank": 2,
        "kind": "neutral_stack",
        "policy_bucket": "explore",
        "experiment_kind": "neutral_stack",
        "target": "headless low-risk neutral stack",
        "stack_members": "headless_no_compare|compare_gate",
        "stage": "headless_bundle_audit",
        "observed_cost_ms": "",
        "expected_delta_seconds": "0.000",
        "p_owned": "0.900",
        "p_safe": "0.960",
        "p_gate": "0.920",
        "p_local": "1.000",
        "cost_attempt_seconds": "1800.0",
        "uncertainty": "0.100",
        "lambda": "0.050",
        "score_evidence": "0.000000",
        "ownership_confidence": "0.900",
        "correctness_safety": "0.960",
        "locality": "1.000",
        "legacy_corl_score": "",
        "score": "0.000000",
        "recorded_at": recorded_at,
        "sample_count": "",
        "samples_ms": "",
        "samples": "",
        "mean_ms": "",
        "mean_seconds": "",
        "median_ms": "",
        "median_seconds": "",
        "mad_ms": "",
        "mad_seconds": "",
        "iqr_ms": "",
        "iqr_seconds": "",
        "stdev_ms": "",
        "stdev_seconds": "",
        "range_ms": "",
        "range_seconds": "",
        "noise_flag": "planned",
        "verdict": "neutral",
        "control_head": control_head,
        "control_median_ms": control_median_ms_text,
        "control_median_seconds": control_median_seconds,
        "delta_ms": "",
        "delta_seconds": "",
        "acceptance_policy": "neutral stack retained for bundle audit; not promotion proof",
        "evidence_status": "bundle_audit_pending",
        "promotion_sample_floor": str(PROMOTION_SAMPLE_FLOOR),
        "bundle_audit_sample_floor": str(BUNDLE_AUDIT_SAMPLE_FLOOR),
        "notes": "Safe neutral stack placeholder keeps bundle validation visible at the run root.",
    },
]
attempt_fieldnames = [
    "rank",
    "kind",
    "policy_bucket",
    "experiment_kind",
    "target",
    "stack_members",
    "stage",
    "observed_cost_ms",
    "expected_delta_seconds",
    "p_owned",
    "p_safe",
    "p_gate",
    "p_local",
    "cost_attempt_seconds",
    "uncertainty",
    "lambda",
    "score_evidence",
    "ownership_confidence",
    "correctness_safety",
    "locality",
    "legacy_corl_score",
    "score",
    "recorded_at",
    "sample_count",
    "samples_ms",
    "samples",
    "mean_ms",
    "mean_seconds",
    "median_ms",
    "median_seconds",
    "mad_ms",
    "mad_seconds",
    "iqr_ms",
    "iqr_seconds",
    "stdev_ms",
    "stdev_seconds",
    "range_ms",
    "range_seconds",
    "noise_flag",
    "verdict",
    "control_head",
    "control_median_ms",
    "control_median_seconds",
    "delta_ms",
    "delta_seconds",
    "acceptance_policy",
    "evidence_status",
    "promotion_sample_floor",
    "bundle_audit_sample_floor",
    "notes",
]
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
        "timing_summary": f"sample_count={row['sample_count']}; median_ms={row['median_ms']}; delta_ms={row['delta_ms']}; noise_flag={row['noise_flag']}",
        "semantic_risk": "low" if row["policy_bucket"] != "reserve" else "medium",
        "stack_compatibility": "stackable" if row["experiment_kind"] == "neutral_stack" else "single",
        "queue_state": "NOISY_PENDING" if noise_status == "NOISY" else "bundle_audit_pending" if row["experiment_kind"] == "neutral_stack" else row["evidence_status"],
        "build_status": "pass",
        "compare_status": "pass" if str(compare_rc) == "0" else "failed",
        "timing_status": "screening_only",
        "measured_samples": row["sample_count"],
        "required_samples": str(BUNDLE_AUDIT_SAMPLE_FLOOR if row["experiment_kind"] == "neutral_stack" else PROMOTION_SAMPLE_FLOOR),
        "retry_condition": "rerun when same-host jitter is below threshold" if noise_status == "NOISY" else "collect stronger evidence before promotion",
        "notes": row["notes"],
    }
    for row in attempt_rows
    if row["kind"] != "control"
]
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
        "sample_count": row["sample_count"],
        "promotion_sample_floor": str(PROMOTION_SAMPLE_FLOOR),
        "bundle_audit_sample_floor": str(BUNDLE_AUDIT_SAMPLE_FLOOR),
        "aggregate_gain_seconds": row["delta_seconds"],
        "timing_summary": f"sample_count={row['sample_count']}; median_ms={row['median_ms']}; delta_ms={row['delta_ms']}; noise_flag={row['noise_flag']}",
        "semantic_risk": "low" if row["policy_bucket"] != "reserve" else "medium",
        "stack_compatibility": "stackable" if row["experiment_kind"] == "neutral_stack" else "single",
        "validation_status": "bundle_audit_pending",
        "retry_condition": "rerun when same-host jitter is below threshold" if noise_status == "NOISY" else "collect stronger evidence before promotion",
        "notes": "Neutral stack evidence retained; requires bundle audit before promotion.",
    }
    for row in attempt_rows
    if row["experiment_kind"] == "neutral_stack" or row["verdict"] == "neutral"
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

run_state = {
    "status": "stopped",
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
    "timing_status": "screening_only",
    "patch_queue_path": str(out_dir / "patch_queue.tsv"),
    "neutral_pool_path": str(out_dir / "neutral_pool.tsv"),
    "retry_conditions_path": str(out_dir / "retry_conditions.tsv"),
}
with (out_dir / "run_state.json").open("w", encoding="utf-8") as handle:
    json.dump(run_state, handle, indent=2, ensure_ascii=False)
    handle.write("\n")

heartbeat = {
    "updated_at": recorded_at,
    "phase": "stopped",
    "current_step": last_exit_reason,
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
  exit 1
fi

log "waiting for existing PsiTraderRunner processes"
for _ in $(seq 1 60); do
  if pgrep -f "$RUNNER" >/dev/null 2>&1; then
    sleep 10
  else
    break
  fi
done

if pgrep -f "$RUNNER" >/dev/null 2>&1; then
  log "ERROR existing PsiTraderRunner still running after wait"
  write_failure_state "runner_busy" "not_run" "not_run" "not_run"
  exit 1
fi

set_compare false
grep "isCompareFile" "$CONFIG" | tee -a "$RUN_DIR/summary.txt"

log "building"
if ! (cd "$ROOT" && cmake --build "$BUILD_DIR" -j2 > "$RUN_DIR/build.log" 2>&1); then
  log "ERROR build failed"
  tail -80 "$RUN_DIR/build.log" > "$RUN_DIR/build_tail.txt"
  write_failure_state "build_failed" "failed" "not_run" "not_run"
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
    exit 1
  fi
  cat "$RUN_DIR/$label.result" >> "$RUN_DIR/current_no_compare.txt"
done
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

log "running current-safe compare"
set_compare true
if run_runner "compare" "$RUN_DIR/compare.log" "compare" "measured"; then
  compare_rc=0
else
  compare_rc=$?
fi
set_compare false
{
  cat "$RUN_DIR/compare.result"
  find "$OUTPUT_DIR" -name "*.parquet" | wc -l | awk '{print "output_parquet_count="$1}'
  grep -E "compareFile error|basic_string|length_error|Aborted|Segmentation fault|terminate called" "$RUN_DIR/compare.log" | wc -l | awk '{print "compare_error_grep_count="$1}'
  echo "compare_rc=$compare_rc"
} | tee "$RUN_DIR/current_compare.txt"

if [ "$compare_rc" -ne 0 ]; then
  log "ERROR compare failed"
  write_failure_state "compare_failed" "pass" "failed" "screening_only"
  exit "$compare_rc"
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
