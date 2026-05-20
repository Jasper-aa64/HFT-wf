#!/usr/bin/env bash
#
# GateKeeper Mode B: review an existing patch/diff.
#
# This entrypoint is for quality custody after code has already been changed.
# It skips the Builder phase and reviews a selected diff against a brief,
# deterministic evaluator output, and a critic checklist.
#
# Usage:
#   ./scripts/gatekeeper_review_existing.sh [--patch input.patch] [--files selected_files.txt] [--report FORMAT] <brief.md>
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
RUNS_DIR="$PROJECT_ROOT/gatekeeper_runs"
REPORT_FORMAT="${GATEKEEPER_REPORT_FORMAT:-none}"
TEMP_COMPAT_BIN=""
BRIEF_FILE=""
INPUT_PATCH=""
SELECTED_FILES=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --help|-h)
            echo "Usage: $0 [--patch input.patch] [--files selected_files.txt] [--report FORMAT] <brief.md>"
            echo ""
            echo "Options:"
            echo "  --patch PATH       Explicit patch selected by the Manager"
            echo "  --files PATH       Explicit selected_files.txt selected by the Manager"
            echo "  --report FORMAT    Generate final report: none, docx, md-pdf, all (default: none)"
            exit 0
            ;;
        --patch)
            if [[ $# -lt 2 ]]; then
                echo "ERROR: --patch requires a value"
                exit 1
            fi
            INPUT_PATCH="$2"
            shift 2
            ;;
        --files)
            if [[ $# -lt 2 ]]; then
                echo "ERROR: --files requires a value"
                exit 1
            fi
            SELECTED_FILES="$2"
            shift 2
            ;;
        --report)
            if [[ $# -lt 2 ]]; then
                echo "ERROR: --report requires a value"
                exit 1
            fi
            case "$2" in
                none|docx|md-pdf|all)
                    REPORT_FORMAT="$2"
                    ;;
                *)
                    echo "ERROR: --report must be one of: none, docx, md-pdf, all"
                    exit 1
                    ;;
            esac
            shift 2
            ;;
        -*)
            echo "ERROR: Unknown option: $1"
            exit 1
            ;;
        *)
            BRIEF_FILE="$1"
            shift
            ;;
    esac
done

if [[ -z "$BRIEF_FILE" || ! -f "$BRIEF_FILE" ]]; then
    echo "ERROR: brief file not found: ${BRIEF_FILE:-<missing>}"
    echo "Usage: $0 [--patch input.patch] [--files selected_files.txt] [--report FORMAT] <brief.md>"
    exit 1
fi

if [[ -n "$INPUT_PATCH" && ! -f "$INPUT_PATCH" ]]; then
    echo "ERROR: patch file not found: $INPUT_PATCH"
    exit 1
fi

if [[ -n "$SELECTED_FILES" && ! -f "$SELECTED_FILES" ]]; then
    echo "ERROR: files list not found: $SELECTED_FILES"
    exit 1
fi

if [[ -n "$INPUT_PATCH" && -z "$SELECTED_FILES" ]]; then
    echo "ERROR: --patch requires --files so scope can be verified"
    exit 1
fi

if [[ -z "$INPUT_PATCH" && -n "$SELECTED_FILES" ]]; then
    echo "ERROR: --files requires --patch so the selected diff is explicit"
    exit 1
fi

