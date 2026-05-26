#!/usr/bin/env python3
"""Create TWAP profile/hotspot context for the headless optimization loop.

The Psi optimization loop expects profile.tsv and hotspots.tsv before it can
generate candidates. TWAP does not have a profiler adapter yet, so this script
builds a source-backed hotspot snapshot for the aggregation position push path.

The rows are deliberately marked as static estimates. They are used to give the
patch agent touched files, symbols, and risk boundaries; acceptance still comes
only from remote build + TWAP correctness + paired push timing.
"""

from __future__ import annotations

import argparse
import csv
import re
from dataclasses import dataclass
from pathlib import Path


PROFILE_FIELDS = ["stage", "total_ms", "count", "avg_ms", "source", "touched_files", "symbols", "notes"]
HOTSPOT_FIELDS = [
    "rank",
    "stage",
    "total_ms",
    "avg_ms",
    "count",
    "score",
    "notes",
    "touched_files",
    "symbols",
    "expected_delta_seconds",
]


@dataclass(frozen=True)
class HotspotSpec:
    stage: str
    touched_files: tuple[str, ...]
    symbols: tuple[str, ...]
    base_cost: float
    count: int
    risk: str
    notes: str
    expected_delta_seconds: float


HOTSPOTS = [
    HotspotSpec(
        stage="twap.push.build_aggregation.cache_scan",
        touched_files=("PsiGrpcServer/twap_sale_service.cpp",),
        symbols=("buildTwapSaleAggregationPushMessage", "m_userAccountsCache", "m_twapSalePositionCache"),
        base_cost=120.0,
        count=1,
        risk="low",
        notes="static_estimate; scans all user accounts for one stock and rebuilds one aggregation payload from caches",
        expected_delta_seconds=0.006,
    ),
    HotspotSpec(
        stage="twap.push.order_status_config_lookup",
        touched_files=("PsiGrpcServer/twap_sale_service.cpp",),
        symbols=("buildTwapSaleAggregationPushMessage", "getOrderStatus", "getTwapSaleOrderConfigCache"),
        base_cost=105.0,
        count=2,
        risk="low",
        notes="static_estimate; per-account order status/config cache lookups sit inside the push path",
        expected_delta_seconds=0.004,
    ),
    HotspotSpec(
        stage="twap.push.aggregate_loop_and_sort",
        touched_files=("PsiGrpcServer/twap_sale_service.cpp",),
        symbols=("buildTwapSaleAggregationPushMessage", "subPositionInfo", "std::sort"),
        base_cost=82.0,
        count=1,
        risk="low",
        notes="static_estimate; summary accumulation, weighted price, and sub-position sorting run for every push",
        expected_delta_seconds=0.003,
    ),
    HotspotSpec(
        stage="twap.push.json_serialize",
        touched_files=("PsiUtils/PsiCfgLoader.cpp", "PsiUtils/PsiCfgLoader.h"),
        symbols=("twapSalePositionAggregationPushToJson", "subPositionInfoList", "accountDesc"),
        base_cost=78.0,
        count=1,
        risk="medium",
        notes="static_estimate; RapidJSON payload construction is repeated for every aggregation push; schema must stay unchanged",
        expected_delta_seconds=0.003,
    ),
    HotspotSpec(
        stage="twap.push.session_filter_and_queue",
        touched_files=("PsiGrpcServer/twap_sale_service.cpp",),
        symbols=("sendTwapSaleAggregationPushMessage", "matchesSearchStockCode", "twap_sale_message_queue"),
        base_cost=65.0,
        count=1,
        risk="low",
        notes="static_estimate; session scan, searchStockCode filter, and queue lock are on the fan-out path",
        expected_delta_seconds=0.002,
    ),
    HotspotSpec(
        stage="twap.push.redis_update_ingest",
        touched_files=("PsiGrpcServer/twap_sale_service.cpp", "PsiMemSQL/PsiMemSQL.cpp", "PsiMemSQL/PsiMemSQL.h"),
        symbols=("saveOrUpdatePSITwapStockPositionInfo", "sendTwapSaleAggregationPushMessage", "twapSalePositionOnUpdate"),
        base_cost=58.0,
        count=1,
        risk="medium",
        notes="static_estimate; Redis position update path persists DB/cache then fans out push; do not reintroduce DB query in push builder",
        expected_delta_seconds=0.002,
    ),
    HotspotSpec(
        stage="twap.push.stream_queue_poll",
        touched_files=("PsiGrpcServer/twap_sale_service.cpp",),
        symbols=("subPositionInfoListAggregation", "twap_sale_message_queue", "std::this_thread::sleep_for"),
        base_cost=40.0,
        count=1,
        risk="low",
        notes="static_estimate; streaming response loop should avoid empty queue CPU spin while keeping latency stable",
        expected_delta_seconds=0.001,
    ),
]


