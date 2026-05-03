"""
Step 1 — Overclock Mode: AutoGen Reflection (minimal)
Goal: see Builder/Critic message types in your terminal. Do not modify anything.

Requires:
  pip install "autogen-agentchat>=0.4" "autogen-ext[openai]"
  export ANTHROPIC_API_KEY=sk-...   (or OPENAI_API_KEY)

What to observe:
  1. Builder writes code
  2. Critic responds with APPROVE or REJECT + reasons
  3. If REJECT, Builder tries again
  4. Loop stops when Critic approves or max turns hit

This is the skeleton. Step 2 will replace the Critic prompt with a
default-reject version. Step 3 will add a real Executor.
"""

import asyncio
import os
from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.conditions import TextMentionTermination
from autogen_agentchat.teams import RoundRobinGroupChat
from autogen_agentchat.ui import Console
from autogen_ext.models.anthropic import AnthropicChatCompletionClient
# from autogen_ext.models.openai import OpenAIChatCompletionClient  # alternative


# ── Model ────────────────────────────────────────────────────────────────────

def get_model():
    """Use Anthropic if key present, otherwise switch to OpenAI."""
    if os.getenv("ANTHROPIC_API_KEY"):
        return AnthropicChatCompletionClient(model="claude-3-5-haiku-20241022")
    raise EnvironmentError(
        "Set ANTHROPIC_API_KEY (or swap to OpenAIChatCompletionClient)"
    )


# ── Agents ───────────────────────────────────────────────────────────────────

BUILDER_PROMPT = """You are a Python developer.
When given a task, write a short, clean Python function that solves it.
Output ONLY the code block. No explanation."""

# Step 2 will replace this with a default-reject prompt.
CRITIC_PROMPT = """You are a code reviewer. You did NOT write this code.
Your job is to find problems, not to validate.

Review the code for:
1. Correctness — does it actually solve the task?
2. Edge cases — what inputs would break it?
3. Style — is it readable?

If you find ANY issue, respond with:
  REJECT
  Reason: <specific reason>

Only if every check passes, respond with:
  APPROVE
  Confirmed: <what specifically works>

Default to REJECT if in doubt. One word approval without evidence is not allowed."""


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

    # Stop when Critic says APPROVE
    termination = TextMentionTermination("APPROVE")

    team = RoundRobinGroupChat(
        participants=[builder, critic],
        termination_condition=termination,
        max_turns=6,  # hard ceiling: 3 builder turns + 3 critic turns
    )

    print("=" * 60)
    print("TASK:", TASK.strip())
    print("=" * 60)

    # Stream messages to terminal so you can watch the exchange
    await Console(team.run_stream(task=TASK))

    print("\n" + "=" * 60)
    print("Done. What to notice:")
    print("  - Did Critic reject on first pass?")
    print("  - Did Builder improve after rejection?")
    print("  - What would a DEFAULT-REJECT prompt change? (Step 2)")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