BRIEF_ABS="$(cd "$(dirname "$BRIEF_FILE")" && pwd)/$(basename "$BRIEF_FILE")"
BRIEF_REL=""
case "$BRIEF_ABS" in
    "$PROJECT_ROOT"/*)
        BRIEF_REL="${BRIEF_ABS#$PROJECT_ROOT/}"
        ;;
esac

cleanup_temp_compat() {
    if [[ -n "$TEMP_COMPAT_BIN" && -d "$TEMP_COMPAT_BIN" ]]; then
        rm -rf "$TEMP_COMPAT_BIN"
    fi
}
trap cleanup_temp_compat EXIT

ensure_python3() {
    if command -v python3 &>/dev/null && python3 - <<'PY' >/dev/null 2>&1
import sys
print(sys.version)
PY
    then
        return 0
    fi

    if command -v python &>/dev/null; then
        TEMP_COMPAT_BIN="$(mktemp -d)"
        cat > "$TEMP_COMPAT_BIN/python3" <<'PYWRAP'
#!/usr/bin/env bash
python "$@"
PYWRAP
        chmod +x "$TEMP_COMPAT_BIN/python3"
        export PATH="$TEMP_COMPAT_BIN:$PATH"
        echo "WARN: python3 was unavailable; using python via compatibility wrapper"
        return 0
    fi

    echo "WARN: neither python3 nor python is available; Python-based evaluators/reports may fail"
}

extract_allowed_files() {
    local in_allowed=0
    local files=()
    while IFS= read -r line; do
        line="${line%$'\r'}"
        if [[ "$line" =~ ^allowed_files: ]]; then
            in_allowed=1
            continue
        fi
        if [[ $in_allowed -eq 1 ]]; then
            if [[ "$line" =~ ^[[:space:]]*-[[:space:]]*(.+)$ ]]; then
                local file="${BASH_REMATCH[1]}"
                file="${file%$'\r'}"
                files+=("$file")
            elif [[ ! "$line" =~ ^[[:space:]] ]]; then
                break
            fi
        fi
    done < "$1"
    printf '%s\n' "${files[@]}"
}

extract_checklist() {
    local in_checklist=0
    while IFS= read -r line; do
        line="${line%$'\r'}"
        if [[ "$line" =~ ^checklist: ]]; then
            in_checklist=1
            continue
        fi
        if [[ $in_checklist -eq 1 ]]; then
            if [[ "$line" =~ ^[[:space:]]*-[[:space:]]*(.+)$ ]]; then
                echo "- [ ] ${BASH_REMATCH[1]}"
            elif [[ ! "$line" =~ ^[[:space:]] ]]; then
                break
            fi
        fi
    done < "$1"
}

verify_allowed_files() {
    local changed_files="$1"
    local allowed_files="$2"
    local violations=()
    while IFS= read -r changed; do
        changed="${changed%$'\r'}"
        [[ -z "$changed" ]] && continue
        local allowed=0
        while IFS= read -r pattern; do
            pattern="${pattern%$'\r'}"
            [[ -z "$pattern" ]] && continue
            if [[ "$changed" == $pattern ]]; then
                allowed=1
                break
            fi
        done < "$allowed_files"
        if [[ $allowed -eq 0 ]]; then
            violations+=("$changed")
        fi
    done < "$changed_files"

    if [[ ${#violations[@]} -gt 0 ]]; then
        echo "ERROR: Existing patch modifies files outside allowed scope:"
        printf '  - %s\n' "${violations[@]}"
        return 1
    fi
    return 0
}

write_retry_evidence() {
    local attempt_dir="$1"
    local gate="$2"
    local summary="$3"

    cat > "$attempt_dir/retry_evidence.md" << EVIDENCE
# Retry Evidence

## Verdict
REJECT

## Failed Gate
$gate

## Summary
$summary

## Missing Proof
See the artifacts below for the exact failed command, scope violation, or critic finding.

## Evidence Artifacts
EVIDENCE

    if [[ -f "$attempt_dir/eval.log" ]]; then
        echo "- eval.log" >> "$attempt_dir/retry_evidence.md"
    fi
    if [[ -f "$attempt_dir/critic.md" ]]; then
        echo "- critic.md" >> "$attempt_dir/retry_evidence.md"
    fi
    if [[ -f "$attempt_dir/patch.diff" ]]; then
        echo "- patch.diff" >> "$attempt_dir/retry_evidence.md"
    fi
    if [[ -f "$attempt_dir/all_changed_files.txt" ]]; then
        echo "- all_changed_files.txt" >> "$attempt_dir/retry_evidence.md"
    fi

    cat >> "$attempt_dir/retry_evidence.md" << EVIDENCE

## Expected Evidence Shape
- Selected files stay inside allowed_files.
- Executor exits 0 and writes enough log output to prove the required behavior.
- Critic-Review can cite patch or executor evidence for every checklist item.
EVIDENCE

    if [[ -f "$attempt_dir/all_changed_files.txt" ]]; then
        cat >> "$attempt_dir/retry_evidence.md" << EVIDENCE

## Selected Files
\`\`\`text
$(cat "$attempt_dir/all_changed_files.txt")
\`\`\`
EVIDENCE
    fi

    if [[ -f "$attempt_dir/eval.log" && "$gate" == "EXECUTOR" ]]; then
        cat >> "$attempt_dir/retry_evidence.md" << EVIDENCE

## Executor Log Tail
\`\`\`text
$(tail -120 "$attempt_dir/eval.log")
\`\`\`
EVIDENCE
    fi

    if [[ -f "$attempt_dir/critic.md" && "$gate" == "CRITIC" ]]; then
        local critic_retry
        critic_retry=$(awk '
            BEGIN { capture=0 }
            /^#+[[:space:]]*Retry Evidence/ { capture=1; print; next }
            capture && /^#+[[:space:]]/ { exit }
            capture { print }
        ' "$attempt_dir/critic.md")
        if [[ -n "$critic_retry" ]]; then
            cat >> "$attempt_dir/retry_evidence.md" << EVIDENCE

## Critic Retry Evidence
\`\`\`markdown
$critic_retry
\`\`\`
EVIDENCE
        else
            cat >> "$attempt_dir/retry_evidence.md" << EVIDENCE

## Critic Summary
\`\`\`text
$summary
\`\`\`
EVIDENCE
        fi
    fi
}

write_final_decision() {
    local run_dir="$1"
    local final_verdict="$2"
    local gate="$3"
    local summary="$4"
    cat > "$run_dir/final_decision.md" << DECISION
# Final Decision

## Final verdict: $final_verdict

## Attempts used: 1 / 1

## Mode: existing-patch-review

## Gate: $gate

## Summary
$summary

## Artifacts
- brief.md
- critic_checklist.md
- attempt-1/patch.diff
- attempt-1/eval.log
- attempt-1/critic.md
- attempt-1/retry_evidence.md (when rejected)
- attempt-1/decision.md
DECISION
}

generate_report() {
    local run_dir="$1"
    local format="$2"
    [[ "$format" == "none" ]] && return 0
    local reporter="$PROJECT_ROOT/scripts/gatekeeper_report.py"
    if [[ ! -f "$reporter" ]]; then
        echo "WARN: report requested but reporter is missing: $reporter"
        return 0
    fi
    echo ""
    echo ">>> Generating GateKeeper report ($format)"
    if ! python3 "$reporter" "$run_dir" --format "$format"; then
        echo "WARN: report generation failed"
    fi
}

echo "=== GateKeeper Existing Patch Review ==="

if ! git -C "$PROJECT_ROOT" rev-parse --git-dir &>/dev/null; then
    echo "ERROR: Not a git repository"
    exit 1
fi
if ! command -v codex &>/dev/null; then
    echo "ERROR: codex CLI not found"
    exit 1
fi
ensure_python3

EVAL_SCRIPT=$(grep -E "^eval_script:" "$BRIEF_FILE" | head -1 | sed 's/^eval_script: *//' | tr -d '\r' || true)
if [[ -z "$EVAL_SCRIPT" ]]; then
    echo "ERROR: brief must specify eval_script"
    exit 1
