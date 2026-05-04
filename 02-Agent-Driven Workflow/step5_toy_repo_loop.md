# Step 5 — Toy Repo Loop

Goal: Apply the GateKeeper loop to a toy repository before touching the trading project.

## Why a Toy Repo First

The trading project has real complexity:
- Build dependencies
- Existing tests
- Semantic invariants
- Risk of breaking things

A toy repo lets us:
- Verify the loop works end-to-end
- Discover integration issues
- Measure iteration counts
- Tune prompts without risk

## Toy Repo Setup

Create a minimal Python project:

```text
toy-repo/
  src/
    __init__.py
    calculator.py
  tests/
    test_calculator.py
  pyproject.toml
```

Task for the loop:

```
Fix the bug in calculator.divide: it should raise ValueError
when dividing by zero, but currently returns inf.
```

## Running the Loop

```bash
cd 02-Agent-Driven\ Workflow
python step4_executor_logs_to_critic.py
```

Observe:
1. Does Builder write code that passes tests?
2. Does Executor capture the test output?
3. Does Critic find missing evidence?
4. How many iterations before APPROVE?

## What to Record

After running, document:

```markdown
## Run 1 — <date>

Task: <brief>

Iterations: <count>

Issues found:
  - <issue 1>
  - <issue 2>

Fixes applied:
  - <fix 1>

Critic evidence cited:
  - <evidence 1>
  - <evidence 2>

Outcome: APPROVE / REJECT / ESCALATE
```

## Common Failure Modes

1. **Critic approves without evidence**
   - Prompt not strict enough
   - Add explicit "cite stdout lines" requirement

2. **Builder ignores feedback**
   - Not reading rejection reasons
   - Make rejection reasons more specific

3. **Executor timeout**
   - Test too slow
   - Increase timeout or simplify test

4. **Loop never terminates**
   - max_turns too low
   - Or Builder/Critic in a loop without progress
   - Add "stalemate" detection

## Success Criteria

Before moving to Step 6 (trading project), verify:

- [ ] Critic writes checklist before reviewing
- [ ] Executor output is visible in conversation
- [ ] Critic cites specific lines from stdout
- [ ] REJECT happens when evidence is missing
- [ ] APPROVE includes explicit evidence list
- [ ] Loop terminates within max_turns

## Next

Once toy repo works reliably, move to `step6_trading_repo_plan.md` for trading project adaptation.
