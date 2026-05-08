# Psi Evaluator Run 20260507-165802

## Command

```powershell
.\psi_evaluate.ps1 -WithBuild -WithCompare -MeasureRuns 3 -WarmupRuns 1 -BaselineFile "baseline_opt2.txt"
```

## Configuration

- Repo: C:\Users\liangjunming\Desktop\work\Code1\psi-trader-liangjunming
- BaselineDir: C:\Users\liangjunming\Desktop\work\Code1\dataset\baseline\psi-factor-20140102-20140103
- OutputDir: C:\Users\liangjunming\Desktop\work\Code1\dataset\output
- BaselineFile: C:\Users\liangjunming\Desktop\work\Code1\psi-trader-liangjunming\baseline_opt2.txt
- WithBuild: True
- WithCompare: True
- WarmupRuns: 1
- MeasureRuns: 3
- MinImproveSeconds: 5

## Timing

- Run 1: 102.021 s
- Run 2: 110.018 s
- Run 3: 105.99 s
- Median: 105.99 s
- Baseline median: 103.225 s
- Improve seconds: -2.765

## Correctness

- S1020303032014010220140102_0_5447.parquet: exit=0, log=C:\Users\liangjunming\Desktop\work\Code1\psi-trader-liangjunming\compare_logs\psi_evaluate\S1020303032014010220140102_0_5447.compare.log
- S1020303032014010220140102_0_5448.parquet: exit=0, log=C:\Users\liangjunming\Desktop\work\Code1\psi-trader-liangjunming\compare_logs\psi_evaluate\S1020303032014010220140102_0_5448.compare.log
- S1020303032014010220140102_0_5449.parquet: exit=0, log=C:\Users\liangjunming\Desktop\work\Code1\psi-trader-liangjunming\compare_logs\psi_evaluate\S1020303032014010220140102_0_5449.compare.log
- S1020303032014010220140102_0_5450.parquet: exit=0, log=C:\Users\liangjunming\Desktop\work\Code1\psi-trader-liangjunming\compare_logs\psi_evaluate\S1020303032014010220140102_0_5450.compare.log
- S1020303032014010320140103_0_5447.parquet: exit=0, log=C:\Users\liangjunming\Desktop\work\Code1\psi-trader-liangjunming\compare_logs\psi_evaluate\S1020303032014010320140103_0_5447.compare.log
- S1020303032014010320140103_0_5448.parquet: exit=0, log=C:\Users\liangjunming\Desktop\work\Code1\psi-trader-liangjunming\compare_logs\psi_evaluate\S1020303032014010320140103_0_5448.compare.log
- S1020303032014010320140103_0_5449.parquet: exit=0, log=C:\Users\liangjunming\Desktop\work\Code1\psi-trader-liangjunming\compare_logs\psi_evaluate\S1020303032014010320140103_0_5449.compare.log
- S1020303032014010320140103_0_5450.parquet: exit=0, log=C:\Users\liangjunming\Desktop\work\Code1\psi-trader-liangjunming\compare_logs\psi_evaluate\S1020303032014010320140103_0_5450.compare.log
- Correctness: PASS

## Verdict

FAIL_PERF
