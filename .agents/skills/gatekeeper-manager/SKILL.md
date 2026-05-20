---
name: gatekeeper-manager
description: "Use when the user asks to delegate a coding task to GateKeeper, run quality custody on existing changes, produce a GateKeeper report, or choose between managed development and existing-patch review."
---

# GateKeeper Manager

GateKeeper Manager is the user-facing entrypoint for GateKeeper workflows.

It does not replace the GateKeeper scripts. It turns a human task into the right
brief, evaluator, script invocation, evidence review, and final report.

---

## Modes

### Mode A: Managed Development

Use when the user has a task but no patch yet.

Flow:

```text
user task
  -> clarify requirements
  -> write brief.md
  -> choose or create evaluator
  -> run scripts/gatekeeper_cli_loop.sh
  -> read gatekeeper_runs/<timestamp>
  -> generate report
```

The Builder is configurable. Prefer Claude Code/Sonnet when available for
heterogeneous review. If Claude is unavailable, use Codex CLI as Builder and
record that the run is artifact-isolated but not heterogeneous.

The Executor is deterministic shell code. It runs the evaluator.

The Critic is Codex. It prepares the checklist and reviews patch plus logs.

Builder does not receive `critic_checklist.md`. On a rejected attempt, Builder
receives `retry_evidence.md`, which summarizes the failed gate, missing proof,
and expected evidence shape.

### Mode B: Existing Patch Review

Use when code is already changed and the user wants quality custody or a report.

Flow:

```text
existing change
  -> discover intended delta
  -> confirm boundary if ambiguous
  -> materialize input.patch + selected_files.txt
  -> create quality brief if missing
  -> run scripts/gatekeeper_review_existing.sh --patch input.patch --files selected_files.txt
  -> read gatekeeper_runs/<timestamp>
  -> generate report
```

Mode B skips Builder. The existing patch is the input.

Do not let the script guess the intended delta when the user describes a
specific change. The Manager should decide the selected delta first, using:

```text
- staged changes
- unstaged changes
- recent commits
- branch diff
- user-mentioned files/modules
- generated files required by those changes
- unrelated dirty files to exclude
```

If the boundary is ambiguous, ask one confirmation question. Then write:

```text
input.patch
selected_files.txt
allowed_files.txt
brief.md
```

Use `scripts/gatekeeper_materialize_delta.py` after the Manager has selected the
delta. The helper is mechanical; it must not be used as the semantic decision
maker for what belongs to the task.

Examples:

```bash
python3 scripts/gatekeeper_materialize_delta.py \
  --out-dir gatekeeper_runs/<run>/mode-b-input \
  --file path/to/changed.cpp \
  --file path/to/changed_test.cpp

python3 scripts/gatekeeper_materialize_delta.py \
  --out-dir gatekeeper_runs/<run>/mode-b-input \
  --base origin/main \
  --target HEAD
```

---

## Requirement Clarification

Do not bind this skill to one named brainstorming skill.

Use the project’s existing requirement-discovery skill if one is available and
appropriate. If no such skill exists, use this default clarification loop:

1. Inspect the repo, docs, scripts, and current diff before asking the user.
2. Ask only blocking or preference questions.
3. Ask one question at a time.
4. For each preference question, recommend one option and explain the trade-off.
5. Stop clarifying once the brief can state:
   - task goal
   - allowed files
   - forbidden scope
   - evaluator command
   - acceptance criteria
   - report format

Mode A usually needs clarification.

Mode B usually needs only minimal confirmation:

```text
- Which requirement does this diff claim to satisfy?
- Which evaluator should prove it?
- Which files/commits belong to the selected delta, if ambiguous?
- Report format: docx, md-pdf, or all?
```

If these are derivable from context, do not ask.

---

## Brief Contract

Every GateKeeper brief must include:

```yaml
---
task: <one-line task>
allowed_files:
  - <path>
eval_script: scripts/<evaluator>.sh
checklist:
  - <evidence item>
---
```

The markdown body should include:

```text
Task description
Acceptance criteria
Forbidden scope
Evaluator notes
Report expectation
```

Use LF line endings for brief files. CRLF can break shell parsing on Windows if
new paths are added later.

---

## Commands

Mode A:

```bash
./scripts/gatekeeper_cli_loop.sh \
  --project-root <project-root> \
  --max-attempts 3 \
  --builder claude \
  --report all \
  <brief.md>
```

Codex-only fallback:

```bash
./scripts/gatekeeper_cli_loop.sh \
  --project-root <project-root> \
  --max-attempts 3 \
  --builder codex \
  --report all \
  <brief.md>
```

Mode B:

```bash
./scripts/gatekeeper_review_existing.sh \
  --patch gatekeeper_runs/<run>/mode-b-input/input.patch \
  --files gatekeeper_runs/<run>/mode-b-input/selected_files.txt \
  --report all \
  <brief.md>
```

Report-only regeneration:

```bash
python3 scripts/gatekeeper_report.py gatekeeper_runs/<timestamp> --format all
```

Report formats:

```text
docx   -> Word report
md-pdf -> Markdown and PDF
all    -> Word, Markdown, and PDF
none   -> no report
```

---

## Safety Rules

- Never claim PASS from chat alone.
- Never skip the evaluator when a brief specifies one.
- Never treat benchmark output as correctness evidence.
- Never widen `allowed_files` silently to make a patch pass.
- Never pass the full critic checklist to Builder.
- Always preserve `retry_evidence.md` for rejected attempts.
- If Builder and Critic use the same CLI/model family, explicitly label the run
  as non-heterogeneous in the final response and report notes.
- If the evaluator fails, final verdict is REJECT.
- If requirements are impossible or ambiguous, final verdict is ESCALATE.
- The report must cite artifacts from `gatekeeper_runs/<timestamp>`.

---

## Final Response Shape

When done, report:

```text
GateKeeper verdict: APPROVE / REJECT / ESCALATE
Run dir: <path>
Report(s): <paths>
Key reason: <one sentence>
Next action: <one sentence, if needed>
```
