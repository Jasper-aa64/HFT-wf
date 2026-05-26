# TWAP Optimization Harness 适配说明

## 1. 当前结论

HFT-wf 里的 optimization harness 可以用于 TWAP 持仓推送优化，但不能直接按 Psi 现有命令跑。

原因：

1. `scripts/psi_headless_auto_loop.py` 的编排能力可以复用：candidate、独立 workspace、patch command、远端 build、verdict、artifact 归档这些机制是通用的。
2. `scripts/psi_headless_remote.sh` 目前强依赖 Psi Runner、Psi config、parquet compare、factor output，这些不能直接用于 TWAP。
3. TWAP 优化必须先补一个 TWAP 专用 adapter：远端 build、correctness oracle、timing runner、artifact parser 都要换成 TWAP 语义。

一句话：

```text
复用 harness 控制流，不复用 Psi correctness / timing 假设。
```

## 2. 适合用它做什么

适合：

1. 让 agent 提 TWAP 持仓推送优化候选。
2. 每个候选在独立 workspace 里改代码。
3. 自动构建候选版本。
4. 用 TWAP runtime smoke 确认推送语义没变。
5. 用 TWAP 性能工具做新旧 paired timing。
6. 根据 build / correctness / timing 给 verdict。
7. 记录 candidate ledger，避免重复试错。

不适合：

1. 直接把 Psi 的 parquet compare 当 TWAP 正确性验证。
2. 直接把 PsiRunner 单次耗时当 TWAP 推送性能。
3. 让 agent 直接改 `/root/work/Code2` baseline。
4. 只看性能变快，不验证推送 payload 正确性。
5. noisy 结果直接 accepted。

## 3. TWAP 这次优化目标

当前任务来自：

```text
twap 的代码你看下优化空间，先看持仓推送那边的
用昨天发给你的代码
```

优化范围应先限定在：

```text
TWAP 卖出持仓聚合推送链路
Redis twap_stock_position_info
-> 持仓变化处理
-> subPositionInfoListAggregation 推送
-> aggregation_position_info_insert / aggregation_position_info_update payload
```

优先关注：

1. 是否有重复 DB 查询。
2. 是否能复用内存中已有持仓数据。
3. 推送队列是否有空转或锁竞争。
4. 聚合过程是否反复全量扫描。
5. JSON 构造是否有明显重复分配。
6. 多账号同股票聚合是否有不必要排序或拷贝。

暂不扩展：

1. 运行、停止、撤单接口。
2. TWAP 下单链路。
3. 前端渲染。
4. 非持仓推送相关 proto 大改。

## 4. 必须先建立的 TWAP adapter

### 4.0 Baseline 前置条件

如果本次要求“用 5/25 收到的最新 TWAP 代码”，必须先确认它是一个可编译 baseline。

已知风险：

```text
5/25 收到的 TWAP 文件单独替换后，当前 Code2 工程缺少配套公共层接口。
```

之前性能对比时为让它可编译，曾临时补齐：

```text
PsiCfgLoader::orderConfigSaveRsp(cmd, ...)
PsiMemSQL::querySimpleTwapPositionAgg(userId)
```

因此专门会话第一步不能直接优化，应先做：

1. 明确 5/25 最新 TWAP 代码的完整文件集。
2. 补齐或获取配套公共层改动。
3. 在远端 Linux 编译通过。
4. 跑 TWAP correctness smoke 通过。
5. 把这份状态定义为 TWAP optimization baseline。

如果 baseline 不能 build / correctness pass，后续 timing 和优化 verdict 都无效。

### 4.1 Source Root

候选源码来自 Code2：

```text
local source root: C:\Users\liangjunming\Desktop\work\Code2
remote source root: /root/work/Code2
remote host: 192.168.170.62
env: source /root/work/.toolchain/psi-env-code2.sh
```

候选不能直接改 baseline。必须复制到独立 workspace：

```text
<run_dir>/candidate_workspaces/<candidate_id>
```

远端对应：

```text
/root/work/psi_experiments/local_agent_candidates/<run_name>/<candidate_id>
```

### 4.2 Build Command

TWAP 候选至少需要远端构建：

