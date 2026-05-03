# Agent Optimize 项目筛选

> **目标**：在正式写第二篇 `Agent Optimization` 博客之前，先确定一个真正适合做低延迟优化实验的开源项目。
>
> **最终决定 (2026-05-03)**：
> - **HFT 学习主项目**：`kpetridis24/lobsim` — L3 replay / simulator / event stream
> - **First Optimize target**：`mansoor-mamnoon/limit-order-book` — clearer hot path, runnable benchmark, lower dependency friction
> - **架构旁读**：`PandoraTrader` — 国内期货、CTP、实盘/回测分层
> - **Overclock sandbox**：`cpp-trader-backtester` — 博客 1 质量门禁
>
> **选择理由**：兼顾"学 HFT"而不只是"做一次好看的优化实验"。

> **Updated implementation stance (2026-05-03)**:
> `lobsim` should be treated as the primary HFT learning project, not as the first optimization benchmark target. Its L3 replay model, deterministic event stream, and paper execution engine are more valuable for building the mental map needed to understand Jane Street-style correctness discussions and 愚夫一得-style domestic trading-system architecture notes. `limit-order-book` should be validated first for the Agent Optimize workflow because it has a smaller optimization surface, a clearer C++ hot path, and a benchmark path that is already easier to run.

---

## 0. 最终选择

| 用途 | 推荐项目 | 理由 |
|---|---|---|
| HFT 架构学习主项目 | `kpetridis24/lobsim` | L3 replay / simulator / event stream，更能补交易系统理解 |
| Agent Optimize 主候选 | `mansoor-mamnoon/limit-order-book` | benchmark/tests/profiling 更现成，适合先接入优化闭环 |
| Agent Optimize 备选 | `kpetridis24/lobsim` | 如果后续补出稳定 benchmark，可升级为优化实验项目 |
| 国内交易系统旁读 | `PandoraTrader` | 学 CTP、实盘/回测分层、策略接口，不作为优化目标 |
| Overclock sandbox | `cpp-trader-backtester` | 博客 1 质量门禁，不参与博客 2 选型 |

**为什么优先选 lobsim？**

`kpetridis24/lobsim` 更贴近用户现在要补的基础：

```
事件流是什么
order_id 生命周期是什么
replay 为什么必须 deterministic
book state 怎么维护
strategy order 怎么注入
fill / cancel / modify 怎么形成事实
为什么 invariant 比单元测试更重要
```

这类项目比单纯 matching engine 更像"市场微结构实验环境"。它能帮你建立读 HFT/交易系统文章时最缺的地图。

以后看到 Jane Street 讲 correctness、replay、debuggability、不变量，或者愚夫一得讲实盘约束、回测/实盘分层、状态维护，会更容易对应到代码结构。

**执行路线：**

```
Phase 1: Read and map lobsim
Goal: 学 HFT 系统里的事件流、L3 book、replay、paper execution、invariant。
Output: 一张架构图 + 一组 semantic invariants。

Phase 2: Local verification
Goal: clone/build/test/run quickstart，确认两个项目各自承担什么角色。
lobsim remains the learning project even if its benchmark path is weak.
limit-order-book is the first optimization candidate unless local validation fails.

Phase 3: Optimize experiment
Goal: first run agent-assisted optimization on the clearer benchmark target.
Must have: baseline benchmark + invariant gate + allowed_files。
```

**Dependency policy:**

`lobsim` currently depends on system-level Arrow / Parquet packages for its full examples and data path. Installing them with Homebrew is acceptable on macOS, but it should not block the learning plan. If Arrow / Parquet becomes slow or fragile, treat that as dependency-integration friction and continue with:

```text
lobsim:
  study architecture, event lifecycle, replay determinism, and invariants

limit-order-book:
  run the first optimization workflow because build / benchmark feedback is faster
```

---

## 1. 两篇博客的总体定位

### Blog 1 — Overclock

**主题**：Agentic quality gate for code changes

**使用项目**：当前已有的 `cpp-trader-backtester`

**项目定位**：

