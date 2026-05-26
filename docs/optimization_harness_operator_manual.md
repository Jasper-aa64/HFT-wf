# Optimization Harness Operator Manual

This manual defines how to operate the HFT-wf optimization harness without
turning it into a blind candidate conveyor belt.

The harness exists to close an optimization loop:

```text
runtime profile
-> scoped hypothesis
-> bounded agent patch
-> build
-> correctness
-> paired timing
-> verdict
-> ledger
-> operator decision
```

`candidate` is only the experiment row used to record one attempt. It is not
the strategy, and it must not become the main control flow.

## 1. Core Rule

Do not start from "generate more candidates".

Start from measured runtime evidence and a scoped optimization question:

```text
What hot path is expensive?
What work can be removed, cached, batched, or moved without changing behavior?
What evidence would prove the patch is safe and useful?
```

If those questions are not answered, the correct next step is profiling or
scope review, not another candidate run.

## 2. Required Inputs Before a Run

Each bounded optimization run needs these inputs:

- real runtime profile or timing evidence for the target path
- explicit scope decision
- allowed patch shapes
- forbidden patch shapes
- maximum attempts or one prepared patch
- correctness gate
- timing gate
- host readiness policy
- stop conditions

If the profile is missing, static hotspot estimates may only be used as a
planning hint. They must not be described as runtime evidence.

## 3. Scope Decision Template

Before execution, write or state:

```text
Target:
Runtime evidence:
Hypothesis:
Allowed changes:
Forbidden changes:
Correctness gate:
Timing gate:
Host gate:
Max attempts:
Stop conditions:
Promotion authority:
```

Example:

```text
Target: Psi readParquet handlerData.row_loop
Runtime evidence: no_compare / paired timing shows this stage dominates
Hypothesis: remove unused row materialization without changing projection
Allowed changes: local row-loop work reduction in PsiReadWrite.cpp
Forbidden changes: projection pruning, schema changes, compare config changes
Correctness gate: build pass + compare pass
Timing gate: paired A/B with sufficient sample count
Host gate: host weather audit QUIET before promotion timing
Max attempts: one prepared stack
Stop conditions: correctness fail, host noisy, clean accepted, noisy positive
Promotion authority: not devbox if host weather is noisy
```

## 4. Runtime Profile Rules

Preferred evidence:

- runtime stage profile
- same-harness control samples
- paired A/B timing
- profiling logs or hotspot artifacts from the actual runner path

Weak evidence:

- static code estimates
- generic intuition that a line "looks expensive"
- old timing from a different host, branch, config, dataset, or workload

Weak evidence may guide brainstorming. It cannot justify promotion.

## 5. Host Weather Gate

Before promotion timing, run a host weather audit.

Expected artifacts:

- `host_readiness.json`
- `host_jitter_samples.tsv`
- `host_jitter_summary.json`

Decision policy:

- `QUIET`: promotion timing may run.
- `BORDERLINE`: do not run promotion timing; keep the patch prepared.
- `NOISY`: do not run promotion timing; record measurement-quality failure.

Noisy host evidence blocks promotion. It does not reject the patch.

## 6. Candidate Classification

Use candidate rows as accounting, not as strategy.

Common states:

- `accepted`: build pass, correctness pass, paired timing pass, clean verdict,
  and host evidence is acceptable.
- `NOISY_PENDING`: timing direction is unresolved because measurement quality is
  not sufficient.
- `PROMOTION_CANDIDATE_UNDER_NOISY_HOST`: strong positive signal under noisy
  host; requires quiet-window or dedicated-host review.
- `rejected`: correctness fail, build fail, compare fail, or clear performance
  regression.
- `blocked_by_host_weather`: host cannot currently judge promotion.
- `blocked_by_scope`: generated work does not match the current scope decision.
- `blocked_by_semantic_risk`: patch changes behavior shape beyond the allowed
  optimization envelope.

Do not turn `positive_noisy` into an immediate retry loop. It may only re-enter
through a quiet-window gate or a broader stack/stage-level review.

## 7. Failure Classification Comes First

When a run fails, classify the failure before starting another attempt:

- environment failure: runner already active, service not listening, host noisy,
  remote toolchain missing
- evidence failure: missing timing samples, incomplete artifacts, invalid ledger
- correctness failure: build fail, compare fail, lost messages, output mismatch
- semantic risk: patch passes tests but changes a user-dependent or
  factor-dependent behavior shape
- performance failure: clean evidence shows no improvement or a regression

Only performance and correctness failures should count against the patch idea.
Environment and evidence failures should update the ledger and stop or retry
later, not automatically advance to a new candidate.

## 8. Stop Conditions

Stop the bounded run when any of these happens:

- host readiness is not `QUIET` for promotion timing
- correctness gate fails
- artifacts are missing or inconsistent
- generated patch violates the scope decision
- candidate becomes `NOISY_PENDING`
- candidate becomes `PROMOTION_CANDIDATE_UNDER_NOISY_HOST`
- clean `accepted` evidence exists
- max attempts is reached

Stopping is not failure. Stopping preserves the evidence boundary.

## 9. Accepted Is Hard

An optimization is accepted only when all are true:

- patch is scoped and reviewable
- build passes
- correctness passes
- paired timing evidence exists
- verdict is clean accepted under the harness rules
- host readiness is acceptable for promotion
- ledger and artifacts are written

`timing_status=pass` means the timing tool completed. It is not an accepted
optimization.

## 10. Agent Operating Rules

Patch agents should receive the scope decision, not an open-ended instruction to
"optimize".

Good instruction:

```text
Use this runtime profile. Optimize this specific hot path. Only use these patch
shapes. Do not change schema, config, factor set, compare settings, projection,
or per-user payload semantics. Produce one patch. Stop if the safe patch is not
obvious.
```

Bad instruction:

```text
Find more candidates and keep trying.
```

## 11. Psi Notes

Psi promotion decisions require remote Linux evidence. Windows local results are
not performance authority.

Do not run Psi promotion timing when an unrelated high-CPU `PsiTraderRunner` is
active. If one appears during a promotion run, mark the run contaminated and do
not promote from it.

Known unsafe or high-risk patch families must stay blocked unless a new scope
explicitly permits them:

- projection pruning with manual column remapping
- schema or compare-config changes
- factor-specific behavior assumptions hidden inside generic read/write patches
- removing fields that other factors may read outside the current compare
  surface

## 12. TWAP Notes

TWAP long-run work must use runtime stage profiling before more optimization
attempts.

Patch agents must not cache or reuse per-user or per-request push payloads unless
the payload is proven user-independent. Passing a narrow correctness smoke is
not enough to promote such a change.

High-pressure scenario regressions should be rejected, not placed in a neutral
pool.

## 13. Minimal Operator Checklist

Before run:

- profile exists
- scope is written
- host gate is clear or the run is explicitly discovery-only
- stop conditions are written
- ledger path is known

During run:

- verify artifacts are being written
- verify no unrelated runner contaminates timing
- do not reinterpret missing evidence as neutral

After run:

- classify failure or verdict
- update ledger
- keep patch reverted unless accepted and manually promoted
- decide whether the next step is profile, scope change, quiet-window review, or
  stop

The operator's job is not to maximize attempts. The job is to keep every
optimization claim attached to the evidence that can actually support it.
