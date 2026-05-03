# Optimization Program — cpp-trader-backtester

## Objective
Reduce order book average insert latency (µs/order) measured by the `benchmark` binary.
**Target**: ≥ 3% improvement over `experiments/baseline.tsv`.

Current baseline: **0.13237 µs/order** (commit 1cf3337, Apple clang 21, -O3 -march=native)

---

## Allowed files to modify
```
include/order_book.hpp
src/order_book.cpp
include/tick_engine.hpp
src/tick_engine.cpp
```

## Forbidden — never touch
- `src/test_*.cpp` — tests define correctness, not performance
- `scripts/golden/` — golden output must not change
- `experiments/baseline.tsv` — locked reference
- `strategies/` — strategy layer is not the hot path here
- Public API signatures used by strategies (function names, parameter types)

---

## Evaluation command
```bash
# From /Users/mac/Desktop/Quant/
OPT_MODE=1 ./scripts/evaluators/evaluate_cpp_trader.sh
```

This runs 6 phases:
1. Debug/ASan build
2. test_order_book (correctness)
3. test_strategies (correctness)
4. test_types (correctness)
5. Release benchmark
6. Invariant golden diff
7. [OPT_MODE=1] compare_perf.sh — median-of-5, ≥3% gate

---

## On PASS (all 7 phases exit 0)
```bash
cd /Users/mac/Desktop/Quant/cpp-trader-backtester
git add include/order_book.hpp src/order_book.cpp   # only modified files
git commit -m "perf: <one-line description of change>"
```
Then append a summary row to `experiments/results.tsv` (compare_perf.sh does this automatically).

## On FAIL
```bash
cd /Users/mac/Desktop/Quant/cpp-trader-backtester
git restore include/order_book.hpp src/order_book.cpp include/tick_engine.hpp src/tick_engine.cpp
```
`compare_perf.sh` still appends a FAIL row automatically for the record.

---

## Known hot paths — start here
Priority order for investigation:

### 1. `OrderBook::add_order()` — price level insertion
- File: `src/order_book.cpp`
- Current structure: `std::map<Price, PriceLevel>` — O(log n) per insert
- Opportunity: `std::unordered_map` if hash is cheaper than comparison at typical depth

### 2. Matching loop in `match_order()`
- Iterates price levels until quantity exhausted
- Check whether erasing empty levels inside the loop causes rebalancing

### 3. `PriceLevel` memory layout
- File: `include/order_book.hpp`
- Orders stored as `std::deque` — check cache-line behavior under frequent push_back/pop_front
- Consider `std::vector` with index cursor instead of deque for FIFO

### 4. `SymbolRegistry::register_symbol()` in strategy hot path
- Called once per tick — verify it's O(1) not O(n)

---

## Invariants that must hold (checked by golden diff)
- FIFO price-time priority preserved
- Partial fill volume consistency: executed + remaining = original
- best_bid() returns highest bid, not lowest
- cancel_order removes only remaining (not already-filled) quantity

Violating any invariant = automatic FAIL even if latency improves.

---

## Results log
See `experiments/results.tsv` for history.
See `experiments/baseline.tsv` for locked reference.
