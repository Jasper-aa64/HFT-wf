# Optimization Harness Guidelines

> Scope: HFT-wf scripts and task drivers that generate, evaluate, classify,
> retry, or promote optimization candidates for low-latency performance work.

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
- include local build directories, generated build products, benchmark outputs,
  datasets, or compare-gate artifacts in candidate patches. Direct-edit agents
  may inspect code, but candidate materialization must reject generated artifacts
  before remote build or timing starts.

#### 3.2 Validation Layer Contract

The validation layer is the only promotion authority.

Automatic accepted requires all of:

- build pass;
- correctness / compare pass;
- clean timing verdict OR accepted_noisy_replicated (multi-run replicated evidence);
- quiet host-weather decision;
- no active blocking runner;
- no mid-run contamination;
- accepted class allowed by policy;
- risk review complete when the patch changes shared hot-path semantics.

accepted_noisy_single (single-run noisy evidence) is NOT automatic accepted --
the patch is reverted and the candidate is queued for validation through
separate replication runs.

The accepted threshold must not be relaxed to make optimization look better.
True optimizations should become easier to accept because the measurement
pipeline gives them a fair clean window, not because noisy results are accepted.

#### 3.3 True Optimization Handling

When a patch is truly useful but measured under a bad host window, the harness
should preserve it instead of rejecting it:

```text
build pass + compare pass + strong positive + noisy host + single run
  -> accepted_noisy_single
  -> patch reverted, candidate queued for validation replication

build pass + compare pass + strong positive + noisy host + replicated evidence
  -> accepted_noisy_replicated
  -> patch applied, shared-host promotion, artifact marked non-bare-metal
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
| median positive but `noise_flag=NOISY`, single run | `accepted_noisy_single` when statistically conclusive | no | queued for validation replication |
| median positive but `noise_flag=NOISY`, replicated evidence | `accepted_noisy_replicated` when statistically conclusive | yes (shared-host, non-bare-metal) | promoted with caveat |
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
- patch materialization rejects build directories and other fixed-boundary
  generated artifacts;
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

### 8. Public Repository Naming

GitHub-visible names must not include company-specific names, internal product
names, or private abbreviations. This applies to new commit subjects, branch
names, tag names, public run/artifact names, documentation titles, exported
reports, and newly introduced filenames that will be pushed to GitHub.

Use neutral domain names instead, for example:

```text
harness, timing, factor, position, aggregation, low-latency, candidate,
scorecard, remote-run, patch-agent
```

Existing legacy code symbols and internal paths do not need a risky rename just
to satisfy this rule. When touching public-facing text or creating new GitHub
visible names, choose the neutral name from the start.

#### Wrong

```text
<company-prefix>_next_candidate_experiment
<company-prefix> patch agent
```

#### Correct

```text
factor candidate experiment
optimization patch agent
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

---

## Design Decision: Two-Class Gate Policy (Class A / Class B)

**Context**: A fixed-threshold perf gate (e.g. `improve >= 5s`) biases heavily
toward false rejects when the noise floor is comparable to the expected gain.
Small real wins become statistically indistinguishable from zero, and the
workflow stalls once individual gains drop below the threshold.

**Decision**: Split candidates into two classes with different gate contracts.

### Class A -- Algorithmic Certainty

**Definition**: The change is a logical refactor with no plausible regression
mechanism.

Criteria (all must be true):
- no new state visible to callers
- no new cache or branching layout that callers depend on
- no new memory allocation pattern in the hot path

Examples:
- inner-scan O(n^2) replaced by O(n) running variable
- removal of redundant computation already done elsewhere
- using a value already in the right type instead of constructing a copy

Counterexample:
- A `thread_local` per-second cache introduces TLS access cost and a per-call
  branch and is NOT Class A.

**Gate for Class A**:
```text
correctness:  required (compare pass on all output files)
perf:         recorded but not gated
verdict:      accepted_class_a when build_pass and compare_pass are both true
```

### Class B -- Empirical Change (default)

**Definition**: Anything not in Class A. New state, new data layout, swapped
containers, new threading, or any change where reasoning alone cannot rule out a
regression.

**Gate for Class B**:
```text
correctness:  required
perf:         bootstrap CI + permutation p-value; accept when candidate
              median < control median and statistical evidence is conclusive
              (p <= 0.05, bootstrap CI lower bound > 0)
```

### Classification Rules

- When in doubt, use Class B.
- Classification mistakes (Class B tagged as Class A) are only caught by the
  bundle check. Be conservative.
- Class A is appropriate for pure removal of unused assignments, proven-
  correct algorithmic replacements (same semantics, lower complexity), and
  mechanical cleanups that eliminate dead stores or dead branches.

### Updated Verdict Matrix

| verdict | meaning | patch status | allowed by | triggers first-accepted-stop |
|---|---|---|---|---|
| `accepted` | Class B candidate with conclusive perf improvement | applied | Class B | yes |
| `accepted_class_a` | Class A candidate with correctness pass only | applied | Class A | yes |
| `accepted_noisy_single` | Statistically conclusive + noisy, single-run evidence only | reverted (queued for validation) | Class B | no |
| `accepted_noisy_replicated` | Statistically conclusive + noisy, replicated evidence across multiple locked independent runs | applied (shared-host promotion, artifact marked non-bare-metal) | Class B | yes |
| `NOISY_PENDING` | inconclusive due to measurement noise | reverted, retry later | -- | -- |
| `neutral` | positive but not credible enough for acceptance | reverted | -- | -- |
| `rejected` | compare fail, perf non-improvement, or TWAP regression | reverted | -- | -- |
| `infra_blocked` | control baseline unhealthy | reverted | -- | -- |

### Three-Tier Acceptance Policy

The harness uses a three-tier policy for noisy evidence:

1. **accepted_clean** -- quiet + statistically conclusive -> normal promotion (patch applied)
2. **accepted_noisy_single** -- single-run noisy but strong signal -> accepted as evidence only, NOT applied, enters validation queue; patch status set to reverted
3. **accepted_noisy_replicated** -- multiple locked independent runs all strong -> shared-host promotion, artifact marked non-bare-metal; patch status set to applied

**Key rule**: single noisy is NOT clean accepted. It is evidence recorded for later validation. Only replicated noisy may be promoted. The `first_accepted_stop` trigger fires on clean, replicated, and class_a verdicts, but NOT on single noisy.

### Bundle Verification

After every N accepted Class A changes, or before merging an experiment branch:
- 7 measured wall-clock samples
- compare to `original_baseline.txt` (snapshotted at branch start)
- branch-level rebaseline audit: flag if the 7-sample median fails the
  original improvement target

This is a branch-level safety net for cumulative drift after accepted Class A
changes. N = 5 is a reasonable starting value.
