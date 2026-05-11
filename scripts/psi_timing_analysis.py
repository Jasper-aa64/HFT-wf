#!/usr/bin/env python3
"""Paired timing analysis helpers for Psi headless optimization runs."""

from __future__ import annotations

import hashlib
import random
import statistics
from dataclasses import dataclass


DEFAULT_PROMOTION_SAMPLE_FLOOR = 5
DEFAULT_BUNDLE_AUDIT_SAMPLE_FLOOR = 7
# Deprecated absolute-ms fallback thresholds; retained for back-compat with
# callers that cannot yet pass a control-median scale. Prefer the relative
# ratios (DEFAULT_NOISE_RANGE_RATIO, DEFAULT_NOISE_STDEV_RATIO) below.
DEFAULT_NOISE_RANGE_THRESHOLD_MS = 5.0
DEFAULT_NOISE_STDEV_THRESHOLD_MS = 1.5
# Relative noise thresholds, expressed as fractions of the control median.
DEFAULT_NOISE_RANGE_RATIO = 0.02
DEFAULT_NOISE_STDEV_RATIO = 0.005
DEFAULT_NOISE_CV_THRESHOLD = 0.02
DEFAULT_BOOTSTRAP_RESAMPLES = 2000
DEFAULT_PERMUTATION_RESAMPLES = 2000
DEFAULT_CONFIDENCE = 0.95
VERDICT_METHOD = "paired_bootstrap_permutation_v1"


@dataclass(frozen=True)
class PairedTimingEvidence:
    control_samples_ms: list[float]
    candidate_samples_ms: list[float]
    paired_deltas_ms: list[float]
    verdict: str
    reason: str
    noise_flag: str
    required_pairs: int
    paired_sample_count: int
    control_sample_count: int
    candidate_sample_count: int
    control_median_ms: float | None
    control_median_seconds: float | None
    median_delta_ms: float | None
    bootstrap_ci_low_ms: float | None
    bootstrap_ci_high_ms: float | None
    permutation_p_value: float | None
    paired_stdev_ms: float | None
    paired_range_ms: float | None
    paired_mean_ms: float | None
    verdict_method: str = VERDICT_METHOD


def _seed_from_parts(*parts: object) -> int:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(str(part).encode("utf-8"))
        digest.update(b"\0")
    return int.from_bytes(digest.digest()[:8], "big", signed=False)


def _csv(values: list[float]) -> str:
    return ",".join(f"{value:g}" for value in values)


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    position = (len(sorted_values) - 1) * (pct / 100.0)
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    fraction = position - lower
    return sorted_values[lower] * (1.0 - fraction) + sorted_values[upper] * fraction


def paired_samples(control_samples_ms: list[float], candidate_samples_ms: list[float]) -> list[dict[str, float]]:
    paired = []
    for index, (control_ms, candidate_ms) in enumerate(
        zip(control_samples_ms, candidate_samples_ms, strict=False),
        start=1,
    ):
        paired.append(
            {
                "pair_index": float(index),
                "control_ms": float(control_ms),
                "candidate_ms": float(candidate_ms),
                "delta_ms": float(control_ms) - float(candidate_ms),
            }
        )
    return paired


def bootstrap_interval(
    values_ms: list[float],
    *,
    confidence: float = DEFAULT_CONFIDENCE,
    resamples: int = DEFAULT_BOOTSTRAP_RESAMPLES,
    seed_parts: tuple[object, ...] = (),
) -> tuple[float | None, float | None]:
    if not values_ms:
        return None, None
    if len(values_ms) == 1:
        value = float(values_ms[0])
        return value, value
    rng = random.Random(_seed_from_parts("bootstrap", confidence, resamples, *seed_parts))
    ordered: list[float] = []
    for _ in range(max(1, resamples)):
        sample = [values_ms[rng.randrange(len(values_ms))] for _ in values_ms]
        ordered.append(float(statistics.median(sample)))
    ordered.sort()
    alpha = (1.0 - confidence) / 2.0
    return _percentile(ordered, alpha * 100.0), _percentile(ordered, (1.0 - alpha) * 100.0)


