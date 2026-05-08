source=remote-linux
mode=readParquet_row_fill_stock_init
compare=false
factor=5447 params=1,3,5,10
baseline_median=72
candidate_median=66
delta_seconds=-6
delta_percent=8.3
output_parquet_count=8

## no_compare

result_file=HFT-wf/experiments/psi-remote-linux-20260508/candidate_bar_template/no_compare/linux_candidate_bar_template_20260508_172100.txt
log_dir=HFT-wf/experiments/psi-remote-linux-20260508/candidate_bar_template/no_compare/linux_candidate_bar_template_20260508_172100
cold=68
warmup=67
run1=66
run2=66
run3=66

## compare

result_file=HFT-wf/experiments/psi-remote-linux-20260508/candidate_bar_template/compare/linux_candidate_bar_template_compare_20260508_172710.txt
log_dir=HFT-wf/experiments/psi-remote-linux-20260508/candidate_bar_template/compare/linux_candidate_bar_template_compare_20260508_172710
compare_rc=0
compare_seconds=132
compare_error_grep_count=0
