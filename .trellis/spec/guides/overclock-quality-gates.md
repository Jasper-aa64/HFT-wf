# Overclock Quality Gates Guide

> **Purpose**: Capture lessons from the Overclock workflow so future agent runs
> do not repeat the same evaluator, brief, and review mistakes.

Use this guide before writing or reviewing any Overclock brief.

---

## Core Model

Overclock is not ordinary agent chat.

```text
Trellis  = memory, task context, lessons, specs
Overclock = execution harness, adversarial review, retry/approve/escalate
```

The useful loop is:

```text
Trellis task / spec
  -> Overclock brief
  -> Critic-Prep checklist
  -> Builder patch
  -> Executor evidence
  -> Critic-Review
  -> Trellis spec update
```

If a run teaches a reusable lesson, write it back to Trellis. Do not leave it
only in `overclock_runs/` or chat history.

---

## Evaluator Rules

### Tests Must Not Run Under `-DNDEBUG`

Do not run assert-based tests in Release mode.

```text
Debug/ASan:
  build and run correctness tests

Release:
  benchmark smoke only
```

Reason: `assert(...)` is compiled out when `NDEBUG` is defined. A Release gate
can therefore report PASS while skipping the actual checks.

### Benchmark Smoke Is Not A Correctness Gate

Benchmark output proves only that the benchmark ran. It does not prove semantic
correctness or performance improvement.

For optimization work, add a separate comparison gate:

```text
baseline benchmark
new benchmark
threshold / variance policy
semantic invariant
```

---

## Brief Design Rules

### Decompose Architecture Work

Large architecture briefs should be split into contracts that can be proven.

Bad brief shape:

```text
change API
change engine storage
change production strategies
add ownership tests
fix P&L
preserve behavior
```

Better brief sequence:

```text
1. Prove engine-level contract.
2. Migrate production strategy to the contract.
3. Add accounting / P&L behavior.
```

Keep the first implementation brief to a narrow file set. As a default target,
keep complex briefs under four allowed files unless there is a concrete reason
to expand scope.

### Forbid Shortcuts Explicitly

Briefs must include forbidden paths, not only desired behavior.

Example:

```text
Do not call OrderBook::add_order() directly in the ownership test.
Do not call TickEngine::get_order_book() to mutate book state.
Do not use set_trade_callback() to bypass Strategy::on_trade().
```

If the target is an API boundary, tests must exercise that boundary.

---

## Critic Rules

### Reject Boundary Bypasses

The Critic should reject tests that prove a behavior by bypassing the intended
API path.

Example:

```text
Goal:
  Prove strategy ownership through TickEngine.

Reject:
  Test seeds liquidity with OrderBook::add_order().
```

The test may pass, but it does not prove the contract under review.

### Evidence Missing Means Reject

Checklist items must be backed by patch or executor evidence.

```text
No executor log -> reject
No direct assertion/log evidence -> reject
Test claims behavior but does not observe it -> reject
```

The burden of proof is on the patch, not on the rejection.

---

## Strategy / Engine Ownership Lessons

### ID-Before-Callback Must Be Proven Through The Engine Path

For engine/strategy ownership work, prove this exact sequence:

```text
prepare_order()
  -> returns OrderId
strategy records OrderId
submit_prepared_order(OrderId)
  -> matching may trigger Strategy::on_trade()
on_trade sees the OrderId in the strategy-owned set
```

Do not prove it by mutating `OrderBook` directly.

### Ignore Unrelated Trades

Strategies receive all engine trades. Tests and production strategies must first
check whether a trade involves an owned order ID.

```text
if neither buy_order_id nor sell_order_id is owned:
  ignore the trade
```

Only owned fills should update position, trade count, or P&L.

### Split Ownership From P&L

Do not combine ownership tracking and advanced P&L in the same first brief.

Recommended order:

```text
1. Engine-level ID-before-callback contract.
2. Strategy position tracking for owned fills.
3. P&L / average entry accounting.
```

---

## Known Successful Pattern

The successful C2 pattern:

```text
Scope:
  TickEngine API + engine-level test only

Allowed files:
  include/tick_engine.hpp
  src/tick_engine.cpp
  src/test_strategies.cpp

Test:
  LiquidityProviderStrategy submits one sell order.
  TakerStrategy submits one buy order.
  Both record prepared IDs before submit.
  Both verify ownership inside on_trade.
  No direct OrderBook calls.
  Post-run asserts verify order count and trade count.
```

This pattern is the reference for future engine-boundary briefs.

---

## Post-Run Learning

After every non-trivial Overclock run, record:

```text
brief name
verdict
attempts used
why rejected / approved
what the Critic caught
what the Executor missed
what should change in future briefs
```

If the lesson is reusable, update this guide or a more specific Trellis spec.

