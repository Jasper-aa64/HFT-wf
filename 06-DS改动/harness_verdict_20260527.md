# DS改动：优化 Harness 裁决逻辑重构

日期：2026-05-27

---

## 改动概述

本次对 Psi/TWAP 优化 harness 的裁决（verdict）逻辑进行了三个层次的升级，核心目标是**让 VM 环境下的优化验证更合理：不因测量噪声否决统计上已证实的优化**。

---

## 1. Validation Lock v2（已提交：85c5d51）

### 问题

host audit v1 只检查启动前的远端状态。m24 跑到一半，TWAP runner 或其他进程插进来，导致 mid-run contamination，证据无效。

### 改动

新增 `scripts/perf_validation_lock.py`，在远端 devbox 上实现文件锁：

- 锁文件：`/root/work/.perf_validation.lock`
- Psi/TWAP harness 启动 timing 前抢锁，`trap EXIT` 自动释放
- 只释放自己的锁，不释放别人的
- 损坏的锁文件保守处理（视为 held）
- `psi_host_jitter_audit.py` preflight 阶段检查远端锁状态

新增 `tests/test_perf_validation_lock.py`，15 个单元测试。

修改 `psi_headless_remote.sh`、`twap_headless_remote.sh`、`psi_host_jitter_audit.py`。

---

## 2. accepted_noisy：噪声降级不否决

### 问题

旧 verdict 逻辑中，噪声检查（`noise_flag == "NOISY"`）排在统计检查之前。即使统计上已 conclusive（p≤0.05, CI>0），噪声门直接返回 `NOISY_PENDING`。

在 VM 环境下，control 自身抖动就有 2.5% range / 1% stdev，导致几乎所有候选都被噪声门拦截（17/17 全 NOISY_PENDING），包括 stack m24（24 对全正，+7.8s，p=0.0005）。

### 改动（psi_timing_analysis.py）

调整 verdict 顺序：

```
旧：noise_flag → NOISY_PENDING → 统计 → accepted
新：统计 conclusive + noise_flag=ok       → accepted
    统计 conclusive + noise_flag=NOISY    → accepted_noisy  ← 新增
    统计 inconclusive + noise_flag=NOISY  → NOISY_PENDING
```

`accepted_noisy` 是 accepted 的一种，不是 rejected。区别只在标注：建议 bare metal 复验。

### 影响文件

- `psi_timing_analysis.py` — 核心 verdict 逻辑
- `psi_auto_optimize.py` — 计数、队列状态
- `psi_headless_auto_loop.py` — 循环控制
- `run_prepared_candidate.py` — 手动 driver
- `test_twap_harness_replay.py` — 测试适配

---

## 3. Class A/B 两层门禁政策

### 背景

来源于 5月7日批准的 `2.4. Optimization Gate Policy.md`，一直未在 harness 中实现。

核心思想：不是所有性能优化都需要 statistical proof。算法确定性的改动（删除无用赋值、用已有值替换拷贝）只需要 correctness gate，不需要 perf gate。

### Class A — 算法确定性

**定义**：逻辑重构，无可预见的退化机制。

判断标准：
- 没有新状态暴露给调用方
- 没有新缓存或分支布局
- 热路径上没有新的内存分配模式

**门禁**：correctness only（compare pass 即通过）。perf 记录但不拦截。

**已有 Class A 候选**（纯删除无用赋值）：
- `skip_unused_market_strings`
- `skip_unused_preclose_assignment`
- `skip_unused_book_volume_assignment`
- `skip_unused_amount_assignment`
- `stack_skip_unused_row_fields`（以上四个的合并）

### Class B — 经验性改动

**定义**：Class A 之外的任何改动。

**门禁**：correctness + statistical significance（bootstrap CI + permutation p-value）。

默认分类为 Class B。不确定时一律 Class B。

`run_prepared_candidate.py` 新增 `--change-class class_a|class_b` 参数，默认 `class_b`。

### Verdict 矩阵（完整）

