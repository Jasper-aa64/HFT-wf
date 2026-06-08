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
# SNR-native confidence tier constants (B redesign, Option A / CI-native, locked 2026-06-02).
# decisive_k: CI-widths the margin must clear above delta_min_ms.
# sign_min: minimum fraction of paired deltas sharing the sign of the median delta.
DEFAULT_DECISIVE_K = 1.0
DEFAULT_SIGN_MIN = 0.9


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
    # Confidence tier fields (B redesign, 2026-06-02). Added with defaults so
    # existing callers that construct PairedTimingEvidence directly are unaffected.
    confidence_tier_name: str | None = None
    confidence_margin_ms: float | None = None
    confidence_ci_width_ms: float | None = None
    confidence_decisiveness: float | None = None
    confidence_sign_consistency: float | None = None


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


@dataclass(frozen=True)
class ConfidenceTierResult:
    """Result of the SNR-native confidence tier assessment (B redesign, 2026-06-02).

    tier: "decisive" | "marginal" | "weak"
    margin: bootstrap_ci_low_ms - delta_min_ms  (positive = CI floor clears the worthwhile line)
    ci_width: bootstrap_ci_high_ms - bootstrap_ci_low_ms  (spread already absorbed by the CI)
    decisiveness: margin / ci_width  (CI-widths of clearance; None when ci_width == 0)
    sign_consistency: fraction of paired_deltas sharing the sign of median_delta
    """

    tier: str
    margin: float | None
    ci_width: float | None
    decisiveness: float | None
    sign_consistency: float | None