def permutation_p_value(
    values_ms: list[float],
    *,
    observed_statistic: float,
    resamples: int = DEFAULT_PERMUTATION_RESAMPLES,
    seed_parts: tuple[object, ...] = (),
) -> float | None:
    if not values_ms:
        return None
    if len(values_ms) == 1:
        return 1.0 if observed_statistic <= 0.0 else 0.5
    rng = random.Random(_seed_from_parts("permutation", observed_statistic, resamples, *seed_parts))
    extreme = 0
    total = max(1, resamples)
    for _ in range(total):
        flipped = [value if rng.getrandbits(1) else -value for value in values_ms]
        statistic_value = float(statistics.median(flipped))
        if statistic_value >= observed_statistic:
            extreme += 1
    return (extreme + 1.0) / (total + 1.0)


def noise_flag_from_deltas(
    paired_deltas_ms: list[float],
    *,
    range_threshold_ms: float = DEFAULT_NOISE_RANGE_THRESHOLD_MS,
    stdev_threshold_ms: float = DEFAULT_NOISE_STDEV_THRESHOLD_MS,
    cv_threshold: float = DEFAULT_NOISE_CV_THRESHOLD,
) -> str:
    """Deprecated: absolute-ms noise check retained as a fallback.

    Prefer :func:`noise_flag_from_paired` which scales the range/stdev
    thresholds by the control median so they stay meaningful across
    sub-second and multi-second workloads.
    """
    if len(paired_deltas_ms) < 2:
        return "ok"
    ordered = [float(value) for value in paired_deltas_ms]
    stdev_ms = statistics.stdev(ordered)
    range_ms = max(ordered) - min(ordered)
    mean_abs_ms = statistics.mean(abs(value) for value in ordered)
    cv = stdev_ms / mean_abs_ms if mean_abs_ms else 0.0
    if range_ms >= range_threshold_ms or stdev_ms >= stdev_threshold_ms or cv >= cv_threshold:
        return "NOISY"
    return "ok"


def noise_flag_from_paired(
    paired_deltas_ms: list[float],
    control_median_ms: float,
    *,
    range_ratio: float = DEFAULT_NOISE_RANGE_RATIO,
    stdev_ratio: float = DEFAULT_NOISE_STDEV_RATIO,
    cv_threshold: float = DEFAULT_NOISE_CV_THRESHOLD,
) -> str:
    """Relative-ratio noise check. Flags NOISY when paired delta range or
    stdev exceeds a configured fraction of the control median, or when the
    coefficient of variation of the deltas is too large.

    Guards ``control_median_ms <= 0`` by falling back to an absolute-ms
    check using the default deprecated thresholds.
    """
    if len(paired_deltas_ms) < 2:
        return "ok"
    ordered = [float(value) for value in paired_deltas_ms]
    stdev_ms = statistics.stdev(ordered)
    range_ms = max(ordered) - min(ordered)
    mean_abs_ms = statistics.mean(abs(value) for value in ordered)
    cv = stdev_ms / mean_abs_ms if mean_abs_ms else 0.0
    if control_median_ms is None or control_median_ms <= 0.0:
        return noise_flag_from_deltas(ordered, cv_threshold=cv_threshold)
    if (
        range_ms / control_median_ms >= range_ratio
        or stdev_ms / control_median_ms >= stdev_ratio
        or cv >= cv_threshold
    ):
        return "NOISY"
    return "ok"


def format_samples_ms(values_ms: list[float]) -> str:
    return _csv([float(value) for value in values_ms])


def _format_optional_ms(value: float | None) -> str:
    return "" if value is None else f"{value:.3f}"


