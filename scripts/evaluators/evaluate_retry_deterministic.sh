#!/usr/bin/env bash
#
# Deterministic evaluator for testing retry logic.
# Fails on attempt 1, passes on attempt 2+.
# Uses a marker file to track attempts.
#

set -euo pipefail

WORK_DIR="${OVERCLOCK_WORKTREE:-$(pwd)}"
MARKER_FILE="$WORK_DIR/.retry_test_attempt"

echo "=== Evaluator: retry_deterministic ==="
echo "Working directory: $WORK_DIR"

# Check if the required file exists
if [[ ! -f "$WORK_DIR/python-utils/retry_test.py" ]]; then
    echo "FAIL: python-utils/retry_test.py not found"
    exit 1
fi
echo "✓ Found: python-utils/retry_test.py"

# Track attempt number
ATTEMPT_NUM=1
if [[ -f "$MARKER_FILE" ]]; then
    ATTEMPT_NUM=$(cat "$MARKER_FILE")
    ((ATTEMPT_NUM++))
fi
echo "$ATTEMPT_NUM" > "$MARKER_FILE"

echo ""
echo "Attempt number: $ATTEMPT_NUM"

if [[ $ATTEMPT_NUM -eq 1 ]]; then
    echo ""
    echo "FAIL: First attempt intentionally fails to trigger retry"
    echo "This is a deterministic test for retry logic."
    exit 1
fi

echo ""
echo "Running tests..."
cd "$WORK_DIR"
python3 python-utils/retry_test.py

echo ""
echo "=== All tests passed (attempt $ATTEMPT_NUM) ==="
