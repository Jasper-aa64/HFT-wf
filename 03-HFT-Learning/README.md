# HFT Learning Projects

This directory is for local clones of external HFT / market microstructure
projects used for study and agent-optimization experiments.

The external repositories are intentionally gitignored in this workspace. They
should be cloned locally, not committed into this repository.

## Local Clones

```text
03-HFT-Learning/lobsim/
  https://github.com/kpetridis24/lobsim
  Primary learning project candidate:
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
  Compare as a stronger low-latency optimization target with existing benchmark
  and profiling support.
```

## Next Validation Steps

```text
1. Confirm license for both projects.
2. Build each project locally.
3. Run tests.
4. Run or add a baseline benchmark.
5. Identify hot path files and possible allowed_files boundaries.
6. Decide which project becomes the Blog 2 optimization target.
```
