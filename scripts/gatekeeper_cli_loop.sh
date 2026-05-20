#!/usr/bin/env bash
#
# gatekeeper_cli_loop.sh — GateKeeper Mode CLI with Critic-Prep Checklist
#
# Architecture:
#   Critic-Prep = Codex CLI (generates checklist before patch)
#   Builder     = Claude Code CLI or Codex CLI (provider is configurable)
#   Executor    = scripts/ from this project only
#   Critic-Review = Codex CLI (verifies patch against pre-written checklist)
#   Human       = reads final decision package
#
# Flow:
#   Critic-Prep generates checklist
#       ↓
#   (if SETUP_FAILED, stop)
#       ↓
#   Builder writes patch → Executor runs → Critic-Review checks against checklist
#       ↓
#   REJECT → retry with failure evidence → Builder retry
#       ↓
#   Max attempts exhausted → ESCALATE to human
#
# Isolation:
#   Runs in a git worktree to avoid contaminating the main repo.
#   Worktree is preserved after run for human review.
#   Use --apply to copy approved changes to main worktree.
#
# Requirements:
#   - Must run in a git repository
#   - Selected Builder CLI and Codex CLI must be authenticated
#
# Usage:
#   ./scripts/gatekeeper_cli_loop.sh [--apply] [--max-attempts N] [--project-root PATH] <brief.md>
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_ROOT="$SCRIPT_PROJECT_ROOT"
RUNS_DIR="$PROJECT_ROOT/gatekeeper_runs"
WORKTREES_DIR="$PROJECT_ROOT/.gatekeeper_worktrees"

# ── Parse Args ────────────────────────────────────────────────────────────────

AUTO_APPLY=0
MAX_ATTEMPTS=3
BRIEF_FILE=""
CLAUDE_MODEL="${GATEKEEPER_CLAUDE_MODEL:-sonnet}"
BUILDER_PROVIDER="${GATEKEEPER_BUILDER:-claude}"
CODEX_BUILDER_MODEL="${GATEKEEPER_CODEX_BUILDER_MODEL:-}"
REPORT_FORMAT="${GATEKEEPER_REPORT_FORMAT:-none}"
PREP_FILE_LIMIT="${GATEKEEPER_PREP_FILE_LIMIT:-16000}"
TEMP_COMPAT_BIN=""

normalize_path() {
    local raw="$1"
    if command -v cygpath &>/dev/null; then
        cygpath -u "$raw" 2>/dev/null || printf '%s\n' "$raw"
    else
        printf '%s\n' "$raw"
    fi
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --help|-h)
            echo "Usage: $0 [--apply] [--max-attempts N] [--builder claude|codex] [--project-root PATH] [--report FORMAT] <brief.md>"
            echo ""
            echo "Options:"
            echo "  --apply            On APPROVE, automatically copy changes to main worktree"
            echo "  --max-attempts N   Maximum Builder attempts (default: 3, min: 1)"
            echo "  --builder NAME     Builder provider: claude or codex (default: claude)"
            echo "  --project-root PATH Project repo to operate on (default: parent of this script)"
            echo "  --report FORMAT    Generate final report: none, docx, md-pdf, all (default: none)"
            echo "  --help, -h         Show this help message"
            echo ""
            echo "Without --apply, approved changes remain in the worktree for manual review."
            echo ""
            echo "Brief format (markdown with frontmatter):"
            echo ""
            cat << 'EXAMPLE'
---
task: <one-line description>
allowed_files:
  - path/to/file1
  - path/to/file2
eval_script: scripts/evaluate_xxx.sh
checklist:
  - <item 1>
  - <item 2>
---

## Task Description

<detailed description>
EXAMPLE
            exit 0
            ;;
        --apply)
            AUTO_APPLY=1
            shift
            ;;
        --max-attempts)
            if [[ $# -lt 2 ]]; then
                echo "ERROR: --max-attempts requires a value"
                exit 1
            fi
            if [[ ! "$2" =~ ^[0-9]+$ ]]; then
                echo "ERROR: --max-attempts must be a positive integer"
                exit 1
            fi
            MAX_ATTEMPTS="$2"
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
        --builder)
            if [[ $# -lt 2 ]]; then
                echo "ERROR: --builder requires a value"
                exit 1
            fi
            case "$2" in
                claude|codex)
                    BUILDER_PROVIDER="$2"
                    ;;
                *)
                    echo "ERROR: --builder must be one of: claude, codex"
                    exit 1
                    ;;
            esac
            shift 2
            ;;
        --project-root)
            if [[ $# -lt 2 ]]; then
                echo "ERROR: --project-root requires a value"
                exit 1
            fi
            PROJECT_ROOT="$(normalize_path "$2")"
            shift 2
            ;;
        -*)
            echo "ERROR: Unknown option: $1"
            echo "Use --help for usage"
            exit 1
            ;;
        *)
            BRIEF_FILE="$(normalize_path "$1")"
            shift
            ;;
    esac
done

# Validate max-attempts (must be >= 1)
if [[ "$MAX_ATTEMPTS" -lt 1 ]]; then
    echo "ERROR: --max-attempts must be at least 1"
    exit 1
fi

if [[ -z "$BRIEF_FILE" ]]; then
    echo "Usage: $0 [--apply] [--max-attempts N] [--builder claude|codex] [--project-root PATH] [--report FORMAT] <brief.md>"
    echo ""
    echo "Options:"
    echo "  --apply            On APPROVE, automatically copy changes to main worktree"
    echo "  --max-attempts N   Maximum Builder attempts (default: 3, min: 1)"
    echo "  --builder NAME     Builder provider: claude or codex (default: claude)"
    echo "  --project-root PATH Project repo to operate on (default: parent of this script)"
    echo "  --report FORMAT    Generate final report: none, docx, md-pdf, all (default: none)"
    echo ""
    echo "Use --help for more details."
    exit 1
