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
# Requirements:
#   - Must run in a git repository
#   - Working directory must be completely clean (tracked + untracked)
#   - Claude Code and Codex CLI must be authenticated
#
# Usage:
#   ./scripts/overclock_cli_loop.sh <brief.md>
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
RUNS_DIR="$PROJECT_ROOT/overclock_runs"

# ── Helper Functions ──────────────────────────────────────────────────────────

cleanup_changes() {
    # Reset tracked files
    git -C "$PROJECT_ROOT" checkout -- . 2>/dev/null || true

    # Remove untracked files created during this run
    if [[ -f "$RUN_DIR/created_files.txt" ]] && [[ -s "$RUN_DIR/created_files.txt" ]]; then
        while IFS= read -r file; do
            rm -f "$PROJECT_ROOT/$file" 2>/dev/null || true
        done < "$RUN_DIR/created_files.txt"
    fi

    # Remove empty directories created during this run
    if [[ -f "$RUN_DIR/created_dirs.txt" ]] && [[ -s "$RUN_DIR/created_dirs.txt" ]]; then
        while IFS= read -r dir; do
            rmdir "$PROJECT_ROOT/$dir" 2>/dev/null || true
        done < "$RUN_DIR/created_dirs.txt"
    fi
}

extract_allowed_files() {
    # Extract allowed_files from YAML frontmatter
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
            # Support glob patterns
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
    echo "Overclock requires git for patch capture."
    echo "Initialize with:"
    echo "  cd $PROJECT_ROOT && git init && git add . && git commit -m 'init'"
    exit 1
fi

# Check working directory is completely clean (including untracked)
if [[ -n "$(git -C "$PROJECT_ROOT" status --porcelain)" ]]; then
    echo "ERROR: Working directory is not clean"
    echo ""
    echo "Overclock requires a completely clean working directory."
    echo "Current status:"
    git -C "$PROJECT_ROOT" status --short
    echo ""
    echo "Commit, stash, or remove untracked files:"
    echo "  git add -A && git commit -m 'wip'"
    echo "  git clean -fd  # WARNING: removes untracked files"
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
echo "✓ Working directory: clean (tracked + untracked)"
echo "✓ Claude Code: $(claude --version | head -1)"
echo "✓ Codex CLI: $(codex --version)"
echo ""

# ── Args ─────────────────────────────────────────────────────────────────────

if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <brief.md>"
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

BRIEF_FILE="$1"
if [[ ! -f "$BRIEF_FILE" ]]; then
    echo "Error: Brief file not found: $BRIEF_FILE"
    exit 1
fi

# ── Parse Brief ───────────────────────────────────────────────────────────────

# Extract eval_script (must be a script path under scripts/)
EVAL_SCRIPT=$(grep -E "^eval_script:" "$BRIEF_FILE" | head -1 | sed 's/^eval_script: *//' || true)

# Security: only allow scripts under PROJECT_ROOT/scripts/
if [[ -z "$EVAL_SCRIPT" ]]; then
    echo "ERROR: brief must specify eval_script"
    echo "Example: eval_script: scripts/evaluate_safe_divide.sh"
    exit 1
fi

# Normalize path and verify it's under scripts/
EVAL_SCRIPT_PATH="$PROJECT_ROOT/${EVAL_SCRIPT#scripts/}"
EVAL_SCRIPT_PATH="${EVAL_SCRIPT_PATH/\/scripts\//scripts/}"

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
    echo "Making evaluator script executable..."
    chmod +x "$EVAL_SCRIPT_FULL"
fi

echo "Evaluator: $EVAL_SCRIPT"

# Extract allowed_files
ALLOWED_FILES_TMP=$(mktemp)
extract_allowed_files "$BRIEF_FILE" > "$ALLOWED_FILES_TMP"

if [[ ! -s "$ALLOWED_FILES_TMP" ]]; then
    echo "ERROR: brief must specify allowed_files"
    exit 1
fi

echo "Allowed files:"
cat "$ALLOWED_FILES_TMP" | sed 's/^/  /'
echo ""

# ── Setup Run Directory ──────────────────────────────────────────────────────

TIMESTAMP=$(date +%Y%m%d-%H%M%S)
RUN_DIR="$RUNS_DIR/$TIMESTAMP"
mkdir -p "$RUN_DIR"

echo "=== OVERCLOCK CLI LOOP ==="
echo "Time: $TIMESTAMP"
echo "Run dir: $RUN_DIR"
echo "Brief: $BRIEF_FILE"

# Copy brief
cp "$BRIEF_FILE" "$RUN_DIR/brief.md"
cp "$ALLOWED_FILES_TMP" "$RUN_DIR/allowed_files.txt"

# ── Record Pre-existing State ────────────────────────────────────────────────

# Record files that exist before Builder runs
git -C "$PROJECT_ROOT" ls-files > "$RUN_DIR/preexisting_tracked.txt"
find "$PROJECT_ROOT" -type f -not -path "$PROJECT_ROOT/.git/*" -not -path "$RUNS_DIR/*" | \
    sed "s|^$PROJECT_ROOT/||" | sort > "$RUN_DIR/preexisting_all.txt" 2>/dev/null || true

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

# Run Claude Code — NO Bash permission, only file operations
echo "Running Claude Code..."
cd "$PROJECT_ROOT"

set +e
claude --print --allowedTools "Read,Edit,Write" -p "$BUILDER_PROMPT" 2>&1 | tee "$RUN_DIR/builder.log"
CLAUDE_EXIT=$?
set -e

if [[ $CLAUDE_EXIT -ne 0 ]]; then
    echo ""
    echo "Builder exited with code $CLAUDE_EXIT"
fi

# Capture patch and changes
git -C "$PROJECT_ROOT" diff > "$RUN_DIR/patch.diff"
git -C "$PROJECT_ROOT" diff --name-only > "$RUN_DIR/changed_tracked.txt"

# Find newly created (untracked) files
find "$PROJECT_ROOT" -type f -not -path "$PROJECT_ROOT/.git/*" -not -path "$RUNS_DIR/*" | \
    sed "s|^$PROJECT_ROOT/||" | sort > "$RUN_DIR/postexisting_all.txt" 2>/dev/null || true

comm -13 "$RUN_DIR/preexisting_all.txt" "$RUN_DIR/postexisting_all.txt" > "$RUN_DIR/created_files.txt" || true

# Find newly created directories
find "$PROJECT_ROOT" -type d -empty -not -path "$PROJECT_ROOT/.git/*" -not -path "$RUNS_DIR/*" 2>/dev/null | \
    sed "s|^$PROJECT_ROOT/||" | sort > "$RUN_DIR/created_dirs.txt" || true

# Combined changed files (tracked changes + new files)
cat "$RUN_DIR/changed_tracked.txt" "$RUN_DIR/created_files.txt" 2>/dev/null | sort -u > "$RUN_DIR/changed_files.txt"

echo ""
echo "Patch saved: $RUN_DIR/patch.diff"
echo "Changed files:"
cat "$RUN_DIR/changed_files.txt" | sed 's/^/  /' || echo "  (none)"

# Check if patch is empty
if [[ ! -s "$RUN_DIR/patch.diff" ]] && [[ ! -s "$RUN_DIR/created_files.txt" ]]; then
    echo ""
    echo "WARNING: Empty patch - no changes detected"
fi

# ── Scope Verification ────────────────────────────────────────────────────────

echo ""
echo ">>> Scope Verification"

if ! verify_allowed_files "$RUN_DIR/changed_files.txt" "$RUN_DIR/allowed_files.txt"; then
    cat > "$RUN_DIR/decision.md" << DECISION
# Decision

## Verdict: REJECT

## Reason
Patch modifies files outside allowed scope.

## Changed Files (violations)
$(cat "$RUN_DIR/changed_files.txt")

## Allowed Files
$(cat "$RUN_DIR/allowed_files.txt")
DECISION

    cleanup_changes

    echo ""
    echo "=== REJECTED (Scope Violation) ==="
    cat "$RUN_DIR/decision.md"
    exit 1
fi

echo "✓ All changes within allowed scope"

# ── Phase 2: Executor ────────────────────────────────────────────────────────

echo ""
echo ">>> Phase 2: Executor"

echo "Running: $EVAL_SCRIPT_FULL"
set +e
cd "$PROJECT_ROOT"
"$EVAL_SCRIPT_FULL" > "$RUN_DIR/eval.log" 2>&1
EVAL_EXIT=$?
set -e

echo "Executor exit code: $EVAL_EXIT"

# ── Phase 3: Critic (Codex) ──────────────────────────────────────────────────

echo ""
echo ">>> Phase 3: Critic (Codex)"

if [[ $EVAL_EXIT -ne 0 ]]; then
    # Executor failed - auto reject
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

    cleanup_changes

    echo ""
    echo "=== REJECTED (Executor Failed) ==="
    echo "Decision: $RUN_DIR/decision.md"
    echo ""
    cat "$RUN_DIR/decision.md"
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
cd "$PROJECT_ROOT"
codex exec --skip-git-repo-check "$CRITIC_PROMPT" 2>&1 | tee "$RUN_DIR/critic.md"
CODEX_EXIT=$?
set -e

# ── Phase 4: Decision Package ────────────────────────────────────────────────

echo ""
echo ">>> Phase 4: Decision Package"

# Extract verdict from last lines (structured format)
VERDICT_LINE=$(grep -E "^VERDICT:" "$RUN_DIR/critic.md" | tail -1 || true)
VERDICT_SUMMARY=$(grep -E "^SUMMARY:" "$RUN_DIR/critic.md" | tail -1 || true)

if [[ "$VERDICT_LINE" == "VERDICT: APPROVE" ]]; then
    VERDICT="APPROVE"
elif [[ "$VERDICT_LINE" == "VERDICT: REJECT" ]]; then
    VERDICT="REJECT"
else
    # Fallback: look for APPROVE/REJECT anywhere
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
$(cat "$RUN_DIR/changed_files.txt" 2>/dev/null || echo "No changes")

## Scope Check
$(cat "$RUN_DIR/allowed_files.txt" | sed 's/^/Allowed: /')

## Builder Log
See: $RUN_DIR/builder.log

## Executor Result
- Exit code: $EVAL_EXIT
- Script: $EVAL_SCRIPT
- Log: $RUN_DIR/eval.log

## Critic Review
$(cat "$RUN_DIR/critic.md")

## Artifacts
- brief.md
- builder.log
- patch.diff
- eval.log
- critic.md
- changed_files.txt
- allowed_files.txt
DECISION

if [[ "$VERDICT" == "REJECT" ]]; then
    cleanup_changes
fi

echo ""
echo "=== $VERDICT ==="
echo ""
echo "Decision package: $RUN_DIR/decision.md"
echo ""
cat "$RUN_DIR/decision.md"

# Final status
if [[ "$VERDICT" == "APPROVE" ]]; then
    echo ""
    echo "Changes preserved. Review and commit if acceptable:"
    echo "  git add -A && git commit -m 'feat: <message>'"
else
    echo ""
    echo "Changes reverted. See decision.md for details."
fi

# Cleanup temp files
rm -f "$ALLOWED_FILES_TMP"
