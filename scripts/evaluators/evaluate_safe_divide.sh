#!/usr/bin/env bash
#
# Evaluator for safe_divide task
#
# Environment (set by overclock_cli_loop.sh):
#   OVERCLOCK_WORKTREE - path to the worktree where changes were made
#   OVERCLOCK_PROJECT_ROOT - path to main project root
#
# This script should check files in OVERCLOCK_WORKTREE (if set) or current dir.
#

set -euo pipefail

# Determine where to run
if [[ -n "${OVERCLOCK_WORKTREE:-}" ]]; then
    cd "$OVERCLOCK_WORKTREE"
fi

PROJECT_ROOT="${OVERCLOCK_WORKTREE:-$(pwd)}"

echo "=== Evaluator: safe_divide ==="
echo "Working directory: $PROJECT_ROOT"
echo ""

# Create test directory if needed
mkdir -p "$PROJECT_ROOT/python-utils"

# Check if safe_math.py exists
if [[ ! -f "$PROJECT_ROOT/python-utils/safe_math.py" ]]; then
    echo "FAIL: python-utils/safe_math.py not found"
    exit 1
fi

# Check if test file exists
if [[ ! -f "$PROJECT_ROOT/python-utils/test_safe_math.py" ]]; then
    echo "FAIL: python-utils/test_safe_math.py not found"
    exit 1
fi

# Run the test
echo "Running tests..."
python3 "$PROJECT_ROOT/python-utils/test_safe_math.py"

echo ""
echo "=== All tests passed ==="
