# GateKeeper Mode Blog Outline

## Working Title

```text
GateKeeper Mode: Building an Agentic Quality Gate Before Letting AI Change Code
```

Alternative Chinese title:

```text
GateKeeper Mode：让 AI 写代码之前，先给它建一套质量门禁
```

---

## Positioning

This is the first blog in the series.

It is **not** about HFT optimization yet. It is about the safety system required
before agent-assisted optimization makes sense.

Project used:

```text
cpp-trader-backtester = sandbox
```

Purpose:

```text
prove that agents can write code under structured quality gates
```

Do not frame `cpp-trader-backtester` as a serious HFT system. Frame it as a
controlled target for validating the GateKeeper workflow.

---

## Core Thesis

```text
When AI can write code, the bottleneck shifts from "can it produce a patch?"
to "can the system prove the patch is acceptable?"
```

GateKeeper Mode is not a stronger prompt. It is a workflow:

```text
Critic defines evidence before the patch.
Builder writes independently.
Executor verifies facts.
Critic reviews against pre-written checklist.
Reject feeds retry.
Human reads evidence, not raw model chatter.
```

---

## Reader Problem

Common AI coding workflow:

```text
ask one agent to write code
ask the same or another agent if it looks good
skim the diff manually
hope tests catch the rest
```

Failure modes:

```text
friendly reviewer bias
test-passing semantic regression
missing evidence treated as acceptable
manual review fatigue
agent modifies files outside intended scope
hard-to-debug multi-turn drift
```

---

## Main Mechanism

Implemented workflow:

```text
Phase 0: Critic-Prep
  Codex writes critic_checklist.md before any patch exists.

Phase 1: Builder
  Claude Code writes the patch independently.
  Builder does not see the checklist.

Phase 2: Executor
  Shell evaluator runs deterministic checks.

Phase 3: Critic-Review
  Codex checks patch.diff + eval.log against critic_checklist.md.

Phase 4: Judge
  Script writes final_decision.md.
```

Retry rule:

```text
REJECT -> feed evidence to Builder -> retry
max_attempts = 3
after max attempts -> ESCALATE to human
```

Setup failure rule:

```text
Critic-Prep fails or produces empty checklist -> SETUP_FAILED
Builder does not start
No attempt is consumed
```

---

## What Makes This Different From Ordinary AI Review

| Ordinary AI Review | GateKeeper Mode |
|---|---|
| Critic appears after patch | Critic defines evidence before patch |
| Review is impression-based | Review is checklist + evidence based |
| Tests are optional context | Executor is a hard gate |
| Approval can be vague | Malformed approval defaults to reject |
| Human rereads everything | Human reads decision package |
| One-shot interaction | Reject feeds retry with evidence |

Important phrase:

```text
The burden of proof is on the patch, not on the rejection.
```

---

## Evidence To Show

Use these validated runs:

```text
APPROVE:
gatekeeper_runs/20260503-173556/
  critic_checklist.md
  final_decision.md

DETERMINISTIC RETRY:
gatekeeper_runs/20260503-173731/
  attempt-1/eval.log
  attempt-1/decision.md
  attempt-2/decision.md
  final_decision.md

ESCALATE:
gatekeeper_runs/20260503-173917/
  final_decision.md

SEMANTIC REJECT:
gatekeeper_runs/20260503-153929/
  patch.diff
  eval.log
  critic.md
  decision.md
```

Evidence narrative:

```text
1. Good patch passes.
2. Executor failure retries.
3. Repeated failure escalates.
4. Tests can pass while Critic rejects semantic violation.
5. Critic checklist is generated before Builder patch.
6. Real sandbox task rejects once, retries, then produces an accepted invariant test.
7. Follow-up known-issue sweep checks whether the workflow helps find and close
   existing project bugs, not only validate one isolated patch.
```

---

## Suggested Article Structure

### 1. Why "AI Wrote The Code" Is Not The Hard Part

Explain:

```text
AI can produce plausible patches.
The hard part is proving the patch should be accepted.
```

### 2. The Slide Pattern: Internal Adversarial Review

Introduce:

```text
role separation
executable gate
default reject
human reads decision package
```

### 3. From GateKeeper Lite+ To Full GateKeeper Mode

Evolution:

```text
Builder -> Executor -> Critic
then
Critic-Prep -> Builder -> Executor -> Critic-Review
```

### 4. Implementation With Local CLIs

Tools:

```text
Claude Code = Builder
Codex = Critic-Prep and Critic-Review
Shell = Executor
Git worktree = isolation
Script = Judge / Orchestrator
```

Explain why this does not require AutoGen/LangGraph yet:

```text
first prove semantics with visible artifacts
then consider framework migration
```

### 5. The Four Gates

Gates:

