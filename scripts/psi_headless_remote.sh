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

log "run_id=$RUN_ID"
log "root=$ROOT"

if [ -f "$ENV_FILE" ]; then
  # shellcheck disable=SC1090
  source "$ENV_FILE"
else
  log "ERROR missing env file: $ENV_FILE"
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
  exit 1
fi

set_compare false
grep "isCompareFile" "$CONFIG" | tee -a "$RUN_DIR/summary.txt"

log "building"
if ! (cd "$ROOT" && cmake --build "$BUILD_DIR" -j2 > "$RUN_DIR/build.log" 2>&1); then
  log "ERROR build failed"
  tail -80 "$RUN_DIR/build.log" > "$RUN_DIR/build_tail.txt"
  exit 1
fi
log "build passed"

log "running current-safe no_compare"
set_compare false
: > "$RUN_DIR/current_no_compare.txt"
for label in warmup run1 run2 run3; do
  warm_or_cold="measured"
  if [ "$label" = "warmup" ]; then
    warm_or_cold="warmup"
  fi
  if ! run_runner "$label" "$RUN_DIR/$label.no_compare.log" "no_compare" "$warm_or_cold"; then
    log "ERROR no_compare failed at $label"
    exit 1
  fi
  cat "$RUN_DIR/$label.result" >> "$RUN_DIR/current_no_compare.txt"
done
summarize_ms_samples "no_compare" "measured" "measured_no_compare" "$RUN_DIR/current_no_compare.txt"
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
  exit "$compare_rc"
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
  echo "Hold for review:"
  echo "skip_tick_strings measured around 55s but is semantically risky for factors that read tick thscode/exchange."
  echo
  echo "Next target selection should use perf_report.txt if it contains useful symbols."
  echo "Do not retry generateTable without a narrower profiler-backed hypothesis."
} > "$RUN_DIR/hotspot_notes.txt"

log "done"
log "run_dir=$RUN_DIR"
