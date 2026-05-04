#!/usr/bin/env bash
#
# Evaluator for safe_divide task
#
# Environment (set by gatekeeper_cli_loop.sh):
#   GATEKEEPER_WORKTREE - path to the worktree where changes were made
#   GATEKEEPER_PROJECT_ROOT - path to main project root
#
# This script ONLY checks - it does NOT create or modify any files.
#

set -euo pipefail

# Determine where to run
if [[ -n "${GATEKEEPER_WORKTREE:-}" ]]; then
    WORK_DIR="$GATEKEEPER_WORKTREE"
else
    WORK_DIR="$(pwd)"
fi

echo "=== Evaluator: safe_divide ==="
echo "Working directory: $WORK_DIR"
echo ""

# Check if python-utils directory exists
if [[ ! -d "$WORK_DIR/python-utils" ]]; then
    echo "FAIL: python-utils/ directory not found"
    echo "Builder should create: python-utils/safe_math.py"
    echo "Builder should create: python-utils/test_safe_math.py"
    exit 1
fi

# Check if safe_math.py exists
if [[ ! -f "$WORK_DIR/python-utils/safe_math.py" ]]; then
    echo "FAIL: python-utils/safe_math.py not found"
    exit 1
fi
echo "✓ Found: python-utils/safe_math.py"

# Check if test file exists
if [[ ! -f "$WORK_DIR/python-utils/test_safe_math.py" ]]; then
    echo "FAIL: python-utils/test_safe_math.py not found"
    exit 1
fi
echo "✓ Found: python-utils/test_safe_math.py"

# Run the test
echo ""
echo "Running tests..."
cd "$WORK_DIR"
python3 python-utils/test_safe_math.py

echo ""
echo "=== All tests passed ==="