```bash
cd <candidate_workspace>
source /root/work/.toolchain/psi-env-code2.sh
cmake --build build --target PsiGrpcServer -j 4
cmake --build build --target PsiTraderRunner -j 4
cmake --build build --target twap_position_push_perf_test -j 4
```

如果候选涉及 proto：

1. 必须使用远端匹配版本的 protoc。
2. 不能使用 Windows 本地生成的 pb 文件作为最终依据。

### 4.3 Correctness Oracle

TWAP correctness 不能用 Psi parquet compare。应改成 TWAP runtime smoke。

本轮任务先看“持仓推送那边”，因此 adapter 第一版默认使用 `push_only` correctness。最低正确性验证：

1. 服务能启动并监听 `192.168.170.62:18321`。
2. `subPositionInfoListAggregation` 初始聚合列表正确。
3. Redis 发布两账号同股票持仓变化后，推送 payload 正确。
4. 聚合主行 `volume` 等于子账号合计。
5. `subPositionInfoList` 包含各账号明细。
6. `accountDesc` 中文字段不乱码。
7. 0 持仓变化、searchStockCode 过滤等已有边界不回退。

不应把运行、停止、撤单、配置保存接口混入持仓推送 correctness gate。它们可以作为更大范围 TWAP regression suite，但不应阻塞“持仓推送优化 harness”的 timing 阶段。

建议 correctness 输出：

```text
comparison_summary.json
correctness_status: pass / failed
failed_case: ...
payload_samples: ...
```

### 4.4 Timing Runner

TWAP timing 应使用已有性能工具：

```text
PsiGrpcServer/tools/twap_position_push_perf_test.cpp
```

建议固定测试组：

```text
100@50ms
500@20ms
1000@20ms
500@5ms
```

主判断以固定用户、固定账号为准：

```text
userId: dc548fe6083e4523a918aaef1a68b857
accounts: 666665,66666666
```

timing 输出至少包括：

```text
sent
received
lost
unknownPushes
avg
P50
P95
P99
max
```

### 4.5 Verdict Rules

候选只能在以下条件全部满足时进入 promotion candidate：

```text
build pass
correctness pass
sent == received
lost == 0
payload schema unchanged unless explicitly requested
P95 improves in normal frequency 500@20ms / 1000@20ms
high pressure 500@5ms no obvious regression
```

不能 accepted 的情况：

```text
build failed
correctness failed
payload 不一致
accountDesc 乱码
0 持仓边界回退
searchStockCode 过滤回退
lost > 0
只在单次 noisy 样本里变快
```

## 5. 需要改造的 HFT-wf 文件

最小落地建议不是直接修改 `psi_headless_remote.sh`，而是新增 TWAP 专用脚本，避免污染 Psi harness。

建议新增：

```text
scripts/twap_headless_remote.sh
scripts/twap_candidate_generator.py
scripts/twap_patch_agent.py
```

可以复用：

```text
scripts/psi_headless_auto_loop.py 的 workspace / ledger / verdict 思路
scripts/psi_patch_queue.py 的 patch 记录机制
scripts/psi_timing_history.py 的 timing history 结构
```

但第一版更稳妥的做法是：

```text
先写 twap_headless_remote.sh
先手工给一个 candidate patch
跑 build -> correctness -> timing -> artifacts
确认 adapter 成立后，再接 auto_loop 和 patch_agent
```

## 6. 第一阶段不要做什么

不要一上来让 agent 大范围优化。

第一阶段目标只是证明 harness adapter 能闭环：

```text
latest TWAP baseline build/correctness pass
baseline build
candidate workspace build
correctness smoke
paired timing
comparison_summary.json
timing_samples.tsv
run_state.json
```

如果 adapter 没闭环，任何优化结论都不可信。

## 7. 给专门会话的启动提示

可以直接把下面这段发给专门会话：

