# Critic-Prep Checklist Phase for GateKeeper

## Goal

Add a Critic-Prep phase that runs **before** Builder writes any code. The Critic generates `critic_checklist.md` which defines what evidence counts as proof. This makes the Critic an adversary that sets the evidentiary standard upfront, rather than just a post-hoc reviewer.

## What I already know

From `02-Agent-Driven Workflow/05. GateKeeper CLI MVP Status.md`:

- Current flow: Builder → Executor → Critic → Decision (with retry loop)
- Critic currently writes a checklist during review
- Problem: Critic can shift goalposts after seeing the patch
- Solution: Pre-write checklist before Builder, use it during review

Current Critic prompt tells it to:
1. Write a checklist of what must be proven
2. Find evidence for each item
3. APPROVE only if ALL items have evidence

Target change:
1. Critic-Prep writes checklist before Builder sees it
2. Critic-Review uses pre-written checklist (doesn't write a new one)
3. Builder never sees the checklist (stays independent)

## Requirements (evolving)

1. **Critic-Prep Phase** (before attempt loop starts)
   - Input: brief.md, allowed_files, current target file snapshots (if exist)
   - Output: `gatekeeper_runs/<timestamp>/critic_checklist.md`
   - Run: Codex CLI in read-only sandbox
   - Timing: Once per run, not per attempt

2. **Critic-Prep Failure Handling** (NEW)
   - If Codex exits non-zero: SETUP_FAILED
   - If checklist is empty or missing: SETUP_FAILED
   - Do NOT start Builder
   - Do NOT consume an attempt
   - Write `final_decision.md` with `Final verdict: SETUP_FAILED`
   - Include cleanup commands

3. **Critic-Review Phase** (updated)
   - Input: critic_checklist.md, patch.diff, eval.log
   - Must NOT write a new checklist
   - Must check each pre-written item for evidence
   - REJECT if any checklist item lacks evidence

4. **Builder Independence**
   - Builder prompt does NOT include critic_checklist.md
   - Builder only sees: brief.md, allowed_files, retry evidence

5. **Checklist Stability**
   - critic_checklist.md is generated once at run start
   - Not regenerated between attempts
   - Stored at `gatekeeper_runs/<timestamp>/critic_checklist.md`

## Acceptance Criteria

- [x] critic_checklist.md generated before attempt-1 Builder runs
- [x] critic_checklist.md contains specific, testable items derived from brief
- [x] Critic-Review prompt uses pre-written checklist
- [x] Critic-Review does NOT generate new checklist
- [x] Builder prompt does NOT include checklist content
- [x] SETUP_FAILED handling for Critic-Prep failure
- [x] SETUP_FAILED handling for empty checklist
- [x] All existing validation tests still pass

## Definition of Done

- bash -n scripts/gatekeeper_cli_loop.sh passes
- tests/test_verdict_parsing.sh passes
- Attempt-1 APPROVE case passes
- Deterministic retry case passes
- critic_checklist.md artifact exists and is used by Critic-Review
- Documentation updated

## Out of Scope

- Attacker role (future)
- Multi-builder parallelism (future)
- AutoGen/LangGraph migration (future)
- Trading system integration (future)
- Per-attempt checklist regeneration (keep stable for MVP)

## Technical Notes

### Current Critic Prompt Structure

Located at line ~620 in `scripts/gatekeeper_cli_loop.sh`:

```
1. Write a checklist of what must be proven.
2. For each checklist item, find evidence in the patch or executor log.
3. If ANY item lacks evidence, REJECT.
4. If ALL items have evidence, APPROVE.
```

### Target Critic-Prep Prompt

```
You are the Critic-Prep in an GateKeeper workflow.

Your job: Before any code is written, define what evidence proves the task is complete.

Input:
- Task brief
- Allowed files
- Current state of target files (if they exist)

Output: A checklist where each item:
1. Is specific and testable
2. Can be proven by patch content or executor log
3. Covers all acceptance criteria from the brief

Format (EXACT):
## Checklist
- [ ] <specific, testable item 1>
- [ ] <specific, testable item 2>
...
```

### Target Critic-Review Prompt

```
You are the Critic-Review in an GateKeeper workflow.

Your job: Check if the patch provides evidence for EVERY pre-written checklist item.

Pre-written checklist:
<content of critic_checklist.md>

Patch:
<patch.diff>

Executor log:
<eval.log>

For each checklist item:
1. Find evidence in patch or executor log
2. If ANY item lacks evidence, REJECT
3. If ALL items have evidence, APPROVE

Output format (EXACT):
VERDICT: APPROVE | REJECT
SUMMARY: <one line>
```

## Open Questions

* (None currently blocking - requirements clear from user prompt)
