# Trellis 实践手册

Source: <https://docs.trytrellis.app/zh>  
Version: 0.5.x · 2026-05

---

## 一句话

Trellis 是 AGENTS.md 的进化版：它不只给 AI 一张地图，还把"地图自动注入、会话状态持久、任务追踪、规则自更新"全部做成了工程基础设施。

> AGENTS.md = 静态地图  
> Trellis = 动态框架（自动注入 + 持久记忆 + 工作流）

---

## 安装

```bash
npm install -g @mindfoldhq/trellis@rc

# 在项目根目录初始化
trellis init                        # 基础初始化，自动检测 git 用户名
trellis init --cursor               # 同时支持 Cursor
trellis init -t electron-fullstack  # 使用内置模板
```

支持平台：Claude Code, Cursor, Codex, Gemini CLI, GitHub Copilot, Kiro, Windsurf, OpenCode, 以及任何读取 `.agents/skills/` 的 agent。

---

## 目录结构

```
project-root/
├── AGENTS.md                     ← 可选；Trellis 通过 hooks 自动注入 spec，不强依赖此文件
├── .trellis/
│   ├── workflow.md               ← 工作流说明
│   ├── spec/                     ← 编码规范（核心）
│   │   ├── frontend/
│   │   ├── backend/
│   │   └── guides/
│   ├── workspace/
│   │   └── {name}/               ← 每个开发者独立的会话日志
│   └── tasks/                    ← 任务追踪文件
└── .claude/                      ← Claude Code 专用
    ├── hooks/                    ← session-start, inject-workflow-state...
    └── skills/                   ← 自定义 skill 文件夹
```

**关键**：`.trellis/workspace/{name}/` 按开发者隔离，`gitignore` 掉 `.trellis/.developer`，多人协作不冲突。

---

## 五个核心概念

| 概念 | 位置 | 作用 |
|---|---|---|
| **Spec** | `.trellis/spec/` | Markdown 编码规范；AI 编码前自动读取 |
| **Task** | `.trellis/tasks/` | 工作单元：需求 + 上下文 + 进度 |
| **Workspace** | `.trellis/workspace/{name}/` | 跨会话记忆：日志、进展、决策 |
| **Skill** | `.claude/skills/` 等 | 自动触发的工作流模块（不需要手动调用） |
| **Hook** | `.claude/hooks/` 等 | session 启动时自动注入 spec + workspace |

---

## 日常工作流

```
开始新会话
  → Hook 自动注入 spec + 上次 workspace 状态
  → /start 或 brainstorm skill 自动触发

开发阶段
  → before-dev skill：任务拆解 + 确认理解
  → 编码（AI 按 spec 来）
  → check skill：自动校验是否符合规范

完成阶段
  → /finish-work 或 update-spec skill：把本次学到的好实践写回 spec
  → workspace 日志更新，下次会话可用
```

**重点**：大部分 skill 自动触发，不需要手动敲命令。AI 匹配你的意图后自动调用对应 skill。

---

## 写好 Spec 的规则

Spec 是 Trellis 的核心资产，质量决定 AI 输出质量。

**放什么：**
- 违反会出 bug 的规则（不是建议，是约束）
- 带具体例子的规范（错误示例 vs 正确示例）
- 架构边界和禁止的依赖关系

**不放什么：**
- 通用常识（AI 已知）
- 会频繁变化的细节（放 task 里）
- 超过需要的背景知识

**格式建议：**

```markdown
## Rule: [规则名]

**Condition**: 什么情况下适用  
**Requirement**: 具体要求是什么  
**Why**: 为什么这个规则存在  

✅ Good:
[正确示例代码]

❌ Bad:
[错误示例代码]
```

**坏案例驱动更新**：AI 犯了一个错 → 找到根因 → 写进对应 spec → 下次自动避免。

---

## 内置 Skill 速查

| Skill | 触发时机 | 做什么 |
|---|---|---|
| `brainstorm` | 新任务开始 | 需求拆解、提问、确认理解 |
| `before-dev` | 开始写代码前 | 确认计划、标记潜在风险 |
| `check` | 代码完成后 | 对照 spec 做合规校验 |
| `update-spec` | 发现新规律后 | 把好实践写回 spec |
| `break-loop` | AI 卡死/循环错误时 | 根因分析 + 退出死循环 |

自定义 skill 只需在 `.claude/skills/my-skill/` 下放一个 `SKILL.md`，`description` 字段写**触发条件**（不是 skill 名字）。

---

## Claude Code 专用说明

Trellis 对 Claude Code 的集成最完整：

```
.claude/
├── hooks/
│   ├── session-start.py          # 会话开始时自动注入 spec
│   ├── inject-workflow-state.py  # 注入 workspace 状态
│   └── inject-subagent-context.py # sub-agent 调用时注入上下文
└── skills/                       # 自动触发的 skill 文件夹
```

Sub-agent 支持：`trellis-research`（调研）、`trellis-implement`（实现）、`trellis-check`（验收），Claude Code 通过 Task tool 调用。

---

## Trellis vs 只用 AGENTS.md

| 场景 | 选择 |
|---|---|
| 小项目、单人、快速上手 | 只用 `AGENTS.md` |
| 多人团队，规范需要版本管理和共享 | Trellis |
| 需要跨会话记忆（AI 记住上次在做什么） | Trellis |
| 希望工作流自动化（不用手动喂上下文） | Trellis |
| 工作流已稳定，只需要守规矩 | `AGENTS.md` 够用 |

简单说：**AGENTS.md 是静态文件；Trellis 是把 AGENTS.md 活化的运行时。**

---

## References

- Trellis Official Docs: <https://docs.trytrellis.app/zh>
- Multi-Platform Configuration: <https://docs.trytrellis.app/guide/ch13-multi-platform>
- Custom Skills: <https://docs.trytrellis.app/advanced/custom-skills>