```text
你要在 HFT-wf 里把现有 Psi optimization harness 适配到 Code2 的 TWAP 持仓推送优化。

不要直接改 Code2 baseline。
不要直接复用 Psi parquet compare。
不要把 noisy timing 当 accepted。

第一阶段只做 adapter，不做正式优化：
1. 阅读 HFT-wf/scripts/psi_headless_auto_loop.py 和 psi_headless_remote.sh。
2. 新增 TWAP 专用 remote runner，例如 scripts/twap_headless_remote.sh。
3. 远端 source root 使用 /root/work/Code2，环境使用 /root/work/.toolchain/psi-env-code2.sh。
4. build 阶段编译 PsiGrpcServer、PsiTraderRunner、twap_position_push_perf_test。
5. correctness oracle 使用 TWAP runtime smoke：subPositionInfoListAggregation 初始列表、Redis 持仓变化推送、volume 聚合、subPositionInfoList、accountDesc 中文不乱码、0 持仓和 searchStockCode 边界。
6. timing runner 使用 twap_position_push_perf_test，固定用户 dc548fe6083e4523a918aaef1a68b857，固定账号 666665/66666666，跑 100@50ms、500@20ms、1000@20ms、500@5ms。
7. 产出 artifacts：run_state.json、comparison_summary.json、timing_samples.tsv、build.log、correctness.log、timing logs。
8. verdict 规则：build pass + correctness pass + lost=0 + 正常频率 P95 改善，才允许 promotion_candidate；否则 rejected / NOISY_PENDING。

完成后只提交 HFT-wf harness adapter，不提交 Code2 优化 patch。
```

## 8. 当前可用性判断

当前 HFT-wf harness：

```text
编排层：可复用
patch workspace：可复用
ledger：可复用
Psi remote script：不可直接用于 TWAP
Psi correctness：不可用于 TWAP
Psi timing：不可用于 TWAP
```

因此适配优先级：

1. 先做 TWAP remote adapter。
2. 再接 TWAP correctness。
3. 再接 TWAP timing。
4. 最后才让 patch agent 生成优化候选。

## 9. TWAP Candidate Seed File

TWAP runs should not use the Psi profile/hotspot generator. Feed explicit
TWAP candidates into the auto-loop with `--candidate-seed-file`.

Example:

```powershell
python scripts\psi_headless_auto_loop.py `
  --candidate-seed-file <seed.json> `
  --remote-batch-script scripts/twap_headless_remote.sh `
  --control-root /root/work/Code2 `
  --twap-measure-cases "3:50:60"
```

Seed file shape:

```json
{
  "evidence": [
    {
      "candidate_id": "twap_push_cache_position_lookup",
      "lane": "evidence",
      "target": "twap.position_push.aggregation",
      "hypothesis": "Avoid expensive per-push work in aggregation push path.",
      "expected_effect": "Lower P95 push latency while preserving aggregation payload.",
      "semantic_risk": "medium",
      "touched_files": ["PsiGrpcServer/twap_sale_service.cpp"],
      "source_evidence": {"kind": "manual_twap_review"}
    }
  ],
  "insight": [],
  "combination": []
}
```

`builtin:fake-nonempty` is allowed only for harness smoke. It proves candidate
workspace sync and remote TWAP gates; it is not real performance evidence.

## 10. TWAP Hotspot Long-Run Mode

For real TWAP optimization long runs, do not rely on hand-written candidate
seed files as the main path. The current long-run path is:

```text
TWAP source snapshot
-> scripts/twap_profile_hotspots.py writes profile.tsv / hotspots.tsv
-> psi_candidate_generator.py builds evidence / insight / combination lanes
-> twap_codex_patch_agent.py edits an isolated candidate workspace
-> twap_headless_remote.sh runs remote Linux build + TWAP correctness + timing
-> attempts.tsv / neutral_pool.tsv / patch_manifest.json record the verdict
```

`--candidate-seed-file` remains useful for adapter smoke tests and manually
bounded probes, but it is not the preferred "trustee" long-run mode.

The TWAP profile rows are static source-backed hotspot estimates, not timing
acceptance evidence. They only provide target ordering, touched files, and
source symbols for the patch agent. A candidate can be promoted only from
remote artifacts:

```text
build_status = pass
correctness_status = pass
timing_status = pass
lost = 0
normal-frequency P95 clears the configured improvement threshold
stress case has no configured regression
```

If every generated candidate has already been attempted and no combination
candidate remains, the run must stop with `no_targets` instead of looping.
