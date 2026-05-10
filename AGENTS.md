<!-- TRELLIS:START -->
# Trellis Instructions

These instructions are for AI assistants working in this project.

This project is managed by Trellis. The working knowledge you need lives under `.trellis/`:

- `.trellis/workflow.md` — development phases, when to create tasks, skill routing
- `.trellis/spec/` — package- and layer-scoped coding guidelines (read before writing code in a given layer)
- `.trellis/workspace/` — per-developer journals and session traces
- `.trellis/tasks/` — active and archived tasks (PRDs, research, jsonl context)

If a Trellis command is available on your platform (e.g. `/trellis:finish-work`, `/trellis:continue`), prefer it over manual steps. Not every platform exposes every command.

If you're using Codex or another agent-capable tool, additional project-scoped helpers may live in:
- `.agents/skills/` — reusable Trellis skills
- `.codex/agents/` — optional custom subagents

## Subagents

- Track all subagents to terminal status before using their results.
- On Codex, prefer visible status updates and short checks; do not let one long synchronous wait block the user conversation.
- Spawn subagents automatically when:
  - Parallelizable work (e.g., install + verify, npm test + typecheck, multiple tasks from plan)
  - Long-running or blocking tasks where a worker can run independently.
  - Isolation for risky changes or checks

Managed by Trellis. Edits outside this block are preserved; edits inside may be overwritten by a future `trellis update`.

<!-- TRELLIS:END -->

---

# Quant Workspace

Personal workspace for quant engineering, agentic workflows, and technical blog notes.

## Project Map

| Directory | Purpose |
|---|---|
| `01-博客与资料/` | Blog notes and article summaries |
| `02-Agent-Driven Workflow/01. Agent-Driven Workflow.md` | GateKeeper Mode, autonomous optimization loop, agent review patterns |
| `cpp-trader-backtester-main/` | C++ low-latency order book backtester |
| `.trellis/spec/` | Reusable specs for AI-generated artifacts |

## Key Specs

- **Illustration style**: `.trellis/spec/guides/illustration-style.md`  
  Hand-drawn notebook aesthetic. No dark SaaS gradients. Read before generating any blog visual.
- **Remote execution workspace topology**: `.trellis/spec/guides/remote-execution-workflow.md`  
  Keep the local Trellis + project mirror as the control plane, and use the remote machine as the execution plane.

## Documentation Rules

- Write all project notes and specs in English.
- Keep source references at the end when summarizing an external article.
- "Map, not manual" — short navigation in top-level docs, details in linked files.
