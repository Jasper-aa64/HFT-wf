#!/usr/bin/env bash
#
# Evaluator for safe_divide task
# Creates test file and runs it
#

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

echo "=== Evaluator: safe_divide ==="

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
cd "$PROJECT_ROOT"
python3 python-utils/test_safe_math.py

echo ""
echo "=== All tests passed ==="
