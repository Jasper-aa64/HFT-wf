#!/usr/bin/env bash
# Performance regression check.
# Runs benchmark 5×, takes median, compares against experiments/baseline.tsv.
# Requires OPT_MODE=1 (called by evaluate_cpp_trader.sh in optimization mode).
#
# Exit 0 → improvement >= THRESHOLD_PCT
# Exit 1 → regression or insufficient improvement
#
# Appends one row to experiments/results.tsv regardless of outcome.

set -euo pipefail

PROJECT_DIR="${OVERCLOCK_WORKTREE:-.}/cpp-trader-backtester"
RELEASE_DIR="$PROJECT_DIR/build-release"
BASELINE_FILE="$PROJECT_DIR/experiments/baseline.tsv"
RESULTS_FILE="$PROJECT_DIR/experiments/results.tsv"
THRESHOLD_PCT=3

if [[ ! -f "$RELEASE_DIR/benchmark" ]]; then
    echo "ERROR: benchmark binary not found — run Release build first"
    exit 1
fi

if [[ ! -f "$BASELINE_FILE" ]]; then
    echo "ERROR: baseline.tsv not found at $BASELINE_FILE"
    exit 1
fi

# Read baseline avg latency (µs/order)
baseline=$(grep "avg_latency_us_per_order" "$BASELINE_FILE" | grep -v "^#" | awk -F'\t' '{print $4}' | tail -1)
if [[ -z "$baseline" ]]; then
    echo "ERROR: could not parse avg_latency_us_per_order from baseline.tsv"
    exit 1
fi

echo "Baseline: ${baseline} µs/order"
echo "Running benchmark 5 times..."

# Collect 5 samples
declare -a samples
for i in 1 2 3 4 5; do
    val=$("$RELEASE_DIR/benchmark" 2>&1 | grep "Avg latency:" | grep "µs/order" | awk '{print $3}')
    samples+=("$val")
    echo "  run $i: $val µs/order"
done

# Median of 5 (sort, pick index 3 = 1-indexed middle)
median=$(printf '%s\n' "${samples[@]}" | sort -n | sed -n '3p')
echo "Median: ${median} µs/order"

# Compute improvement % — lower latency = better
pct=$(awk "BEGIN { printf \"%.2f\", ($baseline - $median) / $baseline * 100 }")
echo "Improvement vs baseline: ${pct}%"

# Append to results.tsv
commit=$(git -C "$PROJECT_DIR" rev-parse --short HEAD 2>/dev/null || echo "unknown")
timestamp=$(date -u +%FT%T)
mkdir -p "$(dirname "$RESULTS_FILE")"
echo -e "${timestamp}\torder_book\tavg_latency_us_per_order\t${median}\tµs/order\timprovement=${pct}%\tcommit=${commit}" \
    >> "$RESULTS_FILE"
echo "Result appended to $RESULTS_FILE"

# Gate
result=$(awk "BEGIN { print ($pct >= $THRESHOLD_PCT) ? \"PASS\" : \"FAIL\" }")
if [[ "$result" == "PASS" ]]; then
    echo "compare_perf: PASS (${pct}% >= ${THRESHOLD_PCT}% threshold)"
    exit 0
else
    echo "compare_perf: FAIL (${pct}% < ${THRESHOLD_PCT}% threshold — not enough improvement)"
    exit 1
fi
