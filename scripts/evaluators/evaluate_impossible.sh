#!/usr/bin/env bash
#
# Evaluator that always fails - for testing ESCALATE scenario
#

set -euo pipefail

echo "=== Evaluator: impossible (always fails) ==="
echo ""

# This evaluator is designed to always fail
# Used for testing the ESCALATE scenario after max attempts

echo "Checking impossible.py..."
if [[ -f "${OVERCLOCK_WORKTREE}/python-utils/impossible.py" ]]; then
    echo "✓ File exists"
else
    echo "FAIL: File not found"
fi

echo ""
echo "FAIL: This evaluator is designed to always fail for testing."
echo "It tests the ESCALATE scenario after max attempts exhausted."
exit 1
