#!/usr/bin/env bash
#
# overclock_cli_loop.sh — Overclock Mode CLI Branch with Retry Loop
#
# Architecture:
#   Builder  = Claude Code CLI (Read, Edit, Write only — no Bash)
#   Executor = scripts/ from this project only
#   Critic   = Codex CLI (structured verdict output)
#   Human    = reads final decision package
#
# Retry Loop:
#   REJECT → retry with failure evidence → Builder retry → re-run
#   After max attempts, ESCALATE to human.
#
# Isolation:
#   Runs in a git worktree to avoid contaminating the main repo.
#   Worktree is preserved after run for human review.
#   Use --apply to copy approved changes to main worktree.
#
# Requirements:
#   - Must run in a git repository
#   - Claude Code and Codex CLI must be authenticated
#
# Usage:
#   ./scripts/overclock_cli_loop.sh [--apply] [--max-attempts N] <brief.md>
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
RUNS_DIR="$PROJECT_ROOT/overclock_runs"
WORKTREES_DIR="$PROJECT_ROOT/.overclock_worktrees"

# ── Parse Args ────────────────────────────────────────────────────────────────

AUTO_APPLY=0
MAX_ATTEMPTS=3
BRIEF_FILE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --help|-h)
            echo "Usage: $0 [--apply] [--max-attempts N] <brief.md>"
            echo ""
            echo "Options:"
            echo "  --apply            On APPROVE, automatically copy changes to main worktree"
            echo "  --max-attempts N   Maximum Builder attempts (default: 3, min: 1)"
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
        -*)
            echo "ERROR: Unknown option: $1"
            echo "Use --help for usage"
            exit 1
            ;;
        *)
            BRIEF_FILE="$1"
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
    echo "Usage: $0 [--apply] [--max-attempts N] <brief.md>"
    echo ""
    echo "Options:"
    echo "  --apply            On APPROVE, automatically copy changes to main worktree"
    echo "  --max-attempts N   Maximum Builder attempts (default: 3, min: 1)"
    echo ""
    echo "Use --help for more details."
    exit 1
fi

if [[ ! -f "$BRIEF_FILE" ]]; then
    echo "Error: Brief file not found: $BRIEF_FILE"
    exit 1
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
        if [[ "$line" =~ ^allowed_files: ]]; then
            in_allowed=1
            continue
        fi
        if [[ $in_allowed -eq 1 ]]; then
            if [[ "$line" =~ ^[[:space:]]*-[[:space:]]*(.+)$ ]]; then
                files+=("${BASH_REMATCH[1]}")
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
        [[ -z "$changed" ]] && continue

        local allowed=0
        while IFS= read -r pattern; do
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
DECISION
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
CLEANUP
    fi
}

# ── Pre-flight Checks ────────────────────────────────────────────────────────

echo "=== Pre-flight Checks ==="

# Check git repository
if ! git -C "$PROJECT_ROOT" rev-parse --git-dir &>/dev/null; then
    echo "ERROR: Not a git repository"
    echo ""
    echo "Overclock requires git for worktree isolation."
    echo "Initialize with:"
    echo "  cd $PROJECT_ROOT && git init && git add . && git commit -m 'init'"
    exit 1
fi

# Check CLI availability
if ! command -v claude &>/dev/null; then
    echo "ERROR: claude CLI not found"
    echo "Install: npm install -g @anthropic-ai/claude-code"
    exit 1
fi

if ! command -v codex &>/dev/null; then
    echo "ERROR: codex CLI not found"
    echo "Install: npm install -g @openai/codex"
    exit 1
fi

echo "✓ Git repository: OK"
echo "✓ Claude Code: $(claude --version | head -1)"
echo "✓ Codex CLI: $(codex --version)"
echo "✓ Max attempts: $MAX_ATTEMPTS"
if [[ $AUTO_APPLY -eq 1 ]]; then
    echo "✓ Auto-apply: ENABLED"
else
    echo "✓ Auto-apply: disabled (use --apply to enable)"
fi
echo ""

# ── Parse Brief ───────────────────────────────────────────────────────────────

EVAL_SCRIPT=$(grep -E "^eval_script:" "$BRIEF_FILE" | head -1 | sed 's/^eval_script: *//' || true)

if [[ -z "$EVAL_SCRIPT" ]]; then
    echo "ERROR: brief must specify eval_script"
    echo "Example: eval_script: scripts/evaluators/evaluate_xxx.sh"
    exit 1
fi

