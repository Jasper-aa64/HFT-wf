#!/usr/bin/env bash
#
# overclock_cli_loop.sh — Overclock Mode CLI Branch
#
# Architecture:
#   Builder  = Claude Code CLI
#   Executor = shell command from brief
#   Critic   = Codex CLI
#   Human    = reads final decision package
#
# Requirements:
#   - Must run in a git repository (for patch capture)
#   - Working directory must be clean (no uncommitted changes)
#   - Claude Code and Codex CLI must be authenticated
#
# Usage:
#   ./scripts/overclock_cli_loop.sh <brief.md>
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
RUNS_DIR="$PROJECT_ROOT/overclock_runs"

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

# Check working directory is clean
if ! git -C "$PROJECT_ROOT" diff-index --quiet HEAD -- 2>/dev/null; then
    echo "ERROR: Working directory has uncommitted changes"
    echo ""
    echo "Overclock requires a clean working directory."
    echo "Commit or stash your changes first:"
    echo "  git status"
    echo "  git stash"
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
echo "✓ Working directory: clean"
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
eval_command: <shell command to run>
checklist:
  - <item 1>
  - <item 2>
---

## Task Description

<detailed description>

## Allowed Files

- path/to/file1
- path/to/file2

## Evaluator Command

```bash
<command>
```

## Checklist

- [ ] Item 1
- [ ] Item 2
EXAMPLE
    exit 1
fi

BRIEF_FILE="$1"
if [[ ! -f "$BRIEF_FILE" ]]; then
    echo "Error: Brief file not found: $BRIEF_FILE"
    exit 1
fi

# ── Parse Brief ───────────────────────────────────────────────────────────────

# Extract eval_command from brief (looking for eval_command: or ```bash block)
EVAL_COMMAND=$(grep -E "^eval_command:" "$BRIEF_FILE" | head -1 | sed 's/^eval_command: *//' || true)

# If no eval_command in frontmatter, look for code block after "Evaluator Command"
if [[ -z "$EVAL_COMMAND" ]]; then
    EVAL_COMMAND=$(awk '/## Evaluator Command/,/```bash/{/```bash/{getline; print; exit}}' "$BRIEF_FILE" 2>/dev/null || true)
fi

# Default to evaluate.sh if no command specified
if [[ -z "$EVAL_COMMAND" ]]; then
    EVAL_COMMAND="$SCRIPT_DIR/evaluate.sh"
fi

echo "Evaluator: $EVAL_COMMAND"

# ── Setup Run Directory ──────────────────────────────────────────────────────

TIMESTAMP=$(date +%Y%m%d-%H%M%S)
RUN_DIR="$RUNS_DIR/$TIMESTAMP"
mkdir -p "$RUN_DIR"

echo ""
echo "=== OVERCLOCK CLI LOOP ==="
echo "Time: $TIMESTAMP"
echo "Run dir: $RUN_DIR"
echo "Brief: $BRIEF_FILE"

# Copy brief
cp "$BRIEF_FILE" "$RUN_DIR/brief.md"

# ── Phase 1: Builder (Claude Code) ───────────────────────────────────────────

echo ""
echo ">>> Phase 1: Builder (Claude Code)"

BUILDER_PROMPT=$(cat <<PROMPT
You are the Builder in an Overclock workflow.

Your job: Write the smallest patch that satisfies the task.

$(cat "$BRIEF_FILE")

Rules:
- Make the smallest patch that satisfies the task.
- Edit ONLY the allowed files.
- Do not change tests or golden outputs unless explicitly allowed.
- Do not commit.
- Do not run broad refactors.
- After editing, output a summary of changed files.

The Executor will run tests and Codex will review your patch.
Do not ask for confirmation. Just make the edits.
PROMPT
)

echo "$BUILDER_PROMPT" > "$RUN_DIR/builder_prompt.md"

# Run Claude Code with --allowedTools to auto-accept edits
echo "Running Claude Code..."
cd "$PROJECT_ROOT"

set +e
# Use --print for non-interactive mode, auto-accept edits
claude --print --allowedTools "Edit,Write,Read,Bash" -p "$BUILDER_PROMPT" 2>&1 | tee "$RUN_DIR/builder.log"
CLAUDE_EXIT=$?
set -e

if [[ $CLAUDE_EXIT -ne 0 ]]; then
    echo ""
    echo "Builder exited with code $CLAUDE_EXIT"
fi

# Capture patch
git -C "$PROJECT_ROOT" diff > "$RUN_DIR/patch.diff"
git -C "$PROJECT_ROOT" diff --name-only > "$RUN_DIR/changed_files.txt"

echo ""
echo "Patch saved: $RUN_DIR/patch.diff"
echo "Changed files:"
cat "$RUN_DIR/changed_files.txt" | sed 's/^/  /'

# Check if patch is empty
if [[ ! -s "$RUN_DIR/patch.diff" ]]; then
    echo ""
    echo "WARNING: Empty patch - no changes detected"
fi

# ── Phase 2: Executor ────────────────────────────────────────────────────────

echo ""
echo ">>> Phase 2: Executor"

echo "Running: $EVAL_COMMAND"
set +e
cd "$PROJECT_ROOT"
eval "$EVAL_COMMAND" > "$RUN_DIR/eval.log" 2>&1
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

    # Reset changes on failure
    git -C "$PROJECT_ROOT" checkout -- .

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

## Output Format

REJECT
Missing evidence:
- <item>: <what's missing>
Required next action:
- <action>

OR

APPROVE
Evidence:
- <item>: <specific evidence>
Remaining risk:
- <risk>
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

# Extract verdict from critic output
if grep -q "^APPROVE" "$RUN_DIR/critic.md" 2>/dev/null; then
    VERDICT="APPROVE"
else
    VERDICT="REJECT"
    # Reset changes on reject
    git -C "$PROJECT_ROOT" checkout -- .
fi

cat > "$RUN_DIR/decision.md" << DECISION
# Decision Package — $TIMESTAMP

## Task
$(head -20 "$RUN_DIR/brief.md")

## Verdict: $VERDICT

## Changed Files
$(cat "$RUN_DIR/changed_files.txt" 2>/dev/null || echo "No changes")

## Builder Log
See: $RUN_DIR/builder.log

## Executor Result
- Exit code: $EVAL_EXIT
- Command: $EVAL_COMMAND
- Log: $RUN_DIR/eval.log

## Critic Review
$(cat "$RUN_DIR/critic.md")

## Artifacts
- brief.md
- builder.log
- patch.diff
- eval.log
- critic.md
DECISION

echo ""
echo "=== $VERDICT ==="
echo ""
echo "Decision package: $RUN_DIR/decision.md"
echo ""
cat "$RUN_DIR/decision.md"

# If APPROVE, keep changes. If REJECT, already reset above.
if [[ "$VERDICT" == "APPROVE" ]]; then
    echo ""
    echo "Changes preserved. Review and commit if acceptable."
else
    echo ""
    echo "Changes reverted. See decision.md for details."
fi
