# Optimization Harness Guidelines

> Scope: HFT-wf scripts and task drivers that generate, evaluate, classify,
> retry, or promote optimization candidates for Psi/TWAP style performance work.

---

## Scenario: Search Aggressively, Promote Conservatively

### 1. Scope / Trigger

Use this spec whenever code touches:

- candidate generation or patch-agent prompts
- prepared candidate drivers
- remote build / correctness / timing orchestration
- timing verdict replay
- candidate ledger classification
- promotion review, accepted decisions, or baseline updates
- host weather, validation lock, or measurement-quality artifacts

The long-term goal of the optimization harness is to exhaust performance
opportunities as much as practical:

```text
find more real optimizations -> amplify weak signals -> retest fairly -> promote only clean wins
```

This goal does **not** mean accepting more risk. It means separating the system
into two layers:

```text
search layer: aggressive, exploratory, signal-seeking
validation layer: conservative, contamination-aware, promotion-authoritative
```

### 2. Signatures

Candidate and run artifacts should preserve enough structure for later replay:

```text
candidate_id
target
lane
stack_members
build_status
compare_status
paired_sample_count
median_delta_ms
confidence_interval_ms
p_value
noise_flag
timing_verdict
measurement_quality
classification
promotion_review_status
accepted
```

Host/weather artifacts should include:

```text
host_readiness.json
host_jitter_samples.tsv
host_jitter_summary.json
validation_lock_metadata.json
```

Timing summaries should be extendable with measurement-instability fields:

```text
control_cov
control_relative_range
paired_delta_range_ms
paired_relative_range
active_runner_seen
midrun_contamination_seen
```

### 3. Contracts

#### 3.1 Search Layer Contract

The search layer may be aggressive:

- generate small work-reduction candidates;
- keep compare-pass positive-noisy candidates;
- combine semantically compatible candidates into stacks;
- use lower-budget discovery runs before promotion review;
- record directional evidence even when promotion is blocked.

The search layer must not:

- mark a candidate accepted;
- update baseline;
- treat noisy positive evidence as clean proof;
- retry positive-noisy candidates immediately unless the ledger explicitly
  says the candidate is a quiet-window retry.

#### 3.2 Validation Layer Contract

The validation layer is the only promotion authority.

Automatic accepted requires all of:

- build pass;
- correctness / compare pass;
- clean timing verdict;
- quiet host-weather decision;
- no active blocking runner;
- no mid-run contamination;
- accepted class allowed by policy;
- risk review complete when the patch changes shared hot-path semantics.

The accepted threshold must not be relaxed to make optimization look better.
True optimizations should become easier to accept because the measurement
pipeline gives them a fair clean window, not because noisy results are accepted.

#### 3.3 True Optimization Handling

When a patch is truly useful but measured under a bad host window, the harness
should preserve it instead of rejecting it:

```text
build pass + compare pass + strong positive + noisy host
  -> PROMOTION_CANDIDATE_UNDER_NOISY_HOST
  -> quiet-window / locked promotion review
```

When the measurement itself is too unstable, classify the run as measurement
quality failure, not patch failure:

```text
high relative range OR mid-run contamination
  -> UNSTABLE_MEASUREMENT
  -> do not train future candidate generation from this run
  -> do not accept
```

### 4. Validation & Error Matrix

| Condition | Classification | Accepted | Retry Meaning |
|---|---|---:|---|
| build fails | `build_fail` | no | fix patch or abandon |
| compare fails | `compare_fail` | no | semantic risk, do not timing-retry |
| host audit not quiet before promotion | `blocked_by_host_weather` | no | wait for clean host |
| validation lock held by another run | `infra_blocked_by_validation_lock` | no | do not start timing |
| active runner appears mid-run | `UNSTABLE_MEASUREMENT` / invalid run | no | rerun only under lock |
| median positive but `noise_flag=NOISY` | `PROMOTION_CANDIDATE_UNDER_NOISY_HOST` when strong enough | no | quiet-window promotion review |
| paired/control relative range too high | `UNSTABLE_MEASUREMENT` | no | measurement pipeline issue |
| clean positive timing with all gates pass | `accepted_candidate` | yes | manual risk review if required |

### 5. Good / Base / Bad Cases

Good:

```text
micro patches pass compare -> several are positive-noisy -> compatible patches
are stacked -> stack gets a larger signal -> locked clean m24 confirms -> manual
risk review -> accepted.
```

Base:

```text
micro patch pass compare -> timing noisy -> candidate is retained as
positive-noisy or promotion candidate -> no immediate retry.
```

Bad:

```text
micro patch pass compare -> one noisy positive timing sample -> accepted.
```

Bad:

```text
strong stack starts m24 -> unrelated runner appears mid-run -> timing completes
anyway -> accepted.
```

### 6. Tests Required

When changing harness code in this area, add or update tests that assert:

- noisy positive evidence is not accepted;
- positive-noisy candidates are retained for quiet-window review;
- retry-only candidates cannot be regenerated as ordinary candidates;
- validation lock blocks a second performance timing run before timing starts;
- a run cannot release another run's lock;
- active runner / mid-run contamination is recorded as measurement-quality
  failure, not patch failure;
- verdict replay does not let `timing_status=pass` override rejected or noisy
  candidate decisions;
- stack candidates preserve `stack_members` in manifests and ledgers.

### 7. Wrong vs Correct

#### Wrong

```text
Goal: squeeze performance.
Action: lower the accepted threshold or ignore noisy host flags so more patches
can be promoted.
```

#### Correct

```text
Goal: squeeze performance.
Action: search aggressively, preserve strong noisy signals, combine compatible
work-reduction patches, run promotion only under clean locked measurement, and
accept only clean wins.
```

#### Wrong

```text
NOISY_PENDING means the candidate failed.
```

#### Correct

```text
NOISY_PENDING means the current measurement cannot authorize promotion. The
candidate may still be valuable and should be classified according to signal
strength, semantic risk, and retry policy.
```

---

## Design Decision: Optimization Exhaustion Without Promotion Drift

**Context**: The harness should help find as much real performance as possible,
but performance timing on shared machines is noisy and can produce false wins.

**Decision**: Use an aggressive search layer and a conservative validation
layer. This keeps the system capable of discovering small and combined
optimizations while preventing noisy measurements from updating baseline.

**Why**:

- real optimizations get more fair chances to reach accepted;
- noisy fake wins become harder to promote;
- the ledger can distinguish patch quality from measurement quality;
- paper evidence remains defensible because every claim maps to artifacts.

**Related references**:

- TUNA: unstable/noisy measurements should be handled in the measurement
  pipeline, not accepted blindly or solved only by adding samples.
- Performance Roulette: noisy system measurements can corrupt automatic
  optimization decisions.
