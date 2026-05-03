# Step 6 — Trading Project Adaptation

Goal: Apply Overclock Mode to the real trading project.

## Prerequisites

Before this step:
- [ ] Step 5 toy repo loop verified working
- [ ] `scripts/evaluate.sh` runs successfully on trading project
- [ ] Understand trading project structure

## Trading Project Structure

```text
cpp-trader-backtester/
  src/
    order_book.cpp
    matching_engine.cpp
    strategies/
  include/
    order_book.hpp
    types.hpp
  tests/
    test_order_book.cpp
    test_strategies.cpp
  CMakeLists.txt
```

## Executor Commands

For trading project, `scripts/evaluate.sh` should run:

```bash
cmake -B build -S .
cmake --build build -j
./build/test_order_book
./build/test_strategies
./build/test_types
```

Optional semantic checks:

```bash
./scripts/check_orderbook_invariants.sh  # if exists
./scripts/run_backtest_regression.sh     # if exists
```

## Sample Task Briefs

### Task 1: OrderBook FIFO Bug

```
Task: Fix OrderBook FIFO violation in partial fill scenario

Problem:
  When an order is partially filled, the remaining volume
  should stay at the front of the price level. Currently,
  it gets moved to the back.

Allowed files:
  src/order_book.cpp
  include/order_book.hpp
  tests/test_order_book.cpp

Forbidden files:
  src/matching_engine.cpp
  src/strategies/*

Executor must run:
  cmake --build build -j
  ./build/test_order_book

Critic must verify evidence for:
  - Partial fill leaves remaining volume at front
  - Trade count increments correctly
  - Best bid/ask unchanged for remaining volume
```

### Task 2: Add Price-Time Priority Test

```
Task: Add test coverage for price-time priority

Requirement:
  Orders at the same price should be filled in arrival order.
  Add test cases that verify this explicitly.

Allowed files:
  tests/test_order_book.cpp

Executor must run:
  cmake --build build -j
  ./build/test_order_book

Critic must verify:
  - Test for same-price orders exists
  - Test explicitly checks fill order
  - Test prints PASS/FAIL for time priority
```

## Critic Checklist for Trading Project

The Critic should use a checklist like:

```text
Task: <brief>

Checklist:
  [ ] Patch changes only allowed files
  [ ] Patch addresses the specific bug/feature
  [ ] Existing tests still pass
  [ ] New tests cover the changed behavior
  [ ] Semantic invariants tested (if applicable)
  [ ] No performance claims used as correctness evidence
  [ ] Remaining risks named explicitly
```

## Escalation Policy for Trading Project

```text
Escalate to human when:
  - Patch wants to change test oracle (expected outputs)
  - Patch expands beyond allowed files
  - Executor broken (build fails for unrelated reasons)
  - Retry budget exhausted (3 attempts)
  - Task brief ambiguous or conflicts with code

Do automatically:
  - Reject failed builds
  - Reject failed tests
  - Send logs back to Builder
  - Retry within budget

Never do automatically:
  - Merge to main
  - Change test oracle
  - Expand task scope
```

## Integration with Trellis

Trellis provides context, not execution:

```text
.trellis/spec/         → Critic reads for project rules
.trellis/tasks/        → Task brief and acceptance criteria
AGENTS.md              → Coding conventions
```

The Overclock loop:
- Reads spec for rules and conventions
- Runs executor for evidence
- Produces decision package for human

## Decision Package Template

```markdown
## Decision Package — <date>

### Task
<brief>

### Changed Files
- <file 1>
- <file 2>

### Builder Claim
<what Builder says the patch does>

### Executor Commands
```bash
cmake --build build -j
./build/test_order_book
```

### Executor Result
- Exit code: 0
- Tests passed: 12
- Tests failed: 0

### Critic Checklist
- [x] Patch changes only allowed files
- [x] Patch addresses the specific bug
- [x] Existing tests still pass
- [x] New tests cover the changed behavior
- [ ] Semantic invariants not tested (risk accepted)

### Critic Verdict
APPROVE

Evidence:
- Line 45 in stdout: "test_partial_fill_fifo: PASS"
- Line 52 in stdout: "test_remaining_volume_at_front: PASS"
- Build exit code: 0

### Remaining Risks
- No integration test with matching engine
- Large-order behavior not tested

### Decision
[ ] Accept
[ ] Reject
[ ] Escalate

Human signature: ____________
```

## Running on Trading Project

```bash
# Ensure evaluate.sh works
./scripts/evaluate.sh

# Run Overclock loop with trading task
cd 02-Agent-Driven\ Workflow

# Create a trading-specific step file or modify TASK in step4
python step4_executor_logs_to_critic.py
```

## Monitoring

Track over time:
- Average iterations per task
- Most common rejection reasons
- Types of evidence cited
- Human intervention rate

Goal: Reduce human intervention while maintaining quality.
