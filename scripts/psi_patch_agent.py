#!/usr/bin/env python3
"""Patch agent for the Psi headless auto-loop.

This script is the external --patch-command that the auto-loop invokes to
generate real source code modifications in a candidate workspace. It bridges
the harness (which handles build/compare/timing/verdict) with an LLM agent
(which generates the actual optimization code).

Interface contract (called by psi_headless_auto_loop.py):
  - Receives candidate context via PSI_* environment variables
  - Modifies files in PSI_CANDIDATE_WORKSPACE
  - Exits 0 if changes were made, non-zero otherwise

Environment variables consumed:
  PSI_CANDIDATE_ID           - unique candidate identifier
  PSI_CANDIDATE_LANE         - evidence / insight / combination
  PSI_CANDIDATE_TARGET       - stage or function to optimize
  PSI_CANDIDATE_TOUCHED_FILES - pipe-separated predicted files
  PSI_CANDIDATE_METADATA_JSON - full candidate dict as JSON
  PSI_CANDIDATE_WORKSPACE    - path to the isolated workspace
  PSI_SOURCE_ROOT            - original source tree (read-only reference)
  PSI_RUN_DIR                - run root for logs/artifacts
  PSI_ITERATION              - current iteration number

Configuration:
  PSI_PATCH_AGENT_MODE       - "api" (default), "cli", or "template"
  ANTHROPIC_API_KEY          - required for "api" mode
  PSI_PATCH_AGENT_MODEL      - model to use (default: claude-sonnet-4-6)
  PSI_PATCH_AGENT_MAX_TOKENS - max tokens for response (default: 4096)
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


def load_candidate_context() -> dict[str, Any]:
    metadata_json = os.environ.get("PSI_CANDIDATE_METADATA_JSON", "{}")
    try:
        metadata = json.loads(metadata_json)
    except json.JSONDecodeError:
        metadata = {}

    return {
        "candidate_id": os.environ.get("PSI_CANDIDATE_ID", ""),
        "lane": os.environ.get("PSI_CANDIDATE_LANE", ""),
        "target": os.environ.get("PSI_CANDIDATE_TARGET", ""),
        "touched_files": [
            f for f in os.environ.get("PSI_CANDIDATE_TOUCHED_FILES", "").split("|") if f
        ],
        "workspace": os.environ.get("PSI_CANDIDATE_WORKSPACE", ""),
        "source_root": os.environ.get("PSI_SOURCE_ROOT", ""),
        "run_dir": os.environ.get("PSI_RUN_DIR", ""),
        "iteration": os.environ.get("PSI_ITERATION", "0"),
        "hypothesis": metadata.get("hypothesis", ""),
        "expected_effect": metadata.get("expected_effect", ""),
        "semantic_risk": metadata.get("semantic_risk", ""),
        "source_evidence": metadata.get("source_evidence", {}),
    }


def find_relevant_sources(workspace: Path, target: str, touched_files: list[str]) -> dict[str, str]:
    """Find and read source files relevant to the optimization target."""
    sources: dict[str, str] = {}

    cpp_extensions = {".cpp", ".h", ".hpp", ".cc", ".cxx"}
    candidates: list[Path] = []

    for touched in touched_files:
        path = workspace / touched
        if path.exists() and path.suffix in cpp_extensions:
            candidates.append(path)

    if not candidates:
        target_clean = target.replace(".", "/").replace("::", "/")
        for ext in cpp_extensions:
            for match in workspace.rglob(f"*{ext}"):
                if any(part in str(match) for part in target_clean.split("/")):
                    candidates.append(match)

    if not candidates:
        for ext in cpp_extensions:
            for match in workspace.rglob(f"*{ext}"):
                candidates.append(match)
                if len(candidates) > 20:
                    break
            if len(candidates) > 20:
                break

    for path in candidates[:10]:
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            rel = str(path.relative_to(workspace)).replace("\\", "/")
            sources[rel] = content
        except (OSError, ValueError):
            continue

    return sources


def build_optimization_prompt(ctx: dict[str, Any], sources: dict[str, str]) -> str:
    """Build the prompt that asks the LLM to generate optimized code."""

    source_blocks = []
    for path, content in sources.items():
        lines = content.split("\n")
        if len(lines) > 500:
            content = "\n".join(lines[:500]) + f"\n// ... ({len(lines) - 500} more lines)"
        source_blocks.append(f"### {path}\n```cpp\n{content}\n```")

    sources_text = "\n\n".join(source_blocks)

    return f"""You are a C++ performance optimization expert working on a high-frequency trading system called Psi.

## Task
Generate an optimized version of the target code. The optimization must be:
1. Semantically equivalent (same output for same input)
2. Focused on reducing wall-clock time
3. Minimal in scope (touch as few lines as possible)

