# Blog 1 写作上下文包
# 给外部大模型使用，包含所有写作所需材料

---

## 任务

写一篇技术博客，主题是 GateKeeper Mode：一套在让 AI 修改代码之前，强制建立质量门禁的 workflow。

目标读者：有 AI 辅助编程经验、写过或维护过中等规模项目的开发者。

语言：中文。风格：技术严谨，不拖沓，有真实证据支撑，不吹嘘 AI，诚实展示失败案例。

---

## 核心主张

```
当 AI 能写代码之后，瓶颈不再是"它能不能写出 patch"，
而是"系统能不能证明这个 patch 应该被接受"。
```

GateKeeper Mode 不是一个更强的 prompt，它是一套 workflow：

- Critic 在 patch 存在之前就定义好验收标准
- Builder 独立写代码，看不到 checklist
- Executor 跑确定性检查
- Critic 对照 checklist 审查 patch + 执行日志
- Reject → 把证据喂给 Builder → 重试
- 人只读决策包，不用盯每一轮对话

---

## 文章结构（按此顺序写）

### 1. 为什么"AI 写了代码"不是难点

普通 AI 编程工作流：
- 让一个 agent 写代码
- 让同一个或另一个 agent 看看对不对
- 手动扫一眼 diff
- 希望测试能抓到剩下的问题

典型失败模式：
- 友好评审偏见（review AI 倾向于认可而非质疑）
- 测试通过但语义回退（测试覆盖的是"能跑通"，不是"行为正确"）
- 缺少证据被当成可以接受
- 手动 review 疲劳
- Agent 改了范围外的文件
- 多轮对话产生难以追溯的漂移

### 2. 内部对抗评审模式

关键设计：

| 普通 AI 评审 | GateKeeper Mode |
|---|---|
| Critic 出现在 patch 之后 | Critic 在 patch 之前定义证据标准 |
| 评审基于印象 | 评审基于 checklist + 证据 |
| 测试是可选背景 | Executor 是硬门禁 |
| 批准可以含糊 | 格式错误的批准默认为 reject |
| 人重新读全部内容 | 人只读决策包 |
| 一次性交互 | Reject 带证据进入重试 |

核心原则：**举证责任在 patch 上，不在 rejection 上。**

### 3. 从 GateKeeper Lite 到完整 GateKeeper Mode

演进路径：

```
早期版本：
Builder → Executor → Critic

完整版本（GateKeeper Mode）：
Critic-Prep → Builder → Executor → Critic-Review
```

Critic-Prep 是关键升级：让 Critic 在 Builder 开始工作之前，
基于 brief 和允许修改的文件范围，先写出验收 checklist。
这样 Critic-Review 就无法被 Builder 的实现方式影响。

### 4. 本地 CLI 实现

工具分工：
- Claude Code（CLI）= Builder：写 patch，在 git worktree 隔离
- Codex（CLI）= Critic-Prep 和 Critic-Review：写 checklist，审查证据
- Shell 脚本 = Executor：编译 + 测试 + 评估器
- Git worktree = 隔离：每次 GateKeeper run 在独立分支
- Shell 脚本 = Judge/Orchestrator：决定 APPROVE / REJECT / ESCALATE，写 final_decision.md

为什么不用 AutoGen / LangGraph：先用可见 artifact（文件、日志、diff）证明语义，
再考虑框架迁移。

### 5. 四个门禁

```
scope gate:   allowed_files.txt 限制 Builder 能改哪些文件
setup gate:   critic_checklist.md 必须存在且包含 items
executor gate: 编译 + 测试 + 评估器 exit code
critic gate:   每个 checklist item 必须有证据
```

### 6. 真实运行结果（展示以下六个场景）

见下方"证据材料"部分。

### 7. Known-Issue Sweep：用 GateKeeper 处理已知 bug

继第一个真实任务（volume invariant）通过之后，
对项目已知问题跑了三个 brief：

1. add missing best_bid() regression coverage
2. review Tick layout / alignas issue
3. fix strategy fill ownership validation

结果：第三个任务持续 ESCALATE。失败本身是有价值的结果：
它暴露了评估器本身的弱点。

**关键发现：评估器在 Release 模式下编译测试，项目的 Release flags 定义了 -DNDEBUG，
assert() 因此全部被编译掉。已通过测试的断言实际上没有运行。
评估器的质量门禁变成了冒烟测试。**

修复：把测试改到 Debug/ASan 模式，Release 只用于 benchmark。

### 8. Callback 时序问题（通过 GateKeeper 暴露的设计 bug）

strategy accounting 任务还暴露了一个同步设计问题：

