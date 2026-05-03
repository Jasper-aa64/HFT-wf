#!/usr/bin/env bash
#
# overclock_cli_loop.sh — Overclock Mode CLI Branch
#
# Architecture:
#   Builder  = Claude Code CLI (Read, Edit, Write only — no Bash)
#   Executor = scripts/ from this project only
#   Critic   = Codex CLI (structured verdict output)
#   Human    = reads final decision package
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
#   ./scripts/overclock_cli_loop.sh [--apply] <brief.md>
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
RUNS_DIR="$PROJECT_ROOT/overclock_runs"
WORKTREES_DIR="$PROJECT_ROOT/.overclock_worktrees"

# ── Parse Args ────────────────────────────────────────────────────────────────

AUTO_APPLY=0
BRIEF_FILE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --apply)
            AUTO_APPLY=1
            shift
            ;;
        *)
            BRIEF_FILE="$1"
            shift
            ;;
    esac
done

if [[ -z "$BRIEF_FILE" ]]; then
    echo "Usage: $0 [--apply] <brief.md>"
    echo ""
    echo "Options:"
    echo "  --apply    On APPROVE, automatically copy changes to main worktree"
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

## Allowed Files

- path/to/file1
- path/to/file2

## Evaluator Script

scripts/evaluate_xxx.sh

## Checklist

- [ ] Item 1
- [ ] Item 2

NOTES:
- eval_script must be a path under scripts/ (security)
- allowed_files supports glob patterns (e.g., "src/*.cpp")
EXAMPLE
    exit 1
fi

if [[ ! -f "$BRIEF_FILE" ]]; then
    echo "Error: Brief file not found: $BRIEF_FILE"
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

echo "=== OVERCLOCK CLI LOOP ==="
echo "Time: $TIMESTAMP"
echo "Run dir: $RUN_DIR"
echo "Worktree: $WORKTREE_PATH"
echo "Branch: $WORKTREE_BRANCH"
echo "Brief: $BRIEF_FILE"

# Copy brief
cp "$BRIEF_FILE" "$RUN_DIR/brief.md"
cp "$ALLOWED_FILES_TMP" "$RUN_DIR/allowed_files.txt"

# ── Create Worktree ──────────────────────────────────────────────────────────

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

# ── Phase 1: Builder (Claude Code) ───────────────────────────────────────────

echo ""
echo ">>> Phase 1: Builder (Claude Code)"

BUILDER_PROMPT=$(cat <<PROMPT
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
)

echo "$BUILDER_PROMPT" > "$RUN_DIR/builder_prompt.md"

# Run Claude Code IN THE WORKTREE
echo "Running Claude Code in worktree..."
cd "$WORKTREE_PATH"

set +e
claude --print --allowedTools "Read,Edit,Write" -p "$BUILDER_PROMPT" 2>&1 | tee "$RUN_DIR/builder.log"
CLAUDE_EXIT=$?
set -e

if [[ $CLAUDE_EXIT -ne 0 ]]; then
    echo ""
    echo "Builder exited with code $CLAUDE_EXIT"
fi

# Capture changed tracked files
git -C "$WORKTREE_PATH" diff --name-only > "$RUN_DIR/changed_files.txt"

# Capture new (untracked) files
git -C "$WORKTREE_PATH" ls-files --others --exclude-standard > "$RUN_DIR/new_files.txt" || true

# Combine for scope check
cat "$RUN_DIR/changed_files.txt" "$RUN_DIR/new_files.txt" 2>/dev/null | sort -u > "$RUN_DIR/all_changed_files.txt" || true

# Create comprehensive patch that includes new files
# Use 'git add -N' to stage new files without content, then diff
if [[ -s "$RUN_DIR/new_files.txt" ]]; then
    echo "Including new files in patch..."
    while IFS= read -r newfile; do
        [[ -z "$newfile" ]] && continue
        git -C "$WORKTREE_PATH" add -N "$newfile" 2>/dev/null || true
    done < "$RUN_DIR/new_files.txt"
fi

# Now capture full diff (including new files)
git -C "$WORKTREE_PATH" diff > "$RUN_DIR/patch.diff"