## Candidate Context
- Target: {ctx['target']}
- Hypothesis: {ctx['hypothesis']}
- Expected effect: {ctx['expected_effect']}
- Semantic risk: {ctx['semantic_risk']}
- Lane: {ctx['lane']}

## Source Evidence
{json.dumps(ctx.get('source_evidence', {}), indent=2)}

## Source Files
{sources_text}

## Output Format
For each file you modify, output a block in this exact format:

```patch_file:<relative/path/to/file>
<complete new file content>
```

Rules:
- Output the COMPLETE file content, not a diff
- Only output files you actually changed
- Do NOT add comments explaining the optimization
- Do NOT change function signatures or public interfaces
- Do NOT change output behavior
- Focus on: branch prediction, cache locality, avoiding redundant computation, loop optimization, avoiding allocations in hot paths
- If you cannot find a valid optimization, output nothing

Generate the optimized code now."""


def parse_llm_response(response: str) -> dict[str, str]:
    """Parse the LLM response into file path -> content pairs."""
    patches: dict[str, str] = {}

    pattern = r"```patch_file:([^\n]+)\n(.*?)```"
    for match in re.finditer(pattern, response, re.DOTALL):
        path = match.group(1).strip()
        content = match.group(2)
        if path and content.strip():
            patches[path] = content

    return patches


def call_anthropic_api(prompt: str) -> str | None:
    """Call the Anthropic API directly using the SDK."""
    try:
        import anthropic
    except ImportError:
        print("ERROR: anthropic SDK not installed. pip install anthropic", file=sys.stderr)
        return None

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set", file=sys.stderr)
        return None

    model = os.environ.get("PSI_PATCH_AGENT_MODEL", "claude-sonnet-4-6")
    max_tokens = int(os.environ.get("PSI_PATCH_AGENT_MAX_TOKENS", "4096"))

    try:
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text if message.content else None
    except Exception as exc:
        print(f"ERROR: Anthropic API call failed: {exc}", file=sys.stderr)
        return None


def call_claude_cli(prompt: str, workspace: Path) -> str | None:
    """Call claude CLI as a fallback."""
    prompt_file = workspace / ".psi_patch_prompt.txt"
    prompt_file.write_text(prompt, encoding="utf-8")

    try:
        result = subprocess.run(
            ["claude", "--print", "--no-input", "-p", prompt],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        print(f"ERROR: claude CLI failed: {exc}", file=sys.stderr)
    finally:
        prompt_file.unlink(missing_ok=True)

    return None


def apply_patches(workspace: Path, patches: dict[str, str]) -> list[str]:
    """Write patched files to the workspace. Returns list of modified paths."""
    modified: list[str] = []
    for rel_path, content in patches.items():
        target = workspace / rel_path
        if not target.exists():
            continue
        original = target.read_text(encoding="utf-8", errors="replace")
        if content.strip() == original.strip():
            continue
        target.write_text(content, encoding="utf-8")
        modified.append(rel_path)
    return modified


def main() -> int:
    ctx = load_candidate_context()
    workspace = Path(ctx["workspace"])

    if not workspace.exists():
        print(f"ERROR: workspace does not exist: {workspace}", file=sys.stderr)
        return 1

    print(f"patch_agent: candidate={ctx['candidate_id']} target={ctx['target']} lane={ctx['lane']}")

    sources = find_relevant_sources(workspace, ctx["target"], ctx["touched_files"])
    if not sources:
        print("ERROR: no relevant source files found in workspace", file=sys.stderr)
        return 1

    print(f"patch_agent: found {len(sources)} source files")

    prompt = build_optimization_prompt(ctx, sources)

    mode = os.environ.get("PSI_PATCH_AGENT_MODE", "api")
    response: str | None = None

    if mode == "api":
        response = call_anthropic_api(prompt)
        if response is None and mode == "api":
            print("patch_agent: API mode failed, trying CLI fallback", file=sys.stderr)
            response = call_claude_cli(prompt, workspace)
    elif mode == "cli":
        response = call_claude_cli(prompt, workspace)
    elif mode == "template":
        print("ERROR: template mode not yet implemented", file=sys.stderr)
        return 1
    else:
        print(f"ERROR: unknown PSI_PATCH_AGENT_MODE={mode}", file=sys.stderr)
        return 1

    if not response:
        print("ERROR: no response from LLM", file=sys.stderr)
        return 1

    patches = parse_llm_response(response)
    if not patches:
        print("ERROR: LLM response contained no valid patches", file=sys.stderr)
        log_dir = Path(ctx["run_dir"]) / "logs" if ctx["run_dir"] else workspace
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / f"patch_agent_raw_{ctx['candidate_id']}.txt").write_text(
            response, encoding="utf-8"
        )
        return 1

    modified = apply_patches(workspace, patches)
    if not modified:
        print("ERROR: patches parsed but no files were actually modified", file=sys.stderr)
        return 1

    print(f"patch_agent: modified {len(modified)} files: {', '.join(modified)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