```
submit_order()
  → add_order()
    → match_order()
      → execute_trade()
        → trade_callback_()    # Strategy::on_trade 在这里执行
  → return order_id            # 已经太晚了，strategy 无法预先注册 id
```

这不是多线程竞争，是同步回调的顺序/重入问题。
在 callback 执行时，调用方还没拿到 order_id，所以 strategy 无法判断成交是否属于自己。

**解决方案：两阶段提交 API——`prepare_order()` 先同步返回 order_id，
strategy 记录 id 后再调用 `submit_prepared_order()` 触发撮合。**

### 9. 这套方案还不能解决什么

边界：
- 不是 HFT 优化（那是 Blog 2 的事）
- 不能替代领域测试
- 不能证明 agent 永远正确
- 还没把 Attacker 作为阻断门禁

### 10. 为什么这对 Agent 优化很重要（衔接 Blog 2）

```
在正确性和证据门禁就位之前，不应该让 agent 做性能优化。
```

---

## 证据材料

### 场景 1：一次通过（APPROVE on attempt 1）

**运行目录**：`gatekeeper_runs/20260503-173556/`

**任务**：在 `python-utils/safe_add.py` 写一个 `safe_add` 函数处理 None 输入

**Critic 预写 checklist**（在 Builder 开始前生成）：
```
- [ ] File python-utils/safe_add.py defines function safe_add
- [ ] Function signature is exactly safe_add(a: float | None, b: float | None) -> float
- [ ] safe_add(10.0, 2.0) returns 12.0
- [ ] safe_add(None, 2.0) returns 0.0
- [ ] safe_add(10.0, None) returns 0.0
- [ ] safe_add(None, None) returns 0.0
- [ ] safe_add(-5.0, 3.0) returns -2.0
- [ ] File python-utils/test_safe_add.py exists and includes tests for all required cases
- [ ] Evaluator script scripts/evaluators/evaluate_safe_add.sh passes successfully in executor log
- [ ] No files outside the allowed list are modified
```

**结果**：
```
final_decision.md:
  Final verdict: APPROVE
  Attempts used: 1 / 3
  Gate: CRITIC — All checklist items have direct evidence
```

---

### 场景 2：确定性重试（RETRY → APPROVE）

**运行目录**：`gatekeeper_runs/20260503-173731/`

**任务**：特意设计成 attempt-1 必然失败（evaluator 第一次执行返回 exit 1），用于验证重试机制

**Critic checklist**（预写）：
```
- [ ] File python-utils/retry_test.py exists
- [ ] Defines a function named hello
- [ ] Signature is exactly def hello() -> str:
- [ ] hello() returns exactly "hello"
- [ ] Evaluator passes in executor log
```

**attempt-1 eval.log 末尾**：
```
Attempt number: 1
FAIL: First attempt intentionally fails to trigger retry
This is a deterministic test for retry logic.
```

**结果**：
```
final_decision.md:
  Final verdict: APPROVE
  Attempts used: 2 / 3

  Attempt 1: REJECT — Gate: EXECUTOR (exit code 1)
  Attempt 2: APPROVE — Gate: CRITIC
```

---

### 场景 3：超出重试上限（ESCALATE）

**运行目录**：`gatekeeper_runs/20260503-173917/`

**任务**：两次都确定性失败（max_attempts=2），验证 ESCALATE 路径

**结果**：
```
final_decision.md:
  Final verdict: ESCALATE
  Attempts used: 2 / 2

  Attempt 1: REJECT — Gate: EXECUTOR
  Attempt 2: REJECT — Gate: EXECUTOR
```

附带 cleanup 命令，等待人工介入。

---

### 场景 4：语义 Reject（测试通过，Critic 仍然拒绝）

**运行目录**：`gatekeeper_runs/20260503-153929/`

**任务**：写 `safe_divide`，要求只捕获 `ZeroDivisionError`，不能过度 catch

**Executor 结果**：4/4 测试全通过

**Critic 审查结果**：
```
Verdict: REJECT

3. Function catches ONLY ZeroDivisionError
   Evidence: FAILED — Patch shows except Exception:

6. No over-catching of unrelated exceptions
   Evidence: FAILED — except Exception: catches too much
```

**关键**：测试通过 ≠ 满足所有要求。Critic 的 checklist 覆盖了测试无法覆盖的语义约束。

---

### 场景 5：真实 sandbox 任务（cpp-trader-backtester）

**运行目录**：`gatekeeper_runs/20260503-205323/`

**任务**：为 C++ 订单簿项目添加 volume consistency invariant 测试
（真实项目，真实 C++ 编译，Debug/ASan 构建）

