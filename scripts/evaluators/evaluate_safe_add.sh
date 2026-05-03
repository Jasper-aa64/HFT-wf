#!/usr/bin/env bash
#
# Evaluator for safe_add test
#

set -euo pipefail

WORK_DIR="${OVERCLOCK_WORKTREE:-$(pwd)}"

echo "=== Evaluator: safe_add ==="
echo "Working directory: $WORK_DIR"
echo ""

# Check if files exist
if [[ ! -f "$WORK_DIR/python-utils/safe_add.py" ]]; then
    echo "FAIL: python-utils/safe_add.py not found"
    exit 1
fi
echo "✓ Found: python-utils/safe_add.py"

if [[ ! -f "$WORK_DIR/python-utils/test_safe_add.py" ]]; then
    echo "FAIL: python-utils/test_safe_add.py not found"
    exit 1
fi
echo "✓ Found: python-utils/test_safe_add.py"

echo ""
echo "Running tests..."
cd "$WORK_DIR"
python3 python-utils/test_safe_add.py

echo ""
echo "=== All tests passed ==="