fi
if [[ "$EVAL_SCRIPT" != scripts/* || ! -f "$PROJECT_ROOT/$EVAL_SCRIPT" ]]; then
    echo "ERROR: evaluator script not found under scripts/: $EVAL_SCRIPT"
    exit 1
fi
if [[ ! -x "$PROJECT_ROOT/$EVAL_SCRIPT" ]]; then
    chmod +x "$PROJECT_ROOT/$EVAL_SCRIPT"
fi

TIMESTAMP=$(date +%Y%m%d-%H%M%S)
RUN_DIR="$RUNS_DIR/$TIMESTAMP"
ATTEMPT_DIR="$RUN_DIR/attempt-1"
mkdir -p "$ATTEMPT_DIR"
cp "$BRIEF_FILE" "$RUN_DIR/brief.md"

extract_allowed_files "$BRIEF_FILE" > "$RUN_DIR/allowed_files.txt"
if [[ ! -s "$RUN_DIR/allowed_files.txt" ]]; then
    echo "ERROR: brief must specify allowed_files"
    exit 1
fi

echo "Run dir: $RUN_DIR"
echo "Evaluator: $EVAL_SCRIPT"
echo "Report format: $REPORT_FORMAT"

if [[ -n "$INPUT_PATCH" ]]; then
    echo "Input patch: $INPUT_PATCH"
    echo "Selected files: $SELECTED_FILES"
    cp "$INPUT_PATCH" "$RUN_DIR/input.patch"
    cp "$SELECTED_FILES" "$RUN_DIR/selected_files.txt"
    cp "$INPUT_PATCH" "$ATTEMPT_DIR/patch.diff"
    sed 's/\r$//' "$SELECTED_FILES" | awk 'NF { print }' | sort -u > "$ATTEMPT_DIR/all_changed_files.txt"
else
    echo "WARN: no explicit --patch/--files provided; falling back to current worktree diff"
    git -C "$PROJECT_ROOT" diff --name-only HEAD -- . ':(exclude)gatekeeper_runs/**' > "$ATTEMPT_DIR/changed_files.raw"
    git -C "$PROJECT_ROOT" ls-files --others --exclude-standard -- . ':(exclude)gatekeeper_runs/**' > "$ATTEMPT_DIR/new_files.raw" || true
    awk -v brief="$BRIEF_REL" 'brief != "" && $0 == brief { next } { print }' "$ATTEMPT_DIR/changed_files.raw" > "$ATTEMPT_DIR/changed_files.txt"
    awk -v brief="$BRIEF_REL" 'brief != "" && $0 == brief { next } { print }' "$ATTEMPT_DIR/new_files.raw" > "$ATTEMPT_DIR/new_files.txt"
    cat "$ATTEMPT_DIR/changed_files.txt" "$ATTEMPT_DIR/new_files.txt" | sort -u > "$ATTEMPT_DIR/all_changed_files.txt"
fi

if [[ ! -s "$ATTEMPT_DIR/all_changed_files.txt" ]]; then
    echo "ERROR: no existing changes to review"
    write_final_decision "$RUN_DIR" "SETUP_FAILED" "SETUP" "No existing changes to review."
    exit 1
fi

if [[ -z "$INPUT_PATCH" ]]; then
    if [[ -n "$BRIEF_REL" ]]; then
        git -C "$PROJECT_ROOT" diff --binary HEAD -- . ':(exclude)gatekeeper_runs/**' ":(exclude)$BRIEF_REL" > "$ATTEMPT_DIR/patch.diff"
    else
        git -C "$PROJECT_ROOT" diff --binary HEAD -- . ':(exclude)gatekeeper_runs/**' > "$ATTEMPT_DIR/patch.diff"
    fi
    while IFS= read -r newfile; do
        [[ -z "$newfile" ]] && continue
        git -C "$PROJECT_ROOT" diff --no-index -- /dev/null "$newfile" >> "$ATTEMPT_DIR/patch.diff" 2>/dev/null || true
    done < "$ATTEMPT_DIR/new_files.txt"
fi

echo "Changed files:"
cat "$ATTEMPT_DIR/all_changed_files.txt" | sed 's/^/  /'

if ! verify_allowed_files "$ATTEMPT_DIR/all_changed_files.txt" "$RUN_DIR/allowed_files.txt"; then
    SUMMARY="Existing patch modifies files outside allowed scope."
    echo "$SUMMARY" > "$ATTEMPT_DIR/summary.txt"
    cat > "$ATTEMPT_DIR/decision.md" << DECISION
# Attempt Decision

## Verdict: REJECT

## Gate: SCOPE

## Summary
$SUMMARY
DECISION
    write_retry_evidence "$ATTEMPT_DIR" "SCOPE" "$SUMMARY"
    write_final_decision "$RUN_DIR" "REJECT" "SCOPE" "$SUMMARY"
    generate_report "$RUN_DIR" "$REPORT_FORMAT"
    exit 1
fi

extract_checklist "$BRIEF_FILE" > "$RUN_DIR/critic_checklist.md"
if [[ ! -s "$RUN_DIR/critic_checklist.md" ]]; then
    cat > "$RUN_DIR/critic_prep_prompt.md" << PROMPT
You are the Critic-Prep in a GateKeeper existing-patch review workflow.

Define what evidence proves this already-written patch is acceptable.
Do not approve anything. Output a markdown checklist only.

## Task Brief

$(cat "$RUN_DIR/brief.md")

## Allowed Files

$(cat "$RUN_DIR/allowed_files.txt")

Output format:

## Checklist

- [ ] <specific evidence item>
PROMPT
    set +e
    cd "$PROJECT_ROOT"
    codex exec --sandbox read-only --skip-git-repo-check -o "$RUN_DIR/critic_checklist.md" "$(cat "$RUN_DIR/critic_prep_prompt.md")" > "$RUN_DIR/critic_prep.log" 2>&1
    PREP_EXIT=$?
    set -e
    if [[ $PREP_EXIT -ne 0 || ! -s "$RUN_DIR/critic_checklist.md" ]]; then
        SUMMARY="Critic-Prep failed or produced an empty checklist."
        write_final_decision "$RUN_DIR" "SETUP_FAILED" "CRITIC_PREP" "$SUMMARY"
        generate_report "$RUN_DIR" "$REPORT_FORMAT"
        exit 1
    fi
fi

echo ""
echo ">>> Executor"
set +e
cd "$PROJECT_ROOT"
export GATEKEEPER_WORKTREE="$PROJECT_ROOT"
export GATEKEEPER_PROJECT_ROOT="$PROJECT_ROOT"
export GATEKEEPER_RUN_DIR="$RUN_DIR"
export GATEKEEPER_ATTEMPT="1"
export GATEKEEPER_ATTEMPT_DIR="$ATTEMPT_DIR"
"$PROJECT_ROOT/$EVAL_SCRIPT" > "$ATTEMPT_DIR/eval.log" 2>&1
EVAL_EXIT=$?
set -e
echo "Executor exit code: $EVAL_EXIT"

if [[ $EVAL_EXIT -ne 0 ]]; then
    SUMMARY="Executor failed with exit code $EVAL_EXIT."
    echo "$SUMMARY" > "$ATTEMPT_DIR/summary.txt"
    cat > "$ATTEMPT_DIR/decision.md" << DECISION
# Attempt Decision

## Verdict: REJECT

## Gate: EXECUTOR

## Summary
$SUMMARY
DECISION
    write_retry_evidence "$ATTEMPT_DIR" "EXECUTOR" "$SUMMARY"
    write_final_decision "$RUN_DIR" "REJECT" "EXECUTOR" "$SUMMARY"
    generate_report "$RUN_DIR" "$REPORT_FORMAT"
    exit 1
fi

cat > "$ATTEMPT_DIR/critic_prompt.md" << PROMPT
You are the Critic-Review in a GateKeeper existing-patch review workflow.
You did NOT write this patch.

Default posture: REJECT unless the patch proves itself with evidence.

## Pre-written Checklist

\`\`\`
$(cat "$RUN_DIR/critic_checklist.md")
\`\`\`

## Patch Diff

\`\`\`diff
$(cat "$ATTEMPT_DIR/patch.diff")
\`\`\`

## Executor Log

\`\`\`
$(cat "$ATTEMPT_DIR/eval.log")
\`\`\`

For EACH checklist item:
1. Find evidence in patch or executor log.
2. If any checklist item lacks evidence, REJECT.
3. If all checklist items have evidence, APPROVE.

If you REJECT, include a "Retry Evidence" section with:
- failed checklist item summary
- missing proof
- relevant patch/log location when available
- expected evidence shape

LAST TWO LINES must be exactly:

VERDICT: APPROVE
SUMMARY: <one line summary>

or

VERDICT: REJECT
SUMMARY: <one line summary>
PROMPT

echo ""
echo ">>> Critic-Review"
set +e
cd "$PROJECT_ROOT"
codex exec --sandbox read-only --skip-git-repo-check "$(cat "$ATTEMPT_DIR/critic_prompt.md")" 2>&1 | tee "$ATTEMPT_DIR/critic.md"
CODEX_EXIT=$?
set -e

if [[ $CODEX_EXIT -ne 0 ]]; then
    VERDICT="REJECT"
    SUMMARY="Codex Critic-Review failed with exit code $CODEX_EXIT."
else
    VERDICT_LINE=$(grep -E "^VERDICT:" "$ATTEMPT_DIR/critic.md" | tail -1 || true)
    SUMMARY_LINE=$(grep -E "^SUMMARY:" "$ATTEMPT_DIR/critic.md" | tail -1 || true)
    SUMMARY="${SUMMARY_LINE#SUMMARY: }"
    if [[ "$VERDICT_LINE" == "VERDICT: APPROVE" ]]; then
        VERDICT="APPROVE"
    elif [[ "$VERDICT_LINE" == "VERDICT: REJECT" ]]; then
        VERDICT="REJECT"
    else
        VERDICT="REJECT"
        SUMMARY="Malformed critic output: no valid VERDICT line found."
    fi
fi

echo "$SUMMARY" > "$ATTEMPT_DIR/summary.txt"
cat > "$ATTEMPT_DIR/decision.md" << DECISION
# Attempt Decision

## Verdict: $VERDICT

## Gate: CRITIC

## Summary
$SUMMARY

## Evidence
- patch.diff
- eval.log
- critic.md
- retry_evidence.md (when rejected)
DECISION

if [[ "$VERDICT" == "REJECT" ]]; then
    write_retry_evidence "$ATTEMPT_DIR" "CRITIC" "$SUMMARY"
fi

write_final_decision "$RUN_DIR" "$VERDICT" "CRITIC" "$SUMMARY"
generate_report "$RUN_DIR" "$REPORT_FORMAT"

echo ""
echo "=== $VERDICT ==="
echo "Final decision: $RUN_DIR/final_decision.md"
if [[ "$REPORT_FORMAT" != "none" ]]; then
    echo "Reports: $RUN_DIR/reports"
fi

if [[ "$VERDICT" == "APPROVE" ]]; then
    exit 0
fi
exit 1
