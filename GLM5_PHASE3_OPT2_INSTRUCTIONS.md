# GLM5 Headless Work Instructions — Phase 3 Optimization Round 2

> 第一轮已完成：移除冗余字段，延迟从 0.13237 → 0.12104 µs/order（8.56%）。
> 本轮目标：继续降低延迟，目标改善 ≥ 3%（相对 baseline.tsv 的原始基线 0.13237）。
> 即通过标准：≤ 0.12840 µs/order（当前已是 0.12104，所以只要不大幅劣化就能通过 perf gate）。
> 真正目标：尽可能低，为 Blog 2 积累数据。

---

## 环境
- 项目：`/Users/mac/Desktop/Quant/cpp-trader-backtester`
- 脚本：`/Users/mac/Desktop/Quant/scripts/`
- 当前代码状态：commit `427325d`（已含 Round 1 优化）
- 评估命令：`OPT_MODE=1 ./scripts/evaluators/evaluate_cpp_trader.sh`（从 `/Users/mac/Desktop/Quant/` 运行）

---

## 背景：热路径分析

当前 `PriceLevel` 定义（`include/order_book.hpp`）：
```cpp
struct PriceLevel {
    std::list<Order*> orders;    // ← 这是本轮目标
    Quantity total_quantity = 0;
};
```

`std::list` 的问题：
- 每个节点单独堆分配，内存不连续
- `front()` / `pop_front()` 需要追指针，cache miss
- 高频 add/match 操作每次都在跳内存

订单簿操作模式是 **FIFO 队列**：只从 front 取，只从 back 插入。
`std::deque` 完全支持相同接口，且内存布局按 chunk 分配，cache 局部性更好。

---

## TASK 0 — 验证当前基线可复现

```bash
cd /Users/mac/Desktop/Quant
OPT_MODE=1 ./scripts/evaluators/evaluate_cpp_trader.sh 2>&1 | tail -5
```

**通过标准**：输出包含 `compare_perf: PASS` 和 `=== ALL GATES PASSED ===`

**若失败**：停止，记录 `TASK 0 FAILED`，不继续。

---

## TASK 1 — 方案 A：std::list → std::deque（优先尝试）

这是 drop-in 替换，API 完全兼容，风险极低。

### 1a. 修改 `include/order_book.hpp`

找到这两行：
```cpp
#include <list>
```
替换为：
```cpp
#include <deque>
```

找到：
```cpp
    struct PriceLevel {
        std::list<Order*> orders;
        Quantity total_quantity = 0;
    };
```
替换为：
```cpp
    struct PriceLevel {
        std::deque<Order*> orders;
        Quantity total_quantity = 0;
    };
```

### 1b. `src/order_book.cpp` 无需修改

`deque` 与 `list` 接口完全一致（`front()`, `pop_front()`, `push_back()`, `erase(it)`），
`cancel_order` 中的 `level.orders.erase(order_it)` 对 `deque` 也有效。

### 1c. 运行评估

```bash
cd /Users/mac/Desktop/Quant
OPT_MODE=1 ./scripts/evaluators/evaluate_cpp_trader.sh 2>&1 | tee /tmp/phase3_opt2a.log
```

**通过标准**：
```
compare_perf: PASS
=== ALL GATES PASSED ===
```

**若正确性失败（test_order_book / test_strategies 报错）**：
```bash
cd /Users/mac/Desktop/Quant/cpp-trader-backtester
git restore include/order_book.hpp src/order_book.cpp
```
跳到 TASK 2（备选方案）。

**若 perf gate 失败（改善 < 3% 相对原始基线，或劣化）**：
同样 git restore，跳 TASK 2。

**若通过**：跳到 TASK 3（提交）。

---

## TASK 2 — 方案 B：vector + head_index 游标（若方案 A 不够）

比 deque 更激进：用 `std::vector` 做底层存储，用索引游标代替 `pop_front`（避免 O(n) 移动）。

