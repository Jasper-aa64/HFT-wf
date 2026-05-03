"""
Step 4 — Overclock Mode: Executor Logs → Critic Context
Goal: Critic sees executor output as part of the review context.

This is the complete loop:
  1. Builder writes code
  2. Executor runs code and captures output
  3. Executor output is injected into the conversation
  4. Critic reviews: code + executor logs against checklist
  5. Decision: APPROVE (with evidence) or REJECT (with missing items)

Key insight: Critic must find evidence in the executor logs,
not just read the code and say "looks fine".
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


# ── Executor ─────────────────────────────────────────────────────────────────

class Executor:
    """Runs shell commands and returns structured results."""

    def __init__(self, workdir: Path | None = None):
        self.workdir = workdir or Path.cwd()

    def run(self, command: str, timeout: int = 30) -> dict:
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

    def format_result(self, result: dict) -> str:
        """Format executor result for Critic consumption."""
        lines = [
            "## EXECUTOR OUTPUT",
            f"Exit code: {result['exit_code']}",
            f"Success: {result['success']}",
            "",
        ]
        if result["stdout"]:
            lines.append("### stdout:")
            lines.append("```")
            lines.append(result["stdout"])
            lines.append("```")
            lines.append("")
        if result["stderr"]:
            lines.append("### stderr:")
            lines.append("```")
            lines.append(result["stderr"])
            lines.append("```")
        return "\n".join(lines)


# ── Custom Agent that injects Executor output ─────────────────────────────────

class ExecutorAgent(AssistantAgent):
    """Agent that runs code and injects executor output into conversation."""

    def __init__(self, executor: Executor, **kwargs):
        super().__init__(**kwargs)
        self.executor = executor
        self._last_code: str | None = None

    async def on_messages(self, messages, cancellation_token):
        """Intercept messages, extract code, run it, inject result."""
        # Look for code in the last message
        last_msg = messages[-1] if messages else None
        if last_msg and hasattr(last_msg, "content"):
            content = last_msg.content
            # Extract Python code block
            if "```python" in content:
                start = content.find("```python") + len("```python")
                end = content.find("```", start)
                if end > start:
                    code = content[start:end].strip()
                    self._last_code = code

        # If we have code, run it
        if self._last_code:
            result = self.executor.run_python_code(self._last_code)
            executor_output = self.executor.format_result(result)

            # Return as a message
            from autogen_agentchat.base import Response
            from autogen_agentchat.messages import TextMessage

            return Response(
                chat_message=TextMessage(
                    content=executor_output,
                    source=self.name,
                )
            )

        # No code found, pass through
        return await super().on_messages(messages, cancellation_token)


# ── Prompts ───────────────────────────────────────────────────────────────────

BUILDER_PROMPT = """You are a Python developer.
When given a task, write a complete, runnable Python script.

Your script will be EXECUTED by a machine. Make sure:
- It runs without errors
- It includes test cases that print "PASS" or "FAIL"
- All edge cases from the task are explicitly tested
- Each test prints what it's testing and the result

Output ONLY the Python code block. Format:
```python
# your code here
```
"""

CRITIC_PROMPT = """You did NOT write this code. Your job is to REJECT it unless proven.

## Process

1. First, write a CHECKLIST of what the task requires
2. Read the code
3. Read the EXECUTOR OUTPUT (exit code, stdout, stderr)
4. For each checklist item, find EVIDENCE in the executor output

## Default rule

Exit code != 0 → REJECT
Any test shows FAIL → REJECT
Required test missing → REJECT
Evidence not in stdout → REJECT

## Output format

REJECT:
Missing evidence:
  - <checklist item>: <what's missing or what failed>

APPROVE:
Evidence:
  - <checklist item>: <specific line from stdout proving it passes>

You must cite actual lines from the executor output.
"Looks correct" without evidence = REJECT.
"""


# ── Task ─────────────────────────────────────────────────────────────────────

TASK = """
Write a Python function `median(numbers: list[float]) -> float` that:
- Returns the median of a non-empty list
- Raises ValueError for an empty list
- Does NOT modify the input list

Include test cases that print PASS/FAIL for:
1. median([1, 2, 3]) == 2.0
2. median([1, 2, 3, 4]) == 2.5
3. median([5]) == 5.0
4. median([]) raises ValueError
5. Original list unchanged after call (e.g., [3, 1, 2] stays [3, 1, 2])
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

    # Executor agent that injects logs
    executor_agent = ExecutorAgent(
        name="Executor",
        executor=executor,
        model_client=model,  # Required by parent class but not used for LLM calls
        system_message="I run code and return results.",
    )

    critic = AssistantAgent(
        name="Critic",
        model_client=model,
        system_message=CRITIC_PROMPT,
    )

    termination = TextMentionTermination("APPROVE")

    # Order: Builder → Executor → Critic
    team = RoundRobinGroupChat(
        participants=[builder, executor_agent, critic],
        termination_condition=termination,
        max_turns=9,  # 3 full cycles
    )

    print("=" * 60)
    print("TASK:", TASK.strip())
    print("=" * 60)
    print("FULL LOOP ACTIVE")
    print("  Builder writes code")
    print("  Executor runs it and returns logs")
    print("  Critic reviews code + logs against checklist")
    print("=" * 60)

    await Console(team.run_stream(task=TASK))

    print("\n" + "=" * 60)
    print("Done. What to observe:")
    print("  - Did Executor inject exit code and stdout?")
    print("  - Did Critic cite specific lines from stdout?")
    print("  - Did Critic reject when evidence was missing?")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