| 条件 | Verdict | 含义 |
|---|---|---|
| Class A + build pass + compare pass | `accepted_class_a` | 通过，perf 记录不拦 |
| Class B + 统计 conclusive + 安静 | `accepted` | 通过 |
| Class B + 统计 conclusive + 噪声 | `accepted_noisy` | 通过，建议 bare metal 复验 |
| Class B + 统计 inconclusive + 噪声 | `NOISY_PENDING` | 等安静窗口重测 |
| 统计 inconclusive + 安静 | `neutral` | 保留，待 bundle audit |
| CI 包含 0 或在零下 | `rejected` | 不通过 |
| build/compare fail | `rejected` | 不通过 |

### 影响文件

- `psi_timing_analysis.py` — `change_class` 参数，Class A 直接通过
- `psi_headless_auto_loop.py` — `count_verdict_rows` 扩展至 7 元组
- `run_prepared_candidate.py` — `--change-class` 参数
- `optimization-harness-guidelines.md` — 新增 Class A/B 章节

---

## 4. count_verdict_rows 返回值扩展

从 5 元组扩展到 7 元组：

```
旧：(accepted, neutral, rejected, NOISY_PENDING, infra_blocked)
新：(accepted, neutral, rejected, NOISY_PENDING, infra_blocked, accepted_noisy, accepted_class_a)
```

前 5 个元素保持兼容，后 2 个为新增 verdict 计数。

---

## 5. Trellis 升级：0.5.19 → 0.6.0-beta.21

74 个模板文件自动更新，跨所有平台（.claude/、.codex/、.cursor/、.opencode/、.gemini/、.agents/、.trellis/）。

主要新功能：
- `trellis mem` — 会话搜索
- `trellis channel` — 多 agent 协作
- `trellis upgrade` — 版本升级命令
- `trellis-spec-bootstarp` skill

备份：`.trellis\.backup-2026-05-27T07-35-32\`

---

## 验证

- 42/42 测试通过（含 validation lock 15 个、回归 27 个）
- 所有 Python 文件 py_compile 通过
- bash 脚本语法检查通过

---

## 6. Codex review 后修正：accepted_noisy 三层语义

日期：2026-05-28

原始版本把 `accepted_noisy` 写成 accepted 的一种，容易让单轮 noisy 结果直接进入 applied / baseline 语义。修正后拆成三层：

| Verdict | 含义 | patch status | 是否计入 clean accepted |
|---|---|---|---|
| `accepted` | clean / quiet 条件下统计显著 | applied | yes |
| `accepted_noisy_single` | 单轮 noisy 但统计强正，接受为证据 | reverted，进入 replication 队列 | no |
| `accepted_noisy_replicated` | 多个 locked 独立窗口均强正 | applied，标注 shared-host / non-bare-metal | yes |

关键修正：

- `accepted_noisy_single` 不再 `applied`，不触发 `first_accepted_stop`。
- `accepted_noisy_replicated` 可以在没有 bare metal 的情况下作为 shared-host promotion 证据，但 artifact 必须标注非裸机环境。
- `accepted_class_a` 继续允许 correctness-only，但必须经过 Class A hard whitelist。
- Class A hard whitelist 已接入 auto-loop 生产路径：不通过时强制降级为 `class_b`。
- `attempts.tsv` schema 增加 `change_class` / `replicated`，防止 remote writer 因 paired evidence 新字段崩溃。
- `psi_auto_optimize.py` 已改为识别 `accepted_noisy_single` / `accepted_noisy_replicated`，不再使用旧的 `accepted_noisy` verdict。
- `psi_headless_remote.sh` 的 accepted summary 现在把 promotion verdict 集合统一为：
  - `accepted`
  - `accepted_class_a`
  - `accepted_noisy_replicated`

新增测试：

- noisy single / replicated verdict 分流
- `judge_verdict` 大小写兼容
- Class A forbidden / allowed pattern
- auto-loop 生产路径 Class A 降级
- `evidence_fields` 与 `ATTEMPTS_FIELDNAMES` schema 一致

当前验证：

- `python -m py_compile scripts\psi_timing_analysis.py scripts\psi_headless_auto_loop.py scripts\psi_auto_optimize.py scripts\psi_attempts_schema.py tests\test_psi_timing_analysis.py`
- `python -m pytest tests -q`
- 结果：57/57 passed
- 本机 Windows 没有 `bash` 命令，`psi_headless_remote.sh` 的 `bash -n` 未能在本机执行；需要在远端 Linux 或带 bash 的环境复核。