def confidence_tier(
    bootstrap_ci_low_ms: float | None,
    bootstrap_ci_high_ms: float | None,
    median_delta_ms: float | None,
    paired_deltas_ms: list[float],
    permutation_p_value_arg: float | None,
    *,
    delta_min_ms: float = 0.0,
    decisive_k: float = DEFAULT_DECISIVE_K,
    sign_min: float = DEFAULT_SIGN_MIN,
) -> ConfidenceTierResult:
    """SNR-native confidence tier — Option A / CI-native, locked 2026-06-02.

    Replaces the ``noise_flag`` gate in the verdict ladder.  The CI already
    absorbed the paired-delta spread; requiring ``noise_flag != NOISY`` on top
    double-taxes the same spread.  This function asks instead: *how many
    CI-widths does the CI floor sit above the minimum-worthwhile delta?*

    Tier rules (Option A — CI-native; permutation p gates only the marginal tier):
    - **decisive**: margin > 0  AND  decisiveness >= decisive_k  AND
                    sign_consistency >= sign_min.
                    Accepts without replication even on a noisy host.
    - **marginal**: margin > 0  AND  permutation_p <= 0.05  AND  not decisive.
                    Requires replication for the upgraded verdict.
    - **weak**: otherwise (margin <= 0, or p > 0.05, or sign mixed).

    delta_min_ms: minimum worthwhile effect — human-frozen contract value;
      defaults to 0.0 for backward compatibility.  Mark "→ contract field when
      A lands" when wiring into the unified scorecard.
    """
    if bootstrap_ci_low_ms is None or bootstrap_ci_high_ms is None:
        return ConfidenceTierResult(
            tier="weak",
            margin=None,
            ci_width=None,
            decisiveness=None,
            sign_consistency=None,
        )

    margin = bootstrap_ci_low_ms - delta_min_ms
    ci_width = bootstrap_ci_high_ms - bootstrap_ci_low_ms

    # decisiveness = margin / ci_width.  When ci_width == 0 the CI is a point
    # estimate; store None to signal "degenerate" but treat as >= decisive_k
    # (zero uncertainty with positive margin is maximally decisive).
    if ci_width > 0.0:
        decisiveness: float | None = margin / ci_width
    elif margin > 0.0:
        decisiveness = None  # zero-width CI, positive margin → effectively ∞
    else:
        decisiveness = 0.0

    # Sign consistency: fraction of deltas sharing the sign of median_delta.
    if paired_deltas_ms and median_delta_ms is not None and median_delta_ms != 0.0:
        positive_median = median_delta_ms > 0.0
        matching = sum(1 for d in paired_deltas_ms if (d > 0.0) == positive_median)
        sign_consistency: float | None = matching / len(paired_deltas_ms)
    elif paired_deltas_ms:
        sign_consistency = 1.0
    else:
        sign_consistency = None

    # Tier assignment.
    decisiveness_ok = decisiveness is None or decisiveness >= decisive_k
    sign_ok = sign_consistency is not None and sign_consistency >= sign_min

    if margin > 0.0 and decisiveness_ok and sign_ok:
        tier = "decisive"
    elif (
        margin > 0.0
        and permutation_p_value_arg is not None
        and permutation_p_value_arg <= 0.05
    ):
        tier = "marginal"
    else:
        tier = "weak"

    return ConfidenceTierResult(
        tier=tier,
        margin=margin,
        ci_width=ci_width,
        decisiveness=decisiveness,
        sign_consistency=sign_consistency,
    )


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
    change_class: str = "class_b",
    replicated: bool = False,
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
    # B redesign parameters (2026-06-02).  delta_min_ms defaults to None,
    # meaning "compute as 0.5 % of control median" — mark as "→ contract field
    # when A (unified scorecard) lands".
    delta_min_ms: float | None = None,
    decisive_k: float = DEFAULT_DECISIVE_K,
    sign_min: float = DEFAULT_SIGN_MIN,
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
    clear_non_improvement = (
        pair_count >= required_pairs
        and median_delta_ms is not None
        and median_delta_ms <= 0.0
        and bootstrap_high_ms is not None
        and bootstrap_high_ms <= 0.0
    )

    # SNR-native confidence tier (B redesign, Option A / CI-native, 2026-06-02).
    # delta_min_ms defaults to 0.5 % of control_median when not supplied.
    # This will become a human-frozen contract field when the unified scorecard (A) lands.
    _delta_min = (
        delta_min_ms
        if delta_min_ms is not None
        else (control_median_ms * 0.005 if control_median_ms > 0.0 else 0.0)
    )
    ct = confidence_tier(
        bootstrap_low_ms,
        bootstrap_high_ms,
        median_delta_ms,
        deltas_ms,
        p_value,
        delta_min_ms=_delta_min,
        decisive_k=decisive_k,
        sign_min=sign_min,
    )

    if not build_pass:
        verdict = "rejected"
        reason = "build failed before paired timing evidence could be trusted."
    elif not compare_pass:
        verdict = "rejected"
        reason = "compare failed; the paired timing evidence is invalid."
    elif change_class == "class_a" and build_pass and compare_pass:
        verdict = "accepted_class_a"
        reason = "Class A algorithmic change: correctness pass is sufficient; perf recorded but not gated."
    elif clear_non_improvement:
        verdict = "rejected"
        reason = (
            f"paired evidence shows non-improvement; median delta={median_delta_ms:.3f}ms against "
            f"control median {control_median_ms:.3f}ms with bootstrap CI "
            f"[{_format_optional_ms(bootstrap_low_ms)}, {_format_optional_ms(bootstrap_high_ms)}]ms "
            f"and permutation p={p_value:.6f}."
        )
    elif pair_count < required_pairs:
        verdict = "neutral"
        reason = f"screening only; collected {pair_count} paired samples, need at least {required_pairs}."
    elif median_delta_ms is None or median_delta_ms <= 0.0:
        verdict = "rejected"
        reason = (
            "paired median delta is not positive; candidate is not faster under the interleaved A/B samples."
        )
    elif ct.tier == "decisive":
        # Decisive: CI margin clears delta_min by >= decisive_k widths AND sign-consistent.
        # noise_flag is retained as a diagnostic field but no longer gates acceptance here.
        _decisiveness_str = (
            f"{ct.decisiveness:.3f}" if ct.decisiveness is not None else "∞"
        )
        verdict = "accepted"
        reason = (
            f"paired median delta={median_delta_ms:.3f}ms against control median {control_median_ms:.3f}ms "
            f"with bootstrap CI [{_format_optional_ms(bootstrap_low_ms)}, {_format_optional_ms(bootstrap_high_ms)}]ms "
            f"(CI-native decisive: margin={_format_optional_ms(ct.margin)}ms, "
            f"decisiveness={_decisiveness_str}, "
            f"sign_consistency={ct.sign_consistency:.2f}); "
            f"permutation p={_format_optional_ms(p_value) if p_value is not None else 'n/a'} (reported, not gating)."
        )
    elif ct.tier == "marginal":
        if replicated:
            verdict = "accepted_noisy_replicated"
            reason = (
                f"paired median delta={median_delta_ms:.3f}ms against control median {control_median_ms:.3f}ms "
                f"is statistically conclusive (bootstrap CI [{_format_optional_ms(bootstrap_low_ms)}, "
                f"{_format_optional_ms(bootstrap_high_ms)}]ms, permutation p={p_value:.6f}) "
                f"with replicated evidence across multiple locked independent runs; "
                f"measurement environment was noisy (range={_format_optional_ms(paired_range_ms)}ms, "
                f"stdev={_format_optional_ms(paired_stdev_ms)}ms) but replication supports shared-host promotion; "
                f"artifact marked non-bare-metal."
            )
        else:
            _marginal_dec = f"{ct.decisiveness:.3f}" if ct.decisiveness is not None else "0.000"
            _marginal_sign = f"{ct.sign_consistency:.2f}" if ct.sign_consistency is not None else "0.00"
            verdict = "accepted_noisy_single"
            reason = (
                f"paired median delta={median_delta_ms:.3f}ms against control median {control_median_ms:.3f}ms "
                f"is statistically conclusive (bootstrap CI [{_format_optional_ms(bootstrap_low_ms)}, "
                f"{_format_optional_ms(bootstrap_high_ms)}]ms, permutation p={p_value:.6f}) "
                f"but confidence tier is marginal (decisiveness={_marginal_dec} "
                f"< {decisive_k}, sign_consistency={_marginal_sign}); "
                f"accepted as evidence only — NOT applied; queued for validation replication."
            )
    else:
        # weak tier — fall back to noise_flag to distinguish NOISY_PENDING from neutral.
        if noise_flag == "NOISY":
            verdict = "NOISY_PENDING"
            reason = (
                f"paired jitter is noisy against control median {control_median_ms:.3f}ms "
                f"and the statistical evidence is not yet conclusive "
                f"(confidence tier: weak, margin={_format_optional_ms(ct.margin)}ms); "
                f"range={_format_optional_ms(paired_range_ms)}ms, stdev={_format_optional_ms(paired_stdev_ms)}ms."
            )
        else:
            verdict = "neutral"
            reason = (
                f"paired median delta={median_delta_ms:.3f}ms is positive but not yet credible enough for acceptance "
                f"(confidence tier: weak, margin={_format_optional_ms(ct.margin)}ms); "
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
        confidence_tier_name=ct.tier,
        confidence_margin_ms=ct.margin,
        confidence_ci_width_ms=ct.ci_width,
        confidence_decisiveness=ct.decisiveness,
        confidence_sign_consistency=ct.sign_consistency,
    )