- 这是一个 sandbox，不追求项目本身足够真实
- 重点展示 Overclock 如何作为质量门禁，约束 agent 修改代码
- 关注编译、测试、静态检查、基准冒烟、allowed_files、failure feedback loop
- 不把它包装成完整 HFT 系统

**核心问题**：

> 当 agent 能自动改代码时，如何防止它把项目改坏？

**文章主线**：

```
agent proposes patch
  -> overclock runs quality gate
  -> compile / test / lint / benchmark smoke / invariant check
  -> failed result feeds back to agent
  -> agent repairs patch
  -> human reviews final diff
```

---

### Blog 2 — Agent Optimization

**主题**：Agent-assisted low-latency optimization on a realistic trading subsystem

**使用项目**：待定，本文档负责筛选

**项目定位**：

- 不能用 `cpp-trader-backtester`，它太 toy
- 不默认使用 `PandoraTrader`，原因见第 9 节
- 更适合的主项目应该是 limit order book、matching engine、market replay 或 exchange simulator

**核心问题**：

> Agent 能不能在不破坏交易语义的前提下，优化一个明确的低延迟 hot path？

**文章主线**：

```
select project
  -> identify hot path
  -> define semantic invariants
  -> run baseline benchmark
  -> restrict allowed_files
  -> let agent optimize
  -> run quality + invariant + benchmark gate
  -> analyze speedup and regressions
```

---

## 2. 为什么不能直接开始写 Optimize

如果不先选项目，第二篇博客很容易变成空泛内容：

```
以后我要优化 HFT 系统
以后我要关注 latency
以后我要加 benchmark
```

这类内容没有实验闭环，不值得写。

真正的 Optimize 博客必须有：

```
可构建的项目
明确的 hot path
可重复的 benchmark
语义正确性检查
优化前后的对比数字
失败案例和修复过程
```

所以当前阶段先做项目筛选，不直接写博客正文。

---

## 3. 项目选择标准

### Must Have

| 标准 | 说明 |
|---|---|
| C++ 优先 | 贴近低延迟交易系统；适合研究内存、缓存、分支、数据结构 |
| 有 order book / matching / replay / simulator 路径 | 必须有交易系统中真实的性能路径，而不是策略胶水 |
| 能本地构建 | 不能只看 README，必须能 clone → build → test |
| 有测试，或容易补 semantic invariant | 优化不能只看速度，必须防止语义被破坏 |
| 有 benchmark，或容易补 benchmark | 没有基线就无法证明优化有效 |
| 子系统边界清楚 | 方便使用 allowed_files 限定 agent 修改范围 |
| 开源且适合公开展示 | 方便写 GitHub / blog |

### Nice to Have

| 标准 | 说明 |
|---|---|
| 有 CI | 更容易接入 Overclock gate |
| 有 profiling 入口 | 更容易找到 hot path |
| 有 replay 数据路径 | 更接近真实市场数据处理 |
| 有 Python bindings | 方便研究、可视化 |
| License 明确且友好 | MIT / Apache-2.0 更适合公开记录 |

### Avoid

| 类型 | 原因 |
|---|---|
| 主要是 API wrapper | 性能路径不清楚，优化空间不典型 |
| 主要是策略胶水代码 | 适合学架构，不适合做 micro-optimization |
| 没有 deterministic test path | agent 优化后很难验证是否破坏语义 |
| 项目过大 | 一篇博客无法讲清楚 |
| 项目过小 | 优化会显得像 toy example |

---

## 4. 候选项目矩阵

> 评分是初筛判断，不是最终结论。标注 `[README]` 的数据来自项目 README，未经本地复现。最终结论必须经过本地 clone / build / test / benchmark 验证。

### 4.1 mansoor-mamnoon/limit-order-book

- **链接**：https://github.com/mansoor-mamnoon/limit-order-book
- **语言**：C++20 + Python SDK (pybind11)
- **星标**：~42（截至筛选时）
- **简介**：exchange-style matching engine，支持 limit / market / cancel / modify / IOC / FOK / POST_ONLY / STP，附带 Python replay 和分析管道

**已在公开资料中确认的技术细节**：

