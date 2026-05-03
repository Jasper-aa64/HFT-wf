# Setup HFT Learning Projects

## Goal

Clone and set up two HFT learning projects based on the selection criteria:

1. **Primary learning project**: `kpetridis24/lobsim` - L3 limit order book replay + paper execution simulator
2. **Optimization candidate**: `mansoor-mamnoon/limit-order-book` - High-performance C++20 LOB engine

## Why These Projects

### lobsim (Primary)

Learning value:
- Event stream architecture
- L3 order book state management
- Replay determinism
- Paper execution simulation
- Invariant-based correctness

This addresses the user's difficulty understanding Jane Street / 渔夫一得 articles by providing concrete code for:
- 系统边界
- 事件流
- 状态一致性
- 不变量
- 回放和复现

### mansoor (Optimization Candidate)

Benchmark/optimization value:
- C++20 core
- Existing benchmarks
- Memory pool
- Side-specialized matching
- Profiling infrastructure

This is the fallback for Agent Optimize experiments if lobsim's benchmarks are insufficient.

## Requirements

1. Clone both projects to appropriate locations
2. Update project selection document with final choices
3. Document build/test status for each project
4. Create initial architecture notes for lobsim

## Acceptance Criteria

- [ ] lobsim cloned and builds successfully
- [ ] mansoor cloned and builds successfully
- [ ] Project selection document updated
- [ ] Quick assessment: which project has better benchmark coverage

## Definition of Done

- Both projects runnable locally
- Documentation reflects final selection
- Ready for Phase 1 (read and map) or Phase 2 (optimization experiment)
