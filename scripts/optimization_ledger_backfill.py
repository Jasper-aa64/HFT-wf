#!/usr/bin/env python3
"""Backfill optimization_ledger.tsv from historical run artifacts."""

from __future__ import annotations

import argparse
from pathlib import Path

from optimization_ledger import LEDGER_FILENAME, append_ledger_row, read_ledger_rows_from_artifacts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Backfill the append-only optimization ledger from existing artifacts.")
    parser.add_argument("--root", action="append", type=Path, default=[], help="Root directory to scan. May be repeated.")
    parser.add_argument("--output", type=Path, default=Path(LEDGER_FILENAME), help="Output TSV path.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    roots = args.root or [Path(r"C:\optimization_lr"), Path(r"C:\factor_lr"), Path(".trellis/tasks")]
    count = 0
    for row in read_ledger_rows_from_artifacts(roots):
        append_ledger_row(args.output, row)
        count += 1
    print(f"ledger_rows_backfilled={count}")
    print(f"ledger_output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
