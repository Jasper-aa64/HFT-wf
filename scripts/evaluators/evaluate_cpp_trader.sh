#!/usr/bin/env bash
# Overclock evaluator for cpp-trader-backtester
# Runs build, tests, and benchmark smoke check
#
# Expected environment:
#   OVERCLOCK_WORKTREE - path to the git worktree (set by overclock_cli_loop.sh)

set -euo pipefail

PROJECT_DIR="${OVERCLOCK_WORKTREE:-.}/cpp-trader-backtester"
BUILD_DIR="$PROJECT_DIR/build"

# Phase 1: Build
echo "=== BUILD ==="
cmake -S "$PROJECT_DIR" -B "$BUILD_DIR" -DCMAKE_BUILD_TYPE=Release 2>&1
cmake --build "$BUILD_DIR" -j4 2>&1

# Phase 2: Run tests
echo ""
echo "=== TESTS ==="
echo "--- test_order_book ---"
"$BUILD_DIR/test_order_book" 2>&1

echo ""
echo "--- test_strategies ---"
"$BUILD_DIR/test_strategies" 2>&1

echo ""
echo "--- test_types ---"
"$BUILD_DIR/test_types" 2>&1

# Phase 3: Benchmark smoke
# Semantic invariant is enforced through test_order_book
echo ""
echo "=== BENCHMARK SMOKE ==="
"$BUILD_DIR/benchmark" 2>&1

echo ""
echo "=== ALL GATES PASSED ==="
