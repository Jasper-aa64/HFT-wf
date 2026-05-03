"""
Step 2 — Overclock Mode: Default-Reject Critic
Goal: Replace the friendly reviewer with a skeptic who defaults to REJECT.

Key change from Step 1:
  - Critic writes a CHECKLIST first, before reviewing any code
  - Critic requires EVIDENCE for each checklist item
  - Missing evidence = REJECT (not "looks good to me")

This is the core adversarial posture. Step 3 will add a real Executor.
"""

import asyncio
import os
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


# ── Agents ───────────────────────────────────────────────────────────────────

BUILDER_PROMPT = """You are a Python developer.
When given a task, write a short, clean Python function that solves it.
Output ONLY the code block. No explanation.

If your previous attempt was rejected, read the rejection reasons carefully
and fix ALL issues mentioned. Do not ignore feedback."""

# Default-reject Critic prompt — the key difference
CRITIC_PROMPT = """You did NOT write this code. Your job is to REJECT it unless it proves itself.

## Before reviewing

First, write a CHECKLIST of what must be proven. Example format:

Task: <brief>
Checklist:
  [ ] Code changes only what the task requires
  [ ] Edge cases are handled
  [ ] No unnecessary complexity added
  [ ] Tests cover risky behavior

## Review process

Compare:
1. The original task brief
2. The code diff
3. The checklist you wrote

## Default rule

Evidence missing = REJECT.
Unclear coverage = REJECT.
"Looks fine" without proof = REJECT.

## Output format

If ANY checklist item lacks evidence:

REJECT
Missing evidence:
  - <item>: <what's missing>

Only if EVERY checklist item has concrete evidence in the code:

APPROVE
Evidence:
  - <item>: <specific line/behavior that proves it>

Do NOT approve based on reading alone. Each approval must cite specific evidence."""


# ── Task ─────────────────────────────────────────────────────────────────────

TASK = """
Write a Python function `median(numbers: list[float]) -> float` that:
- Returns the median of a non-empty list
- Raises ValueError for an empty list
- Does NOT modify the input list
"""


# ── Main ─────────────────────────────────────────────────────────────────────

async def main():
    model = get_model()

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
    print("DEFAULT-REJECT CRITIC ACTIVE")
    print("Watch for: Does Critic write a checklist first?")
    print("=" * 60)

    await Console(team.run_stream(task=TASK))

    print("\n" + "=" * 60)
    print("Done. What to notice:")
    print("  - Did Critic write a checklist before reviewing?")
    print("  - Did Critic demand specific evidence?")
    print("  - Did Builder have to work harder to get APPROVE?")
    print("  - Next: Step 3 adds real Executor to provide evidence")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
