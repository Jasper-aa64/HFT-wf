#!/usr/bin/env bash
# Overclock evaluator for cpp-trader-backtester
# Runs Debug/ASan build for correctness tests, Release build for benchmark smoke
#
# Expected environment:
#   OVERCLOCK_WORKTREE - path to the git worktree (set by overclock_cli_loop.sh)
#
# Design rationale:
#   - Tests use assert() which is compiled out in Release mode (-DNDEBUG)
#   - Debug/ASan build ensures assertions are checked
#   - Release build is only for performance benchmarking

set -euo pipefail

PROJECT_DIR="${OVERCLOCK_WORKTREE:-.}/cpp-trader-backtester"
DEBUG_DIR="$PROJECT_DIR/build-debug"
RELEASE_DIR="$PROJECT_DIR/build-release"

# Phase 1: Debug/ASan build for correctness
echo "=== BUILD (Debug/ASan) ==="
cmake -S "$PROJECT_DIR" -B "$DEBUG_DIR" -DCMAKE_BUILD_TYPE=Debug \
    -DCMAKE_CXX_FLAGS="-fsanitize=address -fno-omit-frame-pointer" 2>&1
cmake --build "$DEBUG_DIR" -j4 2>&1

# Phase 2: Run tests in Debug mode (assertions enabled)
echo ""
echo "=== TESTS (Debug/ASan) ==="
echo "--- test_order_book ---"
"$DEBUG_DIR/test_order_book" 2>&1

echo ""
echo "--- test_strategies ---"
"$DEBUG_DIR/test_strategies" 2>&1

echo ""
echo "--- test_types ---"
"$DEBUG_DIR/test_types" 2>&1

# Phase 3: Release build for benchmark smoke
echo ""
echo "=== BUILD (Release) ==="
cmake -S "$PROJECT_DIR" -B "$RELEASE_DIR" -DCMAKE_BUILD_TYPE=Release 2>&1
cmake --build "$RELEASE_DIR" -j4 2>&1

# Phase 4: Benchmark smoke (Release for realistic performance)
echo ""
echo "=== BENCHMARK SMOKE (Release) ==="
"$RELEASE_DIR/benchmark" 2>&1


# Phase 5: Invariant check (golden diff — always runs)
echo ""
echo "=== INVARIANT CHECK ==="
"$(dirname "$0")/../check_orderbook_invariants.sh"

# Phase 6: Performance regression check (only in optimization mode)
if [[ "${OPT_MODE:-0}" == "1" ]]; then
    echo ""
    echo "=== PERFORMANCE REGRESSION CHECK ==="
    "$(dirname "$0")/../compare_perf.sh"
fi

echo ""
echo "=== ALL GATES PASSED ==="