echo ""
echo "Patch saved: $RUN_DIR/patch.diff"
echo "Changed files:"
cat "$RUN_DIR/changed_files.txt" | sed 's/^/  /' || echo "  (none)"
echo "New files:"
cat "$RUN_DIR/new_files.txt" | sed 's/^/  /' || echo "  (none)"

# Check if patch is empty
if [[ ! -s "$RUN_DIR/patch.diff" ]]; then
    echo ""
    echo "WARNING: Empty patch - no changes detected"
fi

# ── Scope Verification ────────────────────────────────────────────────────────

echo ""
echo ">>> Scope Verification"

if ! verify_allowed_files "$RUN_DIR/all_changed_files.txt" "$RUN_DIR/allowed_files.txt"; then
    cat > "$RUN_DIR/decision.md" << DECISION
# Decision

## Verdict: REJECT

## Reason
Patch modifies files outside allowed scope.

## Changed Files (violations)
$(cat "$RUN_DIR/all_changed_files.txt")

## Allowed Files
$(cat "$RUN_DIR/allowed_files.txt")
DECISION

    echo ""
    echo "=== REJECTED (Scope Violation) ==="
    cat "$RUN_DIR/decision.md"
    echo ""
    echo "Worktree preserved at: $WORKTREE_PATH"
    echo "To clean up: git worktree remove $WORKTREE_PATH && git branch -D $WORKTREE_BRANCH"
    exit 1
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

"$PROJECT_ROOT/$EVAL_SCRIPT" > "$RUN_DIR/eval.log" 2>&1
EVAL_EXIT=$?
set -e

echo "Executor exit code: $EVAL_EXIT"

# ── Phase 3: Critic (Codex) ──────────────────────────────────────────────────

echo ""
echo ">>> Phase 3: Critic (Codex)"

if [[ $EVAL_EXIT -ne 0 ]]; then
    cat > "$RUN_DIR/decision.md" << DECISION
# Decision

## Verdict: REJECT

## Reason
Executor failed with exit code $EVAL_EXIT.

