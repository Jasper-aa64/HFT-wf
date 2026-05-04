#!/usr/bin/env bash
#
# evaluate.sh — GateKeeper Mode Executor Script
#
# This is the deterministic machine judge.
# Exit 0 = pass, non-zero = fail.
#
# Usage:
#   ./scripts/evaluate.sh
#
# GateKeeper mode calls this script; Critic reviews the output.
#

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

echo "=== GATEKEEPER EXECUTOR ==="
echo "Project: $PROJECT_ROOT"
echo "Time: $(date -Iseconds)"
echo ""

# ── Build ────────────────────────────────────────────────────────────────────

echo ">>> Build phase"

if [[ -d "cpp-trader-backtester" ]]; then
    echo "Found cpp-trader-backtester, running CMake build..."

    # Create build directory if needed
    mkdir -p cpp-trader-backtester/build

    # Configure
    cd cpp-trader-backtester
    cmake -B build -S . 2>&1 || {
        echo "FAIL: CMake configure failed"
        exit 1
    }

    # Build
    cmake --build build -j "$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)" 2>&1 || {
        echo "FAIL: Build failed"
        exit 1
    }

    echo "Build: PASS"
    cd "$PROJECT_ROOT"
else
    echo "No cpp-trader-backtester found, skipping C++ build."
fi

# ── Tests ─────────────────────────────────────────────────────────────────────

echo ""
echo ">>> Test phase"

TESTS_PASSED=0
TESTS_FAILED=0

# C++ tests
if [[ -d "cpp-trader-backtester/build" ]]; then
    for test_bin in cpp-trader-backtester/build/test_*; do
        if [[ -x "$test_bin" ]]; then
            test_name=$(basename "$test_bin")
            echo "Running: $test_name"
            if "$test_bin" 2>&1; then
                echo "  $test_name: PASS"
                ((TESTS_PASSED++)) || true
            else
                echo "  $test_name: FAIL"
                ((TESTS_FAILED++)) || true
            fi
        fi
    done
fi

# Python tests (if any)
if [[ -f "pytest.ini" ]] || [[ -f "setup.py" ]] || [[ -f "pyproject.toml" ]]; then
    echo "Running Python tests..."
    if command -v pytest &>/dev/null; then
        if pytest --tb=short 2>&1; then
            echo "Python tests: PASS"
            ((TESTS_PASSED++)) || true
        else
            echo "Python tests: FAIL"
            ((TESTS_FAILED++)) || true
        fi
    else
        echo "pytest not found, skipping Python tests"
    fi
fi

# ── Invariants (optional) ─────────────────────────────────────────────────────

echo ""
echo ">>> Invariant checks"

if [[ -x "scripts/check_orderbook_invariants.sh" ]]; then
    echo "Running orderbook invariants..."
    if ./scripts/check_orderbook_invariants.sh 2>&1; then
        echo "Orderbook invariants: PASS"
    else
        echo "Orderbook invariants: FAIL"
        ((TESTS_FAILED++)) || true
    fi
fi

if [[ -x "scripts/run_backtest_regression.sh" ]]; then
    echo "Running backtest regression..."
    if ./scripts/run_backtest_regression.sh 2>&1; then
        echo "Backtest regression: PASS"
    else
        echo "Backtest regression: FAIL"
        ((TESTS_FAILED++)) || true
    fi
fi

# ── Summary ───────────────────────────────────────────────────────────────────

echo ""
echo "=== EXECUTOR SUMMARY ==="
echo "Tests passed: $TESTS_PASSED"
echo "Tests failed: $TESTS_FAILED"
echo ""

if [[ $TESTS_FAILED -gt 0 ]]; then
    echo "VERDICT: FAIL"
    exit 1
fi

echo "VERDICT: PASS"
exit 0
