#!/usr/bin/env python3
"""Generate a dated Psi performance optimization report.

The source intentionally stays ASCII-only. Chinese output strings use unicode
escapes so Windows console/code-page settings cannot corrupt the script text.
"""

from __future__ import annotations

import argparse
import base64
import csv
import html
import json
import shutil
import subprocess
import tempfile
import statistics
from datetime import date
from pathlib import Path

from psi_timing_history import (
    experiment_root_for_path,
    history_context_from_sources,
    history_path_candidates,
    history_table_columns,
    read_history_rows,
    select_history_rows,
)


DEFAULT_CONTROL_LOOP = Path("experiments/psi-remote-linux-20260508/control_loop")
HISTORY_FILE_NAME = "timing_history.tsv"

TITLE_SUFFIX = "\u6027\u80fd\u4f18\u5316\u62a5\u544a"
SECTION_SUMMARY = "\u7b80\u77ed\u603b\u7ed3"
SECTION_CONTROL = "\u5f53\u524d\u63a7\u5236\u57fa\u7ebf"
SECTION_HOTSPOTS = "Profile \u70ed\u70b9"
SECTION_QUEUE = "\u5b9e\u9a8c\u5019\u9009\u961f\u5217"
SECTION_COOLDOWN = "Cooldown / Blocked"
SECTION_PLATEAU = "Plateau \u4e0e\u566a\u58f0\u5224\u65ad"
SECTION_CONVERGENCE = "\u6536\u655b\u5224\u5b9a"
SECTION_HISTORY = "\u7d2f\u79ef\u65f6\u95f4\u5386\u53f2"


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def read_tsv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(path)
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def read_json(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_optional_json(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    return read_json(path)


def read_optional_tsv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    return read_tsv(path)


def table_value(value: str | None) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def md_table(rows: list[dict[str, str]], columns: list[str], limit: int | None = None) -> str:
    visible = rows[:limit] if limit is not None else rows
    if not visible:
        return "\u65e0\u8bb0\u5f55\u3002\n"
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in visible:
        lines.append("| " + " | ".join(table_value(row.get(col)) for col in columns) + " |")
    return "\n".join(lines) + "\n"


def any_value(rows: list[dict[str, str]], column: str) -> bool:
    return any(row.get(column) not in (None, "") for row in rows)


def parse_float(raw: str | None) -> float | None:
    if raw in (None, ""):
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def format_float(raw: float | None, digits: int = 3) -> str:
    if raw is None:
        return ""
    return f"{raw:.{digits}f}"


def csv_number_text(raw: object) -> str:
    if isinstance(raw, list):
        return ",".join(str(item) for item in raw)
    if raw is None:
        return ""
    return str(raw)


def history_group(control_head: str) -> str:
    return f"timing_history_v1|control_head={control_head}|sample_unit=ms"


def median_absolute_deviation(values: list[float]) -> float:
    if not values:
        return 0.0
    center = statistics.median(values)
    return statistics.median([abs(value - center) for value in values])


def timing_columns(rows: list[dict[str, str]]) -> list[str]:
    columns = ["sample_count"]
    if any_value(rows, "samples_ms"):
        columns.append("samples_ms")
    columns.append("samples")
    for field in ("control_samples_ms", "candidate_samples_ms", "paired_deltas_ms"):
        if any_value(rows, field):
            columns.append(field)
    for metric in ("mean", "median", "mad", "iqr", "stdev", "range"):
        ms_column = f"{metric}_ms"
        seconds_column = f"{metric}_seconds"
        if any_value(rows, ms_column):
            columns.append(ms_column)
        columns.append(seconds_column)
    return columns


def convergence_columns(rows: list[dict[str, str]]) -> list[str]:
    columns = ["rank", "kind", "policy_bucket", "target", "verdict"]
    if any_value(rows, "sample_count"):
        columns.append("sample_count")
    for field in (
        "timing_verdict",
        "timing_verdict_reason",
        "control_sample_count",
        "candidate_sample_count",
        "paired_sample_count",
        "control_samples_ms",
        "candidate_samples_ms",
        "paired_deltas_ms",
    ):
        if any_value(rows, field):
            columns.append(field)
    for metric in ("delta", "median", "median_delta", "bootstrap_ci_low", "bootstrap_ci_high", "mad", "iqr", "stdev", "range"):
        ms_column = f"{metric}_ms"
        seconds_column = f"{metric}_seconds"
        if any_value(rows, ms_column):
            columns.append(ms_column)
        if any_value(rows, seconds_column):
            columns.append(seconds_column)
    if any_value(rows, "permutation_p_value"):
        columns.append("permutation_p_value")
    columns.extend(["noise_flag", "stop_reason"])
    return columns


def optional_columns(rows: list[dict[str, str]], candidates: list[str]) -> list[str]:
    return [column for column in candidates if any_value(rows, column)]


def noisy_candidate_count(retry_conditions: list[dict[str, str]], patch_queue: list[dict[str, str]]) -> int:
    targets = {
        (row.get("target") or "").strip()
        for row in retry_conditions
        if (row.get("status") or "").strip() == "NOISY_PENDING"
    }
    targets.update(
        (row.get("target") or "").strip()
        for row in patch_queue
        if (row.get("queue_state") or "").strip() == "NOISY_PENDING"
    )
    targets.discard("")
    return len(targets)


def summarize_control(attempts: list[dict[str, str]]) -> dict[str, str]:
    for row in attempts:
        if row.get("kind") == "control":
            return row
    return {}


def history_columns(rows: list[dict[str, str]]) -> list[str]:
    columns = ["bundle_id", "recorded_at", "kind", "target"]
    for metric in ("median", "mad", "iqr", "stdev", "range", "delta"):
        ms_column = f"{metric}_ms"
        seconds_column = f"{metric}_seconds"
        if any_value(rows, ms_column):
            columns.append(ms_column)
        elif any_value(rows, seconds_column):
            columns.append(seconds_column)
    columns.extend(["noise_flag", "compatibility_tag"])
    return columns


def control_distribution_text(control: dict[str, str]) -> str:
    fields = [
        ("sample_count", "sample_count"),
        ("median_ms", "median_ms"),
        ("mad_ms", "mad_ms"),
        ("iqr_ms", "iqr_ms"),
        ("stdev_ms", "stdev_ms"),
        ("range_ms", "range_ms"),
    ]
    parts = [f"{label}=`{control.get(column, '')}`" for label, column in fields if control.get(column)]
    if not parts:
        return "Current control distribution: no robust timing fields recorded."
    return "Current control distribution: " + "; ".join(parts) + "."


def convergence_reason_text(state: dict[str, object], noise_status: str) -> str:
    reason = str(state.get("last_exit_reason") or "")
    batch_continuation = str(state.get("batch_continuation") or "")
    next_round_action = str(state.get("next_round_action") or "")
    reason_label = reason or "none"
    sample_floor = "at least 5 measured samples for promotion and 7 for bundle audit"
    if reason == "convergence_proven":
        detail = "Convergence may be claimed only when the minimum sample floor and UCB rule are both satisfied."
    elif reason == "budget_stop":
        detail = "Budget stop means the loop stopped spending attempts; it is not convergence proof."
    elif noise_status.lower() in {"noisy", "noisy_pending"}:
        detail = "NOISY_PENDING pauses candidate-level judgment only; it does not replace the global stop reason."
    elif reason.upper() in {"NOISY", "NOISY_PENDING"}:
        detail = "Legacy NOISY rows should be treated as candidate-level pause state, not as a global stop reason."
    elif reason == "remote_failed":
        detail = "Remote failures stop the batch; the low-level failure reason stays in failure_analysis.json."
    elif not reason and next_round_action == "continue":
        detail = (
            "The batch completed without a global stop reason; the loop should continue to the next round."
            if batch_continuation == "continue_to_next_round"
            else "The batch completed and the next round action should be reviewed from the run state."
        )
    else:
        detail = "Treat the run-state reason as context, not as promotion proof by itself."
    return f"Convergence reason: `{reason_label}`. {detail} Sample policy: {sample_floor}."


def find_browser() -> Path | None:
    candidates = [
        Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
        Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
        Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
        Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    for name in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge"):
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def chart_reference_lines(images: list[Path]) -> list[str]:
    lines: list[str] = []
    for image in images:
        image_ref = image.resolve().as_posix()
        alt = image.name
        lines.extend([f"![{alt}]({image_ref})", ""])
    return lines


def build_markdown(
    report_date: str,
    profile: list[dict[str, str]],
    hotspots: list[dict[str, str]],
    attempts: list[dict[str, str]],
    cooldown: list[dict[str, str]],
    images: list[Path],
    patch_queue: list[dict[str, str]] | None = None,
    neutral_pool: list[dict[str, str]] | None = None,
    retry_conditions: list[dict[str, str]] | None = None,
    history_rows: list[dict[str, str]] | None = None,
    history_path: Path | None = None,
    run_state: dict[str, object] | None = None,
    failure_analysis: dict[str, object] | None = None,
    comparison_summary: dict[str, object] | None = None,
) -> str:
    state = run_state or {}
    failure_analysis = failure_analysis or {}
    comparison_summary = comparison_summary or {}
    patch_queue = patch_queue or []
    neutral_pool = neutral_pool or []
    retry_conditions = retry_conditions or []
    control = summarize_control(attempts)
    exploit = [row for row in attempts if row.get("policy_bucket") in {"exploit", "evidence"}]
    explore = [
        row
        for row in attempts
        if row.get("policy_bucket") in {"explore", "insight", "combination"} or row.get("experiment_kind") == "neutral_stack"
    ]
    reserve = [row for row in attempts if row.get("policy_bucket") == "reserve"]
    blocked = [row for row in cooldown if row.get("status") == "blocked"]
    cooled = [row for row in cooldown if row.get("status") != "blocked"]

    control_head = str(control.get("control_head") or state.get("control_head") or "unknown")
    control_median = control.get("median_seconds") or control.get("control_median_seconds", "unknown")
    control_median_ms = control.get("median_ms") or control.get("control_median_ms")
    noise_flag = control.get("noise_flag", "unknown")
    sample_text = control.get("samples_ms") or control.get("samples", "")
    stop_reason = str(state.get("last_exit_reason") or "")
    stop_reason_text = stop_reason or "none"
    batch_continuation = str(state.get("batch_continuation") or "")
    next_round_action = str(state.get("next_round_action") or "")
    epsilon = state.get("epsilon")
    ucb95 = state.get("ucb95_expected_delta")
    noisy_count = state.get("noisy_candidate_count")
    if noisy_count in (None, ""):
        noisy_count = noisy_candidate_count(retry_conditions, patch_queue)
    control_baseline_text = f"bundle median `{control_median}s`"
    if control_median_ms:
        control_baseline_text = f"bundle median `{control_median_ms}ms` (`{control_median}s`)"
    comparison_block: list[str] = []
    if comparison_summary:
        def sample_text_from(value: object) -> str:
            if isinstance(value, list):
                return ",".join(str(item) for item in value)
            return "" if value is None else str(value)

        old_control = comparison_summary.get("control") if isinstance(comparison_summary.get("control"), dict) else {}
        candidate = comparison_summary.get("candidate") if isinstance(comparison_summary.get("candidate"), dict) else {}
        updated_baseline = (
            comparison_summary.get("updated_baseline")
            if isinstance(comparison_summary.get("updated_baseline"), dict)
            else {}
        )
        paired = comparison_summary.get("paired") if isinstance(comparison_summary.get("paired"), dict) else {}
        if not old_control and (
            comparison_summary.get("baseline_samples_ms") is not None
            or comparison_summary.get("baseline_median_ms") is not None
        ):
            old_control = {
                "sample_count": len(comparison_summary.get("baseline_samples_ms") or []),
                "samples_ms": csv_number_text(comparison_summary.get("baseline_samples_ms")),
                "median_ms": csv_number_text(comparison_summary.get("baseline_median_ms")),
                "stdev_ms": "",
                "range_ms": "",
                "noise_flag": "",
            }
        if not candidate and (
            comparison_summary.get("candidate_samples_ms") is not None
            or comparison_summary.get("candidate_median_ms") is not None
        ):
            candidate = {
                "sample_count": len(comparison_summary.get("candidate_samples_ms") or []),
                "samples_ms": csv_number_text(comparison_summary.get("candidate_samples_ms")),
                "median_ms": csv_number_text(comparison_summary.get("candidate_median_ms")),
                "stdev_ms": "",
                "range_ms": "",
                "noise_flag": "",
            }
        if not updated_baseline and candidate:
            updated_baseline = dict(candidate)
        comparison_rows = [
            {
                "role": str(comparison_summary.get("control_role") or "old_control"),
                "sample_count": str(old_control.get("sample_count", "")),
                "samples_ms": sample_text_from(old_control.get("samples_ms", "")),
                "median_ms": str(old_control.get("median_ms", "")),
                "stdev_ms": str(old_control.get("stdev_ms", "")),
                "range_ms": str(old_control.get("range_ms", "")),
                "noise_flag": str(old_control.get("noise_flag", "")),
            },
            {
                "role": str(comparison_summary.get("candidate_role") or "candidate"),
                "sample_count": str(candidate.get("sample_count", "")),
                "samples_ms": sample_text_from(candidate.get("samples_ms", "")),
                "median_ms": str(candidate.get("median_ms", "")),
                "stdev_ms": str(candidate.get("stdev_ms", "")),
                "range_ms": str(candidate.get("range_ms", "")),
                "noise_flag": str(candidate.get("noise_flag", "")),
            },
            {
                "role": "paired_delta",
                "sample_count": str(paired.get("paired_sample_count", "")),
                "samples_ms": sample_text_from(paired.get("paired_deltas_ms", "")),
                "median_ms": str(paired.get("median_delta_ms", "")),
                "stdev_ms": str(paired.get("paired_stdev_ms", "")),
                "range_ms": str(paired.get("paired_range_ms", "")),
                "noise_flag": str(paired.get("noise_flag", "")),
            },
            {
                "role": str(comparison_summary.get("updated_baseline_role") or "updated_baseline"),
                "sample_count": str(updated_baseline.get("sample_count", "")),
                "samples_ms": sample_text_from(updated_baseline.get("samples_ms", "")),
                "median_ms": str(updated_baseline.get("median_ms", "")),
                "stdev_ms": str(updated_baseline.get("stdev_ms", "")),
                "range_ms": str(updated_baseline.get("range_ms", "")),
                "noise_flag": str(updated_baseline.get("noise_flag", "")),
            },
        ]
        paired_samples = comparison_summary.get("paired_samples")
        paired_rows = paired_samples if isinstance(paired_samples, list) else []
        timing_verdict = comparison_summary.get("timing_verdict") or comparison_summary.get("decision") or comparison_summary.get("verdict", "")
        timing_reason = comparison_summary.get("timing_verdict_reason") or comparison_summary.get("verdict_reason") or ""
        ci_low = paired.get("bootstrap_ci_low_ms") or comparison_summary.get("bootstrap_ci_low_ms", "")
        ci_high = paired.get("bootstrap_ci_high_ms") or comparison_summary.get("bootstrap_ci_high_ms", "")
        p_value = paired.get("permutation_p_value") or comparison_summary.get("permutation_p_value", "")
        median_delta = paired.get("median_delta_ms") or comparison_summary.get("median_delta_ms", "")
        comparison_block = [
            "## Timing comparison summary",
            "",
            f"Timing verdict: `{timing_verdict}`; reason: `{timing_reason}`.",
            (f"Verdict reason: `{timing_reason}`" if timing_reason else ""),
            f"Decision: `{comparison_summary.get('decision') or comparison_summary.get('verdict', '')}`; "
            f"accepted: `{comparison_summary.get('accepted', '')}`; "
            f"compare_result: `{comparison_summary.get('compare_result', '')}`.",
            f"Median paired delta: `{median_delta}ms`; bootstrap CI: `[{ci_low}, {ci_high}]ms`; permutation p-value: `{p_value}`.",
            "",
            md_table(
                comparison_rows,
                ["role", "sample_count", "samples_ms", "median_ms", "stdev_ms", "range_ms", "noise_flag"],
            ),
            "Paired delta uses `control_ms - candidate_ms`; positive means the candidate is faster.",
            "",
        ]
        if paired_rows:
            comparison_block.extend(
                [
                    "Raw paired A/B samples:",
                    "",
                    md_table(
                        [
                            {
                                "pair_index": str(row.get("pair_index", "")) if isinstance(row, dict) else "",
                                "control_ms": str(row.get("control_ms", "")) if isinstance(row, dict) else "",
                                "candidate_ms": str(row.get("candidate_ms", "")) if isinstance(row, dict) else "",
                                "delta_ms": str(row.get("delta_ms", "")) if isinstance(row, dict) else "",
                            }
                            for row in paired_rows
                        ],
                        ["pair_index", "control_ms", "candidate_ms", "delta_ms"],
                    ),
                    "",
                ]
            )

    history_rows = history_rows or []
    current_context = history_context_from_sources(control, state, history_rows)
    compatible_history, incompatible_history, current_group = select_history_rows(
        history_rows,
        host_key=current_context["host_key"],
        control_head=current_context["control_head"],
        active_gate=current_context["active_gate"],
        sample_unit=current_context["sample_unit"],
    )
    current_time_window = current_context["time_window"]
    compatible_control_rows = [row for row in compatible_history if row.get("kind") == "control"]
    compatible_candidate_rows = [row for row in compatible_history if row.get("kind") != "control"]
    current_window_rows = [row for row in compatible_history if row.get("time_window") == current_time_window]
    current_window_control_rows = [row for row in current_window_rows if row.get("kind") == "control"]
    compatible_control_medians_ms = [value for value in (parse_float(row.get("median_ms")) for row in compatible_control_rows) if value is not None]
    current_window_control_medians_ms = [
        value for value in (parse_float(row.get("median_ms")) for row in current_window_control_rows) if value is not None
    ]
    history_control_median = statistics.median(compatible_control_medians_ms) if compatible_control_medians_ms else None
    history_control_mad = median_absolute_deviation(compatible_control_medians_ms) if compatible_control_medians_ms else None
    history_control_range = (
        max(compatible_control_medians_ms) - min(compatible_control_medians_ms)
        if len(compatible_control_medians_ms) > 1
        else 0.0
    )
    current_window_control_median = (
        statistics.median(current_window_control_medians_ms) if current_window_control_medians_ms else None
    )
    current_window_control_mad = (
        median_absolute_deviation(current_window_control_medians_ms) if current_window_control_medians_ms else None
    )
    history_rows_display = sorted(
        compatible_history,
        key=lambda row: (
            row.get("recorded_at", ""),
            row.get("bundle_id", ""),
            row.get("kind", ""),
            row.get("target", ""),
        ),
    )
    history_columns = history_table_columns(history_rows_display)
    history_note = (
        "\u517c\u5bb9\u7ec4\u4ec5\u7edf\u8ba1\u5f53\u524d control_head \u4e0b\u7684 ms-native \u6837\u672c\uff1b"
        "\u4e0d\u517c\u5bb9\u7684\u5386\u53f2\u884c\u4fdd\u7559\u5728\u5386\u53f2\u6587\u4ef6\u91cc\uff0c"
        "\u4f46\u4e0d\u6df7\u5165\u5f53\u524d\u566a\u58f0\u5e26\u3002"
    )
    current_control_text = control_distribution_text(control) if control else "Current control row is missing."
    same_host_text = (
        "Same-host compatible comparison: "
        f"`{len(compatible_control_rows)}` control rows and `{len(compatible_candidate_rows)}` candidate/diagnostic rows "
        f"match host_key `{current_context['host_key']}`, control_head `{current_context['control_head']}`, "
        f"active_gate `{current_context['active_gate']}`, and sample_unit `{current_context['sample_unit']}`."
    )
    historical_window_text = (
        "Historical-window comparison: "
        f"time_window `{current_time_window}` has `{len(current_window_control_rows)}` compatible control rows; "
        f"window median `{format_float(current_window_control_median)}ms`, "
        f"window MAD `{format_float(current_window_control_mad)}ms`; "
        f"all compatible-history median `{format_float(history_control_median)}ms`, "
        f"MAD `{format_float(history_control_mad)}ms`, range `{format_float(history_control_range)}ms`."
    )
    timing_verdict_state = str(state.get("timing_verdict") or state.get("timing_status") or "")
    noise_status_text = (
        f"Noise status: control noise_flag `{noise_flag}`, run_state noise_status `{state.get('noise_status', 'unknown')}`. "
        f"timing verdict `{timing_verdict_state or 'unknown'}`. Noisy candidates paused: `{noisy_count}`. "
        "NOISY_PENDING pauses candidate-level judgment; it does not accept, reject, or prove convergence."
    )
    convergence_reason = convergence_reason_text(state, str(state.get("noise_status") or noise_flag))
    sample_policy = state.get("sample_policy") if isinstance(state.get("sample_policy"), dict) else {}
    sample_policy_text = (
        "Sample policy: "
        f"screening `{sample_policy.get('screening_measured_samples', 3)}` measured; "
        f"promotion `{sample_policy.get('promotion_measured_samples', 5)}` measured; "
        f"bundle audit `{sample_policy.get('bundle_audit_measured_samples', 7)}` measured. "
        "Screening rows are not promotion proof; paired A/B samples are required for timing acceptance."
    )
    failure_analysis_text = ""
    if failure_analysis:
        failure_reason = str(failure_analysis.get("reason") or state.get("failure_analysis_reason") or stop_reason)
        failure_action = str(failure_analysis.get("next_round_action") or state.get("next_round_action") or "continue")
        failure_tense = "before the next round" if failure_action == "continue" else "before the batch stopped"
        failure_analysis_text = (
            f"Failure analysis: reason `{failure_reason}` recorded {failure_tense}; "
            f"batch continuation `{failure_action}`."
        )

    title = f"{report_date} {TITLE_SUFFIX}"
    lines: list[str] = [
        f"# {title}",
        "",
        "\u9879\u76ee\uff1aPsi Trader \u8fdc\u7aef Linux \u6027\u80fd\u4f18\u5316",
        f"\u63a7\u5236\u7248\u672c\uff1a`{control_head}`",
        f"\u63a7\u5236\u57fa\u7ebf\uff1a{control_baseline_text}",
        "",
    ]

    lines.extend(chart_reference_lines(images))

    lines.extend(
        [
            f"## 1. {SECTION_SUMMARY}",
            "",
            "\u672c\u6027\u80fd\u4f18\u5316\u62a5\u544a\u8bb0\u5f55 profile \u9a71\u52a8\u6027\u80fd\u5b9e\u9a8c\u7684\u63a7\u5236\u9762\uff1a"
            "\u4ee5\u672c\u8f6e control-loop artifact \u8bb0\u5f55\u7684\u7248\u672c\u4f5c\u4e3a\u5b89\u5168\u951a\u70b9\uff0c"
            "\u628a profile\u3001hotspot \u6392\u540d\u3001\u5019\u9009\u961f\u5217\u548c cooldown \u72b6\u6001\u843d\u5230\u673a\u5668\u53ef\u8bfb\u6587\u4ef6\u3002"
            "\u672c\u8f6e profile run \u53ea\u6807\u8bb0\u4e3a `DIAGNOSTIC_ONLY`\uff0c\u4e0d\u8bb0\u4e3a PASS/FAIL_PERF\u3002",
            "",
            f"## 2. {SECTION_CONTROL}",
            "",
            md_table(
                [control] if control else [],
                ["control_head", *timing_columns([control] if control else []), "noise_flag"],
            ),
            current_control_text,
            f"\u767d\u5929/\u5f53\u524d\u6296\u52a8\u6807\u8bb0\uff1a`{noise_flag}`\u3002\u6837\u672c\uff1a`{sample_text}`\u3002",
            "",
            f"## 3. {SECTION_HOTSPOTS}",
            "",
            md_table(hotspots, ["rank", "stage", "total_ms", "avg_ms", "count", "score"], limit=8),
            "",
            f"## 4. {SECTION_QUEUE}",
            "",
            "Exploit \u5019\u9009\uff1a",
            "",
            md_table(exploit, ["rank", "target", "stage", "score", "verdict", "notes"]),
            "",
            "Explore / neutral stack \u5019\u9009\uff1a",
            "",
            md_table(explore, ["rank", "target", "stack_members", "score", "verdict", "notes"]),
            "",
            "Reserve \u5019\u9009\uff1a",
            "",
            md_table(reserve, ["rank", "target", "stage", "score", "notes"], limit=8),
            "",
            f"## 5. {SECTION_COOLDOWN}",
            "",
            "Cooldown\uff1a",
            "",
            md_table(cooled, ["target", "status", "cooldown_runs_remaining", "reason"]),
            "",
            "Blocked\uff1a",
            "",
            md_table(blocked, ["target", "status", "reason", "notes"]),
            "",
            f"## 6. {SECTION_PLATEAU}",
            "",
            f"\u8fd0\u884c\u539f\u56e0\uff1a`{stop_reason_text}`\u3002batch_continuation: `{batch_continuation or 'unknown'}`\uff1bnext_round_action: `{next_round_action or 'unknown'}`\u3002epsilon: `{epsilon}`\uff1bUCB_95(E[delta]): `{ucb95}`\u3002",
            noise_status_text,
            convergence_reason,
            sample_policy_text,
            failure_analysis_text,
            "",
            "\u5f53\u524d\u4e0d\u80fd\u53ea\u6309\u6700\u5927 hotspot \u8d2a\u5fc3\u5c1d\u8bd5\u3002"
            "\u5b9e\u9a8c\u961f\u5217\u540c\u65f6\u4fdd\u7559 exploit\u3001reserve \u548c neutral stack\uff0c"
            "\u7528\u4e8e\u9a8c\u8bc1\u4e2d\u6027\u5019\u9009\u662f\u5426\u5b58\u5728\u7ec4\u5408\u6536\u76ca\u3002"
            "\u82e5\u8fde\u7eed\u5019\u9009\u65e0 accepted\u3001neutral stack \u65e0\u6536\u76ca\uff0c"
            "\u4e14 profile \u663e\u793a\u5f53\u524d target \u6210\u672c\u4e0b\u964d\u6216\u8f6c\u79fb\uff0c\u624d\u6807\u8bb0\u4e3a\u5e73\u53f0\u671f\u3002",
            "",
            "\u9a8c\u6536\u53e3\u5f84\uff1a\u5019\u9009\u5fc5\u987b compare pass\uff1b"
            "\u6027\u80fd\u63a5\u53d7\u9608\u503c\u4ee5 control-loop artifact \u4e2d\u8bb0\u5f55\u7684\u672c\u8f6e gate policy \u4e3a\u51c6\u3002",
            "",
            f"## 7. {SECTION_CONVERGENCE}",
            "",
            "\u6536\u655b\u53ea\u80fd\u5728\u6700\u5c0f\u6837\u672c\u6570\u8fbe\u6807\u4e14 `UCB_95(E[delta]) <= epsilon` \u65f6\u5199\u4e3a `convergence_proven`\u3002"
            "`stall-limit` \u89e6\u53d1\u7684\u662f `budget_stop`\uff1b`NOISY` \u53ea\u6682\u505c\u6027\u80fd\u5224\u5b9a\u3002",
            "",
            "Delta convention: `delta = control - candidate`; positive delta means the candidate is faster. "
            "When millisecond fields exist, use `delta_ms`, `median_ms`, and `samples_ms` for sub-second decisions.",
            "",
            md_table(
                attempts,
                convergence_columns(attempts),
                limit=12,
            ),
            "",
        ]
    )
    lines.extend(comparison_block)

    if patch_queue or neutral_pool or retry_conditions:
        lines.extend(
            [
                "## 8. Headless artifact surface",
                "",
                f"Patch queue rows: `{len(patch_queue)}`; neutral pool rows: `{len(neutral_pool)}`; retry-condition rows: `{len(retry_conditions)}`.",
                "",
                "Patch queue:",
                "",
                md_table(
                    patch_queue,
                    optional_columns(
                        patch_queue,
                        [
                            "rank",
                            "candidate_id",
                            "target",
                            "patch_path",
                            "policy_bucket",
                            "experiment_kind",
                            "stack_members",
                            "touched_files",
                            "hypothesis",
                            "compare_result",
                            "timing_summary",
                            "semantic_risk",
                            "stack_compatibility",
                            "queue_state",
                            "build_status",
                            "compare_status",
                            "timing_status",
                            "measured_samples",
                            "required_samples",
                            "retry_condition",
                        ],
                    ),
                    limit=12,
                ),
                "",
                "Neutral pool:",
                "",
                md_table(
                    neutral_pool,
                    optional_columns(
                        neutral_pool,
                        [
                            "candidate_id",
                            "target",
                            "lane",
                            "patch_path",
                            "touched_files",
                            "hypothesis",
                            "experiment_kind",
                            "stack_members",
                            "compare_result",
                            "sample_count",
                            "aggregate_gain_seconds",
                            "timing_summary",
                            "semantic_risk",
                            "stack_compatibility",
                            "validation_status",
                            "retry_condition",
                        ],
                    ),
                    limit=12,
                ),
                "",
                "Retry conditions:",
                "",
                md_table(
                    retry_conditions,
                    optional_columns(
                        retry_conditions,
                        ["target", "status", "noise_flag", "retry_after", "required_condition", "last_exit_reason"],
                    ),
                    limit=12,
                ),
                "",
            ]
        )

    if history_rows:
        lines.extend(
            [
                f"## 9. {SECTION_HISTORY}",
                "",
                f"\u5386\u53f2\u6587\u4ef6\uff1a`{history_path}`\u3002",
                f"Host key\uff1a`{current_context['host_key']}`\uff1bactive_gate\uff1a`{current_context['active_gate']}`\uff1b"
                f"sample_unit\uff1a`{current_context['sample_unit']}`\uff1bwarm_or_cold\uff1a`{current_context['warm_or_cold']}`\uff1b"
                f"time_window\uff1a`{current_context['time_window']}`\u3002",
                f"\u517c\u5bb9\u7ec4\uff1a`{current_group}`\u3002",
                f"\u517c\u5bb9 control \u884c\uff1a`{len(compatible_control_rows)}`\uff1b"
                f"\u517c\u5bb9 candidate \u884c\uff1a`{len(compatible_candidate_rows)}`\uff1b"
                f"\u4e0d\u517c\u5bb9\u5386\u53f2\u884c\uff1a`{len(incompatible_history)}`\u3002",
                f"\u5386\u53f2 control \u4e2d\u4f4d\u6570\uff1a`{format_float(history_control_median)}ms`\u3002",
                f"\u5386\u53f2 control MAD\uff1a`{format_float(history_control_mad)}ms`\uff1b"
                f"\u5386\u53f2 control \u8303\u56f4\uff1a`{format_float(history_control_range)}ms`\u3002",
                same_host_text,
                historical_window_text,
                history_note,
                "",
                md_table(
                    history_rows_display,
                    history_columns,
                    limit=12,
                ),
                "",
            ]
        )

    if not profile:
        lines.extend(["Profile rows: 0\u3002", ""])
    return "\n".join(lines)


def image_by_name(images: list[Path]) -> dict[str, Path]:
    mapping: dict[str, Path] = {}
    for image in images:
        mapping[image.name] = image
        mapping[image.resolve().as_posix()] = image
    return mapping


def markdown_to_html(markdown: str, images: list[Path]) -> str:
    body_lines: list[str] = []
    in_table = False
    images_by_name = image_by_name(images)
    for raw in markdown.splitlines():
        line = raw.rstrip()
        if line.startswith("# "):
            if in_table:
                body_lines.append("</tbody></table>")
                in_table = False
            body_lines.append(f"<h1>{html.escape(line[2:])}</h1>")
        elif line.startswith("## "):
            if in_table:
                body_lines.append("</tbody></table>")
                in_table = False
            body_lines.append(f"<h2>{html.escape(line[3:])}</h2>")
        elif line.startswith("!["):
            if in_table:
                body_lines.append("</tbody></table>")
                in_table = False
            image_path_text = ""
            if "](" in line and line.endswith(")"):
                image_path_text = line.rsplit("](", 1)[1][:-1]
            image = images_by_name.get(image_path_text) or images_by_name.get(Path(image_path_text).name)
            if image and image.exists():
                encoded = base64.b64encode(image.read_bytes()).decode("ascii")
                body_lines.append(f'<img src="data:image/png;base64,{encoded}" alt="{html.escape(image.name)}">')
            else:
                body_lines.append(f"<p>{html.escape(line)}</p>")
        elif line.startswith("| ") and line.endswith(" |"):
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if all(set(cell) <= {"-"} for cell in cells):
                continue
            if not in_table:
                body_lines.append("<table><tbody>")
                in_table = True
                tag = "th"
            else:
                tag = "td"
            body_lines.append("<tr>" + "".join(f"<{tag}>{html.escape(cell)}</{tag}>" for cell in cells) + "</tr>")
        elif not line.strip():
            if in_table:
                body_lines.append("</tbody></table>")
                in_table = False
        else:
            if in_table:
                body_lines.append("</tbody></table>")
                in_table = False
            body_lines.append(f"<p>{html.escape(line).replace('`', '')}</p>")
    if in_table:
        body_lines.append("</tbody></table>")

    body = "\n".join(body_lines)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<style>
body {{ margin: 0; padding: 26px 34px; font-family: "Microsoft YaHei", "Segoe UI", Arial, sans-serif; color: #1f2937; line-height: 1.45; font-size: 11.5px; }}
h1 {{ margin: 0 0 14px; font-size: 22px; letter-spacing: 0; }}
h2 {{ margin: 22px 0 8px; padding-bottom: 5px; border-bottom: 1px solid #d8dee9; font-size: 15px; letter-spacing: 0; }}
p {{ margin: 7px 0 10px; }}
img {{ display: block; max-width: 670px; width: 100%; margin: 16px 0; border: 1px solid #e5e7eb; }}
table {{ width: 100%; border-collapse: collapse; margin: 10px 0 16px; font-size: 9.8px; }}
th, td {{ border: 1px solid #d1d5db; padding: 4px 5px; vertical-align: top; }}
th {{ background: #f3f4f6; text-align: left; font-weight: 700; }}
@page {{ margin: 14mm 12mm; }}
</style>
</head>
<body>
{body}
</body>
</html>
"""


def validate_pdf(path: Path, browser_output: bytes) -> None:
    if not path.exists():
        output = browser_output.decode("utf-8", errors="replace")
        raise RuntimeError(f"PDF was not generated: {path}\nBrowser output:\n{output}")
    data = path.read_bytes()
    if len(data) == 0 or not data.startswith(b"%PDF-"):
        output = browser_output.decode("utf-8", errors="replace")
        raise RuntimeError(f"Generated file is not a valid PDF: {path}\nBrowser output:\n{output}")


def write_pdf(markdown: str, pdf_path: Path, images: list[Path]) -> None:
    browser = find_browser()
    if browser is None:
        raise RuntimeError("Chrome/Edge not found; rerun with --no-pdf")

    pdf_path = pdf_path.resolve()
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="psi_report_") as tmp:
        html_path = Path(tmp) / "report.html"
        html_path.write_text(markdown_to_html(markdown, images), encoding="utf-8")
        completed = subprocess.run(
            [
                str(browser),
                "--headless",
                "--disable-gpu",
                f"--print-to-pdf={str(pdf_path)}",
                html_path.resolve().as_uri(),
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        validate_pdf(pdf_path, completed.stdout or b"")


AUTO_LOOP_CAPABILITY_CHECKLIST = [
    ("three_lane_candidate_generator", "scripts/psi_candidate_generator.py"),
    ("patch_queue_manifest", "scripts/psi_patch_queue.py"),
    ("neutral_pool_tsv", "neutral_pool.tsv"),
    ("candidate_level_noisy_pending", "retry_conditions.tsv"),
    ("neutral_stack_builder", "scripts/psi_neutral_stack.py"),
    ("auto_loop_closed_loop", "scripts/psi_headless_auto_loop.py"),
    ("status_surface_complete", "run_state.json + heartbeat.json"),
    ("report_template_extended", "psi_daily_report.py auto-loop section"),
]


def render_auto_loop_section(
    run_state: dict[str, object],
    attempts: list[dict[str, str]],
    neutral_pool: list[dict[str, str]],
    retry_conditions: list[dict[str, str]],
    patch_manifest: dict[str, object],
) -> str:
    mode = str(run_state.get("mode") or "")
    if mode != "headless_auto_loop" and not run_state.get("lane_counts"):
        # nothing to append for non-auto-loop runs
        return ""

    lane_counts = run_state.get("lane_counts") or {}
    control_distribution = run_state.get("control_distribution") or {}
    latest_lane = str(run_state.get("latest_lane") or "")
    latest_verdict = str(run_state.get("latest_verdict") or "")
    latest_candidate_id = str(run_state.get("latest_candidate_id") or "")
    stop_reason = str(run_state.get("last_exit_reason") or "")
    stop_detail = str(run_state.get("last_exit_detail") or "")

    # 1. harness capability checklist
    checklist_lines = ["## A. Harness capability checklist", ""]
    for name, source in AUTO_LOOP_CAPABILITY_CHECKLIST:
        checklist_lines.append(f"- [x] `{name}` ({source})")
    checklist_lines.append("")

    # 2. batch list + per-candidate lane / verdict / timing summary
    batch_table = md_table(
        attempts,
        [col for col in (
            "iteration",
            "candidate_id",
            "lane",
            "target",
            "sample_count",
            "control_median_ms",
            "candidate_median_ms",
            "delta_ms",
            "compare_result",
            "noise_flag",
            "verdict",
        ) if any_value(attempts, col)],
        limit=40,
    ) if attempts else "no attempts recorded.\n"

    # 3. control distribution + same-host jitter
    control_text = (
        "Control distribution (auto-loop refresh): "
        f"sample_count=`{control_distribution.get('sample_count', '')}`; "
        f"median_of_medians_ms=`{control_distribution.get('median_of_medians_ms', '')}`; "
        f"stdev_of_medians_ms=`{control_distribution.get('stdev_of_medians_ms', '')}`; "
        f"range_ms=`{control_distribution.get('range_ms', '')}`; "
        f"trusted=`{control_distribution.get('trusted', '')}`."
    )

    # 4. accepted optimizations
    accepted_rows = [row for row in attempts if (row.get("verdict") or "").strip() == "accepted"]
    if accepted_rows:
        accepted_block = md_table(
            accepted_rows,
            ["iteration", "candidate_id", "lane", "target", "delta_ms", "samples_ms", "notes"],
        )
    else:
        accepted_block = "No accepted optimizations in this run.\n"

    # 5. neutral + which stacks they entered
    neutral_block = md_table(
        neutral_pool,
        [col for col in (
            "candidate_id",
            "lane",
            "target",
            "touched_files",
            "compare_result",
            "timing_summary",
            "semantic_risk",
            "stack_compatibility",
            "retry_condition",
        ) if any_value(neutral_pool, col)],
        limit=40,
    ) if neutral_pool else "No neutral candidates retained.\n"

    # 6. rejected candidates + failure category
    rejected_rows = [row for row in attempts if (row.get("verdict") or "").strip() == "rejected"]
    rejected_block = md_table(
        rejected_rows,
        [col for col in (
            "iteration",
            "candidate_id",
            "lane",
            "target",
            "compare_result",
            "delta_ms",
            "noise_flag",
            "retry_condition",
            "notes",
        ) if any_value(rejected_rows, col)],
        limit=40,
    ) if rejected_rows else "No rejected candidates.\n"

    # 7. NOISY_PENDING candidates with retry_condition
    noisy_rows = [
        row
        for row in attempts
        if (row.get("verdict") or "").strip() == "NOISY_PENDING"
    ]
    noisy_block = md_table(
        noisy_rows,
        [col for col in (
            "iteration",
            "candidate_id",
            "lane",
            "target",
            "noise_flag",
            "retry_condition",
        ) if any_value(noisy_rows, col)],
        limit=40,
    ) if noisy_rows else "No NOISY_PENDING candidates.\n"

    # 8. baseline update
    baseline_text = (
        "Baseline updated: the accepted candidate median is recorded via timing_history.tsv."
        if accepted_rows
        else "Baseline not updated this run."
    )

    # 9. stop reason
    stop_block = f"Stop reason: `{stop_reason or 'unknown'}`; detail: `{stop_detail}`."

    # 10. next-step suggestions
    suggestions: list[str] = []
    if lane_counts.get("evidence", 0):
        suggestions.append("Evidence lane still has candidates; run another iteration before widening scope.")
    if lane_counts.get("insight", 0) and not accepted_rows:
        suggestions.append("Insight lane is populated; try a narrow Class A / cache-locality experiment.")
    if neutral_pool and lane_counts.get("combination", 0):
        suggestions.append("Neutral pool has compatible members; promote a neutral-stack bundle audit next.")
    if noisy_rows:
        suggestions.append("NOISY_PENDING rows exist; wait for quieter host window before rerunning those targets.")
    if not suggestions:
        suggestions.append("Run another iteration after refreshing the profile snapshot.")

    patch_entries = patch_manifest.get("entries") if isinstance(patch_manifest, dict) else []
    if not isinstance(patch_entries, list):
        patch_entries = []
    patch_status_counts: dict[str, int] = {}
    for entry in patch_entries:
        status = str((entry or {}).get("status", ""))
        patch_status_counts[status] = patch_status_counts.get(status, 0) + 1
    patch_summary_line = "; ".join(f"{name}=`{count}`" for name, count in sorted(patch_status_counts.items()) if name)

    lines = [
        "## Z. Psi headless auto-loop summary",
        "",
        "\n".join(checklist_lines),
        f"Active run: `{run_state.get('run_id', '')}`; iterations=`{run_state.get('iteration', 0)}`;"
        f" latest lane=`{latest_lane}`; latest candidate=`{latest_candidate_id}`;"
        f" latest verdict=`{latest_verdict}`.",
        f"Lane counts: evidence=`{lane_counts.get('evidence', 0)}`; "
        f"insight=`{lane_counts.get('insight', 0)}`; combination=`{lane_counts.get('combination', 0)}`.",
        f"Patch manifest status counts: {patch_summary_line or 'none'}.",
        "",
        "### B. Experiments in this run",
        "",
        batch_table,
        "",
        "### C. Control distribution",
        "",
        control_text,
        "",
        "### D. Accepted optimizations",
        "",
        accepted_block,
        "",
        "### E. Neutral candidates",
        "",
        neutral_block,
        "",
        "### F. Rejected candidates",
        "",
        rejected_block,
        "",
        "### G. NOISY_PENDING candidates",
        "",
        noisy_block,
        "",
        "### H. Baseline",
        "",
        baseline_text,
        "",
        "### I. Stop reason",
        "",
        stop_block,
        "",
        "### J. Next-step suggestions",
        "",
    ]
    for suggestion in suggestions:
        lines.append(f"- {suggestion}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate Psi performance optimization Markdown/PDF report.")
    parser.add_argument("--date", default=date.today().isoformat(), help="Report date, YYYY-MM-DD.")
    parser.add_argument(
        "--control-loop-dir",
        type=Path,
        default=DEFAULT_CONTROL_LOOP,
        help="Directory containing profile.tsv, hotspots.tsv, attempts.tsv, cooldown.tsv.",
    )
    parser.add_argument(
        "--report-root",
        type=Path,
        help="Report root directory. Defaults to <experiment-root>/reports for the selected control-loop dir.",
    )
    parser.add_argument("--image", type=Path, action="append", default=[], help="Optional image to reference/embed. Repeat for multiple images.")
    parser.add_argument("--run-state", type=Path, help="Optional run_state.json with status and convergence metrics.")
    parser.add_argument("--no-pdf", action="store_true", help="Only write Markdown.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    root = repo_root()

    control_dir = args.control_loop_dir
    if not control_dir.is_absolute():
        control_dir = root / control_dir

    if args.report_root is None:
        report_root = control_dir / "reports"
    else:
        report_root = args.report_root
    if not report_root.is_absolute():
        report_root = root / report_root

    images: list[Path] = []
    for image in args.image:
        resolved = image if image.is_absolute() else root / image
        images.append(resolved)

    run_state_path = args.run_state
    if run_state_path and not run_state_path.is_absolute():
        run_state_path = root / run_state_path

    profile = read_tsv(control_dir / "profile.tsv")
    hotspots = read_tsv(control_dir / "hotspots.tsv")
    attempts = read_tsv(control_dir / "attempts.tsv")
    cooldown = read_tsv(control_dir / "cooldown.tsv")
    patch_queue = read_optional_tsv(control_dir / "patch_queue.tsv")
    neutral_pool = read_optional_tsv(control_dir / "neutral_pool.tsv")
    retry_conditions = read_optional_tsv(control_dir / "retry_conditions.tsv")
    failure_analysis = read_optional_json(control_dir / "failure_analysis.json")
    comparison_summary = read_optional_json(control_dir / "comparison_summary.json")
    history_candidates = history_path_candidates(control_dir)
    history_rows = read_history_rows(history_candidates)
    shared_history_path = experiment_root_for_path(control_dir) / HISTORY_FILE_NAME
    history_path = shared_history_path if shared_history_path.exists() else next(
        (path for path in history_candidates if path.exists()),
        shared_history_path,
    )

    run_state = read_json(run_state_path) if run_state_path else {}
    patch_manifest = read_optional_json(control_dir / "patches" / "patch_manifest.json")
    markdown = build_markdown(
        args.date,
        profile,
        hotspots,
        attempts,
        cooldown,
        images,
        patch_queue=patch_queue,
        neutral_pool=neutral_pool,
        retry_conditions=retry_conditions,
        history_rows=history_rows,
        history_path=history_path if history_rows else None,
        run_state=run_state,
        failure_analysis=failure_analysis,
        comparison_summary=comparison_summary,
    )
    auto_loop_section = render_auto_loop_section(run_state, attempts, neutral_pool, retry_conditions, patch_manifest)
    if auto_loop_section:
        markdown = markdown.rstrip() + "\n\n" + auto_loop_section + "\n"
    title = f"{args.date} {TITLE_SUFFIX}"
    report_dir = report_root / args.date
    report_dir.mkdir(parents=True, exist_ok=True)
    md_path = report_dir / f"{title}.md"
    pdf_path = report_dir / f"{title}.pdf"
    md_path.write_text(markdown, encoding="utf-8", newline="\n")

    if not args.no_pdf:
        write_pdf(markdown, pdf_path, images)

    print(f"markdown={md_path}")
    if not args.no_pdf:
        print(f"pdf={pdf_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
