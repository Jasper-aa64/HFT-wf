# Optimization Program - Psi Factor Pipeline

## Objective

Improve wall-clock runtime of the Psi factor pipeline without changing output parquet semantics.

Target project:

```text
C:\Users\liangjunming\Desktop\work\Code1\psi-trader-liangjunming
```

Primary metric:

```text
wall-clock milliseconds over warm measured runs, with seconds summaries kept as derived compatibility output
```

Gate policy:

```text
Class A / algorithmic: correctness must pass; bundle drift audit still required; performance is recorded only
Class B / empirical: correctness must pass; Welch p < 0.05 and candidate mean < baseline mean
Bundle / rebaseline: 7-sample median must improve by at least 5 seconds versus original_baseline.txt
```

Control-loop policy:

```text
Current control baseline: recorded in the remote control bundle and mirrored
  locally in the active run artifacts.
Formal control-loop artifacts:
  <run-root>/profile.tsv
  <run-root>/hotspots.tsv
  <run-root>/attempts.tsv
  <run-root>/cooldown.tsv
Recorder: scripts/psi_control_loop.py
Profile runs: DIAGNOSTIC_ONLY, not PASS/FAIL_PERF
Selection policy: three lanes: evidence + insight + combination; not greedy-only hotspot picking
Neutral stacks: allowed for low-risk exploration candidates
```

Profile ranking is the evidence lane, not the whole search strategy. A high
ranked hotspot is a strong prompt for a candidate, but it must not suppress a
well-argued cache/locality hypothesis elsewhere.

Selection lanes:

```text
Evidence lane: profile/hotspots ranked by observed cost, ownership confidence, correctness safety, and locality.
Insight lane: agent-proposed Class A or cache/locality candidates, including small improvements outside the top hotspot when the rationale is strong.
Combination lane: neutral-stack validation for individually neutral, low-risk candidates.
```

Class A does not mean "no testing" or "guaranteed no regression". It means the
diff has no clear performance-regression mechanism, so one noisy timing sample
does not reject it by itself. Correctness, compare output, and bundle drift
audit still apply.

The harness should constrain verification quality, evidence recording, and
rollback discipline. It should not overconstrain candidate generation as model
reasoning improves.

The policy is not "no sub-agent"; it is visible, bounded Codex sub-agents with
the main thread staying conversational. Detached remote work such as
`nohup`, `tmux`, or `screen` is forbidden unless the user explicitly approves
it and the run exposes a visible status artifact. The main thread should not
sit behind a single long blocking wait when a sub-agent is active; use short
status checks, mailbox updates, or user-triggered status queries instead.
If the user asks a question mid-run, answer it directly and continue the batch
tracking afterward.

## Allowed Files

For the first optimization loop, edit only:

```text
PsiFactorPipline/PsiReadWrite.cpp
PsiFactorPipline/PsiReadWrite.h
```

Do not edit:

```text
PsiTraderRunner/config.yaml
tools/compare_parquet_factor.cpp
dataset files
baseline parquet files
tests or evaluator scripts
docs as part of an optimization attempt
```

Do not treat Windows as the performance authority. The remote Linux control
bundle is authoritative for the Psi loop, while HFT-wf remains the local
evidence and planning repo.

## Evaluator

Run from the `HFT-wf` repository:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\psi_evaluate.ps1 `
  -Repo C:\Users\liangjunming\Desktop\work\Code1\psi-trader-liangjunming `
  -WithBuild -WithCompare -Class empirical
```

The evaluator is the judge. Do not argue with the result.

Pass requires:

```text
1. build succeeds
2. runner completes
3. every candidate parquet matches baseline via compare_parquet_factor
4. performance gate matches the selected class
```

Use these commands for the three implemented gates:

```powershell
# Class A: logic-safe refactor, correctness gate required, perf recorded only.
powershell -ExecutionPolicy Bypass -File scripts\psi_evaluate.ps1 `
  -WithBuild -WithCompare -Class algorithmic

# Class B: empirical change, correctness + Welch t-test.
powershell -ExecutionPolicy Bypass -File scripts\psi_evaluate.ps1 `
  -WithBuild -WithCompare -Class empirical

# Bundle verification: branch-level audit before merge.
powershell -ExecutionPolicy Bypass -File scripts\psi_evaluate.ps1 `
  -WithBuild -WithCompare -Rebaseline
```

For Linux remote execution, use the same evaluator under PowerShell 7 with the
remote paths or explicit executable paths:

```powershell
pwsh -File scripts/psi_evaluate.ps1 `
  -Repo /root/work/Code1/psi-trader-liangjunming `
  -BaselineDir /root/work/Code1/dataset/baseline/psi-factor-20140102-20140103 `
  -OutputDir /root/work/Code1/dataset/output `
  -WithBuild -WithCompare -Class empirical
```

If the Linux build layout differs from the default `build/build_x64/...` path,
pass `-RunnerPath`, `-ComparePath`, `-RunnerWorkDir`, or `-BuildCommand`
explicitly. The remote environment must already expose `cmake`, Arrow/Parquet
headers, and a runnable `PsiTraderRunner`; the evaluator does not install them.

## Loop Rules

Before each edit, write one short hypothesis:

```text
Hypothesis: <what cost is being reduced and why>
Target: <function/file>
Expected effect: <wall-clock or allocation/write reduction>
Risk: <what could change semantics>
```

After each edit:

```text
classify the change, then run evaluator with the matching gate
```

On PASS:

```powershell
git add PsiFactorPipline/PsiReadWrite.cpp PsiFactorPipline/PsiReadWrite.h
git commit -m "perf: <short description>"
```

Then append a row to:

```text
experiments/psi_results.tsv
```

On FAIL:

```powershell
git restore PsiFactorPipline/PsiReadWrite.cpp PsiFactorPipline/PsiReadWrite.h
```

Then append the failure reason to:

```text
experiments/psi_results.tsv
```

## Optimization Guidance

Start with local, low-risk changes in `PsiReadWrite.cpp`.

Class A is reserved for changes where the diff itself rules out a plausible
performance regression mechanism:

```text
algorithmic complexity reduction in the same function
removing redundant work with no new hot-path allocation
using an already-computed value instead of recomputing it
```

Class A still requires the correctness gate and later bundle drift audit. Do
not accept a Class A candidate only because it is labeled algorithmic, and do
not reject it only because one daytime sample is noisy.

Class B is the default when there is doubt:

```text
new cache or state
new branch in the hot path
new container or data layout
threading, IO, compression, or allocation strategy changes
semantic bug fixes even when outputs are expected to match
```

Prefer:

```text
avoid repeated time conversion
avoid repeated string allocation
reserve exact sizes
reuse fixed-width buffers
reduce map lookups in hot loops
reduce compare-only work from production output path
```

Cache/locality candidates are valid even when they do not target the top timed
stage if they reduce repeated access, improve data locality, reduce pointer
chasing or allocation, or reuse hot data. Record the affected stage, expected
locality mechanism, and semantic risk before testing.

Avoid:

```text
changing factor formulas
changing output schema
changing parquet key columns
changing date/time formatting
changing row order unless compare and downstream consumers prove it is safe
changing config to make the benchmark easier
```

Known correction:

`PsiReadWrite::compareFile()` is not a sufficient judge because it logs mismatches but does not fail the process. Use `compare_parquet_factor` for the correctness gate.

Control-loop recording:

```text
Record timestamp, samples, mean, median, stddev, and range in attempts.tsv.
Record warm/cold distinction; compare warm measured runs against warm baselines.
Mark runs as noisy when variance or range crosses the configured threshold.
Use cooldown.tsv to hold known targets instead of retrying them greedily.
The default exploration quota should include at least one low-risk neutral stack.
```

Report generation:

```powershell
python scripts\psi_daily_report.py `
  --date <YYYY-MM-DD> `
  --control-loop-dir <run-root> `
  --run-state <run-root>\run_state.json `
  --image <run-root>\charts\runtime_convergence.png `
  --image <run-root>\charts\convergence_decision.png
```

The report script writes performance optimization reports only. The default
location is the selected run artifact's `reports/<date>` directory; a dated
workspace is only used when `--report-root` is passed explicitly. The script
writes only Markdown and PDF to:

```text
<run-root>/reports/<date>/<date> 性能优化报告.md
<run-root>/reports/<date>/<date> 性能优化报告.pdf
```

Do not name the file `report.*`, and do not call the document a daily report in
the title or body. It uses a temporary HTML file only for PDF rendering and
deletes it afterwards.

Timing artifact compatibility:

```text
Headless timing artifacts must preserve millisecond capture for warmup, compare,
and measured no-compare samples. Existing seconds fields and whole-second result
lines remain derived compatibility output for older reports and notes, but
accept/reject evidence should prefer the millisecond fields when present.
```
