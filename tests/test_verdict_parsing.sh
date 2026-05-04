#!/usr/bin/env bash
#
# Test verdict parsing logic for GateKeeper CLI
# Verifies default-reject posture and strict format requirements
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_DIR=$(mktemp -d)
trap 'rm -rf "$TEST_DIR"' EXIT

test_verdict() {
    local content="$1"
    local expected="$2"
    local test_name="$3"
    
    echo "$content" > "$TEST_DIR/critic.md"
    
    VERDICT_LINE=$(grep -E "^VERDICT:" "$TEST_DIR/critic.md" | tail -1 || true)
    
    if [[ "$VERDICT_LINE" == "VERDICT: APPROVE" ]]; then
        VERDICT="APPROVE"
    elif [[ "$VERDICT_LINE" == "VERDICT: REJECT" ]]; then
        VERDICT="REJECT"
    else
        VERDICT="REJECT"
    fi
    
    if [[ "$VERDICT" == "$expected" ]]; then
        echo "✓ $test_name"
        return 0
    else
        echo "✗ $test_name: got $VERDICT, expected $expected"
        return 1
    fi
}

echo "=== Verdict Parsing Tests ==="
echo ""

FAILED=0

test_verdict "VERDICT: APPROVE" "APPROVE" "Exact APPROVE" || ((FAILED++))
test_verdict "VERDICT: REJECT" "REJECT" "Exact REJECT" || ((FAILED++))
test_verdict "I think this looks good" "REJECT" "No VERDICT line" || ((FAILED++))
test_verdict "APPROVE" "REJECT" "APPROVE without VERDICT:" || ((FAILED++))
test_verdict "verdict: approve" "REJECT" "Lowercase format" || ((FAILED++))
test_verdict "VERDICT: APPROVE with extra" "REJECT" "Extra content after APPROVE" || ((FAILED++))

echo ""
if [[ $FAILED -eq 0 ]]; then
    echo "All tests passed."
    exit 0
else
    echo "$FAILED tests failed."
    exit 1
fi
