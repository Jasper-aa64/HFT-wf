#!/usr/bin/env python3
"""Materialize a Manager-selected GateKeeper Mode B delta.

This helper is intentionally mechanical. The Manager decides which files or
refs are in scope; this script only writes input.patch and selected_files.txt
so gatekeeper_review_existing.sh can run without guessing the intended diff.
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


def run_git(repo: Path, args: list[str], *, check: bool = True) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if check and completed.returncode != 0:
        raise SystemExit(completed.stderr.strip() or f"git {' '.join(args)} failed")
    return completed.stdout


def normalize_files(raw: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in raw:
        path = item.strip().replace("\\", "/")
        if not path or path in seen:
            continue
        seen.add(path)
        result.append(path)
    return sorted(result)


def changed_files_from_diff(repo: Path, base: str, target: str | None) -> list[str]:
    args = ["diff", "--name-only", base]
    if target:
        args.append(target)
    return normalize_files(run_git(repo, args).splitlines())


def changed_files_from_worktree(repo: Path) -> list[str]:
    tracked = run_git(repo, ["diff", "--name-only", "HEAD"]).splitlines()
    untracked = run_git(repo, ["ls-files", "--others", "--exclude-standard"], check=False).splitlines()
    return normalize_files([*tracked, *untracked])


def write_patch_for_files(repo: Path, files: list[str], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("", encoding="utf-8")
    if not files:
        return

    tracked_patch = run_git(repo, ["diff", "--binary", "HEAD", "--", *files], check=False)
    output.write_text(tracked_patch, encoding="utf-8")

    tracked_set = set(run_git(repo, ["ls-files", "--", *files], check=False).splitlines())
    untracked = [path for path in files if path not in tracked_set and (repo / path).exists()]
    if not untracked:
        return

    with output.open("a", encoding="utf-8", newline="\n") as handle:
        for path in untracked:
            diff = subprocess.run(
                ["git", "-C", str(repo), "diff", "--no-index", "--", "/dev/null", path],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            ).stdout
            handle.write(diff)


def write_patch_for_refs(repo: Path, base: str, target: str | None, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    args = ["diff", "--binary", base]
    if target:
        args.append(target)
    output.write_text(run_git(repo, args), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Materialize GateKeeper Mode B input.patch and selected_files.txt.")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--base", default="HEAD", help="Base ref for --target or worktree diff (default: HEAD)")
    parser.add_argument("--target", help="Optional target ref; with --base, materializes git diff base target")
    parser.add_argument("--file", action="append", default=[], help="Manager-selected file path; repeatable")
    parser.add_argument("--files-from", type=Path, help="Text file of Manager-selected paths")
    parser.add_argument("--worktree", action="store_true", help="Materialize current worktree diff")
    args = parser.parse_args()

    repo = args.repo.resolve()
    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    selected: list[str] = []
    explicit_files = list(args.file)
    if args.files_from:
        explicit_files.extend(args.files_from.read_text(encoding="utf-8", errors="replace").splitlines())

    modes = sum(bool(x) for x in (explicit_files, args.target, args.worktree))
    if modes != 1:
        raise SystemExit("Choose exactly one delta source: --file/--files-from, --target, or --worktree")

    if explicit_files:
        selected = normalize_files(explicit_files)
        write_patch_for_files(repo, selected, out_dir / "input.patch")
    elif args.target:
        selected = changed_files_from_diff(repo, args.base, args.target)
        write_patch_for_refs(repo, args.base, args.target, out_dir / "input.patch")
    else:
        selected = changed_files_from_worktree(repo)
        write_patch_for_files(repo, selected, out_dir / "input.patch")

    (out_dir / "selected_files.txt").write_text("\n".join(selected) + ("\n" if selected else ""), encoding="utf-8")
    print(f"patch={out_dir / 'input.patch'}")
    print(f"files={out_dir / 'selected_files.txt'}")
    print(f"count={len(selected)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
