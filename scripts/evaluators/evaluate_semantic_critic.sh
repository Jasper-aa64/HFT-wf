#!/usr/bin/env bash
#
# Evaluator for semantic critic test
# Tests basic functionality but does NOT check exception specificity
# That's the Critic's job to verify from the patch
#

set -euo pipefail

WORK_DIR="${OVERCLOCK_WORKTREE:-$(pwd)}"

echo "=== Evaluator: semantic_critic ==="
echo "Working directory: $WORK_DIR"
echo ""

# Check if files exist
if [[ ! -f "$WORK_DIR/python-utils/safe_math_v2.py" ]]; then
    echo "FAIL: python-utils/safe_math_v2.py not found"
    exit 1
fi
echo "✓ Found: python-utils/safe_math_v2.py"

if [[ ! -f "$WORK_DIR/python-utils/test_safe_math_v2.py" ]]; then
    echo "FAIL: python-utils/test_safe_math_v2.py not found"
    exit 1
fi
echo "✓ Found: python-utils/test_safe_math_v2.py"

echo ""
echo "Running tests..."
cd "$WORK_DIR"
python3 python-utils/test_safe_math_v2.py

echo ""
echo "=== All tests passed ==="
echo ""
echo "NOTE: This evaluator only checks basic functionality."
echo "The Critic must verify exception handling specificity from the patch."
