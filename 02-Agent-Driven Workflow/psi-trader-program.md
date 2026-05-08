# Optimization Program - Psi Factor Pipeline

## Objective

Improve wall-clock runtime of the Psi factor pipeline without changing output parquet semantics.

Target project:

```text
C:\Users\liangjunming\Desktop\work\Code1\psi-trader-liangjunming
```

Primary metric:

```text
wall-clock seconds over warm measured runs
```

Gate policy:

```text
Class A / algorithmic: correctness must pass; performance is recorded only
Class B / empirical: correctness must pass; Welch p < 0.05 and candidate mean < baseline mean
Bundle / rebaseline: 7-sample median must improve by at least 5 seconds versus original_baseline.txt
```

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