## Executor Log
\`\`\`
$(cat "$RUN_DIR/eval.log")
\`\`\`

## Patch
See $RUN_DIR/patch.diff

## Next Steps
1. Review the executor log for failures
2. Fix the issues in the patch
3. Re-run the loop
DECISION

    echo ""
    echo "=== REJECTED (Executor Failed) ==="
    cat "$RUN_DIR/decision.md"
    echo ""
    echo "Worktree preserved at: $WORKTREE_PATH"
    echo "To clean up: git worktree remove $WORKTREE_PATH && git branch -D $WORKTREE_BRANCH"
    exit $EVAL_EXIT
fi

# Executor passed - run Codex review
CRITIC_PROMPT=$(cat <<PROMPT
You are the Critic in an Overclock workflow.
You did NOT write this patch.

Default posture: REJECT unless the patch proves itself with evidence.

## Task Brief
$(cat "$RUN_DIR/brief.md")

## Patch Diff
\`\`\`diff
$(cat "$RUN_DIR/patch.diff")
\`\`\`

## Executor Log
\`\`\`
$(cat "$RUN_DIR/eval.log")
\`\`\`

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
PROMPT
)

echo "$CRITIC_PROMPT" > "$RUN_DIR/critic_prompt.md"

echo "Running Codex..."
set +e
cd "$WORKTREE_PATH"
codex exec --skip-git-repo-check "$CRITIC_PROMPT" 2>&1 | tee "$RUN_DIR/critic.md"
CODEX_EXIT=$?
set -e

# ── Phase 4: Decision Package ────────────────────────────────────────────────

echo ""
echo ">>> Phase 4: Decision Package"

VERDICT_LINE=$(grep -E "^VERDICT:" "$RUN_DIR/critic.md" | tail -1 || true)
VERDICT_SUMMARY=$(grep -E "^SUMMARY:" "$RUN_DIR/critic.md" | tail -1 || true)

if [[ "$VERDICT_LINE" == "VERDICT: APPROVE" ]]; then
    VERDICT="APPROVE"
elif [[ "$VERDICT_LINE" == "VERDICT: REJECT" ]]; then
    VERDICT="REJECT"
else
    if grep -qE "^APPROVE\b" "$RUN_DIR/critic.md" 2>/dev/null; then
        VERDICT="APPROVE"
    else
        VERDICT="REJECT"
    fi
    VERDICT_SUMMARY="VERDICT: $VERDICT (fallback parsing)"
fi

cat > "$RUN_DIR/decision.md" << DECISION
# Decision Package — $TIMESTAMP

## Task
$(head -20 "$RUN_DIR/brief.md")

## Verdict: $VERDICT

## Summary
${VERDICT_SUMMARY#SUMMARY: }

## Changed Files
$(cat "$RUN_DIR/all_changed_files.txt" 2>/dev/null || echo "No changes")

## Scope Check
$(cat "$RUN_DIR/allowed_files.txt" | sed 's/^/Allowed: /')

## Builder Log
See: $RUN_DIR/builder.log

## Executor Result
- Exit code: $EVAL_EXIT
- Script: $EVAL_SCRIPT
- Log: $RUN_DIR/eval.log

## Critic Result
- Exit code: $CODEX_EXIT
- Log: $RUN_DIR/critic.md

## Artifacts
- brief.md
- builder.log
- patch.diff
- eval.log
- critic.md
- changed_files.txt
- new_files.txt
- allowed_files.txt

## Worktree
- Path: $WORKTREE_PATH
- Branch: $WORKTREE_BRANCH
DECISION

echo ""
echo "=== $VERDICT ==="
echo ""
echo "Decision package: $RUN_DIR/decision.md"
echo ""

if [[ "$VERDICT" == "APPROVE" ]]; then
    if [[ $AUTO_APPLY -eq 1 ]]; then
        echo "Auto-applying approved changes to main worktree..."

        # Apply the patch to main repo
        if [[ -s "$RUN_DIR/patch.diff" ]]; then
            git -C "$PROJECT_ROOT" apply "$RUN_DIR/patch.diff" || {
                echo ""
                echo "ERROR: Could not apply patch to main worktree"
                echo "Patch is saved at: $RUN_DIR/patch.diff"
                echo "Apply manually with: git apply $RUN_DIR/patch.diff"
            }
        fi

        # Copy new files
        if [[ -s "$RUN_DIR/new_files.txt" ]]; then
            while IFS= read -r newfile; do
                [[ -z "$newfile" ]] && continue
                mkdir -p "$PROJECT_ROOT/$(dirname "$newfile")"
                cp "$WORKTREE_PATH/$newfile" "$PROJECT_ROOT/$newfile"
            done < "$RUN_DIR/new_files.txt"
        fi

        cat "$RUN_DIR/decision.md"

        echo ""
        echo "Approved changes applied to main worktree."
        echo "Review and commit if acceptable:"
        echo "  git add -A && git commit -m 'feat: <message>'"
        echo ""
        echo "Or discard:"
        echo "  git checkout -- . && git clean -fd"
    else
        cat "$RUN_DIR/decision.md"

        echo ""
        echo "=== APPROVED - Manual Action Required ==="
        echo ""
        echo "Worktree with approved changes: $WORKTREE_PATH"
        echo "Branch: $WORKTREE_BRANCH"
        echo ""
        echo "To apply to main worktree:"
        echo "  cd $PROJECT_ROOT"
        echo "  git apply $RUN_DIR/patch.diff"
        echo "  # Or copy new files manually from $WORKTREE_PATH"
        echo ""
        echo "To clean up worktree after applying:"
        echo "  git worktree remove $WORKTREE_PATH"
        echo "  git branch -D $WORKTREE_BRANCH"
    fi
else
    cat "$RUN_DIR/decision.md"
    echo ""
    echo "=== REJECTED ==="
    echo ""
    echo "Worktree preserved for inspection: $WORKTREE_PATH"
    echo "To clean up: git worktree remove $WORKTREE_PATH && git branch -D $WORKTREE_BRANCH"
fi

# Cleanup temp files
rm -f "$ALLOWED_FILES_TMP"