**Critic checklist（预写，13 项）**：
```
- [ ] Only cpp-trader-backtester/src/test_order_book.cpp is modified
- [ ] New function test_volume_invariant() exists
- [ ] Test records executed quantity via set_trade_callback
- [ ] Test verifies remaining resting volume after matching
- [ ] Test explicitly checks: executed_quantity + remaining_volume == total_submitted_quantity
- [ ] Build passes according to evaluate_cpp_trader.sh
- [ ] All order book tests pass
- [ ] No production files are changed
...（共 13 项）
```

**结果**：
```
final_decision.md:
  Final verdict: APPROVE
  Attempts used: 2 / 3

  Attempt 1: REJECT — Gate: CRITIC
    Missing explicit verification that executed quantity equals expected matched quantity.
  Attempt 2: APPROVE — Gate: CRITIC
    All checklist items have direct evidence.
```

**Git commit**：`b0712a6 feat(cpp-trader): Add volume consistency invariant test`

---

### 场景 6：Stress test ESCALATE（暴露评估器弱点）

**运行目录**：`gatekeeper_runs/20260503-214848/`

**任务**：修复 strategy 层的 ownership 验证和 fill accounting
（更复杂的任务，涉及多个文件）

**结果**：
```
final_decision.md:
  Final verdict: ESCALATE
  Attempts used: 3 / 3

  Attempt 1: REJECT — Gate: CRITIC
    Missing evidence that production strategy tests verify owned buy/sell fills.
    Executor log suggests assertions were disabled.

  Attempt 2: REJECT — Gate: CRITIC
    Owned fill behavior is not proven; executor output shows expected position
    updates did not occur while tests still reported PASS.

  Attempt 3: REJECT — Gate: EXECUTOR (exit code 1)
```

**Attempt-1 Critic 发现的问题（Codex 写的）**：
```
Checklist item: MomentumStrategy::on_trade ignores unrelated trades
Evidence: FAILED — executor log shows assertions were disabled via -DNDEBUG.
Tests that passed may not have actually checked the assertions.
```

**根因**：评估器用 Release 模式编译测试。项目 Release flags 包含 -DNDEBUG，
assert() 全部被编译掉。所有 assert 断言的测试条件实际上没有运行。

**修复**：评估器改为 Debug/ASan 模式跑测试，Release 只用于 benchmark smoke。

**结论**：这次失败本身是有价值的结果。GateKeeper 不只是 patch 过滤器；
它也是调试质量系统本身的工具。

---

## 工具角色对照

| 角色 | 工具 | 说明 |
|------|------|------|
| Builder | Claude Code（CLI） | 写 patch，在 git worktree 隔离，看不到 checklist |
| Critic-Prep | Codex（CLI） | 读 brief + allowed_files，写验收 checklist |
| Executor | Shell 脚本 | 编译、测试、评估器，exit code 决定通过/失败 |
| Critic-Review | Codex（CLI） | 读 patch.diff + eval.log，对照 checklist 逐项核查 |
| Judge | Shell 脚本 | 解析 Critic 输出，写 final_decision.md，决定重试或 ESCALATE |
| 人 | 用户 | 只读 final_decision.md，不需要盯每一轮对话 |

---

## 重要句子（可直接引用）

```
GateKeeper Mode 是质量控制系统，不是 prompt 风格。
```

```
Critic 有价值，是因为它在 Builder 影响之前就定义了证据标准。
```

```
测试通过是证据，不是满足所有要求的证明。
```

```
人应该审查证据包，而不是盯着 agent 的每一轮中间对话。
```

```
举证责任在 patch 上，不在 rejection 上。
```

```
一个 Executor 门禁只有它运行的命令那么强。
如果测试依赖 assert()，在 -DNDEBUG 下运行就把质量门禁变成了冒烟测试。
```

---

## 写作注意事项

1. **不要把 cpp-trader-backtester 包装成严肃的 HFT 系统**。它是受控 sandbox，
   用来验证 GateKeeper workflow 能接受好的 patch、能拒绝有问题的 patch。

2. **诚实展示失败**。场景 6 的 ESCALATE 是这篇文章最有价值的部分之一，
   因为它展示了 GateKeeper 如何暴露质量系统自身的弱点，而不只是过滤 patch。

3. **不需要解释所有代码细节**。读者不需要看懂 C++ 订单簿，
   只需要理解"真实项目，真实编译，真实测试"的含义。

4. **callback 时序问题可以简短提及**，不需要展开讲解决方案代码。
   重点是：GateKeeper 运行期间发现了设计 bug，而不只是验证了修复。

5. **Blog 2 衔接**：文章末尾自然过渡到"在正确性门禁就位之后，
   才有资格让 agent 做性能优化"，但不展开 Blog 2 的内容。
