#!/usr/bin/env bash
# Invariant check: run test_order_book and diff against golden output.
# Any deviation means a correctness regression — exit 1.
#
# Usage: ./check_orderbook_invariants.sh
# OVERCLOCK_WORKTREE env var sets project root (default: .)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${OVERCLOCK_WORKTREE:-.}/cpp-trader-backtester"
DEBUG_DIR="$PROJECT_DIR/build-debug"
GOLDEN="$SCRIPT_DIR/golden/orderbook_invariants.txt"

if [[ ! -f "$GOLDEN" ]]; then
    echo "ERROR: golden file not found: $GOLDEN"
    exit 1
fi

if [[ ! -f "$DEBUG_DIR/test_order_book" ]]; then
    echo "ERROR: test_order_book binary not found — run Debug build first"
    exit 1
fi

actual=$("$DEBUG_DIR/test_order_book" 2>&1)

if diff <(echo "$actual") "$GOLDEN" > /dev/null 2>&1; then
    echo "check_orderbook_invariants: PASS"
else
    echo "INVARIANT VIOLATION: test_order_book output differs from golden"
    echo "--- golden"
    echo "+++ actual"
    diff "$GOLDEN" <(echo "$actual") || true
    exit 1
fi