| 特性 | 状态 |
|---|---|
| Catch2 单元测试 | ✅ 确认 |
| `bench_tool`（percentiles + histogram CSV） | ✅ 确认 |
| `replay_tool`（snapshot / resume，含 proof） | ✅ 确认 |
| Slab 内存池 | ✅ 确认 |
| Side-specialized matching（branch elimination） | ✅ 确认 |
| Cache-hot best-level pointers | ✅ 确认 |
| Profiling toggle（`-fno-omit-frame-pointer -g`） | ✅ 确认 |
| Docker + GitHub Actions + GHCR 发布 | ✅ 确认 |
| 吞吐量 20M+ msgs/sec，p50≈0.04µs | `[README]` 未本地复现 |
| License | ❓ 需 clone 后确认 |

**优点**：

- hot path 文件边界相对清楚（BookCore）
- benchmark 工具已内置，有 CSV 输出，方便 agent gate 集成
- snapshot/resume proof 是天然的 semantic invariant 框架
- Python SDK 方便博客可视化

**风险**：

- 吞吐量数字 [README] 声称激进，必须本地复现；博客不能建立在无法复现的数字上
- 项目复杂度较高（replay pipeline + Python SDK + analytics），allowed_files 需要仔细划定
- 星标数量较少，社区验证有限

**当前判断**：**优先验证 #1**

**Updated role after local setup (2026-05-03)**:

```text
Primary role:
  First Agent Optimize target.

Why:
  The benchmark path is already more practical, the hot path is concentrated
  around BookCore / book_core.cpp, and the project is small enough for an
  evidence-based optimization loop.

Risk to resolve before blogging:
  License metadata needs confirmation because the README badge says MIT but
  the local clone does not currently expose a LICENSE file.
```

---

### 4.2 kpetridis24/lobsim

- **链接**：https://github.com/kpetridis24/lobsim
- **语言**：C++20 + Python bindings (pybind11)
- **简介**：deterministic L3 limit order book replay + paper execution engine，面向市场微结构研究

**已在公开资料中确认的技术细节**：

| 特性 | 状态 |
|---|---|
| CMake 构建 + pybind11 | ✅ 确认 |
| Deterministic replay（同一输入多次结果一致） | ✅ 确认 |
| 严格 L3 order_id 生命周期管理 | ✅ 确认 |
| Python 接口（`PaperTradingSimulator`，`NormalizedLobEvent`） | ✅ 确认 |
| Streamlit demo（replay exploration + strategy injection） | ✅ 确认 |
| HN 上有公开讨论（2026-01） | ✅ 确认 |
| 有 benchmark 数字 | ❓ README 未见独立 bench_tool，需本地确认 |
| License | ❓ 需 clone 后确认 |

**优点**：

- deterministic replay 本身就是 semantic invariant 框架，天然适合"优化前后 snapshot hash 必须一致"
- L3 严格性（每条 event 都有 order_id）使 invariant 更容易定义
- 架构比 mansoor-mamnoon 简洁，allowed_files 更容易划定
- 项目 2026 年初才公开，相对新鲜，适合写"我选择这个不知名项目的原因"的叙事

**风险**：

- 没有看到独立的 bench_tool，benchmark harness 可能需要手写
- 项目较新，已知 edge case 可能较少
- 需要确认本地 build 是否顺畅（依赖 pybind11）

**当前判断**：**优先验证 #2**

**Updated role after local setup (2026-05-03)**:

```text
Primary role:
  HFT learning project.

Why:
  The strongest value is not raw optimization readiness. It is the L3 replay
  model, order-id lifecycle, deterministic event stream, and paper execution
  boundary. Those concepts are exactly the missing map needed before reading
  more advanced Jane Street and 愚夫一得 material.

Optimization role:
  Secondary. Promote it to an optimization target only after a stable benchmark
  or replay-invariant harness exists.
```

---

### 4.3 brprojects/Limit-Order-Book

- **链接**：https://github.com/brprojects/Limit-Order-Book
- **语言**：C++
- **简介**：LOB + matching engine，有 GoogleTest 单元测试和集成测试，有性能测试说明

**已在公开资料中确认的技术细节**：

