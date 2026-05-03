#!/usr/bin/env bash
#
# Evaluator that always passes - used for testing malformed critic output
#

set -euo pipefail

WORK_DIR="${OVERCLOCK_WORKTREE:-$(pwd)}"

echo "=== Evaluator: always-pass ==="
echo "Working directory: $WORK_DIR"
echo ""
echo "This evaluator always passes for testing purposes."
echo "PASS: All checks passed (synthetic)"
exit 0
