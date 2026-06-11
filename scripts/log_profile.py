#!/usr/bin/env python3
"""Build profile artifacts from copied runner logs.

This parser is intentionally local and non-invasive: it reads copied ``*.log``
files and writes TSV/Markdown artifacts without touching the source tree.
"""

from __future__ import annotations

import argparse
import csv
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path


TIMESTAMP_RE = re.compile(r"\d{2}:\d{2}:\d{2}\.\d{3,6}")
STAGE_MAX_MS = {
    "read_table": 5_000,
    "handlerData": 60_000,
    "generate_write": 20_000,
    "clear_data": 5_000,
    "compute_total": 10_000,
}
READ_PATTERNS = (
    ("read_table", re.compile(r"readParquet\s+ReadTable\s+cost\s+time\s*:\s*(\d+)")),
    ("handlerData", re.compile(r"readParquet\s+handlerData\s+cost\s+time\s*:\s*(\d+)")),
    (
        "generate_write",
        re.compile(r"readParquet\s+generate_table\s+and\s+write\s+cost\s+time\s*:\s*(\d+)"),
    ),
    ("clear_data", re.compile(r"readParquet\s+clearData\s+cost\s+time\s*:\s*(\d+)")),
)
COMPUTE_RE = re.compile(r"compute\s+factorType:(\d+)\s+done,\s+cost:(\d+)ms")


@dataclass(frozen=True)
class ParsedEvent:
    stage: str
    total_ms: int
    source: str
    timestamp: str
    factor_type: str = ""


@dataclass
class ParseStats:
    files_seen: int = 0
    matched_events: int = 0
    duplicate_events: int = 0
    rejected_events: int = 0
    compare_logs_skipped: int = 0
    rejected_by_stage: dict[str, int] | None = None

    def reject(self, *stages: str) -> None:
        self.rejected_events += 1
        if self.rejected_by_stage is None:
            self.rejected_by_stage = defaultdict(int)
        for stage in stages:
            self.rejected_by_stage[stage] += 1


def timestamp_before(line: str, offset: int) -> str:
    timestamps = TIMESTAMP_RE.findall(line[:offset])
    return timestamps[-1] if timestamps else "unknown"


def effective_max_ms(stage: str, max_ms: int) -> int:
    stage_max = STAGE_MAX_MS.get(stage, max_ms)
    if stage.startswith("compute_"):
        stage_max = STAGE_MAX_MS["compute_total"]
    return min(max_ms, stage_max)


def parse_ms(raw: str, stage: str, max_ms: int) -> int | None:
    value = int(raw)
    if value < 0 or value > effective_max_ms(stage, max_ms):
        return None
    return value


def iter_log_events(log_file: Path, input_dir: Path, max_ms: int, stats: ParseStats) -> list[ParsedEvent]:
    source = log_file.relative_to(input_dir).as_posix()
    events: list[ParsedEvent] = []

    with log_file.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            for stage, pattern in READ_PATTERNS:
                for match in pattern.finditer(line):
                    total_ms = parse_ms(match.group(1), stage, max_ms)
                    if total_ms is None:
                        stats.reject(stage)
                        continue
                    events.append(
                        ParsedEvent(
                            stage=stage,
                            total_ms=total_ms,
                            source=source,
                            timestamp=timestamp_before(line, match.start()),
                        )
                    )

            for match in COMPUTE_RE.finditer(line):
                factor_type = match.group(1)
                total_ms = parse_ms(match.group(2), "compute_total", max_ms)
                if total_ms is None:
                    stats.reject("compute_total", f"compute_{factor_type}")
                    continue
                timestamp = timestamp_before(line, match.start())
                events.append(
                    ParsedEvent(
                        stage="compute_total",
                        total_ms=total_ms,
                        source=source,
                        timestamp=timestamp,
                        factor_type=factor_type,
                    )
                )
                events.append(
                    ParsedEvent(
                        stage=f"compute_{factor_type}",
                        total_ms=total_ms,
                        source=source,
                        timestamp=timestamp,
                        factor_type=factor_type,
                    )
                )

    return events


