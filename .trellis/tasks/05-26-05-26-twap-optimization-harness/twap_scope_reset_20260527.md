# TWAP Optimization Scope Reset - 2026-05-27

## Conclusion

Position push first-pass optimization is exhausted for now.

The lane produced useful evidence and improved the harness, but it has not produced a safe Code2 optimization patch:

- no clean accepted candidate
- multi-subscriber tail latency is easy to regress
- many candidate ideas are tightly coupled to user/account/stock/subscription semantics
- small local savings are often covered by fanout, queueing, Redis/gRPC, and scheduler jitter

Do not continue treating "TWAP optimization space" as only "position push optimization".

## Evidence

Relevant runs:

- `twap_multisub_longrun_20260527_0845`
  - 3 candidates
  - 0 accepted
  - 2 neutral
  - 1 rejected
  - follow-up confirmation showed `json_serialize_total` was unstable and not promotable

- `twap_json_confirm_20260527_0910`
  - build/correctness/timing completed
  - `100_i50_s4` candidate P95 regressed from about `5.59ms` to `41.56ms`
  - exposed a gate bug where `100_i50_s4` was not included in normal-frequency regression checks

- `twap_json_confirm_20260527_0920`
  - after gate fix, same candidate did not reproduce the 41ms spike
  - still only `screening_only`, not accepted

- `twap_seed002_longrun_20260527_1015`
  - source root aligned and TWAP runner used correctly
  - 2 position-push seed candidates both rejected
  - max normal-frequency P95 regression was about `44-46ms`

## Harness State

Gate status: freeze for now.

Allowed harness changes from here:

- fix false accept
- fix false reject
- fix missing artifact / wrong artifact mapping
- fix source-root alignment bugs

Do not keep changing verdict rules just because candidates fail.

## Metrics Policy

P95 remains useful, but only as a provisional tail-latency guardrail.

It must not be the only acceptance criterion. Acceptance needs:

- correctness: `lost=0`, duplicate/unknown push count zero, payload semantics preserved
- latency: P50/P90/P95/P99 or max do not show meaningful regressions
- fanout: worst-subscriber P95 does not regress
- stability: paired multi-run direction is consistent
- control health: control has no loss and no active-runner contamination
- A/A calibration: thresholds should be larger than natural baseline jitter

Current temporary guardrails:

- normal-frequency P95 regression over `1ms` rejects
- stress P95 regression over `5ms` rejects

These are provisional until A/A baseline jitter is measured.

## Next Scope

Move from `position_push` to TWAP global hot-path discovery.

Candidate lanes to explore next:

- `twap.order_status_lookup`
- `twap.order_config_lookup`
- `twap.subscription_filter`
- `twap.redis_update_ingest`
- `twap.timer_dispatch`
- `twap.order_lifecycle_state`
- `twap.shared_serialization`

Rules for the next candidate batch:

- avoid large push/message/fanout rewrites
- prefer deterministic waste: repeated lookup, repeated key construction, unnecessary sort/pass, lock-scope cleanup
- do not cache or reuse payload across users/accounts/subscriptions unless user-independence is proven
- do not promote any Code2 patch automatically

