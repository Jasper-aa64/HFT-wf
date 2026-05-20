# 用 Agent 做低延时优化：先把沙盒搭出来

Blog 1 讲的是 GateKeeper Mode：在 AI 修改代码之前，先建立质量门禁。

Blog 2 的主题往前走一步：当正确性门禁已经存在后，能不能让 agent 自己做性能优化？

我的答案是：可以，但重点不是“让模型更聪明”，而是给它一个不会说谎的实验沙盒。

```text
agent 提假设
agent 改一处代码
evaluator 编译、跑数、比对 parquet、计时
变快且正确 -> commit
不正确或不够快 -> restore
记录结果，进入下一轮
```

这不是一个聊天技巧，而是一个控制闭环。

---

## 1. 为什么不能直接说“优化一下”

性能优化最危险的地方，是结果看起来很容易量化。

一个版本从 107 秒变成 101 秒，数字很好看。但如果输出 parquet 里某个因子的时间、股票代码、factor value 有了细微偏差，这个优化就没有意义。交易系统里的性能优化，本质上是在不改变语义的前提下改变实现。如果语义验证不够强，agent 会自然地把“跑得快”当成唯一目标。

所以这个阶段的核心不是 prompt，而是 evaluator。

Blog 1 里的结论在这里继续成立：

```text
在正确性和证据门禁就位之前，不应该让 agent 做性能优化。
```

---

## 2. 这次不再用 toy order book

我把目标换成了当前真实项目：

```text
C:\Users\liangjunming\Desktop\work\Code1\psi-trader-liangjunming
```

这不是一个干净的小 demo。它有真实 C++ 构建、Arrow/Parquet 依赖、真实行情 parquet、因子输出、日志线程、Windows 环境里的路径和构建问题。

这正是它有价值的地方。

一个真实优化 harness 至少要处理四件事：

```text
1. build 是否稳定
2. runner 是否能完成
3. 输出 parquet 是否和 baseline 一致
4. wall clock 是否真的变好
```

如果这四件事不能被一个命令判断，agent loop 就还没有资格开始。

---

## 3. 正确性：不要相信日志，要相信 exit code

项目里已经有 `PsiReadWrite::compareFile()`，它会读取参考 parquet，并把当前结果和参考结果做对比。

但它不适合作为自动化裁判。

原因很简单：它发现差异后只是打日志，不会让进程失败。对人类来说，日志可以看；对 agent harness 来说，没有 exit code 就没有判定。

真正适合做 gate 的是独立工具：

```text
tools/compare_parquet_factor.cpp
```

它按下面的 key 做比对：

```text
date|time|thscode|factor_type
```

并比较 `factor_value`。一致返回 `0`，不一致返回非零。这个行为才是 evaluator 需要的。

这里有一个小教训：代码里“有比较逻辑”和“有自动化正确性门禁”不是一回事。前者是辅助观察，后者必须能让流水线失败。

---

## 4. 计时：先用 wall clock，不急着拆阶段

之前文档里写过一个错误结论：总耗时约 140-150 秒/天。

重新看 `timing_logs/run_*.log` 后，实际更接近：

```text
cold run: 107s
warm runs: 105s, 101s, 101s, 102s
```

这几个数字说明两件事：

第一，当前 wall clock 已经足够稳定，warm run 大约在 101-105 秒之间。

第二，fmtlog 的阶段日志有交错问题，不能直接拿来当自动化裁判。阶段日志仍然对人有价值，但第一版 evaluator 应该用完整运行耗时做 gate。

所以第一版策略是：

```text
warmup 1 次，不计入
measure 3 次
取中位数
至少提升 5 秒才自动接受
```

这个 `5 秒` 是当前项目的工程阈值，不是论文结论。

---

## 5. 第一个优化：timestamp 转换缓存

第一个确定性目标在 `PsiFactorPipline/PsiReadWrite.cpp`：

```cpp
timestampUsToInt64()
```

`readParquet()` 每读一行都会调用它。原实现每次都走一次 `localtime_s` / `localtime_r`，再拼出 `YYYYMMDDhhmmssmmm` 这种整数格式。

但输入时间的秒级部分在连续 tick 中会大量重复。只要秒没变，日期、小时、分钟、秒这些前缀就不会变，变化的只是最后的毫秒部分。

所以第一刀很直接：

```text
缓存上一秒的 formatted base
同一秒内直接返回 cached_base + millisecond_part
```

这类优化不需要 agent 盲试。它是人读代码后可以直接证明方向正确的 baseline improvement。真正适合 agent search 的，是后续更不确定的部分，比如 `generateTable()` 里的字符串构造、Arrow builder append、compare map 的开销。

---

## 6. Harness 文件

第二阶段的最小闭环由两个文件组成：

```text
02-Agent-Driven Workflow/psi-trader-program.md
scripts/psi_evaluate.ps1
```

`program.md` 是 agent 的规则：能改哪些文件，不能改哪些文件，成功后怎么 commit，失败后怎么 restore。

`psi_evaluate.ps1` 是裁判：

```text
cmake --build
warmup
3 次 measure
compare_parquet_factor 比对 baseline
根据中位数和阈值决定 PASS/FAIL
```

后面可以再包一层 CLI loop，让它像 GateKeeper 一样一条命令跑多轮：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\psi_optimize_loop.ps1 `
  -Repo C:\Users\liangjunming\Desktop\work\Code1\psi-trader-liangjunming `
  -Iterations 20 `
  -Program "02-Agent-Driven Workflow\psi-trader-program.md"
```

但第一步不是多 agent，也不是并行搜索，而是让单个 evaluator 的 PASS/FAIL 可信。

---

## 7. 当前卡点也要写进实验记录

这次实现 timestamp 缓存后，我没有把它写成“已验证成功”。

原因是构建验证还没有完整通过：

```text
直接调用 c++.exe 编译 PsiReadWrite.cpp 成功
cmake --build build --target PsiTraderRunner 仍在 Make recipe 的对象编译步骤失败
失败没有吐出编译器诊断
```

这不是性能结论，而是 harness 结论：真实项目的第一道门禁，往往不是 benchmark，而是构建可重复性。

如果连 build gate 都不稳定，后面的 agent loop 会把大量时间浪费在不可解释的失败上。这个卡点必须先解决，不能跳过去。

---

## 8. 我现在对 Agent 优化的理解

Agent 做性能优化的关键不是让它“想出天才优化”，而是让它在一个清晰边界内持续试错。

人类负责设计沙盒：

```text
什么能改
什么不能改
什么叫正确
什么叫变快
什么时候保留
什么时候回滚
怎么记录每一次尝试
```

Agent 负责在这个沙盒里大量提出假设、改代码、跑裁判。

低延时优化过去依赖工程师的经验和耐力。现在可以把一部分耐力交给 agent，但前提是：裁判必须比 agent 更可靠。