if [[ "$EVAL_SCRIPT" != scripts/* ]]; then
    echo "ERROR: eval_script must be under scripts/ directory"
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
WORKTREE_BRANCH="overclock/$TIMESTAMP"
WORKTREE_PATH="$WORKTREES_DIR/$TIMESTAMP"

echo "=== OVERCLOCK CLI LOOP (Retry Loop v1) ==="
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

    # ── Phase 1: Builder (Claude Code) ───────────────────────────────────────────

    echo ""
    echo ">>> Phase 1: Builder (Claude Code)"

    # Build prompt - original for first attempt, retry prompt for subsequent
    if [[ $ATTEMPT -eq 1 ]]; then
        cat > "$ATTEMPT_DIR/builder_prompt.md" << PROMPT
You are the Builder in an Overclock workflow.

Your job: Write the smallest patch that satisfies the task.

$(cat "$BRIEF_FILE")

Rules:
- Make the smallest patch that satisfies the task.
- Edit ONLY the allowed files. Changing other files will cause automatic rejection.
- Do not run any shell commands or tests.
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

        cat > "$ATTEMPT_DIR/builder_prompt.md" << PROMPT
You are the Builder in an Overclock workflow.

Previous attempt was rejected.

Attempt: $ATTEMPT of $MAX_ATTEMPTS

Reason:
$(cat "$PREV_DIR/summary.txt" 2>/dev/null || echo "Unknown")

$(if [[ -f "$PREV_DIR/eval.log" ]]; then
echo "Executor log:"
echo '```'"
cat "$PREV_DIR/eval.log"
echo '```'
fi)

$(if [[ -f "$PREV_DIR/critic.md" ]]; then
echo "Critic notes:"
echo '```'"
cat "$PREV_DIR/critic.md"
echo '```'
fi)

$(if [[ -f "$PREV_DIR/patch.diff" ]]; then
echo "Previous patch (DO NOT repeat this):"
echo '```diff'"
cat "$PREV_DIR/patch.diff"
echo '```'
fi)

---

## Original Task

$(cat "$BRIEF_FILE")

---

## Your Job

Fix the patch. Do not repeat the rejected mistake.
Write the smallest patch that satisfies the task and addresses the rejection reason.

Rules:
- Make the smallest patch that satisfies the task.
- Edit ONLY the allowed files. Changing other files will cause automatic rejection.
- Do not run any shell commands or tests.
- Do not commit.
- Do not run broad refactors.
- After editing, output a summary of changed files.
PROMPT
    fi

    echo "Running Claude Code in worktree..."
    cd "$WORKTREE_PATH"

    set +e
    claude --print --allowedTools "Read,Edit,Write" -p "$(cat "$ATTEMPT_DIR/builder_prompt.md")" 2>&1 | tee "$ATTEMPT_DIR/builder.log"
    CLAUDE_EXIT=$?
    set -e

    # Builder failure counts as attempt
    if [[ $CLAUDE_EXIT -ne 0 ]]; then
        LAST_GATE="BUILDER"
        LAST_SUMMARY="Builder (Claude Code) failed with exit code $CLAUDE_EXIT. See builder.log."
        echo "$LAST_SUMMARY" > "$ATTEMPT_DIR/summary.txt"
        write_attempt_decision "$ATTEMPT_DIR" "REJECT" "$LAST_GATE" "$LAST_SUMMARY"

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
    export OVERCLOCK_WORKTREE="$WORKTREE_PATH"
    export OVERCLOCK_PROJECT_ROOT="$PROJECT_ROOT"

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

    # ── Phase 3: Critic (Codex) ──────────────────────────────────────────────────

    echo ""
    echo ">>> Phase 3: Critic (Codex)"

    # Build critic prompt
    cat > "$ATTEMPT_DIR/critic_prompt.md" << 'CRITIC_HEADER'
You are the Critic in an Overclock workflow.
You did NOT write this patch.

Default posture: REJECT unless the patch proves itself with evidence.
CRITIC_HEADER

    cat >> "$ATTEMPT_DIR/critic_prompt.md" << CRITIC_BODY

## Task Brief
$(cat "$RUN_DIR/brief.md")

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

1. Write a checklist of what must be proven.
2. For each checklist item, find evidence in the patch or executor log.
3. If ANY item lacks evidence, REJECT.
4. If ALL items have evidence, APPROVE.

## Output Format (EXACT - use this format)

First, write your analysis.

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
    codex exec --sandbox read-only --skip-git-repo-check "$(cat "$ATTEMPT_DIR/critic_prompt.md")" 2>&1 | tee "$ATTEMPT_DIR/critic.md"
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