| 特性 | 状态 |
|---|---|
| GoogleTest 单元测试 + 集成测试 | ✅ 确认 |
| Generate_Orders / Process_Orders 两个辅助模块 | ✅ 确认 |
| CMake 构建 | ✅ 确认 |
| 吞吐量 1.4M TPS | `[README]` 未本地复现 |
| Python 数据可视化脚本（`data_visualisation.py`） | ✅ 确认 |
| License | ❓ 需 clone 后确认 |

**优点**：

- 结构相对教学化，子系统边界清楚（Book / Limit / Order 三层）
- GoogleTest 已集成，invariant 容易补充
- 对 hot path 和 allowed_files 的划定更直观

**风险**：

- 吞吐量 1.4M TPS 相比 mansoor 差一个数量级，博客中的优化叙事需要合理定位
- 性能测试依赖外部生成数据（`orders.txt` 因文件过大已从 repo 删除），需要自己补 benchmark harness
- 整体可能偏教学化，优化空间有限

**当前判断**：**备选验证**

---

### 4.4 PIYUSH-KUMAR1809/order-matching-engine

- **链接**：https://github.com/PIYUSH-KUMAR1809/order-matching-engine
- **语言**：未确认（README 极简，只有标题 "High performance order matching engine"）
- **简介**：公开信息极少

**风险**：

- README 内容不足，无法评估测试、benchmark、结构、license
- 原始文档中提及"有 src/tests/scripts/run_benchmark 等结构"，但未能从公开资料中核实
- 在 blog-quality 项目中，不建议把核心实验建立在未有公开验证的项目上

**当前判断**：**暂不验证，从候选列表移除或降为最低优先级**

---

### 4.5 PandoraTrader

- **链接**：https://github.com/pegasusTrader/PandoraTrader
- **语言**：C++
- **简介**：国内期货交易平台，CTP 实盘接入，策略 + 执行 + 回测分层架构

**定位**：

适合学习的内容：

```
交易 API 抽象
行情 / 交易回调模型
策略层与执行层分离
CTA + Agent 架构
订单 / 持仓本地状态维护
实盘和回测接口分层
国内期货 CTP 语境
```

不适合直接作为 Blog 2 主优化项目的原因：

```
核心库采用邀请授权机制，未完整开放
项目偏平台接口和策略胶水，性能路径不典型
benchmark path 不清楚
优化结果不容易在公开博客中复现
```

**当前判断**：**架构学习对象，不作为 Optimize 主项目**

---

### 4.6 cpp-trader-backtester

- **定位**：当前 sandbox，Overclock Blog 1 实验对象
- **当前判断**：**只用于 Blog 1，不参与 Blog 2 选型**

---

## 5. 候选对比汇总

| 候选 | 语言 | 测试框架 | Benchmark | Hot Path 清晰度 | 推荐优先级 |
|---|---|---|---|---|---|
| mansoor-mamnoon/limit-order-book | C++20 + Python | Catch2 | 内置 bench_tool + CSV | 高（BookCore） | **#1 Optimize target** |
| kpetridis24/lobsim | C++20 + Python | 已本地通过 core tests | 真实数据/benchmark 待补 | 高（L3 replay / paper execution） | **#1 Learning project** |
| brprojects/Limit-Order-Book | C++ | GoogleTest | 需手写 harness | 中（Book / Limit / Order） | 备选 |
| PIYUSH-KUMAR1809/order-matching-engine | 未确认 | 未确认 | 未确认 | 未知 | 移除 |
| PandoraTrader | C++ | 未确认 | 不适合 | 低（平台胶水） | 架构学习 |
| cpp-trader-backtester | C++ | 简单 | smoke only | — | Blog 1 only |

---

## 6. 暂定推荐路线

### Phase A：Blog 1 先写

使用当前 `cpp-trader-backtester`，重点不是交易系统本身，而是质量门禁方法：

```
build gate
test gate
lint / static check gate
benchmark smoke gate
allowed_files 限制
failure feedback loop
human review boundary
```

产出：

```
Blog 1: Full Overclock Mode as an Agentic Quality Gate
```

### Phase B：分工验证两个候选

