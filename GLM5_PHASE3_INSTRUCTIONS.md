# GLM5 Headless Work Instructions — Phase 3 Optimization Loop

> 这份文件是你的工作队列。按顺序执行，每步执行后验证，验证通过再继续。
> 不需要询问用户，所有判断标准已内置。全部完成后在本文件末尾追加 `## DONE` 和结果摘要。

---

## 环境信息
- 项目路径：`/Users/mac/Desktop/Quant/cpp-trader-backtester`
- 脚本路径：`/Users/mac/Desktop/Quant/scripts/`
- 当前基线延迟：`0.13237 µs/order`（commit 1cf3337）
- 优化目标：降低 ≥ 3%（即 ≤ 0.12840 µs/order）

---

## TASK 0 — 验证基础设施已就位（不需要修改任何东西）

```bash
cd /Users/mac/Desktop/Quant

# 验证所有新文件存在
ls cpp-trader-backtester/experiments/baseline.tsv
ls cpp-trader-backtester/program.md
ls scripts/check_orderbook_invariants.sh
ls scripts/compare_perf.sh
ls scripts/golden/orderbook_invariants.txt

# 验证 evaluator 可运行（不带 OPT_MODE，只跑正确性）
./scripts/evaluators/evaluate_cpp_trader.sh
```

**通过标准**：最后一行输出 `=== ALL GATES PASSED ===`

**失败处理**：如果任何文件不存在，停止并记录 `TASK 0 FAILED: <原因>`

---

## TASK 1 — 阅读热路径代码，制定优化方案

读取以下文件并分析：
```bash
cat cpp-trader-backtester/include/order_book.hpp
cat cpp-trader-backtester/src/order_book.cpp
```

阅读 `program.md` 中的 "Known hot paths" 部分：
```bash
cat cpp-trader-backtester/program.md
```

**输出要求**：在本文件 `## TASK 1 OUTPUT` 处写下：
1. 你认为最有可能改善延迟的 1-2 个具体改动
2. 每个改动的预期原理（为什么更快）
3. 可能的风险（是否会破坏 FIFO / 正确性）

---

## TASK 2 — 实施优化，跑完整 pipeline

按照 `program.md` 的约束实施你在 TASK 1 选择的改动。

```bash
cd /Users/mac/Desktop/Quant/cpp-trader-backtester

# 修改文件后，运行完整优化评估
cd /Users/mac/Desktop/Quant
OPT_MODE=1 ./scripts/evaluators/evaluate_cpp_trader.sh 2>&1 | tee /tmp/phase3_attempt1.log
```

**通过标准**：log 最后两行包含：
```
compare_perf: PASS
=== ALL GATES PASSED ===
```

**失败处理（correctness gate 挂了）**：
```bash
cd /Users/mac/Desktop/Quant/cpp-trader-backtester
git restore include/order_book.hpp src/order_book.cpp include/tick_engine.hpp src/tick_engine.cpp
```
然后尝试 TASK 1 中的第二个方案，重新执行 TASK 2。

**失败处理（perf gate 挂了，correctness 通过）**：
不需要 git restore。分析 log 中的改善百分比，判断是否值得继续优化该方向或换方向。最多尝试 3 次不同方案。

---

## TASK 3 — 成功后提交

```bash
cd /Users/mac/Desktop/Quant/cpp-trader-backtester

# 只 add 修改过的源文件（不 add tests, scripts, golden）
git add include/order_book.hpp src/order_book.cpp   # 按实际修改的文件调整

git commit -m "perf: <一行描述你做了什么>"

git log --oneline -3
```

**通过标准**：`git log` 显示新的 commit 在最顶部。

---

## TASK 4 — 如果 3 次尝试都无法达到 3% 目标

这也是有价值的结果。执行：

```bash
# 确保工作目录干净
cd /Users/mac/Desktop/Quant/cpp-trader-backtester
git restore .

# 记录尝试摘要
cat experiments/results.tsv
```

然后在本文件末尾写下 `## ESCALATE` 和分析：
- 尝试了哪些改动
- 每次的改善幅度（从 results.tsv 读）
- 为什么没有达到 3%（锁在哪里）
- 建议下一步

---

## TASK 1 OUTPUT
<!-- GLM5 在此写下你的分析 -->


---

## DONE / ESCALATE
<!-- GLM5 在此写下最终结果 -->