def summarize_paired_timing(
    control_samples_ms: list[float],
    candidate_samples_ms: list[float],
    *,
    build_pass: bool = True,
    compare_pass: bool = True,
    required_pairs: int = DEFAULT_PROMOTION_SAMPLE_FLOOR,
    bootstrap_resamples: int = DEFAULT_BOOTSTRAP_RESAMPLES,
    permutation_resamples: int = DEFAULT_PERMUTATION_RESAMPLES,
    confidence: float = DEFAULT_CONFIDENCE,
    sample_floor_for_bundle_audit: int = DEFAULT_BUNDLE_AUDIT_SAMPLE_FLOOR,
    noise_range_threshold_ms: float = DEFAULT_NOISE_RANGE_THRESHOLD_MS,
    noise_stdev_threshold_ms: float = DEFAULT_NOISE_STDEV_THRESHOLD_MS,
    noise_cv_threshold: float = DEFAULT_NOISE_CV_THRESHOLD,
    noise_range_ratio: float = DEFAULT_NOISE_RANGE_RATIO,
    noise_stdev_ratio: float = DEFAULT_NOISE_STDEV_RATIO,
    verdict_context: str = "",
) -> PairedTimingEvidence:
    control = [float(value) for value in control_samples_ms]
    candidate = [float(value) for value in candidate_samples_ms]
    paired = paired_samples(control, candidate)
    deltas_ms = [row["delta_ms"] for row in paired]
    pair_count = len(paired)
    median_delta_ms = float(statistics.median(deltas_ms)) if deltas_ms else None
    paired_mean_ms = float(statistics.mean(deltas_ms)) if deltas_ms else None
    paired_stdev_ms = float(statistics.stdev(deltas_ms)) if len(deltas_ms) > 1 else None
    paired_range_ms = float(max(deltas_ms) - min(deltas_ms)) if len(deltas_ms) > 1 else (0.0 if deltas_ms else None)
    control_median_ms = float(statistics.median(control)) if control else 0.0
    control_median_seconds = control_median_ms / 1000.0 if control else None
    if control_median_ms > 0.0:
        noise_flag = noise_flag_from_paired(
            deltas_ms,
            control_median_ms,
            range_ratio=noise_range_ratio,
            stdev_ratio=noise_stdev_ratio,
            cv_threshold=noise_cv_threshold,
        )
    else:
        noise_flag = noise_flag_from_deltas(
            deltas_ms,
            range_threshold_ms=noise_range_threshold_ms,
            stdev_threshold_ms=noise_stdev_threshold_ms,
            cv_threshold=noise_cv_threshold,
        )
    bootstrap_low_ms, bootstrap_high_ms = bootstrap_interval(
        deltas_ms,
        confidence=confidence,
        resamples=bootstrap_resamples,
        seed_parts=(control, candidate, verdict_context, required_pairs, sample_floor_for_bundle_audit),
    )
    p_value = permutation_p_value(
        deltas_ms,
        observed_statistic=median_delta_ms or 0.0,
        resamples=permutation_resamples,
        seed_parts=(control, candidate, verdict_context, required_pairs, sample_floor_for_bundle_audit),
    )

    if not build_pass:
        verdict = "rejected"
        reason = "build failed before paired timing evidence could be trusted."
    elif not compare_pass:
        verdict = "rejected"
        reason = "compare failed; the paired timing evidence is invalid."
    elif noise_flag == "NOISY":
        verdict = "NOISY_PENDING"
        reason = (
            f"paired jitter is noisy against control median {control_median_ms:.3f}ms; "
            f"range={_format_optional_ms(paired_range_ms)}ms, stdev={_format_optional_ms(paired_stdev_ms)}ms."
        )
    elif pair_count < required_pairs:
        verdict = "neutral"
        reason = f"screening only; collected {pair_count} paired samples, need at least {required_pairs}."
    elif median_delta_ms is None or median_delta_ms <= 0.0:
        verdict = "rejected"
        reason = (
            "paired median delta is not positive; candidate is not faster under the interleaved A/B samples."
        )
    elif (
        bootstrap_low_ms is not None
        and bootstrap_low_ms > 0.0
        and p_value is not None
        and p_value <= 0.05
    ):
        verdict = "accepted"
        reason = (
            f"paired median delta={median_delta_ms:.3f}ms against control median {control_median_ms:.3f}ms "
            f"with bootstrap CI [{bootstrap_low_ms:.3f}, {bootstrap_high_ms:.3f}]ms and permutation p={p_value:.6f}."
        )
    else:
        verdict = "neutral"
        reason = (
            f"paired median delta={median_delta_ms:.3f}ms is positive but not yet credible enough for acceptance; "
            f"bootstrap CI [{_format_optional_ms(bootstrap_low_ms)}, {_format_optional_ms(bootstrap_high_ms)}]ms, "
            f"p={p_value:.6f}."
        )

    return PairedTimingEvidence(
        control_samples_ms=control,
        candidate_samples_ms=candidate,
        paired_deltas_ms=deltas_ms,
        verdict=verdict,
        reason=reason,
        noise_flag=noise_flag,
        required_pairs=required_pairs,
        paired_sample_count=pair_count,
        control_sample_count=len(control),
        candidate_sample_count=len(candidate),
        control_median_ms=control_median_ms if control else None,
        control_median_seconds=control_median_seconds,
        median_delta_ms=median_delta_ms,
        bootstrap_ci_low_ms=bootstrap_low_ms,
        bootstrap_ci_high_ms=bootstrap_high_ms,
        permutation_p_value=p_value,
        paired_stdev_ms=paired_stdev_ms,
        paired_range_ms=paired_range_ms,
        paired_mean_ms=paired_mean_ms,
    )


