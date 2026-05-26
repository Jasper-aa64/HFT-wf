# 参考文献

## Performance Roulette: How Cloud Weather Affects ML-Based System Optimization

- File: `Performance Roulette - How Cloud Weather Affects ML-Based System Optimization.pdf`
- Source: https://openreview.net/pdf?id=5Pr2AVPk6z
- Topic: cloud-weather variability, measurement noise, transferability, and sample cost in ML-based system optimization.
- Relevance: supports the TWAP/Psi optimization-harness discussion that noisy measurements should not be handled only by increasing sample count; the harness needs environment awareness, stronger experiment units, and separate promotion-review states.

## TUNA: Tuning Unstable and Noisy Cloud Applications

- File: `TUNA - Tuning Unstable and Noisy Cloud Applications.pdf`
- Paper: https://www.microsoft.com/en-us/research/uploads/prod/2025/02/TUNA.pdf
- Project page: https://www.microsoft.com/en-us/research/publication/tuna-tuning-unstable-and-noisy-cloud-applications/
- Source: https://github.com/uw-mad-dash/TUNA
- Local source clone: `C:\psi_lr\paper_tuna_repo_20260526`
- Topic: unstable/noisy cloud autotuning, multi-fidelity sampling, outlier detection, noise-adjusted performance signals, and conservative aggregation.
- Relevance: this is the closest reference for the Psi/TWAP optimization harness. It supports separating optimization discovery from promotion authority, keeping strong positive noisy candidates as promotion candidates instead of auto-accepting them, and adding measurement-instability features such as relative range / host metrics to timing artifacts.

Lightweight source check performed on 2026-05-26:

- cloned repository HEAD: `083ea35`
- `py_compile` passed for core entry/manager files:
  - `src/TUNA.py`
  - `src/client/AdjustedDistributedWorkerManager.py`
  - `src/client/NoClusterModel.py`
  - `src/parallel_prior.py`
- sample dataset contains 170 CSV files under `sample_configs`
- quick CloudLab PostgreSQL/TPC-C sample read:
  - traditional median best observed throughput: `4989.405`
  - TUNA median best observed throughput: `5414.999`

Boundary: full reproduction is a distributed experiment, not a local smoke. The README recommends one orchestrator plus ten workers, and the published figure-reproduction commands can run for hours and require cloud or CloudLab infrastructure.
