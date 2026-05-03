#!/usr/bin/env bash
#
# Evaluator for safe_multiply test
#

set -euo pipefail

WORK_DIR="${OVERCLOCK_WORKTREE:-$(pwd)}"

echo "=== Evaluator: safe_multiply ==="
echo "Working directory: $WORK_DIR"
echo ""

# Check if files exist
if [[ ! -f "$WORK_DIR/python-utils/safe_multiply.py" ]]; then
    echo "FAIL: python-utils/safe_multiply.py not found"
    exit 1
fi
echo "✓ Found: python-utils/safe_multiply.py"

if [[ ! -f "$WORK_DIR/python-utils/test_safe_multiply.py" ]]; then
    echo "FAIL: python-utils/test_safe_multiply.py not found"
    exit 1
fi
echo "✓ Found: python-utils/test_safe_multiply.py"

echo ""
echo "Running tests..."
cd "$WORK_DIR"
python3 python-utils/test_safe_multiply.py

echo ""
echo "=== All tests passed ==="
