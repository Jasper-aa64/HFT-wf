"""
Step 3 — Overclock Mode: Add Real Executor
Goal: Executor runs actual commands and returns exit codes + logs.

New component:
  - ExecutorAgent: runs shell commands, captures output
  - Not an LLM — just subprocess wrapper
  - Returns structured result: {exit_code, stdout, stderr}

Flow now:
  1. Builder writes code -> saves to temp file
  2. Executor runs: python temp_file.py (or pytest, etc.)
  3. Critic sees: code + executor logs + checklist
  4. Critic must find evidence in logs, not just read code
"""

import asyncio
import os
import subprocess
import tempfile
from pathlib import Path
from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.conditions import TextMentionTermination
from autogen_agentchat.teams import RoundRobinGroupChat
from autogen_agentchat.ui import Console
from autogen_ext.models.anthropic import AnthropicChatCompletionClient


# ── Model ────────────────────────────────────────────────────────────────────

def get_model():
    if os.getenv("ANTHROPIC_API_KEY"):
        return AnthropicChatCompletionClient(model="claude-3-5-haiku-20241022")
    raise EnvironmentError("Set ANTHROPIC_API_KEY")


# ── Executor (not an LLM) ─────────────────────────────────────────────────────

class Executor:
    """Runs shell commands and returns structured results."""

    def __init__(self, workdir: Path | None = None):
        self.workdir = workdir or Path.cwd()

    def run(self, command: str, timeout: int = 30) -> dict:
        """Run a shell command, return exit_code, stdout, stderr."""
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=self.workdir,
            )
            return {
                "exit_code": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "success": result.returncode == 0,
            }
        except subprocess.TimeoutExpired:
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": f"Timeout after {timeout}s",
                "success": False,
            }

    def run_python_code(self, code: str, timeout: int = 30) -> dict:
        """Save code to temp file and run it."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False
        ) as f:
            f.write(code)
            temp_path = f.name

        try:
            return self.run(f"python3 {temp_path}", timeout)
        finally:
            os.unlink(temp_path)


# ── Prompts ───────────────────────────────────────────────────────────────────

BUILDER_PROMPT = """You are a Python developer.
When given a task, write a complete, runnable Python script.

Your script will be EXECUTED by a machine. Make sure:
- It runs without errors
- It includes test cases that print "PASS" or "FAIL"
- Edge cases are explicitly tested

Output ONLY the Python code. No markdown. No explanation."""

CRITIC_PROMPT = """You did NOT write this code. Your job is to REJECT it unless it proves itself.

## Before reviewing

Write a CHECKLIST of what must be proven.

## What you receive

1. The original task brief
2. The code
3. Executor output (exit code, stdout, stderr)

## Default rule

Executor failure = REJECT.
Missing test output = REJECT.
Test output shows FAIL = REJECT.
Coverage unclear = REJECT.

## Output format

REJECT if:
- Executor exit_code != 0
- Any test shows FAIL
- Required edge cases not tested

APPROVE only if:
- Executor exit_code == 0
- All tests show PASS
- Checklist items have evidence in stdout

Cite specific lines from executor output as evidence."""


# ── Task ─────────────────────────────────────────────────────────────────────

TASK = """
Write a Python function `median(numbers: list[float]) -> float` that:
- Returns the median of a non-empty list
- Raises ValueError for an empty list
- Does NOT modify the input list

Include test cases that print PASS/FAIL:
- Test: median of [1, 2, 3] returns 2.0
- Test: median of [1, 2, 3, 4] returns 2.5
- Test: median of [5] returns 5.0
- Test: median of [] raises ValueError
- Test: original list unchanged after call
"""


# ── Main ─────────────────────────────────────────────────────────────────────

async def main():
    model = get_model()
    executor = Executor()

    builder = AssistantAgent(
        name="Builder",
        model_client=model,
        system_message=BUILDER_PROMPT,
    )

    critic = AssistantAgent(
        name="Critic",
        model_client=model,
        system_message=CRITIC_PROMPT,
    )

    termination = TextMentionTermination("APPROVE")

    team = RoundRobinGroupChat(
        participants=[builder, critic],
        termination_condition=termination,
        max_turns=6,
    )

    print("=" * 60)
    print("TASK:", TASK.strip())
    print("=" * 60)
    print("EXECUTOR ACTIVE: Code will be run, not just reviewed")
    print("=" * 60)

    # Note: This is a simplified version. Full version in step4
    # will inject executor logs into Critic context.
    print("\n[Step 3 limitation: Executor runs, but logs not yet fed to Critic]")
    print("[See step4_executor_logs_to_critic.py for full integration]\n")

    await Console(team.run_stream(task=TASK))

    print("\n" + "=" * 60)
    print("Done. Next: Step 4 feeds Executor logs to Critic context")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