fi

PROJECT_ROOT="$(cd "$PROJECT_ROOT" && pwd)"
RUNS_DIR="$PROJECT_ROOT/gatekeeper_runs"
WORKTREES_DIR="$PROJECT_ROOT/.gatekeeper_worktrees"

if [[ ! -f "$BRIEF_FILE" ]]; then
    if [[ -f "$PROJECT_ROOT/$BRIEF_FILE" ]]; then
        BRIEF_FILE="$PROJECT_ROOT/$BRIEF_FILE"
    else
        echo "Error: Brief file not found: $BRIEF_FILE"
        exit 1
    fi
fi

# Validate max-attempts
if [[ "$MAX_ATTEMPTS" -lt 1 ]]; then
    echo "ERROR: --max-attempts must be at least 1"
    exit 1
fi

# ── Helper Functions ──────────────────────────────────────────────────────────

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
        echo "ERROR: Patch modifies files outside allowed scope:"
        printf '  - %s\n' "${violations[@]}"
        return 1
    fi
    return 0
}

# Reset worktree to original state for retry
reset_worktree() {
    local worktree_path="$1"
    local original_commit="$2"
    echo "Resetting worktree for next attempt..."
    # Hard reset to original commit to clear index state from git add -N
    git -C "$worktree_path" reset --hard "$original_commit" 2>/dev/null || true
    # Remove untracked files
    git -C "$worktree_path" clean -fd 2>/dev/null || true
    echo "✓ Worktree reset to clean state at $original_commit"
}