```text
scope gate: allowed_files
setup gate: critic_checklist.md exists and has items
executor gate: build/test/evaluator exit code
critic gate: every checklist item must have evidence
```

### 6. Validation Results

Summarize validated scenarios:

```text
attempt-1 approve
deterministic retry
escalate
semantic reject
setup failed handling
real sandbox integration
known-issue sweep
stress test exposes weak executor gate
```

Real sandbox evidence:

```text
Project:
  cpp-trader-backtester

Task:
  add volume consistency invariant test

Run:
  gatekeeper_runs/20260503-205323/

Result:
  attempt 1 -> REJECT
  attempt 2 -> APPROVE

Applied patch:
  b0712a6 feat(cpp-trader): Add volume consistency invariant test
```

### 7. Known-Issue Sweep: Testing The Workflow Against Existing Bugs

Purpose:

```text
The first real sandbox task proves GateKeeper can accept a good narrow patch.
The next experiment should test whether the workflow is useful for iterating on
known project problems.
```

Known issues to sweep:

```text
1. Add missing best_bid() regression coverage.
2. Review Tick layout / alignas issue.
3. Review Strategy fill ownership validation.
```

Run them as separate briefs, not one large task:

```text
best_bid coverage:
  expected to be a small test-only patch

Tick layout:
  performance / data-layout task, larger risk surface

Strategy fill ownership:
  semantic correctness task, likely needs careful acceptance criteria
```

What to measure:

```text
Did Critic-Prep write a useful checklist?
Did Builder stay within allowed_files?
Did Executor catch failures?
Did Critic reject missing evidence?
Did retry improve the patch?
Did the workflow expose gaps in the evaluator or brief?
```

Stress-test result to include:

```text
Run:
  gatekeeper_runs/20260503-214848/

Task:
  fix strategy-layer accounting and fill ownership

Result:
  attempt 1 -> REJECT by Critic
  attempt 2 -> REJECT by Critic
  attempt 3 -> REJECT by Executor
  final -> ESCALATE

Why this matters:
  The failed run exposed a real workflow weakness: the evaluator built tests in
  Release mode, and the project's Release flags define NDEBUG. Existing
  assert-based tests were therefore compiled out.
```

Key lesson:

```text
An Executor gate is only as strong as the commands it runs.

If tests depend on assert(), running them under -DNDEBUG turns the quality gate
into a smoke test. For this project, the next GateKeeper iteration must run
tests in Debug/ASan or replace critical assertions with explicit runtime
checks that cannot be compiled out.
```

Callback timing lesson:

```text
The strategy accounting task also exposed a design issue:

submit_order()
  -> add_order()
    -> match_order()
      -> execute_trade()
        -> trade_callback_()      # Strategy::on_trade runs here
  -> return order_id              # too late for strategy to pre-register id

This is not a multithreaded race. It is synchronous callback ordering /
reentrancy. Returning the assigned order id is not sufficient if fills can occur
before the caller receives that id.
```

How to write this in the article:

```text
The stress test did not produce an approved patch. That was the useful result.
It forced the workflow to reveal that the evidence gate itself was too weak.
GateKeeper is not only a patch filter; it is a way to debug the quality system
around the agent.
```

This section should include a short postmortem after the sweep:

```text
What worked in the workflow?
What was too slow?
Where did the brief need tightening?
Should the evaluator change before Agent Optimize?
```

### 8. What This Does Not Solve Yet

Boundaries:

```text
not HFT optimization yet
not a replacement for domain tests
not a proof that agents are correct
not using Attacker as blocking gate yet
```

### 9. Why This Matters For Agent Optimization

Bridge to Blog 2:

```text
Agent optimization is dangerous without gates.
Before optimizing latency, build correctness and evidence gates.
```

---

## Blog 2 Bridge

The next blog is not about this sandbox.

Planned path:

```text
Use lobsim to learn event streams, L3 order lifecycle, replay determinism,
paper execution, and semantic invariants.

Use limit-order-book as a comparison candidate for a more benchmark-ready
low-latency LOB optimization experiment.
```

PandoraTrader role:

```text
architecture reference for domestic futures trading systems,
not the primary optimization benchmark target.
```

---

## Key Sentences To Reuse

```text
GateKeeper Mode is a quality-control system, not a prompt style.
```

```text
The Critic is valuable because it defines the evidence standard before the
Builder can influence it.
```

```text
Passing tests is evidence, not proof of every requirement.
```

```text
The human should review the evidence package, not babysit every intermediate
agent turn.
```

```text
Agent-assisted optimization should begin only after the quality gate exists.
```

---

## References To Mention

- AutoGen Reflection pattern as a known multi-agent review reference.
- AutoKernel / KernelAgent as later optimization-loop inspiration.
- Internal project evidence from `gatekeeper_runs/`.
- Attacker research is background only; do not add Attacker as a blocking gate
  in this article.