def collect_events(input_dir: Path, max_ms: int, include_compare_logs: bool) -> tuple[list[ParsedEvent], ParseStats]:
    stats = ParseStats()
    all_log_files = sorted(input_dir.glob("*.log"))
    log_files = []
    for log_file in all_log_files:
        if log_file.name == "compare.log" and not include_compare_logs:
            stats.compare_logs_skipped += 1
            continue
        log_files.append(log_file)
    stats.files_seen = len(log_files)
    seen: set[tuple[str, str, int, str, str]] = set()
    events: list[ParsedEvent] = []

    for log_file in log_files:
        for event in iter_log_events(log_file, input_dir, max_ms, stats):
            key = (
                event.source,
                event.stage,
                event.total_ms,
                event.timestamp,
                event.factor_type,
            )
            if key in seen:
                stats.duplicate_events += 1
                continue
            seen.add(key)
            stats.matched_events += 1
            events.append(event)

    return events, stats


def summarize(events: list[ParsedEvent]) -> list[dict[str, object]]:
    totals: dict[str, int] = defaultdict(int)
    counts: dict[str, int] = defaultdict(int)
    sources: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for event in events:
        totals[event.stage] += event.total_ms
        counts[event.stage] += 1
        sources[event.stage][event.source] += 1

    rows: list[dict[str, object]] = []
    for stage in sorted(totals, key=lambda item: (-totals[item], item)):
        source_summary = ";".join(
            f"{source}:{count}" for source, count in sorted(sources[stage].items())
        )
        rows.append(
            {
                "stage": stage,
                "total_ms": totals[stage],
                "count": counts[stage],
                "avg_ms": f"{totals[stage] / counts[stage]:.3f}",
                "source": source_summary,
            }
        )
    return rows


def write_profile(profile_path: Path, rows: list[dict[str, object]]) -> None:
    with profile_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("stage", "total_ms", "count", "avg_ms", "source"),
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def write_hotspots(hotspots_path: Path, rows: list[dict[str, object]]) -> None:
    top_total = int(rows[0]["total_ms"]) if rows else 0
    with hotspots_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("rank", "stage", "total_ms", "avg_ms", "count", "score", "notes"),
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        for rank, row in enumerate(rows, start=1):
            total_ms = int(row["total_ms"])
            score = total_ms / top_total if top_total else 0.0
            notes = "relative_to_top_total"
            if str(row["stage"]).startswith("compute_"):
                notes += "; compute_total overlaps compute_<factor>"
            writer.writerow(
                {
                    "rank": rank,
                    "stage": row["stage"],
                    "total_ms": total_ms,
                    "avg_ms": row["avg_ms"],
                    "count": row["count"],
                    "score": f"{score:.6f}",
                    "notes": notes,
                }
            )


