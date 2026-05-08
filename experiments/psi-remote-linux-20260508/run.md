# Remote Linux Psi Baseline

source=remote-linux
factor=5447 params=1,3,5,10

## no_compare

mode=no_compare
result_file=HFT-wf/experiments/psi-remote-linux-20260508/no_compare/linux_original_baseline_20260508_155348.txt
log_dir=HFT-wf/experiments/psi-remote-linux-20260508/no_compare/linux_original_baseline_20260508_155348
baseline_median=72
measured_runs=71,72,72
non_baseline_runs=cold,warmup

## compare

mode=compare
result_file=HFT-wf/experiments/psi-remote-linux-20260508/compare/linux_compare_20260508_161559.txt
log_dir=HFT-wf/experiments/psi-remote-linux-20260508/compare/linux_compare_20260508_161559
cold=141s
warmup=139s
run1=140s
run2=142s
run3=140s
output_parquet_count=8
compare_status=pass
