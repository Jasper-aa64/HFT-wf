# HFT Learning Projects

This directory is for local clones of external HFT / market microstructure
projects used for study and agent-optimization experiments.

The external repositories are intentionally gitignored in this workspace. They
should be cloned locally, not committed into this repository.

## Local Clones

```text
03-HFT-Learning/lobsim/
  https://github.com/kpetridis24/lobsim
  Primary learning project:
  L3 limit order book replay + paper execution simulator.

03-HFT-Learning/limit-order-book/
  https://github.com/mansoor-mamnoon/limit-order-book
  Optimization candidate:
  C++20 limit order book / matching engine with tests and benchmarks.
```

## Current Role

```text
lobsim:
  Learn event streams, L3 order lifecycle, replay determinism, paper execution,
  and semantic invariants.

limit-order-book:
  Low-latency optimization target with existing benchmark and profiling support.
```

## Validation Status (2026-05-03)

### kpetridis24/lobsim

| Check | Status | Notes |
|-------|--------|-------|
| License | Apache-2.0 ✅ | Suitable for public blog |
| Clone | Done | |
| Build | Pass ✅ | liblobsim_core.a, lobsim_core_tests |
| Tests | Pass ✅ | 123/123 tests passed in 0.57s |
| Benchmark | Needs data | sample_data/*.parquet are stubs |
| Hot path | TBD | cpp/src/paper_trading_simulator.cpp is the core |

Dependencies installed: apache-arrow, boost, catch2 (via cmake fetch)

### mansoor-mamnoon/limit-order-book

| Check | Status | Notes |
|-------|--------|-------|
| License | MIT (badge) | LICENSE file missing from repo |
| Clone | Done | |
| Build | Pass ✅ | liblob_core.a, liblob_util.a, bench_tool, replay_tool |
| Tests | Pass ✅ | All tests passed in 0.75s |
| Benchmark | Pass ✅ | ~7M msgs/s median on MacBook Air M2 |
| Hot path | Clear | cpp/src/book_core.cpp is the core |

#### Benchmark Results (5 runs, 2M messages each)

```text
Run 1: 6.93M msgs/s
Run 2: 5.96M msgs/s
Run 3: 5.51M msgs/s
Run 4: 7.45M msgs/s
Run 5: 7.11M msgs/s

Median: ~7.0M msgs/s
Latency: p50=0.042µs, p90=0.084µs, p99=0.084µs
```

## Comparison

| Aspect | lobsim | limit-order-book |
|--------|--------|------------------|
| License | Apache-2.0 ✅ | MIT (missing file) |
| Build | Pass ✅ | Pass ✅ |
| Tests | 123/123 ✅ | Pass ✅ |
| Benchmark | Needs real data | Works ✅ (~7M msgs/s) |
| Hot path | paper_trading_simulator.cpp | book_core.cpp |
| Learning value | L3 replay, paper execution, invariants | Matching engine, memory pool |

## Recommendation

- **Primary HFT learning**: lobsim (L3 event stream, replay determinism, invariants)
- **Optimization experiments**: limit-order-book (benchmark works, hot path clear)