```
1. kpetridis24/lobsim
   Role: HFT learning map
   Verify: build, tests, event flow, L3 lifecycle, replay invariants

2. mansoor-mamnoon/limit-order-book
   Role: first Agent Optimize experiment
   Verify: build, tests, bench_tool, hot path, allowed_files
```

The decision is not "which project is globally better." The decision is:

```
lobsim teaches the trading-system concepts.
limit-order-book tests the optimization workflow.
```

If `lobsim` later gets a stable benchmark / replay-invariant harness, it can become a second optimization case. It does not need to block the first Agent Optimize blog.

### Phase C：Blog 2 再写

候选标题：

```
Blog 2: Agent-Assisted Low-Latency Optimization on a Limit Order Book
```

文章必须包含完整实验闭环（详见第 8 节）。

---

## 7. 本地验证清单

每个候选项目都按同一套流程验证。

### 7.1 基础信息收集

```
Repo URL:
Language / C++ standard:
License:
Stars / Activity:
Build system:
Test framework:
Benchmark path or tool:
Hot path file(s):
Potential allowed_files:
```

### 7.2 构建验证

```bash
git clone <repo-url>
cd <repo>
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j$(nproc)
```

记录：

```
Build status: pass / fail
Compiler version:
OS / hardware:
Build time:
Errors (if any):
```

### 7.3 测试验证

```bash
ctest --test-dir build --output-on-failure
```

记录：

```
Test status: pass / fail / partial
Number of tests:
Failed tests:
Flaky behavior:
```

### 7.4 Benchmark 验证

优先使用项目自带 benchmark。如果没有，手写最小 harness。

建议至少跑 5 次，取中位数而不是单次结果：

```
benchmark_run_1
benchmark_run_2
benchmark_run_3
benchmark_run_4
benchmark_run_5
median / p95:
```

记录：

```
Benchmark command:
Input size:
Warmup runs:
Mean / Median:
p95 / p99 (if available):
Notes on variance:
```

---

## 8. Semantic Invariants 候选

优化交易系统不能只看速度，必须保持语义正确。

### Limit Order Book / Matching Engine

```
1. 买一价 <= 卖一价（撮合过程中除外）
2. 同一价格档位内必须 FIFO
3. 订单成交量不能超过原始订单量
4. 撤单后的订单不能继续成交
5. 部分成交后的剩余量必须正确进入 book
6. market order 不能凭空产生流动性
7. submitted qty = resting qty + executed qty + cancelled qty + rejected qty
8. 相同输入 replay 多次，最终 book snapshot hash 必须一致
```

### Market Replay / L3 Simulator

```
1. 同一事件流 replay 必须 deterministic
2. 每个 order_id 生命周期合法：add -> modify / cancel / fill
3. cancel 不能作用于不存在的 live order
4. fill 不能超过 live quantity
5. final snapshot hash 可复现
```

### Agent 优化 gate 中至少保留

```
build pass
test pass
benchmark not worse than threshold (e.g. -2%)
semantic invariant pass
allowed_files not exceeded
no public API break（除非明确允许）
```

---

## 9. Agent Optimize 实验模板

### 9.1 Baseline 记录

```
Target subsystem:
Hot path:
Baseline command:
Baseline result:
Known bottleneck (hypothesis):
Correctness tests:
Invariant checks:
```

### 9.2 Agent Task Prompt 示例

```
Optimize the matching hot path without changing public behavior.

Allowed files:
- src/order_book.cpp
- include/order_book.hpp

Do not change:
- public API signatures
- test expected results
- benchmark input format

Quality gates:
- cmake build must pass
- all tests must pass
- invariant replay must pass
- benchmark must not regress by more than 2%
```

### 9.3 Review Questions

```
1. Agent 改了什么数据结构？
2. 是否减少了 heap allocation？
3. 是否减少了 map / tree lookup？
4. 是否改变了 matching semantics？
5. 是否只优化了 benchmark case，而破坏了 general case？
6. 是否引入了 undefined behavior？
7. 性能提升是否在多次运行中稳定？
8. 可读性是否还能接受？
```

---

## 10. PandoraTrader 的准确定位