def evidence_fields(evidence: PairedTimingEvidence) -> dict[str, str]:
    return {
        "timing_verdict": evidence.verdict,
        "timing_verdict_reason": evidence.reason,
        "timing_verdict_method": evidence.verdict_method,
        "control_sample_count": str(evidence.control_sample_count),
        "candidate_sample_count": str(evidence.candidate_sample_count),
        "paired_sample_count": str(evidence.paired_sample_count),
        "control_median_ms": _format_optional_ms(evidence.control_median_ms),
        "control_median_seconds": _format_optional_ms(evidence.control_median_seconds),
        "control_samples_ms": format_samples_ms(evidence.control_samples_ms),
        "candidate_samples_ms": format_samples_ms(evidence.candidate_samples_ms),
        "paired_deltas_ms": format_samples_ms(evidence.paired_deltas_ms),
        "paired_deltas_seconds": format_samples_ms([value / 1000.0 for value in evidence.paired_deltas_ms]),
        "median_delta_ms": _format_optional_ms(evidence.median_delta_ms),
        "median_delta_seconds": _format_optional_ms(None if evidence.median_delta_ms is None else evidence.median_delta_ms / 1000.0),
        "bootstrap_ci_low_ms": _format_optional_ms(evidence.bootstrap_ci_low_ms),
        "bootstrap_ci_high_ms": _format_optional_ms(evidence.bootstrap_ci_high_ms),
        "bootstrap_ci_low_seconds": _format_optional_ms(
            None if evidence.bootstrap_ci_low_ms is None else evidence.bootstrap_ci_low_ms / 1000.0
        ),
        "bootstrap_ci_high_seconds": _format_optional_ms(
            None if evidence.bootstrap_ci_high_ms is None else evidence.bootstrap_ci_high_ms / 1000.0
        ),
        "permutation_p_value": "" if evidence.permutation_p_value is None else f"{evidence.permutation_p_value:.6f}",
        "paired_stdev_ms": _format_optional_ms(evidence.paired_stdev_ms),
        "paired_range_ms": _format_optional_ms(evidence.paired_range_ms),
        "paired_mean_ms": _format_optional_ms(evidence.paired_mean_ms),
        "noise_flag": evidence.noise_flag,
    }