def write_summary(
    summary_path: Path,
    input_dir: Path,
    output_dir: Path,
    stats: ParseStats,
    rows: list[dict[str, object]],
    max_ms: int,
    include_compare_logs: bool,
) -> None:
    top_rows = rows[:10]
    with summary_path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("# Log Profile Summary\n\n")
        handle.write(f"- Input directory: `{input_dir}`\n")
        handle.write(f"- Output directory: `{output_dir}`\n")
        handle.write(f"- Log files parsed: {stats.files_seen}\n")
        handle.write(f"- Compare logs included: {'yes' if include_compare_logs else 'no'}\n")
        handle.write(f"- Compare logs skipped: {stats.compare_logs_skipped}\n")
        handle.write(f"- Events retained: {stats.matched_events}\n")
        handle.write(f"- Duplicate event fragments skipped: {stats.duplicate_events}\n")
        handle.write(f"- Implausible timing fragments rejected: {stats.rejected_events}\n")
        handle.write(f"- Global max accepted timing: {max_ms} ms\n\n")

        handle.write("## Stage Timing Limits\n\n")
        handle.write("| Stage | Max ms |\n")
        handle.write("|---|---:|\n")
        for stage in ("read_table", "handlerData", "generate_write", "clear_data", "compute_total", "compute_<id>"):
            limit_stage = "compute_total" if stage == "compute_<id>" else stage
            handle.write(f"| {stage} | {effective_max_ms(limit_stage, max_ms)} |\n")
        handle.write("\n")

        handle.write("## Rejected Fragments By Stage\n\n")
        rejected_by_stage = stats.rejected_by_stage or {}
        if not rejected_by_stage:
            handle.write("No implausible timing fragments were rejected.\n\n")
        else:
            handle.write("| Stage | Rejected fragments |\n")
            handle.write("|---|---:|\n")
            for stage, count in sorted(rejected_by_stage.items()):
                handle.write(f"| {stage} | {count} |\n")
            handle.write("\n")

        handle.write("## Top Hotspots\n\n")
        if not top_rows:
            handle.write("No supported timing lines were found.\n\n")
        else:
            handle.write("| Rank | Stage | Total ms | Avg ms | Count |\n")
            handle.write("|---:|---|---:|---:|---:|\n")
            for rank, row in enumerate(top_rows, start=1):
                handle.write(
                    f"| {rank} | {row['stage']} | {row['total_ms']} | "
                    f"{row['avg_ms']} | {row['count']} |\n"
                )
            handle.write("\n")

        handle.write("## Parsed Patterns\n\n")
        handle.write("- `readParquet ReadTable cost time :<ms>` -> `read_table`\n")
        handle.write("- `readParquet handlerData cost time :<ms>` -> `handlerData`\n")
        handle.write(
            "- `readParquet generate_table and write cost time :<ms>` -> `generate_write`\n"
        )
        handle.write("- `readParquet clearData cost time :<ms>` -> `clear_data`\n")
        handle.write(
            "- `compute factorType:<id> done, cost:<ms>ms` -> "
            "`compute_total` and `compute_<id>`\n\n"
        )

        handle.write("## Limitations\n\n")
        handle.write(
            "- The parser only reads `*.log` files directly under the input directory.\n"
        )
        handle.write(
            "- Interleaved duplicate fragments are deduplicated when source, stage, "
            "timestamp, factor id, and millisecond value are identical.\n"
        )
        handle.write(
            "- Timing values above the stricter of `--max-ms` and the stage limit "
            "are treated as malformed fragments, which avoids counting digits glued "
            "to unrelated log text.\n"
        )
        handle.write(
            "- `compare.log` is skipped by default; pass `--include-compare-logs` "
            "to include correctness-run timings.\n"
        )
        handle.write(
            "- `compute_total` intentionally overlaps the per-factor `compute_<id>` rows.\n"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Parse copied runner logs into profile.tsv and hotspots.tsv.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example:\n"
            "  python scripts/log_profile.py "
            "experiments/remote-linux-20260508/bundle_after_sort_skip "
            "--output experiments/remote-linux-20260508/bundle_after_sort_skip/profile_artifacts\n\n"
            "Use --demo to print this example without writing files."
        ),
    )
    parser.add_argument("input_dir", nargs="?", type=Path, help="Directory containing copied *.log files.")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output directory. Defaults to <input_dir>/profile_artifacts.",
    )
    parser.add_argument(
        "--max-ms",
        type=int,
        default=300_000,
        help="Reject parsed timing values above this threshold as malformed fragments.",
    )
    parser.add_argument(
        "--include-compare-logs",
        action="store_true",
        help="Include compare.log timing data. By default compare.log is skipped.",
    )
    parser.add_argument("--demo", action="store_true", help="Print an example command and exit.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.demo:
        print(
            "python scripts/log_profile.py "
            "experiments/remote-linux-20260508/bundle_after_sort_skip "
            "--output experiments/remote-linux-20260508/bundle_after_sort_skip/profile_artifacts"
        )
        return 0

    if args.input_dir is None:
        parser.error("input_dir is required unless --demo is used")
    input_dir = args.input_dir.resolve()
    if not input_dir.is_dir():
        parser.error(f"input directory does not exist: {input_dir}")
    if args.max_ms <= 0:
        parser.error("--max-ms must be positive")

    output_dir = (args.output or (input_dir / "profile_artifacts")).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    events, stats = collect_events(input_dir, args.max_ms, args.include_compare_logs)
    rows = summarize(events)
    write_profile(output_dir / "profile.tsv", rows)
    write_hotspots(output_dir / "hotspots.tsv", rows)
    write_summary(
        output_dir / "summary.md",
        input_dir,
        output_dir,
        stats,
        rows,
        args.max_ms,
        args.include_compare_logs,
    )

    print(f"parsed_log_files={stats.files_seen}")
    print(f"compare_logs_skipped={stats.compare_logs_skipped}")
    print(f"events_retained={stats.matched_events}")
    print(f"duplicates_skipped={stats.duplicate_events}")
    print(f"fragments_rejected={stats.rejected_events}")
    print(f"profile={output_dir / 'profile.tsv'}")
    print(f"hotspots={output_dir / 'hotspots.tsv'}")
    print(f"summary={output_dir / 'summary.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
