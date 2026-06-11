#!/usr/bin/env python3
"""Shared helpers for optimization report filenames."""

from __future__ import annotations

from pathlib import Path


def safe_report_text(text: object) -> str:
    raw = "" if text is None else str(text).strip()
    cleaned = []
    for ch in raw:
        if ch in '<>:"/\\|?*':
            cleaned.append("_")
        elif ord(ch) < 32:
            cleaned.append("_")
        else:
            cleaned.append(ch)
    return "".join(cleaned).strip()


def report_identity(text: object) -> str:
    return safe_report_text(text)


def reserve_report_paths(report_root: Path, report_date: str, stem: str) -> tuple[Path, Path]:
    """Return a unique markdown/PDF pair and reserve the markdown path."""
    report_dir = report_root / report_date
    report_dir.mkdir(parents=True, exist_ok=True)
    base_stem = safe_report_text(stem)
    if not base_stem:
        raise ValueError("report stem must not be empty")
    suffix = 1
    while True:
        candidate_stem = base_stem if suffix == 1 else f"{base_stem}-{suffix}"
        md_path = report_dir / f"{candidate_stem}.md"
        pdf_path = report_dir / f"{candidate_stem}.pdf"
        if pdf_path.exists():
            suffix += 1
            continue
        try:
            with md_path.open("x", encoding="utf-8"):
                pass
            return md_path, pdf_path
        except FileExistsError:
            pass
        suffix += 1