def evidence_fields(evidence: PairedTimingEvidence, *, change_class: str = "class_b", replicated: bool = False) -> dict[str, str]:
    return {
        "change_class": change_class,
        "replicated": "true" if replicated else "false",
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
        # Confidence tier fields (B redesign, 2026-06-02).
        "confidence_tier": evidence.confidence_tier_name or "",
        "confidence_margin_ms": _format_optional_ms(evidence.confidence_margin_ms),
        "confidence_ci_width_ms": _format_optional_ms(evidence.confidence_ci_width_ms),
        "confidence_decisiveness": (
            "" if evidence.confidence_decisiveness is None
            else f"{evidence.confidence_decisiveness:.4f}"
        ),
        "confidence_sign_consistency": (
            "" if evidence.confidence_sign_consistency is None
            else f"{evidence.confidence_sign_consistency:.4f}"
        ),
    }


def validate_class_a(
    *,
    hypothesis: str = "",
    change_notes: str = "",
    touched_files: list[str] | None = None,
    candidate_id: str = "",
) -> tuple[bool, str]:
    """Hard whitelist for Class A validation.

    Returns (is_valid, reason).  The caller should force class_b when this
    function returns ``False`` regardless of what was originally specified.

    At least one *allowed* pattern must be present, and none of the
    *forbidden* patterns may appear.
    """
    combined = (
        hypothesis
        + " "
        + change_notes
        + " "
        + " ".join(touched_files or [])
        + " "
        + candidate_id
    ).lower()

    # -- allowed patterns (at least one must match) --
    allowed = [
        "dead store",
        "unused assignment",
        "removes dead",
        "unused parameter",
        "unused temporary",
        "replace copy with existing",
        "replace with existing",
        "already computed",
        "already done elsewhere",
        "redundant computation",
        "eliminates unused",
        "removes unused",
        "pure removal",
    ]

    # -- forbidden patterns (NONE must match) --
    forbidden = [
        "new cache",
        "thread_local",
        "static local",
        "new branch",
        "new conditional",
        "adds branch",
        "new state variable",
        "new state",
        "container type",
        "replace type",
        "swap container",
        "aggregation reorder",
        "path reorder",
        "multi-user",
        "subscriber payload reuse",
    ]

    has_allowed = any(pattern in combined for pattern in allowed)
    matched_forbidden = [pattern for pattern in forbidden if pattern in combined]

    if matched_forbidden:
        return False, (
            "Class A rejected: forbidden pattern(s) found: "
            + ", ".join(matched_forbidden)
        )
    if not has_allowed:
        return False, (
            "Class A rejected: no allowed pattern matched; "
            + "change is not a pure dead-store / unused-value removal"
        )
    return True, "valid Class A: allowed pattern matched, no forbidden patterns found"
