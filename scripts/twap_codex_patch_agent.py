#!/usr/bin/env python3
"""Codex patch-command wrapper for TWAP optimization candidates.

The auto-loop calls this script inside an isolated candidate workspace. The
script builds a TWAP-specific prompt and lets `codex exec` edit only that
workspace. The harness, not this script, decides build/correctness/timing.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


def load_context() -> dict[str, Any]:
    metadata_raw = os.environ.get("CANDIDATE_METADATA_JSON", "{}")
    try:
        metadata = json.loads(metadata_raw)
    except json.JSONDecodeError:
        metadata = {}
    return {
        "candidate_id": os.environ.get("CANDIDATE_ID", ""),
        "lane": os.environ.get("CANDIDATE_LANE", ""),
        "target": os.environ.get("CANDIDATE_TARGET", ""),
        "touched_files": [
            value for value in os.environ.get("CANDIDATE_TOUCHED_FILES", "").split("|") if value
        ],
        "workspace": os.environ.get("CANDIDATE_WORKSPACE", ""),
        "run_dir": os.environ.get("RUN_DIR", ""),
        "iteration": os.environ.get("ITERATION", ""),
        "hypothesis": metadata.get("hypothesis", ""),
        "expected_effect": metadata.get("expected_effect", ""),
        "semantic_risk": metadata.get("semantic_risk", ""),
        "source_evidence": metadata.get("source_evidence", {}),
    }


def focus_terms(ctx: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    source = ctx.get("source_evidence") if isinstance(ctx.get("source_evidence"), dict) else {}
    for value in source.get("symbols", []) or []:
        if isinstance(value, str) and value.strip():
            terms.append(value.strip())
    for key in ("target", "hypothesis"):
        for token in re.split(r"[^A-Za-z0-9_]+", str(ctx.get(key, ""))):
            if len(token) >= 8:
                terms.append(token)
    return list(dict.fromkeys(terms))


def extract_windows(text: str, terms: list[str], *, radius: int = 90, max_lines: int = 900) -> str:
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    matched: list[int] = []
    for index, line in enumerate(lines):
        if any(term in line for term in terms):
            matched.append(index)
    if not matched:
        return "\n".join(lines[:max_lines]) + f"\n// ... truncated {len(lines) - max_lines} lines"

    windows: list[tuple[int, int]] = []
    for index in matched:
        start = max(0, index - radius)
        end = min(len(lines), index + radius + 1)
        if windows and start <= windows[-1][1]:
            windows[-1] = (windows[-1][0], max(windows[-1][1], end))
        else:
            windows.append((start, end))

    out: list[str] = []
    emitted = 0
    for start, end in windows:
        if emitted >= max_lines:
            break
        if out:
            out.append("// ...")
        chunk = lines[start:end]
        remaining = max_lines - emitted
        out.extend(chunk[:remaining])
        emitted += min(len(chunk), remaining)
    if emitted < len(lines):
        out.append(f"// ... focused excerpt from {len(lines)} total lines")
    return "\n".join(out)


def read_focus_files(workspace: Path, ctx: dict[str, Any]) -> str:
    blocks: list[str] = []
    terms = focus_terms(ctx)
    for rel in ctx["touched_files"][:6]:
        path = workspace / rel
        if not path.exists() or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        text = extract_windows(text, terms)
        blocks.append(f"## {rel}\n```cpp\n{text}\n```")
    return "\n\n".join(blocks)


def build_prompt(ctx: dict[str, Any], focus_text: str) -> str:
    return f"""You are editing a C++ TWAP sell service candidate workspace.

Hard boundaries:
- Modify only files under this workspace.
- Do not commit.
- Do not edit generated protobuf files.
- Do not edit tests, benchmark tools, configs, schemas, or output artifacts.
- Keep the public gRPC/proto interface and JSON payload semantics unchanged.
- Preserve accountDesc, subPositionInfoList, 0-position push behavior, and searchStockCode filtering behavior.
- Make a minimal optimization patch only. If the candidate is unsafe, leave files unchanged.
- Do not cache or reuse TwapSalePushMessage across different client sessions, requests, or userIds.
- Do not turn per-user/per-request buildTwapSaleAggregationPushMessage calls into a shared cached message unless the prompt explicitly proves the payload is user-independent.
- Do not rewrite the aggregation dataflow shape (for example, replacing collect-then-aggregate with scan-and-aggregate) for a low-risk candidate.

Candidate:
- id: {ctx['candidate_id']}
- lane: {ctx['lane']}
- target: {ctx['target']}
- hypothesis: {ctx['hypothesis']}
- expected effect: {ctx['expected_effect']}
- semantic risk: {ctx['semantic_risk']}
- touched files: {ctx['touched_files']}
- source evidence: {json.dumps(ctx['source_evidence'], ensure_ascii=False)}

Implementation guidance:
- Focus on the TWAP aggregation position push path.
- Prefer removing redundant cache lookups and avoidable reallocations.
- Do not introduce new shared mutable state.
- Do not change behavior to query the database in push path.
- Do not move userId-dependent JSON construction outside the user/request-specific branch.
- Safe examples: reserve vector/JSON array capacity, remove a proven duplicate lookup, reuse a local key string for the same account+stock.
- Unsafe examples: cross-user push message cache, global aggregation cache, changing subscription filtering semantics, or broad loop restructuring.
- Do not broaden the patch outside the candidate's touched files.

Relevant source excerpts:
{focus_text}

Now edit the workspace files directly. Keep the final answer short."""


def main() -> int:
    ctx = load_context()
    workspace = Path(ctx["workspace"]).resolve()
    if not workspace.exists():
        print(f"ERROR workspace does not exist: {workspace}", file=sys.stderr)
        return 1
    if not ctx["touched_files"]:
        print("ERROR no touched files in candidate metadata", file=sys.stderr)
        return 1

    focus_text = read_focus_files(workspace, ctx)
    if not focus_text:
        print("ERROR no readable focus files", file=sys.stderr)
        return 1

    prompt = build_prompt(ctx, focus_text)
    run_dir = Path(ctx["run_dir"]).resolve() if ctx["run_dir"] else workspace
    log_dir = run_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = log_dir / f"twap_codex_prompt_{ctx['candidate_id']}.md"
    output_path = log_dir / f"twap_codex_output_{ctx['candidate_id']}.txt"
    prompt_path.write_text(prompt, encoding="utf-8")

    codex_bin = shutil.which("codex.cmd") or shutil.which("codex.exe") or shutil.which("codex")
    if not codex_bin:
        print("ERROR codex CLI not found on PATH", file=sys.stderr)
        return 1

    command = [
        codex_bin,
        "exec",
        "--cd",
        str(workspace),
        "--sandbox",
        "workspace-write",
        "--output-last-message",
        str(output_path),
        "-",
    ]
    timeout_seconds = int(os.environ.get("TWAP_CODEX_PATCH_TIMEOUT", "900"))
    completed = subprocess.run(
        command,
        input=prompt,
        cwd=str(workspace),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
        check=False,
    )
    log_path = log_dir / f"twap_codex_exec_{ctx['candidate_id']}.log"
    log_path.write_text(completed.stdout or "", encoding="utf-8")
    if completed.returncode != 0:
        print(f"ERROR codex exec failed rc={completed.returncode}; see {log_path}", file=sys.stderr)
        return completed.returncode

    print(f"twap_codex_patch_agent completed; prompt={prompt_path}; output={output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