PandoraTrader 不是废掉，而是换定位。适合用来学习：

```
交易 API 抽象模式
行情 / 交易回调设计
策略层与执行层分离
CTA 框架结构
国内期货 CTP 语境
实盘 / 回测接口分层方法
```

不适合直接作为 Blog 2 主优化项目：

```
核心库未完整开放（邀请授权）
项目主体是平台接口和策略胶水
benchmark 路径不清楚
优化结果在公开博客中难以复现
```

在两篇博客中的定位：

```
Architecture Reference
  -> 学交易系统的分层思路
  -> 理解 CTP / 期货生态
  -> 作为"真实项目长什么样"的参照

Not: Optimization Benchmark Target
```

---

## 11. Jane Street 和"愚夫一得"资料的作用

这些资料放在**学习背景层**，不直接替代实验项目。

**Jane Street**：工程品味、性能与正确性的权衡、复盘方式、不变量思维

**愚夫一得**：国内量化交易工程语境、实盘约束、期货交易系统分层经验

**开源项目**：负责真正动手（clone → build → test → benchmark → agent patch → review → blog）

---

## 12. 决策日志

### 2026-05-03

```
初筛来源：GitHub README + 公开搜索结果（非本地验证）

决定：
1. Blog 1 先用 cpp-trader-backtester 写 Overclock 质量门禁
2. Blog 2 不直接用 cpp-trader-backtester
3. PandoraTrader 作为架构学习材料，不作为 optimize 主项目
4. Optimize 主项目优先从 LOB / matching / replay 项目里选
5. 优先本地验证 mansoor-mamnoon/limit-order-book 和 kpetridis24/lobsim
6. PIYUSH-KUMAR1809/order-matching-engine 从候选列表移除（README 信息不足）
7. 所有 [README] 性能数字在博客中使用前必须本地复现
```

### 2026-05-03 本地克隆状态

```text
Local workspace:
03-HFT-Learning/

Cloned:
- 03-HFT-Learning/lobsim
  repo: https://github.com/kpetridis24/lobsim
  HEAD: 0cb48ed Code review with Claude (#23)

- 03-HFT-Learning/limit-order-book
  repo: https://github.com/mansoor-mamnoon/limit-order-book
  HEAD: 78e1fb0 Update README.md
```

说明：

```text
这两个目录是外部项目 clone，已在主仓库 .gitignore 中排除。
主仓库只记录 03-HFT-Learning/README.md 和本筛选文档，不直接提交外部项目源码。
```

---

## 13. 下一步 Todo

### 项目验证

- [x] clone `mansoor-mamnoon/limit-order-book`
- [ ] 确认 `mansoor-mamnoon/limit-order-book` license
- [ ] 本地 build（Release mode）
- [ ] 运行 Catch2 tests
- [ ] 运行 bench_tool，记录 5 次结果
- [ ] 标记 hot path 文件（BookCore 相关）
- [ ] 判断是否适合 allowed_files 划定
- [x] clone `kpetridis24/lobsim`
- [ ] 确认 `kpetridis24/lobsim` license
- [ ] 本地 build（CMake + pybind11）
- [ ] 尝试 Python quickstart
- [ ] 确认 benchmark 工具是否存在，若无则补最小 harness
- [ ] 定义 replay snapshot invariant
- [ ] 对比两个项目，做最终选择

### 博客准备

- [ ] 写 Blog 1 大纲（Overclock quality gate）
- [ ] 整理 Overclock 输出截图 / 日志
- [ ] 项目选定后写 Blog 2 大纲
- [ ] 记录项目筛选过程（包括失败项目，不只记成功）

---

## 14. 最终选择标准

最终选"最能支撑博客闭环"的项目，不选"最有名的项目"。

最终项目必须能回答：

```
1. 我优化了哪个 hot path？
2. 优化前性能是多少（本地实测）？
3. agent 改了什么？
4. 正确性如何保证？
5. 性能提升是否在多次运行中稳定？
6. 哪些优化失败了？
7. 人类 review 在哪里发挥作用？
8. 这个过程能不能完整复现？
```

如果这些问题答不出来，就不适合作为 Optimize 博客主项目。