### 2a. 修改 `include/order_book.hpp`

`#include <list>` 已可删除（若方案 A 失败后已恢复，此时还是 list，需要改）。
确保有 `#include <vector>`（已有）。

将 PriceLevel 改为：
```cpp
    struct PriceLevel {
        std::vector<Order*> orders;
        size_t head = 0;           // 游标：0 到 head-1 的元素已消费，逻辑上已删除
        Quantity total_quantity = 0;

        // 返回队列头部（FIFO front）
        Order* front() const { return orders[head]; }

        // 逻辑 pop_front：推进游标，不移动内存
        void pop_front() { ++head; }

        // 判断逻辑是否为空
        bool empty() const { return head >= orders.size(); }

        // 追加到队尾
        void push_back(Order* o) { orders.push_back(o); }

        // 清除所有已消费元素（在 erase level 前调用，或按需）
        void compact() {
            if (head > 0) {
                orders.erase(orders.begin(), orders.begin() + head);
                head = 0;
            }
        }
    };
```

### 2b. 修改 `src/order_book.cpp`

**add_order() 中追加订单**（已是 push_back，无需改动）。

**match_order() 中消费订单**（找到所有 `level.orders.front()` 和 `level.orders.pop_front()` 的位置）：

原来代码：
```cpp
Order* contra_order = level.orders.front();
// ... 成交逻辑 ...
if (contra_order->filled >= contra_order->quantity) {
    contra_order->status = OrderStatus::FILLED;
    level.orders.pop_front();
}
```

不需要修改——`front()` 和 `pop_front()` 已经在 PriceLevel 上封装好了。

**cancel_order() 中的 erase**：这里原来是：
```cpp
level.orders.erase(order_it);
```
用 iterator 删除中间某个元素。对 vector 这是 O(n)，但 cancel 不在热路径上，可接受。
不需要特殊处理，vector 的 `erase(it)` 语义相同。

**检查 level.orders.empty()**：原来的 `level.orders.empty()` 调用都要改成 `level.empty()`：

找到 `order_book.cpp` 中所有 `level.orders.empty()` → 替换为 `level.empty()`。

搜索命令：
```bash
grep -n "level.orders.empty\|level.orders.front\|level.orders.pop_front\|level.orders.push_back" \
    /Users/mac/Desktop/Quant/cpp-trader-backtester/src/order_book.cpp
```

### 2c. 运行评估

```bash
cd /Users/mac/Desktop/Quant
OPT_MODE=1 ./scripts/evaluators/evaluate_cpp_trader.sh 2>&1 | tee /tmp/phase3_opt2b.log
```

**通过标准**：同方案 A。

**若失败**：
```bash
cd /Users/mac/Desktop/Quant/cpp-trader-backtester
git restore include/order_book.hpp src/order_book.cpp
```
跳到 TASK 4（ESCALATE）。

---

## TASK 3 — 通过后提交

```bash
cd /Users/mac/Desktop/Quant/cpp-trader-backtester

# 根据实际修改的文件 add
git add include/order_book.hpp
# 如果 order_book.cpp 也改了：
# git add src/order_book.cpp

# 提交，说明用了哪个方案
git commit -m "perf: Replace std::list with std::deque in PriceLevel for cache locality"
# 或方案 B：
# git commit -m "perf: Replace std::list with vector+head cursor in PriceLevel"

git log --oneline -4
```

---

## TASK 4 — ESCALATE（两方案均失败时）

```bash
cd /Users/mac/Desktop/Quant/cpp-trader-backtester
git restore .
cat experiments/results.tsv
```

在本文件末尾写下 `## ESCALATE` 和分析：
- 方案 A 结果（有无尝试，改善幅度，失败原因）
- 方案 B 结果（同上）
- 建议下一个方向

---

## TASK 1 OUTPUT
<!-- GLM5 在此写下分析 -->


---

## DONE / ESCALATE
<!-- GLM5 在此写下最终结果 -->