def read_text(source_root: Path, rel: str) -> str:
    path = source_root / rel
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def source_multiplier(source_root: Path, spec: HotspotSpec) -> float:
    score = 1.0
    for rel in spec.touched_files:
        text = read_text(source_root, rel)
        if not text:
            score *= 0.8
            continue
        symbol_hits = sum(len(re.findall(rf"\b{re.escape(symbol)}\b", text)) for symbol in spec.symbols)
        loop_hits = len(re.findall(r"\b(for|while)\s*\(", text))
        lock_hits = len(re.findall(r"\b(lock_guard|unique_lock|mutex)\b", text))
        score += min(symbol_hits, 8) * 0.08
        score += min(loop_hits, 40) * 0.005
        score += min(lock_hits, 20) * 0.006
    return score


def write_tsv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def build_rows(source_root: Path) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    scored = []
    for spec in HOTSPOTS:
        total = spec.base_cost * source_multiplier(source_root, spec)
        avg = total / max(spec.count, 1)
        scored.append((total, avg, spec))
    scored.sort(key=lambda item: item[0], reverse=True)
    top_total = scored[0][0] if scored else 1.0

    profile_rows: list[dict[str, str]] = []
    hotspot_rows: list[dict[str, str]] = []
    for rank, (total, avg, spec) in enumerate(scored, start=1):
        touched = "|".join(spec.touched_files)
        symbols = "|".join(spec.symbols)
        profile_rows.append(
            {
                "stage": spec.stage,
                "total_ms": f"{total:.3f}",
                "count": str(spec.count),
                "avg_ms": f"{avg:.3f}",
                "source": "twap_static_hotspot:estimated_not_runtime_timing",
                "touched_files": touched,
                "symbols": symbols,
                "notes": spec.notes,
            }
        )
        hotspot_rows.append(
            {
                "rank": str(rank),
                "stage": spec.stage,
                "total_ms": f"{total:.3f}",
                "avg_ms": f"{avg:.3f}",
                "count": str(spec.count),
                "score": f"{total / top_total:.6f}",
                "notes": f"{spec.risk}; {spec.notes}",
                "touched_files": touched,
                "symbols": symbols,
                "expected_delta_seconds": f"{spec.expected_delta_seconds:.6f}",
            }
        )
    return profile_rows, hotspot_rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Write TWAP profile.tsv and hotspots.tsv for optimization runs.")
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()

    source_root = args.source_root.resolve()
    run_dir = args.run_dir.resolve()
    if not source_root.exists():
        raise SystemExit(f"source root does not exist: {source_root}")
    profile_rows, hotspot_rows = build_rows(source_root)
    write_tsv(run_dir / "profile.tsv", profile_rows, PROFILE_FIELDS)
    write_tsv(run_dir / "hotspots.tsv", hotspot_rows, HOTSPOT_FIELDS)
    notes = [
        "TWAP hotspot snapshot",
        f"source_root={source_root}",
        "source=static source estimate, not acceptance evidence",
        "acceptance still requires remote build + correctness + paired timing",
        "",
        "ranked stages:",
    ]
    notes.extend(f"{row['rank']}. {row['stage']} files={row['touched_files']} symbols={row['symbols']}" for row in hotspot_rows)
    (run_dir / "hotspot_notes.txt").write_text("\n".join(notes) + "\n", encoding="utf-8")
    print(f"wrote {run_dir / 'profile.tsv'}")
    print(f"wrote {run_dir / 'hotspots.tsv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