# Write attempt decision
write_attempt_decision() {
    local attempt_dir="$1"
    local verdict="$2"
    local gate="$3"
    local summary="$4"

    cat > "$attempt_dir/decision.md" << DECISION
# Attempt Decision

## Verdict: $verdict

## Gate: $gate

## Summary
$summary

## Evidence
- patch.diff
- eval.log
- critic.md
- retry_evidence.md (when rejected)
DECISION
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

    if [[ -f "$attempt_dir/builder.log" ]]; then
        echo "- builder.log" >> "$attempt_dir/retry_evidence.md"
    fi
    if [[ -f "$attempt_dir/eval.log" ]]; then
        echo "- eval.log" >> "$attempt_dir/retry_evidence.md"
    fi
    if [[ -f "$attempt_dir/critic.md" ]]; then
        echo "- critic.md" >> "$attempt_dir/retry_evidence.md"
    fi
    if [[ -f "$attempt_dir/patch.diff" ]]; then
        echo "- patch.diff" >> "$attempt_dir/retry_evidence.md"
    fi

    cat >> "$attempt_dir/retry_evidence.md" << EVIDENCE

## Expected Evidence Shape
- Patch stays inside allowed files.
- Executor exits 0 and writes enough log output to prove the required behavior.
- Critic-Review can cite patch or executor evidence for every checklist item.

## Next Action
Revise the patch to address the rejected gate. Do not repeat the previous patch blindly.
EVIDENCE

    if [[ -f "$attempt_dir/changed_files.txt" ]]; then
        cat >> "$attempt_dir/retry_evidence.md" << EVIDENCE

## Changed Files
\`\`\`text
$(cat "$attempt_dir/changed_files.txt")
\`\`\`
EVIDENCE
    fi

    if [[ -f "$attempt_dir/builder.log" && "$gate" == "BUILDER" ]]; then
        cat >> "$attempt_dir/retry_evidence.md" << EVIDENCE

## Builder Log Tail
\`\`\`text
$(tail -80 "$attempt_dir/builder.log")
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

# Write final decision
write_final_decision() {
    local run_dir="$1"
    local final_verdict="$2"
    local attempts_used="$3"
    local worktree_path="$4"
    local worktree_branch="$5"

    cat > "$run_dir/final_decision.md" << DECISION
# Final Decision

## Final verdict: $final_verdict

## Attempts used: $attempts_used / $MAX_ATTEMPTS

## Final worktree: $worktree_path

## Final branch: $worktree_branch

## Attempt Summaries
DECISION

    # Append summary of each attempt
    local i=1
    while [[ $i -le $attempts_used ]]; do
        local attempt_dir="$run_dir/attempt-$i"
        if [[ -f "$attempt_dir/decision.md" ]]; then
            echo "" >> "$run_dir/final_decision.md"
            echo "### Attempt $i" >> "$run_dir/final_decision.md"
            grep -A2 "^## Verdict:" "$attempt_dir/decision.md" >> "$run_dir/final_decision.md" 2>/dev/null || true
            if [[ -f "$attempt_dir/summary.txt" ]]; then
                cat "$attempt_dir/summary.txt" >> "$run_dir/final_decision.md"
            fi
        fi
        ((i++))
    done

    # If ESCALATE, add cleanup commands
    if [[ "$final_verdict" == "ESCALATE" ]]; then
        cat >> "$run_dir/final_decision.md" << CLEANUP

## Cleanup Commands

To remove the worktree after human review:
\`\`\`bash
git worktree remove $worktree_path
git branch -D $worktree_branch
\`\`\`

## Last Patch
See: $run_dir/attempt-$attempts_used/patch.diff

## Last Executor Log
See: $run_dir/attempt-$attempts_used/eval.log

## Last Critic Notes
See: $run_dir/attempt-$attempts_used/critic.md

## Last Retry Evidence
See: $run_dir/attempt-$attempts_used/retry_evidence.md
CLEANUP
    fi
}

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

generate_report() {
    local run_dir="$1"
    local format="$2"
    [[ "$format" == "none" ]] && return 0

    local reporter="$SCRIPT_PROJECT_ROOT/scripts/gatekeeper_report.py"
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

append_file_utf8_safe() {
    local path="$1"
    local limit="${2:-0}"
    python3 - "$path" "$limit" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
limit = int(sys.argv[2])
data = path.read_bytes()
if limit > 0:
    data = data[:limit]
sys.stdout.buffer.write(data.decode("utf-8", errors="replace").encode("utf-8"))
PY
}

# ── Pre-flight Checks ────────────────────────────────────────────────────────

echo "=== Pre-flight Checks ==="

# Check git repository
if ! git -C "$PROJECT_ROOT" rev-parse --git-dir &>/dev/null; then
    echo "ERROR: Not a git repository"
    echo ""
    echo "GateKeeper requires git for worktree isolation."
    echo "Initialize with:"
    echo "  cd $PROJECT_ROOT && git init && git add . && git commit -m 'init'"
    exit 1
fi

if ! command -v codex &>/dev/null; then
    echo "ERROR: codex CLI not found"
    echo "Install: npm install -g @openai/codex"
    exit 1
fi

if [[ "$BUILDER_PROVIDER" == "claude" ]] && ! command -v claude &>/dev/null; then
    echo "ERROR: claude CLI not found, but --builder claude was selected"
    echo "Install: npm install -g @anthropic-ai/claude-code"
    echo "Or run with: --builder codex"
    exit 1
fi

echo "✓ Git repository: OK"
echo "✓ Codex CLI: $(codex --version)"
echo "✓ Builder provider: $BUILDER_PROVIDER"
if [[ "$BUILDER_PROVIDER" == "claude" ]]; then
    echo "✓ Claude Code: $(claude --version | head -1)"
    echo "✓ Claude model: $CLAUDE_MODEL"
else
    if [[ -n "$CODEX_BUILDER_MODEL" ]]; then
        echo "✓ Codex builder model: $CODEX_BUILDER_MODEL"
    else
        echo "✓ Codex builder model: CLI default"
    fi
    echo "WARN: Builder and Critic both use Codex CLI; this preserves artifact isolation but is not heterogeneous review."
fi
echo "✓ Max attempts: $MAX_ATTEMPTS"
echo "Report format: $REPORT_FORMAT"
echo "Critic-prep file limit: $PREP_FILE_LIMIT bytes per file"
if [[ $AUTO_APPLY -eq 1 ]]; then
    echo "✓ Auto-apply: ENABLED"
else
    echo "✓ Auto-apply: disabled (use --apply to enable)"
fi
echo ""

# ── Parse Brief ───────────────────────────────────────────────────────────────

ensure_python3

EVAL_SCRIPT=$(grep -E "^eval_script:" "$BRIEF_FILE" | head -1 | sed 's/^eval_script: *//' | tr -d '\r' || true)

if [[ -z "$EVAL_SCRIPT" ]]; then
    echo "ERROR: brief must specify eval_script"
    echo "Example: eval_script: scripts/evaluators/evaluate_xxx.sh"
    exit 1
fi

if [[ "$EVAL_SCRIPT" = /* || "$EVAL_SCRIPT" == *..* ]]; then
    echo "ERROR: eval_script must be a project-relative path without '..'"
    echo "Got: $EVAL_SCRIPT"
    exit 1
fi

EVAL_SCRIPT_FULL="$PROJECT_ROOT/$EVAL_SCRIPT"
if [[ ! -f "$EVAL_SCRIPT_FULL" ]]; then
    echo "ERROR: Evaluator script not found: $EVAL_SCRIPT_FULL"
    exit 1
fi

if [[ ! -x "$EVAL_SCRIPT_FULL" ]]; then
    chmod +x "$EVAL_SCRIPT_FULL"
fi

echo "Evaluator: $EVAL_SCRIPT"

ALLOWED_FILES_TMP=$(mktemp)
extract_allowed_files "$BRIEF_FILE" > "$ALLOWED_FILES_TMP"

if [[ ! -s "$ALLOWED_FILES_TMP" ]]; then
    echo "ERROR: brief must specify allowed_files"
    rm -f "$ALLOWED_FILES_TMP"
    exit 1
fi

echo "Allowed files:"
cat "$ALLOWED_FILES_TMP" | sed 's/^/  /'
echo ""

# ── Setup Run ─────────────────────────────────────────────────────────────────

TIMESTAMP=$(date +%Y%m%d-%H%M%S)
RUN_DIR="$RUNS_DIR/$TIMESTAMP"
mkdir -p "$RUN_DIR"

# Create worktree branch and path
WORKTREE_BRANCH="gatekeeper/$TIMESTAMP"
WORKTREE_PATH="$WORKTREES_DIR/$TIMESTAMP"

echo "=== GATEKEEPER CLI LOOP (Retry Loop v1) ==="
echo "Time: $TIMESTAMP"
echo "Run dir: $RUN_DIR"
echo "Worktree: $WORKTREE_PATH"
echo "Branch: $WORKTREE_BRANCH"
echo "Brief: $BRIEF_FILE"
echo "Max attempts: $MAX_ATTEMPTS"

# Copy brief
cp "$BRIEF_FILE" "$RUN_DIR/brief.md"
cp "$ALLOWED_FILES_TMP" "$RUN_DIR/allowed_files.txt"

# ── Create Worktree (persistent for all attempts) ──────────────────────────────

echo ""
echo ">>> Creating isolated worktree..."

# Ensure worktrees directory exists
mkdir -p "$WORKTREES_DIR"

# Create a new branch for this run
git -C "$PROJECT_ROOT" branch "$WORKTREE_BRANCH" 2>/dev/null || {
    echo "ERROR: Failed to create branch $WORKTREE_BRANCH"
    rm -f "$ALLOWED_FILES_TMP"
    exit 1
}

# Create worktree
git -C "$PROJECT_ROOT" worktree add "$WORKTREE_PATH" "$WORKTREE_BRANCH" 2>/dev/null || {
    echo "ERROR: Failed to create worktree"
    git -C "$PROJECT_ROOT" branch -D "$WORKTREE_BRANCH" 2>/dev/null || true
    rm -f "$ALLOWED_FILES_TMP"
    exit 1
}

echo "✓ Worktree created at: $WORKTREE_PATH"

# Store original commit for reset
ORIGINAL_COMMIT=$(git -C "$WORKTREE_PATH" rev-parse HEAD)

# ── Phase 0: Critic-Prep (Generate Checklist) ──────────────────────────────────

echo ""
echo ">>> Phase 0: Critic-Prep (Generate Checklist)"

# Build critic-prep prompt
# Include current state of allowed target files if they exist
cat > "$RUN_DIR/critic_prep_prompt.md" << 'CRITIC_PREP_HEADER'
You are the Critic-Prep in a GateKeeper workflow.

Your job: Before any code is written, define what evidence proves the task is complete.

You will receive:
- Task brief
- Allowed files list
- Current contents of target files (if they exist)

Your output will be used by Critic-Review to check if the patch provides evidence.

## Output Format (EXACT)

Generate a checklist where each item:
1. Is specific and testable
2. Can be proven by patch content or executor log
3. Covers acceptance criteria from the brief

Format:
CRITIC_PREP_HEADER

cat >> "$RUN_DIR/critic_prep_prompt.md" << CRITIC_PREP_BRIEF

## Task Brief

$(cat "$RUN_DIR/brief.md")

## Allowed Files

CRITIC_PREP_BRIEF

cat "$RUN_DIR/allowed_files.txt" >> "$RUN_DIR/critic_prep_prompt.md"

# Add bounded current contents of allowed target files if they exist.
# Large repositories can make Critic-Prep unusable if every allowed file is
# pasted in full. The checklist should be based on the brief and scoped context,
# not megabytes of generated code.
echo "" >> "$RUN_DIR/critic_prep_prompt.md"
echo "## Current Target File Contents" >> "$RUN_DIR/critic_prep_prompt.md"
echo "" >> "$RUN_DIR/critic_prep_prompt.md"

while IFS= read -r target_file; do
    [[ -z "$target_file" ]] && continue
    # Check in worktree
    if [[ -f "$WORKTREE_PATH/$target_file" ]]; then
        echo "### $target_file" >> "$RUN_DIR/critic_prep_prompt.md"
        local_size=$(wc -c < "$WORKTREE_PATH/$target_file" | tr -d '[:space:]')
        echo "File size: ${local_size} bytes" >> "$RUN_DIR/critic_prep_prompt.md"
        if [[ "$local_size" -gt "$PREP_FILE_LIMIT" ]]; then
            echo "Content below is truncated to first ${PREP_FILE_LIMIT} bytes for Critic-Prep." >> "$RUN_DIR/critic_prep_prompt.md"
        fi
        echo '```' >> "$RUN_DIR/critic_prep_prompt.md"
        append_file_utf8_safe "$WORKTREE_PATH/$target_file" "$PREP_FILE_LIMIT" >> "$RUN_DIR/critic_prep_prompt.md"
        echo "" >> "$RUN_DIR/critic_prep_prompt.md"
        echo '```' >> "$RUN_DIR/critic_prep_prompt.md"
        echo "" >> "$RUN_DIR/critic_prep_prompt.md"
    fi
done < "$RUN_DIR/allowed_files.txt"

cat >> "$RUN_DIR/critic_prep_prompt.md" << 'CRITIC_PREP_FOOTER'

## Checklist Format

Output a markdown checklist. Each item should be:
- Specific: Can be verified by reading patch or executor log
- Testable: Has clear pass/fail criteria
- Complete: Covers all requirements from brief

Example format:
```markdown
## Checklist

- [ ] Function `foo` exists in `path/to/file.py`
- [ ] Function `foo` returns correct result for input X
- [ ] Test file `test_foo.py` exists
- [ ] All tests pass (see executor log)
- [ ] Type hints present on function signature
```

Now generate the checklist for this task.
CRITIC_PREP_FOOTER

echo "Running Codex to generate checklist..."
set +e
cd "$WORKTREE_PATH"

# Use -o to save last message (the checklist) directly
# stdout/stderr goes to log, clean checklist goes to file
codex exec \
    --sandbox read-only \
    --skip-git-repo-check \
    -o "$RUN_DIR/critic_checklist.md" \
    - \
    < "$RUN_DIR/critic_prep_prompt.md" \
    > "$RUN_DIR/critic_prep.log" 2>&1
CRITIC_PREP_EXIT=$?
set -e

# Handle Critic-Prep failure
if [[ $CRITIC_PREP_EXIT -ne 0 ]]; then
    cat > "$RUN_DIR/final_decision.md" << DECISION
# Final Decision

## Final verdict: SETUP_FAILED

## Reason

Critic-Prep failed with exit code $CRITIC_PREP_EXIT.

The checklist generation phase did not complete successfully.
Builder was not started.
No attempts were consumed.

## Critic-Prep Log

See: $RUN_DIR/critic_prep.log

## Cleanup Commands

To remove the worktree:
\`\`\`bash
git worktree remove $WORKTREE_PATH
git branch -D $WORKTREE_BRANCH
\`\`\`
DECISION

    echo ""
    echo "=== SETUP_FAILED (Critic-Prep Failed) ==="
    echo "Exit code: $CRITIC_PREP_EXIT"
    cat "$RUN_DIR/final_decision.md"
    echo ""
    echo "Worktree preserved at: $WORKTREE_PATH"
    echo "To clean up: git worktree remove $WORKTREE_PATH && git branch -D $WORKTREE_BRANCH"
    rm -f "$ALLOWED_FILES_TMP"
    exit 1
fi

# Handle missing or empty checklist
CHECKLIST_ITEMS=$(grep -c "^- \[ \]" "$RUN_DIR/critic_checklist.md" 2>/dev/null || echo "0")
# Trim whitespace from count
CHECKLIST_ITEMS=$(echo "$CHECKLIST_ITEMS" | tr -d '[:space:]')
if [[ "$CHECKLIST_ITEMS" -lt 1 ]]; then
    cat > "$RUN_DIR/final_decision.md" << DECISION
# Final Decision

## Final verdict: SETUP_FAILED

## Reason

Critic-Prep generated an empty checklist.

An empty checklist means Critic-Review has no evidentiary standard.
Builder was not started.
No attempts were consumed.

## Cleanup Commands

To remove the worktree:
\`\`\`bash
git worktree remove $WORKTREE_PATH
git branch -D $WORKTREE_BRANCH
\`\`\`
DECISION

    echo ""
    echo "=== SETUP_FAILED (Empty Checklist) ==="
    cat "$RUN_DIR/final_decision.md"
    echo ""
    echo "Checklist items found: $CHECKLIST_ITEMS"
    echo "Worktree preserved at: $WORKTREE_PATH"
    echo "To clean up: git worktree remove $WORKTREE_PATH && git branch -D $WORKTREE_BRANCH"
    rm -f "$ALLOWED_FILES_TMP"
    exit 1
fi

echo "✓ Checklist generated: $RUN_DIR/critic_checklist.md ($CHECKLIST_ITEMS items)"

# ── Retry Loop ────────────────────────────────────────────────────────────────

ATTEMPT=1
FINAL_VERDICT=""
LAST_GATE=""
LAST_SUMMARY=""

while [[ $ATTEMPT -le $MAX_ATTEMPTS ]]; do
    echo ""
    echo "=========================================="
    echo ">>> Attempt $ATTEMPT of $MAX_ATTEMPTS"
    echo "=========================================="

    # Create attempt directory
    ATTEMPT_DIR="$RUN_DIR/attempt-$ATTEMPT"
    mkdir -p "$ATTEMPT_DIR"

    # Reset worktree for retry (except first attempt)
    if [[ $ATTEMPT -gt 1 ]]; then
        reset_worktree "$WORKTREE_PATH" "$ORIGINAL_COMMIT"
    fi

    # ── Phase 1: Builder ───────────────────────────────────────────────────────

    echo ""
    echo ">>> Phase 1: Builder ($BUILDER_PROVIDER)"

    # Build prompt - original for first attempt, retry prompt for subsequent
    if [[ $ATTEMPT -eq 1 ]]; then
        cat > "$ATTEMPT_DIR/builder_prompt.md" << PROMPT
You are the Builder in a GateKeeper workflow.

Your job: Write the smallest patch that satisfies the task.

$(cat "$RUN_DIR/brief.md")

Rules:
- Make the smallest patch that satisfies the task.
- Edit ONLY the allowed files. Changing other files will cause automatic rejection.
- Do not run tests or broad commands; the Executor will run the evaluator.
- Do not commit.
- Do not run broad refactors.
- After editing, output a summary of changed files.

The Executor will run tests and Codex will review your patch.
Do not ask for confirmation. Just make the edits.
PROMPT
    else
        # Retry prompt with failure evidence
        PREV_ATTEMPT=$((ATTEMPT - 1))
        PREV_DIR="$RUN_DIR/attempt-$PREV_ATTEMPT"

        # Build retry prompt incrementally (like critic_prompt.md)
        cat > "$ATTEMPT_DIR/builder_prompt.md" << 'RETRY_HEADER'
You are the Builder in a GateKeeper workflow.

Previous attempt was rejected.
RETRY_HEADER

        cat >> "$ATTEMPT_DIR/builder_prompt.md" << RETRY_ATTEMPT

Attempt: $ATTEMPT of $MAX_ATTEMPTS
RETRY_ATTEMPT

        echo "" >> "$ATTEMPT_DIR/builder_prompt.md"
        echo "Retry evidence:" >> "$ATTEMPT_DIR/builder_prompt.md"
        echo '```markdown' >> "$ATTEMPT_DIR/builder_prompt.md"
        if [[ -f "$PREV_DIR/retry_evidence.md" ]]; then
            cat "$PREV_DIR/retry_evidence.md" >> "$ATTEMPT_DIR/builder_prompt.md"
        elif [[ -f "$PREV_DIR/summary.txt" ]]; then
            cat "$PREV_DIR/summary.txt" >> "$ATTEMPT_DIR/builder_prompt.md"
        else
            echo "Previous attempt was rejected, but no structured retry evidence was available." >> "$ATTEMPT_DIR/builder_prompt.md"
        fi
        echo '```' >> "$ATTEMPT_DIR/builder_prompt.md"

        # Add previous patch if exists
        if [[ -f "$PREV_DIR/patch.diff" ]]; then
            echo "" >> "$ATTEMPT_DIR/builder_prompt.md"
            echo "Previous patch (DO NOT repeat this):" >> "$ATTEMPT_DIR/builder_prompt.md"
            echo '```diff' >> "$ATTEMPT_DIR/builder_prompt.md"
            cat "$PREV_DIR/patch.diff" >> "$ATTEMPT_DIR/builder_prompt.md"
            echo '```' >> "$ATTEMPT_DIR/builder_prompt.md"
        fi

        # Add original task
        cat >> "$ATTEMPT_DIR/builder_prompt.md" << 'RETRY_TASK'

---

## Original Task
RETRY_TASK

        cat "$RUN_DIR/brief.md" >> "$ATTEMPT_DIR/builder_prompt.md"

        cat >> "$ATTEMPT_DIR/builder_prompt.md" << 'RETRY_FOOTER'

---

## Your Job

Fix the patch. Do not repeat the rejected mistake.
Write the smallest patch that satisfies the task and addresses the rejection reason.

Rules:
- Make the smallest patch that satisfies the task.
- Edit ONLY the allowed files. Changing other files will cause automatic rejection.
- Do not run tests or broad commands; the Executor will run the evaluator.
- Do not commit.
- Do not run broad refactors.
- After editing, output a summary of changed files.
RETRY_FOOTER
    fi

    echo "Running Builder ($BUILDER_PROVIDER) in worktree..."
    cd "$WORKTREE_PATH"

    set +e
    if [[ "$BUILDER_PROVIDER" == "claude" ]]; then
        claude --model "$CLAUDE_MODEL" --print --allowedTools "Read,Edit,Write" -p "$(cat "$ATTEMPT_DIR/builder_prompt.md")" 2>&1 | tee "$ATTEMPT_DIR/builder.log"
        BUILDER_EXIT=$?
    else
        if [[ -n "$CODEX_BUILDER_MODEL" ]]; then
            codex exec --model "$CODEX_BUILDER_MODEL" --sandbox workspace-write --skip-git-repo-check - < "$ATTEMPT_DIR/builder_prompt.md" 2>&1 | tee "$ATTEMPT_DIR/builder.log"
            BUILDER_EXIT=$?
        else
            codex exec --sandbox workspace-write --skip-git-repo-check - < "$ATTEMPT_DIR/builder_prompt.md" 2>&1 | tee "$ATTEMPT_DIR/builder.log"
            BUILDER_EXIT=$?
        fi
    fi
    set -e

    # Builder failure counts as attempt
    if [[ $BUILDER_EXIT -ne 0 ]]; then
        LAST_GATE="BUILDER"
        LAST_SUMMARY="Builder ($BUILDER_PROVIDER) failed with exit code $BUILDER_EXIT. See builder.log."
        echo "$LAST_SUMMARY" > "$ATTEMPT_DIR/summary.txt"
        write_attempt_decision "$ATTEMPT_DIR" "REJECT" "$LAST_GATE" "$LAST_SUMMARY"
        write_retry_evidence "$ATTEMPT_DIR" "$LAST_GATE" "$LAST_SUMMARY"

        # Continue to next attempt or escalate
        if [[ $ATTEMPT -lt $MAX_ATTEMPTS ]]; then
            echo ""
            echo "=== REJECTED (Builder Failed) ==="
            echo "Will retry with failure evidence..."
            ((ATTEMPT++))
            continue
        else
            FINAL_VERDICT="ESCALATE"
            break
        fi
    fi

    # Capture changed tracked files
    git -C "$WORKTREE_PATH" diff --name-only > "$ATTEMPT_DIR/changed_files.txt"

    # Capture new (untracked) files
    git -C "$WORKTREE_PATH" ls-files --others --exclude-standard > "$ATTEMPT_DIR/new_files.txt" || true

    # Combine for scope check
    cat "$ATTEMPT_DIR/changed_files.txt" "$ATTEMPT_DIR/new_files.txt" 2>/dev/null | sort -u > "$ATTEMPT_DIR/all_changed_files.txt" || true

    # Create comprehensive patch that includes new files
    if [[ -s "$ATTEMPT_DIR/new_files.txt" ]]; then
        echo "Including new files in patch..."
        while IFS= read -r newfile; do
            [[ -z "$newfile" ]] && continue
            git -C "$WORKTREE_PATH" add -N "$newfile" 2>/dev/null || true
        done < "$ATTEMPT_DIR/new_files.txt"
    fi

    # Capture full diff
    git -C "$WORKTREE_PATH" diff > "$ATTEMPT_DIR/patch.diff"

    echo ""
    echo "Patch saved: $ATTEMPT_DIR/patch.diff"
    echo "Changed files:"
    cat "$ATTEMPT_DIR/changed_files.txt" | sed 's/^/  /' || echo "  (none)"
    echo "New files:"
    cat "$ATTEMPT_DIR/new_files.txt" | sed 's/^/  /' || echo "  (none)"

    # Check if patch is empty
    if [[ ! -s "$ATTEMPT_DIR/patch.diff" ]]; then
        echo ""
        echo "WARNING: Empty patch - no changes detected"
    fi

    # ── Scope Verification ────────────────────────────────────────────────────────

    echo ""
    echo ">>> Scope Verification"

    if ! verify_allowed_files "$ATTEMPT_DIR/all_changed_files.txt" "$RUN_DIR/allowed_files.txt"; then
        LAST_GATE="SCOPE"
        LAST_SUMMARY="Patch modifies files outside allowed scope."
        echo "$LAST_SUMMARY" > "$ATTEMPT_DIR/summary.txt"
        write_attempt_decision "$ATTEMPT_DIR" "REJECT" "$LAST_GATE" "$LAST_SUMMARY"
        write_retry_evidence "$ATTEMPT_DIR" "$LAST_GATE" "$LAST_SUMMARY"

        if [[ $ATTEMPT -lt $MAX_ATTEMPTS ]]; then
            echo ""
            echo "=== REJECTED (Scope Violation) ==="
            echo "Will retry with failure evidence..."
            ((ATTEMPT++))
            continue
        else
            FINAL_VERDICT="ESCALATE"
            break
        fi
    fi

    echo "✓ All changes within allowed scope"

    # ── Phase 2: Executor ────────────────────────────────────────────────────────

    echo ""
    echo ">>> Phase 2: Executor"

    echo "Running: $EVAL_SCRIPT"
    set +e
    cd "$WORKTREE_PATH"

    # Set environment variable so evaluator knows worktree path
    export GATEKEEPER_WORKTREE="$WORKTREE_PATH"
    export GATEKEEPER_PROJECT_ROOT="$PROJECT_ROOT"
    export GATEKEEPER_RUN_DIR="$RUN_DIR"
    export GATEKEEPER_ATTEMPT="$ATTEMPT"
    export GATEKEEPER_ATTEMPT_DIR="$ATTEMPT_DIR"

    "$PROJECT_ROOT/$EVAL_SCRIPT" > "$ATTEMPT_DIR/eval.log" 2>&1
    EVAL_EXIT=$?
    set -e

    echo "Executor exit code: $EVAL_EXIT"

    # Executor failure counts as attempt
    if [[ $EVAL_EXIT -ne 0 ]]; then
        LAST_GATE="EXECUTOR"
        LAST_SUMMARY="Executor failed with exit code $EVAL_EXIT. See eval.log."
        echo "$LAST_SUMMARY" > "$ATTEMPT_DIR/summary.txt"
        write_attempt_decision "$ATTEMPT_DIR" "REJECT" "$LAST_GATE" "$LAST_SUMMARY"
        write_retry_evidence "$ATTEMPT_DIR" "$LAST_GATE" "$LAST_SUMMARY"

        if [[ $ATTEMPT -lt $MAX_ATTEMPTS ]]; then
            echo ""
            echo "=== REJECTED (Executor Failed) ==="
            echo "Will retry with failure evidence..."
            ((ATTEMPT++))
            continue
        else
            FINAL_VERDICT="ESCALATE"
            break
        fi
    fi

    # ── Phase 3: Critic-Review (Codex) ─────────────────────────────────────────────

    echo ""
    echo ">>> Phase 3: Critic-Review (Codex)"

    # Build critic prompt with pre-written checklist
    cat > "$ATTEMPT_DIR/critic_prompt.md" << 'CRITIC_HEADER'
You are the Critic-Review in a GateKeeper workflow.
You did NOT write this patch.

Default posture: REJECT unless the patch proves itself with evidence.

A checklist was written before the patch was created.
Your job is to verify that each checklist item has evidence.
CRITIC_HEADER

    # Include the pre-written checklist
    cat >> "$ATTEMPT_DIR/critic_prompt.md" << CRITIC_CHECKLIST

## Pre-written Checklist

This checklist was generated BEFORE the patch. You must verify each item.

CRITIC_CHECKLIST

    echo '```' >> "$ATTEMPT_DIR/critic_prompt.md"
    cat "$RUN_DIR/critic_checklist.md" >> "$ATTEMPT_DIR/critic_prompt.md"
    echo '```' >> "$ATTEMPT_DIR/critic_prompt.md"

    cat >> "$ATTEMPT_DIR/critic_prompt.md" << CRITIC_BODY

## Patch Diff
CRITIC_BODY

    echo '```diff' >> "$ATTEMPT_DIR/critic_prompt.md"
    cat "$ATTEMPT_DIR/patch.diff" >> "$ATTEMPT_DIR/critic_prompt.md"
    echo '```' >> "$ATTEMPT_DIR/critic_prompt.md"

    cat >> "$ATTEMPT_DIR/critic_prompt.md" << 'CRITIC_LOG'

## Executor Log
CRITIC_LOG

    echo '```' >> "$ATTEMPT_DIR/critic_prompt.md"
    cat "$ATTEMPT_DIR/eval.log" >> "$ATTEMPT_DIR/critic_prompt.md"
    echo '```' >> "$ATTEMPT_DIR/critic_prompt.md"

    cat >> "$ATTEMPT_DIR/critic_prompt.md" << 'CRITIC_FOOTER'

## Your Job

For EACH checklist item above:
1. Find evidence in the patch or executor log
2. If ANY checklist item lacks evidence, REJECT
3. If ALL checklist items have evidence, APPROVE

Do NOT write a new checklist. Use the pre-written checklist above.

If you REJECT, include a "Retry Evidence" section with:
- failed checklist item summary
- missing proof
- relevant patch/log location when available
- expected evidence shape

## Output Format (EXACT - use this format)

First, write your analysis showing evidence for each checklist item.

Then, on the LAST TWO LINES, output EXACTLY:

VERDICT: APPROVE
SUMMARY: <one line summary of why approved>

OR

VERDICT: REJECT
SUMMARY: <one line summary of what's missing>

The verdict line MUST start with "VERDICT:" and be one of APPROVE or REJECT.
This is parsed programmatically. Do not deviate from this format.
CRITIC_FOOTER

    echo "Running Codex (read-only sandbox)..."
    set +e
    cd "$WORKTREE_PATH"
codex exec --sandbox read-only --skip-git-repo-check - < "$ATTEMPT_DIR/critic_prompt.md" 2>&1 | tee "$ATTEMPT_DIR/critic.md"
    CODEX_EXIT=$?
    set -e

    # ── Phase 4: Attempt Decision ────────────────────────────────────────────────

    echo ""
    echo ">>> Phase 4: Attempt Decision"

    # If Codex CLI failed, force REJECT
    if [[ $CODEX_EXIT -ne 0 ]]; then
        VERDICT="REJECT"
        VERDICT_SUMMARY="Codex CLI failed with exit code $CODEX_EXIT"
    else
        VERDICT_LINE=$(grep -E "^VERDICT:" "$ATTEMPT_DIR/critic.md" | tail -1 || true)
        VERDICT_SUMMARY=$(grep -E "^SUMMARY:" "$ATTEMPT_DIR/critic.md" | tail -1 || true)

        # Require EXACT "VERDICT: APPROVE"
        if [[ "$VERDICT_LINE" == "VERDICT: APPROVE" ]]; then
            VERDICT="APPROVE"
        elif [[ "$VERDICT_LINE" == "VERDICT: REJECT" ]]; then
            VERDICT="REJECT"
        else
            VERDICT="REJECT"
            VERDICT_SUMMARY="Malformed critic output: no VERDICT line found"
        fi
    fi

    LAST_GATE="CRITIC"
    LAST_SUMMARY="${VERDICT_SUMMARY#SUMMARY: }"
    echo "$LAST_SUMMARY" > "$ATTEMPT_DIR/summary.txt"
    write_attempt_decision "$ATTEMPT_DIR" "$VERDICT" "$LAST_GATE" "$LAST_SUMMARY"
    if [[ "$VERDICT" == "REJECT" ]]; then
        write_retry_evidence "$ATTEMPT_DIR" "$LAST_GATE" "$LAST_SUMMARY"
    fi

    if [[ "$VERDICT" == "APPROVE" ]]; then
        FINAL_VERDICT="APPROVE"
        break
    else
        # REJECT - check if we can retry
        if [[ $ATTEMPT -lt $MAX_ATTEMPTS ]]; then
            echo ""
            echo "=== REJECTED (Critic) ==="
            echo "Will retry with failure evidence..."
            ((ATTEMPT++))
            continue
        else
            FINAL_VERDICT="ESCALATE"
            break
        fi
    fi
done

# ── Final Decision Package ─────────────────────────────────────────────────────

echo ""
echo "=========================================="
echo ">>> Final Decision Package"
echo "=========================================="

write_final_decision "$RUN_DIR" "$FINAL_VERDICT" "$ATTEMPT" "$WORKTREE_PATH" "$WORKTREE_BRANCH"
generate_report "$RUN_DIR" "$REPORT_FORMAT"

echo ""
echo "=== $FINAL_VERDICT ==="
echo ""
echo "Attempts used: $ATTEMPT / $MAX_ATTEMPTS"
echo "Final decision: $RUN_DIR/final_decision.md"
echo ""

if [[ "$FINAL_VERDICT" == "APPROVE" ]]; then
    APPROVED_ATTEMPT_DIR="$RUN_DIR/attempt-$ATTEMPT"

    if [[ $AUTO_APPLY -eq 1 ]]; then
        # Check if main worktree is clean before applying
        if [[ -n "$(git -C "$PROJECT_ROOT" status --porcelain)" ]]; then
            echo ""
            echo "ERROR: Main worktree is not clean. Cannot auto-apply."
            echo ""
            echo "Current status:"
            git -C "$PROJECT_ROOT" status --short
            echo ""
            echo "Commit or stash changes first, then manually apply:"
            echo "  git apply $APPROVED_ATTEMPT_DIR/patch.diff"
            echo ""
            echo "Worktree with approved changes: $WORKTREE_PATH"
            echo "To clean up: git worktree remove $WORKTREE_PATH && git branch -D $WORKTREE_BRANCH"
            exit 1
        fi

        echo "Auto-applying approved changes to main worktree..."

        # Apply the patch to main repo
        if [[ -s "$APPROVED_ATTEMPT_DIR/patch.diff" ]]; then
            git -C "$PROJECT_ROOT" apply "$APPROVED_ATTEMPT_DIR/patch.diff" || {
                echo ""
                echo "ERROR: Could not apply patch to main worktree"
                echo "Patch is saved at: $APPROVED_ATTEMPT_DIR/patch.diff"
                echo "Apply manually with: git apply $APPROVED_ATTEMPT_DIR/patch.diff"
                exit 1
            }
        fi

        cat "$RUN_DIR/final_decision.md"

        echo ""
        echo "Approved changes applied to main worktree."
        echo "Review and commit if acceptable:"
        echo '  git add -A && git commit -m "feat: <message>"'
        echo ""
        echo "Or discard:"
        echo "  git checkout -- . && git clean -fd"
    else
        cat "$RUN_DIR/final_decision.md"

        echo ""
        echo "=== APPROVED - Manual Action Required ==="
        echo ""
        echo "Worktree with approved changes: $WORKTREE_PATH"
        echo "Branch: $WORKTREE_BRANCH"
        echo ""
        echo "To apply to main worktree:"
        echo "  cd $PROJECT_ROOT"
        echo "  git apply $APPROVED_ATTEMPT_DIR/patch.diff"
        echo ""
        echo "To clean up worktree after applying:"
        echo "  git worktree remove $WORKTREE_PATH"
        echo "  git branch -D $WORKTREE_BRANCH"
    fi
elif [[ "$FINAL_VERDICT" == "ESCALATE" ]]; then
    cat "$RUN_DIR/final_decision.md"
    echo ""
    echo "=== ESCALATE - Human Review Required ==="
    echo ""
    echo "All $MAX_ATTEMPTS attempts failed."
    echo "Worktree preserved for human inspection: $WORKTREE_PATH"
    echo ""
    echo "Review the last attempt's artifacts:"
    echo "  - $RUN_DIR/attempt-$ATTEMPT/patch.diff"
    echo "  - $RUN_DIR/attempt-$ATTEMPT/eval.log"
    echo "  - $RUN_DIR/attempt-$ATTEMPT/critic.md"
    echo ""
    echo "After review, clean up:"
    echo "  git worktree remove $WORKTREE_PATH"
    echo "  git branch -D $WORKTREE_BRANCH"
else
    # Should not reach here
    echo "ERROR: Unexpected final verdict: $FINAL_VERDICT"
    exit 1
fi

# Cleanup temp files
rm -f "$ALLOWED_FILES_TMP"
