# 一个文件让 AI Coding 效率翻倍：AGENTS.md 实践指南

2026-04-192026-04-19[AI](https://www.cnkirito.moe/categories/AI/)1 小时读完 (大约9019个字)

![AGENTS.md 编写指南](https://image.cnkirito.cn/agentsmd-infographic-01.png)

[AGENTS.md 编写指南](https://image.cnkirito.cn/agentsmd-infographic-01.png)

## [前言](https://www.cnkirito.moe/ai-agents-md-practise/\#%E5%89%8D%E8%A8%80 "前言") 前言

这是我”AI Coding 经验总结”系列的第五篇文章。前几篇聊的是工具选型、工作流范式、Harness Engineering 这些偏宏观的话题，这篇回到一个更具体的问题： **怎么写好一份 AGENTS.md？**

「在代码仓库中放一份上下文文件，告诉 AI 工具这个项目是什么、怎么构建、有什么规矩」——这个做法现在已经有了一个统一的名字：AGENTS.md。在展开实践之前，先花一点篇幅介绍它的前世今生，已经了解的同学可以跳过。

## [AGENTS.md 是什么？](https://www.cnkirito.moe/ai-agents-md-practise/\#AGENTS-md-%E6%98%AF%E4%BB%80%E4%B9%88%EF%BC%9F "AGENTS.md 是什么？") AGENTS.md 是什么？

AGENTS.md 是一个简单的开放格式，用于指导 AI Coding Agent 在你的项目中工作。你可以把它理解为 **给 AI 看的 README**——README.md 是给人类看的项目说明，AGENTS.md 则是给 AI Agent 看的项目指令，包含构建命令、编码规范、测试要求、安全注意事项等 AI 需要知道的上下文。

官方建议的使用方式很简单：

1. 在仓库根目录创建一个 `AGENTS.md` 文件
2. 写上对 Agent 有用的内容：项目概述、构建测试命令、代码风格、安全注意事项
3. 补充额外指引：commit 规范、部署步骤、安全陷阱——任何你会告诉项目新成员的东西
4. 大型 monorepo 可以在子目录放嵌套的 AGENTS.md，Agent 会读最近的那个（OpenAI 自己的仓库有 88 个 AGENTS.md）

格式上没有任何强制要求，就是标准的 Markdown，用什么标题、写什么内容完全自由。

### [前世今生](https://www.cnkirito.moe/ai-agents-md-practise/\#%E5%89%8D%E4%B8%96%E4%BB%8A%E7%94%9F "前世今生") 前世今生

这个概念最早由 Anthropic 通过 Claude Code 的 **CLAUDE.md** 普及。Claude Code 运行时会自动加载当前目录下的 CLAUDE.md，把内容注入到发给模型的请求中。这个设计简单而有效——维护好一份上下文文件，Agent 的表现就会变好；表现变好了，你就更愿意用它，进而更愿意维护这份文件，形成正向循环。

随后各家 AI Coding 工具跟进了自己的版本，一度各自为政：

| 工具 | 上下文文件 |
| --- | --- |
| Claude Code | `CLAUDE.md` |
| Cursor | `.cursorrules` / `.cursor/rules` |
| Copilot | `.github/copilot-instructions.md` |
| Gemini CLI | `GEMINI.md` |
| Cline | `.clinerules` |
| AMP (Sourcegraph) | `AGENT.md`（单数） |
| OpenAI Codex | `AGENTS.md`（复数） |

这种碎片化意味着团队需要为不同工具维护多份内容相同的配置文件，改一次规则要同步好几个地方。

2025 年 5 月，Sourcegraph 旗下的 AMP 率先提议统一标准，建议用 `AGENT.md`（单数），并注册了 agent.md 域名。随后 OpenAI 宣布买下了 agents.md 域名，提议用 `AGENTS.md`（复数），理由是多个 Agent 会共用同一份配置。AMP 随即主动让步对齐，将 agent.md 重定向到 agents.md。

最终 AGENTS.md 成为事实标准，由 Linux Foundation 下属的 Agentic AI Foundation 托管。截至 2026 年初，GitHub 上已有超过 6 万个开源项目使用这个格式。Cursor、Kiro、灵码、Qoder、Copilot 等主流工具均已支持。Claude Code 虽然仍用 CLAUDE.md，但内容完全通用，一个软链接即可兼容：`ln -s AGENTS.md CLAUDE.md`。

过去半年里，我为手头的多个项目都维护了 AGENTS.md——有管控系统、有内核引擎代码、有产品基线、也有文档系统。不同项目的技术栈、仓库结构、团队规模各不相同，但在 AGENTS.md 的实践上逐渐收敛到了一套相似的方法论。这篇文章我挑了其中投入最多、也最通用的一个场景——管控系统（Spring Boot + React 的前后端分离项目）来展开介绍，希望对正在写或者想写 AGENTS.md 的同学有参考价值。

## [没有 AGENTS.md 的日子](https://www.cnkirito.moe/ai-agents-md-practise/\#%E6%B2%A1%E6%9C%89-AGENTS-md-%E7%9A%84%E6%97%A5%E5%AD%90 "没有 AGENTS.md 的日子") 没有 AGENTS.md 的日子

![没有 AGENTS.md 的日子](https://image.cnkirito.cn/agentsmd-infographic-02.png)

[没有 AGENTS.md 的日子](https://image.cnkirito.cn/agentsmd-infographic-02.png)

在聊怎么写之前，先说说为什么要写。

管控系统项目最初引入 AI Coding 工具时，我的体感是： **有了 AI，但效率提升远没有预期那么大**。问题不在工具本身，而在于项目对 AI 不友好。回头看，痛点集中在以下几个方面：

### [前后端上下文割裂](https://www.cnkirito.moe/ai-agents-md-practise/\#%E5%89%8D%E5%90%8E%E7%AB%AF%E4%B8%8A%E4%B8%8B%E6%96%87%E5%89%B2%E8%A3%82 "前后端上下文割裂") 前后端上下文割裂

最初后端和前端分属不同的 Git 仓库。AI Coding 时只能打开一个仓库，改一个涉及前后端联动的功能——比如后端新增一个接口，前端加一个对应的页面——需要在两个窗口之间来回切换。切换的过程中 AI 丢失上下文，你得重新描述一遍背景，效率很低。

后来我把前端仓库直接放到了后端仓库的子目录下，再后来干脆重构成了 monorepo。配合 AGENTS.md 中维护的项目结构说明，AI 在同一个窗口中就能看到 Controller 定义和对应的前端 API 调用。效果立竿见影——团队现在已经不区分前后端了，大家就是在一个仓库里提交代码，AI 也是在一个上下文里全栈编码。

### [AI 不认识私域组件](https://www.cnkirito.moe/ai-agents-md-practise/\#AI-%E4%B8%8D%E8%AE%A4%E8%AF%86%E7%A7%81%E5%9F%9F%E7%BB%84%E4%BB%B6 "AI 不认识私域组件") AI 不认识私域组件

项目前端大量使用了私域组件库（ProTable、ProForm、ProAction 等），这些组件是闭源的，AI 工具的训练数据里没有，也查不到公开文档。最初我维护了一些私域组件的使用文档给 AI 参考，但文档总是滞后于实现，AI 写出来的代码经常用错 prop 或者漏掉必要的配置。

后来我直接把私域组件库的源码放到了参考项目中。AI 不会写私域组件的代码时，可以直接读源码里的 TypeScript 定义和实现—— **源码永远不会过时，它就是最准确的文档**。这个改变之后，AI 写前端代码的质量有了质的提升。

### [AI 不知道项目的规矩](https://www.cnkirito.moe/ai-agents-md-practise/\#AI-%E4%B8%8D%E7%9F%A5%E9%81%93%E9%A1%B9%E7%9B%AE%E7%9A%84%E8%A7%84%E7%9F%A9 "AI 不知道项目的规矩") AI 不知道项目的规矩

每个项目都有自己的编码规约——异常必须通过统一的 BusinessException 抛出而不是直接抛 RuntimeException、响应体由框架统一包装禁止手动构造、分层架构禁止跨层依赖。这些规矩在团队成员脑子里，但 AI 不知道。

结果就是 AI 写出来的代码风格五花八门：有时候直接 `throw new RuntimeException()`，有时候用项目约定的 `BusinessException`；有时候手动 `new Response(code, data)` 包装返回值，有时候又不包；Controller 里直接注入 Repository 跳过 Service 层的情况也时有发生。每次都要人工纠正，纠正完下次还犯。

### [AI 不会启动项目、不会自测](https://www.cnkirito.moe/ai-agents-md-practise/\#AI-%E4%B8%8D%E4%BC%9A%E5%90%AF%E5%8A%A8%E9%A1%B9%E7%9B%AE%E3%80%81%E4%B8%8D%E4%BC%9A%E8%87%AA%E6%B5%8B "AI 不会启动项目、不会自测") AI 不会启动项目、不会自测

AI 改完代码之后，它不知道怎么构建、怎么启动、怎么验证。每个人的本地环境配置方式不统一，启动命令散落在各种文档和聊天记录里。AI 只能把代码改完就停下来，等人手动验证。

这意味着 AI 的工作闭环是断裂的——它只能完成「改代码」这一步，「构建 → 启动 → 验证 → 修复」这个循环全靠人来驱动。夜间让 Agent 自主执行？不可能，因为它连项目都启动不了。

### [痛点总结](https://www.cnkirito.moe/ai-agents-md-practise/\#%E7%97%9B%E7%82%B9%E6%80%BB%E7%BB%93 "痛点总结") 痛点总结

归纳一下，这些痛点的共同根源是： **项目的知识和规范存在于人的脑子里，而不是存在于 AI 能读到的地方**。

AGENTS.md 要解决的就是这个问题——把项目的结构、规矩、命令、验证方式写成 AI 能读懂的格式，放在仓库里，让 AI 打开项目就能理解、改完代码就能验证。配合仓库聚合、参考项目引入、启动脚本封装等改造，形成一套「打开即理解、改完即验证」的开发体验。

## [核心理念：地图，而非手册](https://www.cnkirito.moe/ai-agents-md-practise/\#%E6%A0%B8%E5%BF%83%E7%90%86%E5%BF%B5%EF%BC%9A%E5%9C%B0%E5%9B%BE%EF%BC%8C%E8%80%8C%E9%9D%9E%E6%89%8B%E5%86%8C "核心理念：地图，而非手册") 核心理念：地图，而非手册

![地图，而非手册](https://image.cnkirito.cn/agentsmd-infographic-03.png)

[地图，而非手册](https://image.cnkirito.cn/agentsmd-infographic-03.png)

AGENTS.md 的第一原则是 **渐进式披露**——它是一张地图，不是一本手册。

在我之前的文章中，我介绍过 OpenAI Harness Engineering 的四条原则，其中第一条就是「Map, not Manual」——AGENTS.md 应该是大约 200 行的导航地图，告诉 Agent「去哪里找什么」，详细内容放在链接的文档里。Anthropic 官方博客中也有相同的论述：不仅 Skill 应当采取渐进式披露，CLAUDE.md 也应当存放引用而非手册全文。

什么都重要的时候，什么都不重要。如果把所有内容都塞进 AGENTS.md，它会变成一个 5000 行的巨型文件，AI 的注意力被稀释，真正关键的规则反而容易被忽略。

模型已经足够聪明，它知道什么时候该去查阅详细文档和源码。AGENTS.md 只需要告诉它「文档在哪、源码在哪、什么时候该去看」，不需要把所有内容都搬过来。

### [写进 AGENTS.md 的内容](https://www.cnkirito.moe/ai-agents-md-practise/\#%E5%86%99%E8%BF%9B-AGENTS-md-%E7%9A%84%E5%86%85%E5%AE%B9 "写进 AGENTS.md 的内容") 写进 AGENTS.md 的内容

只有两类内容应该直接写在 AGENTS.md 中：

1. **AI 理解项目全貌的必要信息**——技术栈、仓库结构、核心模块、分层架构
2. **违反会直接导致问题的硬性规则**——编码规约、命名约定、禁止项

### [不写进去的内容](https://www.cnkirito.moe/ai-agents-md-practise/\#%E4%B8%8D%E5%86%99%E8%BF%9B%E5%8E%BB%E7%9A%84%E5%86%85%E5%AE%B9 "不写进去的内容") 不写进去的内容

其他详细信息通过 **文档链接和引用** 指向对应的文档：

|     |     |
| --- | --- |
| ```<br>1<br>2<br>3<br>4<br>5<br>``` | ```<br>AGENTS.md（地图）<br>  → docs/architecture.md          分层架构详细说明<br>  → docs/development.md           开发环境搭建<br>  → docs/design-docs/ref-*.md     参考项目架构说明<br>  → docs/design-docs/*-patterns.md 组件使用模式<br>``` |

判断一条信息该放 AGENTS.md 还是放详细文档，有一个简单的标准： **如果 AI 不知道这条信息就会写出错误的代码，放 AGENTS.md；如果只是写出不够好的代码，放详细文档，AGENTS.md 里放链接。**

## [实践一：仓库聚合——解决上下文割裂](https://www.cnkirito.moe/ai-agents-md-practise/\#%E5%AE%9E%E8%B7%B5%E4%B8%80%EF%BC%9A%E4%BB%93%E5%BA%93%E8%81%9A%E5%90%88%E2%80%94%E2%80%94%E8%A7%A3%E5%86%B3%E4%B8%8A%E4%B8%8B%E6%96%87%E5%89%B2%E8%A3%82 "实践一：仓库聚合——解决上下文割裂") 实践一：仓库聚合——解决上下文割裂

![仓库聚合](https://image.cnkirito.cn/agentsmd-infographic-04.png)

[仓库聚合](https://image.cnkirito.cn/agentsmd-infographic-04.png)

### [方案](https://www.cnkirito.moe/ai-agents-md-practise/\#%E6%96%B9%E6%A1%88 "方案") 方案

管控系统项目经历了从三仓分离到 monorepo 的演进。早期后端、前端组件库、前端主应用分属三个独立 Git 仓库，AI Coding 时上下文割裂严重。

最初的解决方案是 **脚本聚合**——通过一个 `setup-repos.sh` 脚本，将前端仓库克隆到后端项目的子目录下：

|     |     |
| --- | --- |
| ```<br>1<br>2<br>3<br>4<br>``` | ```<br>project-root/                    # 后端（主仓库）<br>  frontend/<br>    component-lib/               # 前端组件库（独立 Git 历史）<br>    web-app/                     # 前端主应用（独立 Git 历史）<br>``` |

关键设计是 `frontend/` 目录已 gitignore，不影响后端 CI/CD，不用 AI 工具的同事完全无感。

后来项目重构时，我们直接采用了 monorepo，前后端代码放在同一个仓库中：

|     |     |
| --- | --- |
| ```<br>1<br>2<br>3<br>4<br>5<br>6<br>7<br>``` | ```<br>project-root/<br>  server/                        # 后端（Spring Boot）<br>  web/                           # 前端（React + TypeScript）<br>  user-guide/                    # 用户手册（Markdown）<br>  reference-projects/            # 参考项目（git submodule）<br>  scripts/                       # 构建、启动、检查脚本<br>  docs/                          # 架构文档、设计文档<br>``` |

monorepo 天然解决了上下文割裂问题——AI 工具在同一个窗口中就能看到 Controller 接口定义和对应的前端 API 调用，实现真正的全栈编码。把用户手册仓库也放进来还有一个额外的好处：AI 可以直接基于代码变更同步更新用户文档，我现在的用户手册基本都是 AI 基于代码生成的，改完功能代码后让 AI 顺手把对应的用户手册也更新掉，不需要再单独维护一份文档。如果你有机会从零搭建或重构，monorepo 是更简洁的选择。存量项目迁移成本太高的话，脚本聚合是一个务实的折中。

## [实践二：统一环境配置——让 AI 能启动你的项目](https://www.cnkirito.moe/ai-agents-md-practise/\#%E5%AE%9E%E8%B7%B5%E4%BA%8C%EF%BC%9A%E7%BB%9F%E4%B8%80%E7%8E%AF%E5%A2%83%E9%85%8D%E7%BD%AE%E2%80%94%E2%80%94%E8%AE%A9-AI-%E8%83%BD%E5%90%AF%E5%8A%A8%E4%BD%A0%E7%9A%84%E9%A1%B9%E7%9B%AE "实践二：统一环境配置——让 AI 能启动你的项目") 实践二：统一环境配置——让 AI 能启动你的项目

![统一环境配置](https://image.cnkirito.cn/agentsmd-infographic-05.png)

[统一环境配置](https://image.cnkirito.cn/agentsmd-infographic-05.png)

### [问题](https://www.cnkirito.moe/ai-agents-md-practise/\#%E9%97%AE%E9%A2%98 "问题") 问题

每个人的本地环境配置方式不统一——有人用 IDE JVM 参数、有人用 shell export、有人写在 `.bashrc` 里。AI 工具不知道环境变量在哪、不知道如何启动服务，无法自主完成验证。

### [方案](https://www.cnkirito.moe/ai-agents-md-practise/\#%E6%96%B9%E6%A1%88-1 "方案") 方案

所有本地环境变量统一配置在 `~/.<project>_env` 文件中（纯 `KEY=VALUE` 格式），启动脚本自动 `source`。

为什么放在 `~` 下而非项目目录？避免意外提交到 Git。AI 工具通过 AGENTS.md 知道去哪里找配置。

AGENTS.md 中也明确写清楚了优先级：

|     |     |
| --- | --- |
| ```<br>1<br>2<br>3<br>``` | ```<br>### 数据库连接<br>1. 先查 ~/.<project>_env（启动脚本自动 source，文件不存在则跳过）<br>2. 若文件不存在，回退到 application.yml 中的缺省值<br>``` |

配套一键启动脚本，封装了 JDK 检测、优雅关闭旧进程、健康检查轮询等逻辑：

|     |     |
| --- | --- |
| ```<br>1<br>2<br>3<br>``` | ```<br>./scripts/start-server.sh                # 构建 + 启动 + 健康检查<br>./scripts/start-server.sh --quick        # 服务健康则秒返回<br>./scripts/start-server.sh --skip-build   # 跳过构建直接重启<br>``` |

AI 不需要理解这些细节，只需要调用一个命令。这是 AGENTS.md 中「快速命令」章节的核心价值—— **把复杂的环境操作封装成一条命令，降低 AI 的认知负担**。

## [实践三：验证闭环——改完代码不算完，跑通接口才算完](https://www.cnkirito.moe/ai-agents-md-practise/\#%E5%AE%9E%E8%B7%B5%E4%B8%89%EF%BC%9A%E9%AA%8C%E8%AF%81%E9%97%AD%E7%8E%AF%E2%80%94%E2%80%94%E6%94%B9%E5%AE%8C%E4%BB%A3%E7%A0%81%E4%B8%8D%E7%AE%97%E5%AE%8C%EF%BC%8C%E8%B7%91%E9%80%9A%E6%8E%A5%E5%8F%A3%E6%89%8D%E7%AE%97%E5%AE%8C "实践三：验证闭环——改完代码不算完，跑通接口才算完") 实践三：验证闭环——改完代码不算完，跑通接口才算完

![验证闭环](https://image.cnkirito.cn/agentsmd-infographic-06.png)

[验证闭环](https://image.cnkirito.cn/agentsmd-infographic-06.png)

这是我实践中感触最深的一环。在上一篇文章中我提到 Harness Engineering 的四条原则之一是「机械验证而非人工检查」，验证闭环就是这条原则的落地。

### [curl 验证规范](https://www.cnkirito.moe/ai-agents-md-practise/\#curl-%E9%AA%8C%E8%AF%81%E8%A7%84%E8%8C%83 "curl 验证规范") curl 验证规范

项目中定义了一套严格的 curl 验证规范，核心原则：

1. **每个 curl 独立执行**——禁止串联多个 curl，一个命令只做一件事
2. **用临时文件传递数据**——curl 输出写入 `/tmp/` 下的临时文件，后续用 `python3` 独立解析
3. **Token 获取模板化**——登录 → 写文件 → 提取 token → 后续请求携带
4. **排查路径明确**——日志文件位置、数据库连接方式

为什么要这么严格？因为 AI Agent 在 shell 中执行命令时，经常遇到兼容性问题。比如 zsh 下管道 + 方括号的 glob 问题，会导致 `curl | python3 -c "print(data['key'])"` 直接报错。用临时文件中转虽然多了一步，但稳定性高得多。

|     |     |
| --- | --- |
| ```<br>1<br>2<br>3<br>4<br>5<br>6<br>7<br>8<br>9<br>10<br>11<br>12<br>13<br>14<br>``` | ```<br># Step 1: 登录，结果写文件<br>curl -s -X POST http://localhost:8080/auth/login \<br>  -H 'Content-Type: application/json' \<br>  -d '{"username":"admin","password":"admin"}' > /tmp/login.json<br># Step 2: 提取 token（独立命令）<br>python3 -c "import json; print(json.load(open('/tmp/login.json'))['data']['token'])" > /tmp/token.txt<br># Step 3: 业务接口调用<br>TOKEN=$(cat /tmp/token.txt)<br>curl -s -X POST http://localhost:8080/providers/list \<br>  -H "Authorization: Bearer $TOKEN" \<br>  -H 'Content-Type: application/json' \<br>  -d '{"page":0,"size":10}' > /tmp/result.json<br>``` |

这套规范的目的是让 Agent 在本地环境中稳定地跑通「改 → 构建 → 启动 → 验证」循环，不会因为 shell 兼容性问题卡住。

### [验证不止于编译通过](https://www.cnkirito.moe/ai-agents-md-practise/\#%E9%AA%8C%E8%AF%81%E4%B8%8D%E6%AD%A2%E4%BA%8E%E7%BC%96%E8%AF%91%E9%80%9A%E8%BF%87 "验证不止于编译通过") 验证不止于编译通过

Claude Code 主创 Boris Cherny 在一次访谈中分享过类似的经验：后端任务可以跑 bash 测试，前端可以接浏览器验证，应用程序可以用 computer use 去检查实际操作结果。当流程变成先完成任务、再自己验证、最后整理结果，Agent 的输出就不只是「看起来做完」，而是更接近真的可用。

对于管控系统来说，验证手段主要是两类：

**后端：bash / curl 验证接口**。这是最基础也最可靠的验证方式——启动服务，curl 调接口，解析响应，确认数据正确。上面的 curl 验证规范就是为此设计的。

**前端：Agent Browser 验证页面**。纯 curl 只能验证接口返回值，但前端页面的渲染、交互、布局问题是看不到的。在调试前端疑难杂症时，我会使用 AI 工具的 Agent Browser 能力（如 Qoder 的 `agent-browser`），让 Agent 自己打开浏览器、操作页面、截屏对比，获取完整的视觉上下文来定位问题。这比让 Agent 猜测 CSS 问题要高效得多。

在我的实践中，验证闭环不仅仅是「代码能编译」，而是「功能能跑通」：

- lint 和格式检查在每次代码变更后自动触发
- 通过启动脚本把应用真正启动起来，用 curl 跑接口验证
- 在 Spec 的 Design 文档里写入验证方案，告诉 Agent「写完代码不算完，自测过功能才算完」

有了这套端到端的验证，Agent 的产出质量完全不同。特别是夜间执行的场景——睡前设计好 Spec，让 Agent 自主执行，第二天早上验收结果——验证闭环是这种工作模式的前提。

## [实践四：自动化检查——规则的执行力](https://www.cnkirito.moe/ai-agents-md-practise/\#%E5%AE%9E%E8%B7%B5%E5%9B%9B%EF%BC%9A%E8%87%AA%E5%8A%A8%E5%8C%96%E6%A3%80%E6%9F%A5%E2%80%94%E2%80%94%E8%A7%84%E5%88%99%E7%9A%84%E6%89%A7%E8%A1%8C%E5%8A%9B "实践四：自动化检查——规则的执行力") 实践四：自动化检查——规则的执行力

![自动化检查](https://image.cnkirito.cn/agentsmd-infographic-07.png)

[自动化检查](https://image.cnkirito.cn/agentsmd-infographic-07.png)

AGENTS.md 中写的规则，如果没有自动化检查，AI 和人都会违反。

### [分层依赖检查](https://www.cnkirito.moe/ai-agents-md-practise/\#%E5%88%86%E5%B1%82%E4%BE%9D%E8%B5%96%E6%A3%80%E6%9F%A5 "分层依赖检查") 分层依赖检查

项目中定义了严格的分层架构规则：

|     |     |
| --- | --- |
| ```<br>1<br>2<br>3<br>4<br>5<br>6<br>``` | ```<br>L0 - entity/          → 只允许依赖 common<br>L1 - repository/      → 只允许依赖 entity, common<br>L2 - core/            → 横切关注点，不允许依赖业务包<br>L3 - config/          → 允许依赖 core, service<br>L4 - service/         → 业务核心层<br>L5 - controller/      → 只允许依赖 service, core, common<br>``` |

光写在 AGENTS.md 里是不够的。我们用一个 shell 脚本扫描所有 Java 文件的 import 语句，按包路径判断所属层级，检查是否违反依赖方向。违规时输出可操作的错误信息：

|     |     |
| --- | --- |
| ```<br>1<br>2<br>3<br>``` | ```<br>✗ service/client/impl/SomeService.java 导入了 entity.SomeEntity<br>  原因: 客户端实现禁止直接依赖业务 Entity，须通过 DTO 传递数据<br>  修复: 在编排层完成 Entity→DTO 转换，客户端只接收 DTO<br>``` |

注意这里的错误信息格式： **WHAT（违规了什么）+ WHY（为什么不允许）+ HOW（怎么修复）**。这不仅是给人看的，也是给 AI 看的——AI 读到这条错误信息后，能直接按照 HOW 的指引去修复，不需要额外的上下文。

集成到 `make lint-arch`，一条命令完成检查。AI Agent 改完代码后可以自主运行检查，形成「改 → 检 → 修」的自动闭环。

### [质量检查命令矩阵](https://www.cnkirito.moe/ai-agents-md-practise/\#%E8%B4%A8%E9%87%8F%E6%A3%80%E6%9F%A5%E5%91%BD%E4%BB%A4%E7%9F%A9%E9%98%B5 "质量检查命令矩阵") 质量检查命令矩阵

通过 Makefile 提供统一入口：

|     |     |
| --- | --- |
| ```<br>1<br>2<br>3<br>4<br>5<br>``` | ```<br>lint-arch:    ./scripts/lint-deps.sh      # 分层依赖检查<br>lint-format:  mvn spotless:check          # 格式检查<br>format:       mvn spotless:apply          # 格式修复<br>build:        mvn package -DskipTests     # 构建<br>test:         mvn test                    # 测试<br>``` |

AI Agent 不需要记住每个检查命令的具体写法，只需要知道 `make lint-arch` 和 `make lint-format`。

## [实践五：参考项目引入——给 AI 喂够上下文](https://www.cnkirito.moe/ai-agents-md-practise/\#%E5%AE%9E%E8%B7%B5%E4%BA%94%EF%BC%9A%E5%8F%82%E8%80%83%E9%A1%B9%E7%9B%AE%E5%BC%95%E5%85%A5%E2%80%94%E2%80%94%E7%BB%99-AI-%E5%96%82%E5%A4%9F%E4%B8%8A%E4%B8%8B%E6%96%87 "实践五：参考项目引入——给 AI 喂够上下文") 实践五：参考项目引入——给 AI 喂够上下文

![参考项目引入](https://image.cnkirito.cn/agentsmd-infographic-08.png)

[参考项目引入](https://image.cnkirito.cn/agentsmd-infographic-08.png)

### [问题](https://www.cnkirito.moe/ai-agents-md-practise/\#%E9%97%AE%E9%A2%98-1 "问题") 问题

前面痛点章节提到过，AI 不认识闭源组件，维护使用文档又总是滞后于实现。但这个问题的范围其实更大——不只是闭源组件，还有开源网关内核的对接细节、其他产品组件的能力同步、相关项目的架构参考，这些都是 AI 训练数据覆盖不到的。靠写文档来补全这些上下文，成本高、覆盖不全，而且很难保持更新。

### [方案：直接引入源码](https://www.cnkirito.moe/ai-agents-md-practise/\#%E6%96%B9%E6%A1%88%EF%BC%9A%E7%9B%B4%E6%8E%A5%E5%BC%95%E5%85%A5%E6%BA%90%E7%A0%81 "方案：直接引入源码") 方案：直接引入源码

后来我换了一个思路—— **不写文档，直接把源码放进来**。在项目中创建 `reference-projects/` 目录，通过 git submodule 引入多个参考项目：

|     |     |
| --- | --- |
| ```<br>1<br>2<br>3<br>4<br>5<br>6<br>7<br>``` | ```<br>reference-projects/<br>  higress/                # 开源 Higress 网关内核源码<br>  nacos/                  # 开源 Nacos 注册配置中心源码<br>  pro-components/         # 私域组件库源码（TypeScript）<br>  other-product-backend/          # 其他产品后端（Go）<br>  other-product-frontend/         # 其他产品前端（React）<br>  himarket/               # 开源 HiMarket AI 开放平台（Spring Boot）<br>``` |

配合 `ignore = all` 避免 CI/CD 干扰，本地开发按需拉取：

|     |     |
| --- | --- |
| ```<br>1<br>2<br>``` | ```<br>git submodule update --init                              # 首次拉取全部<br>git submodule update --init reference-projects/pro-components # 只拉取单个<br>``` |

**源码永远不会过时，它就是最准确的文档。** AI 不会写私域组件的代码时，可以直接读源码里的 TypeScript 定义和实现；需要对接网关内核时，可以直接查看路由和插件的实际代码。这个改变之后，AI 写代码的质量有了质的提升。

同时，为每个参考项目维护一份架构说明文档（`docs/design-docs/ref-*.md`），帮助 AI 快速理解参考代码的结构，而不是让它从零开始探索一个陌生的仓库：

|     |     |
| --- | --- |
| ```<br>1<br>2<br>3<br>4<br>5<br>6<br>7<br>8<br>9<br>10<br>``` | ```<br>## 文档导航（参考项目部分）<br>| 文档 | 说明 |<br>|------|------|<br>| docs/design-docs/ref-higress.md | Higress 网关内核：路由模型、插件机制、CRD 结构 |<br>| docs/design-docs/ref-nacos.md | Nacos：配置中心对接、服务发现集成 |<br>| docs/design-docs/ref-pro-components.md | 私域组件库：ProTable/ProForm 使用模式、TS 类型速查 |<br>| docs/design-docs/ref-other-product-backend.md | 其他产品后端：目录结构、分层架构、核心模块 |<br>| docs/design-docs/ref-other-product-frontend.md | 其他产品前端：页面结构、组件体系、路由设计 |<br>| docs/design-docs/ref-himarket.md | HiMarket AI 开放平台：多模块结构、领域模型 |<br>``` |

这些 ref 文档和 reference-projects 是配套的——ref 文档是「地图」，告诉 AI 参考项目的整体结构和关键模块在哪里；reference-projects 是「源码」，AI 需要细节时直接去读。这些文档本身也是 AI 基于参考项目源码生成的——又一个「AI 基于代码写文档」的例子。

### [为什么不只写文档？](https://www.cnkirito.moe/ai-agents-md-practise/\#%E4%B8%BA%E4%BB%80%E4%B9%88%E4%B8%8D%E5%8F%AA%E5%86%99%E6%96%87%E6%A1%A3%EF%BC%9F "为什么不只写文档？") 为什么不只写文档？

| 方式 | 优点 | 缺点 |
| --- | --- | --- |
| 只写使用文档 | 轻量、聚焦 | 滞后于实现、覆盖不全、边界情况缺失 |
| 引入源码 \+ 架构说明 | 永远准确、覆盖完整 | 仓库体积增大、需要管理 submodule |

对于 AI 工具训练数据中不存在的闭源组件和内部项目，引入源码是目前最有效的方式。文档可以作为补充（帮 AI 快速定位），但不能替代源码本身。

你可能会担心：引入这么多参考仓库，AI 会不会无从下手？实际体验下来完全不用担心。通过 AGENTS.md 的渐进式披露设计——项目结构树标注了每个目录的用途，ref 文档提供了参考项目的架构概览，参考优先级规则明确了什么时候该看哪个项目——现在的大模型已经足够聪明，知道什么时候该去参考项目里找答案，什么时候该在本项目代码里改动。它不会因为仓库里多了几个参考项目就迷失方向，反而会因为有了充足的上下文而写出更准确的代码。

## [为什么选择 AGENTS.md](https://www.cnkirito.moe/ai-agents-md-practise/\#%E4%B8%BA%E4%BB%80%E4%B9%88%E9%80%89%E6%8B%A9-AGENTS-md "为什么选择 AGENTS.md") 为什么选择 AGENTS.md

团队使用的 AI Coding 工具比较分散——Qoder、Cursor、灵码、Kiro、Claude Code 都有人用。不同工具各自有配置机制，Skill、Rule、Hook 的存储目录不统一。

选择 AGENTS.md 作为核心入口的原因：

- **足够通用**——已被多数主流工具识别，一份文件覆盖大部分工具
- **零配置成本**——不需要安装插件或配置 hook，工具打开项目自动读取
- **降低维护负担**——不用为每种工具各维护一份规则文件
- **兼容性好**——Claude Code 不识别 AGENTS.md，但 `ln -s AGENTS.md CLAUDE.md` 即可

基于这个考虑，我们把和特定工具绑定的 rules、hook 等配置作为补充，核心规则全部收敛到 AGENTS.md 一个入口。

## [AGENTS.md 编写模板](https://www.cnkirito.moe/ai-agents-md-practise/\#AGENTS-md-%E7%BC%96%E5%86%99%E6%A8%A1%E6%9D%BF "AGENTS.md 编写模板") AGENTS.md 编写模板

![AGENTS.md 编写模板](https://image.cnkirito.cn/agentsmd-infographic-09.png)

[AGENTS.md 编写模板](https://image.cnkirito.cn/agentsmd-infographic-09.png)

基于实践经验，提炼出一个通用模板：

|     |     |
| --- | --- |
| ```<br>1<br>2<br>3<br>4<br>5<br>6<br>7<br>8<br>9<br>10<br>11<br>12<br>13<br>14<br>15<br>16<br>17<br>18<br>19<br>20<br>21<br>22<br>23<br>24<br>25<br>26<br>27<br>28<br>29<br>30<br>31<br>32<br>33<br>34<br>35<br>``` | ```<br># AGENTS.md<br>## 1. 项目概述<br>一段话说清楚：项目是什么、技术栈、仓库结构。<br>前 10 行必须让 AI 建立项目心智模型。<br>## 2. 快速命令<br>构建、启动、格式化、质量检查的命令速查表。<br>环境变量配置说明（env 文件位置、启动脚本自动 source）。<br>## 3. 后端架构<br>包结构树（ASCII）+ 每个包的用途注释。<br>核心子系统的简要说明 + 详细文档链接。<br>前后端术语映射（如有差异）。<br>## 4. 前端架构<br>技术栈、路由方案、API 层约定、组件库规范。<br>详细文档链接。<br>## 5. 关键约定<br>5-10 条硬性编码规则（违反会直接导致问题的）。<br>每条规则附详细文档链接。<br>## 6. 本地开发及验证流程<br>「改 → 构建 → 启动 → 验证」的完整闭环。<br>curl 验证模板、Token 获取、日志路径。<br>## 7. 质量检查<br>lint、format、build、test 命令矩阵。<br>## 8. 参考项目约定<br>参考项目列表 + 优先级规则。<br>## 9. 文档导航<br>所有详细文档的索引表。<br>``` |

建议控制在 200 行以下。超过这个范围，考虑将细节拆分到 `docs/` 下的专题文档。

## [实施建议](https://www.cnkirito.moe/ai-agents-md-practise/\#%E5%AE%9E%E6%96%BD%E5%BB%BA%E8%AE%AE "实施建议") 实施建议

### [从 /init 和 harness-creator 开始，逐步优化](https://www.cnkirito.moe/ai-agents-md-practise/\#%E4%BB%8E-init-%E5%92%8C-harness-creator-%E5%BC%80%E5%A7%8B%EF%BC%8C%E9%80%90%E6%AD%A5%E4%BC%98%E5%8C%96 "从 /init 和 harness-creator 开始，逐步优化") 从 /init 和 harness-creator 开始，逐步优化

本文介绍的是一个管控系统的实践，你的项目不一定是同样的场景。好消息是，大多数 AI Coding 工具都提供了类似 `/init` 的命令（比如 Claude Code 的 `/init`、Qoder 的 `qoder init`），可以自动扫描项目结构并生成一份初始的 AGENTS.md。自动生成的版本通常能覆盖项目概述和基本的构建命令，是一个不错的起点。

如果你想要更完整的起步，可以试试 [harness-creator](https://market.hiclaw.io/skills/product-69e4ee23e4b0491b51db30bd) Skill——它不仅生成 AGENTS.md，还会一并生成分层架构约束的 lint 脚本、Makefile、验证脚本、参考文档等配套基础设施，基本上把本文提到的实践打包成了一个一键生成的工具。

然后根据你的项目特点逐步优化：如果是全栈项目，补充仓库聚合和前后端联动的说明；如果用了闭源组件，引入参考项目；如果有分层架构约束，加上 lint 脚本。不需要一步到位，从 bad case 驱动迭代就好。

### [通过 Bad Case 驱动](https://www.cnkirito.moe/ai-agents-md-practise/\#%E9%80%9A%E8%BF%87-Bad-Case-%E9%A9%B1%E5%8A%A8 "通过 Bad Case 驱动") 通过 Bad Case 驱动

不要试图一次写完 AGENTS.md。从实际使用中发现的 bad case 出发：

1. AI 犯了一个错误（比如用了错误的命名风格、在错误的层级引入了依赖）
2. 思考：「如果 AGENTS.md 里多写一条 XX 规则，AI 是不是就不会犯这个错」
3. 判断改哪里：全局规则 → AGENTS.md，模块细节 → 对应的 docs/

这是最高效的迭代方式。AGENTS.md 不是一份写完就锁定的文档，它需要随着项目演进持续调整。

### [规则要有执行力](https://www.cnkirito.moe/ai-agents-md-practise/\#%E8%A7%84%E5%88%99%E8%A6%81%E6%9C%89%E6%89%A7%E8%A1%8C%E5%8A%9B "规则要有执行力") 规则要有执行力

重要的规则要有对应的自动化检查。AGENTS.md 中写「禁止跨层依赖」，如果没有 lint 脚本来检查，AI 和人都会违反。

规则的优先级： **能自动化检查的 \> 写在 AGENTS.md 中的 > 口头约定的**。

### [团队共建](https://www.cnkirito.moe/ai-agents-md-practise/\#%E5%9B%A2%E9%98%9F%E5%85%B1%E5%BB%BA "团队共建") 团队共建

鼓励团队成员在遇到 AI bad case 时主动补充规则。但要遵循「地图」原则：

| 改动类型 | 维护位置 | 举例 |
| --- | --- | --- |
| 全局性的架构约定或编码规约 | AGENTS.md | 「所有 Controller 统一 POST」 |
| 某个模块的具体开发规范 | 对应的 docs/ 文档 | 某个 Service 的调用约定 |
| 前端组件的使用模式 | 组件模式文档 | ProTable 的某个 prop 必须传特定值 |
| 参考项目的架构说明 | 对应的 ref-\* 文档 | 某个开源项目的架构分层介绍 |

如果细节规则都怼进 AGENTS.md，上下文会膨胀，重要的规则反而被淹没。

### [标注给谁看](https://www.cnkirito.moe/ai-agents-md-practise/\#%E6%A0%87%E6%B3%A8%E7%BB%99%E8%B0%81%E7%9C%8B "标注给谁看") 标注给谁看

团队中不是所有人都用 AI 工具。在推广 AGENTS.md 时，明确标注每个文件的目标读者，可以降低团队的理解成本：

| 文件 | 读者 | 说明 |
| --- | --- | --- |
| README.md | 人 | 项目介绍、快速开始，给人类看的入口 |
| AGENTS.md | AI 为主，人可浏览 | AI 工具自动读取的项目指令 |
| docs/\*.md | AI 为主，人可参考 | 各模块的开发手册 |
| scripts/\*.sh | 人和 AI 都用 | 构建、启动、部署脚本 |
| setup-repos.sh | 人执行 | 一键环境搭建 |

README.md 和 AGENTS.md 是互补的——README.md 是给人类看的项目说明，聚焦快速开始和贡献指南；AGENTS.md 是给 AI 看的项目指令，聚焦构建命令、编码规范和验证流程。两者的内容可能有少量重叠（比如项目概述），但侧重点不同，不需要合并。

一句话总结： **脚本是人和 AI 共用的，AGENTS.md 和 docs/ 下的文档主要是给 AI 的上下文，人不需要刻意阅读但可以参考。**

## [总览：项目结构与 AGENTS.md 全貌](https://www.cnkirito.moe/ai-agents-md-practise/\#%E6%80%BB%E8%A7%88%EF%BC%9A%E9%A1%B9%E7%9B%AE%E7%BB%93%E6%9E%84%E4%B8%8E-AGENTS-md-%E5%85%A8%E8%B2%8C "总览：项目结构与 AGENTS.md 全貌") 总览：项目结构与 AGENTS.md 全貌

最后，把本文提到的所有实践汇总成一张全景图，方便你对照参考。

### [项目目录结构](https://www.cnkirito.moe/ai-agents-md-practise/\#%E9%A1%B9%E7%9B%AE%E7%9B%AE%E5%BD%95%E7%BB%93%E6%9E%84 "项目目录结构") 项目目录结构

|     |     |
| --- | --- |
| ```<br>1<br>2<br>3<br>4<br>5<br>6<br>7<br>8<br>9<br>10<br>11<br>12<br>13<br>14<br>15<br>16<br>17<br>18<br>19<br>20<br>21<br>22<br>23<br>24<br>25<br>26<br>27<br>28<br>29<br>30<br>31<br>32<br>33<br>34<br>35<br>36<br>``` | ```<br>project-root/<br>  AGENTS.md                         # AI Coding 项目指令（核心入口）<br>  README.md                         # 给人看的项目说明<br>  Makefile                          # 质量检查统一入口（lint-arch/format/build/test）<br>  <br>  server/                           # 后端（Spring Boot）<br>  web/                              # 前端（React + TypeScript）<br>  user-guide/                       # 用户手册（Markdown，AI 基于代码生成）<br>  <br>  scripts/<br>    start-server.sh                 # 后端一键启动（构建+启动+健康检查）<br>    start-web.sh                    # 前端一键启动<br>    lint-deps.sh                    # 分层依赖检查脚本<br>  <br>  docs/<br>    architecture.md                 # 分层架构、依赖规则、领域模型<br>    development.md                  # 环境要求、构建运行、数据库<br>    design-docs/<br>      api-design.md                 # 响应格式、错误码、端点详情<br>      controller-conventions.md     # Controller 层编码规范<br>      gateway-integration.md          # 网关对接详细文档<br>      frontend-architecture.md      # 前端架构、组件库规范<br>      ref-higress.md                # 参考：Higress 网关内核<br>      ref-nacos.md                  # 参考：Nacos 注册配置中心<br>      ref-pro-components.md         # 参考：私域组件库<br>      ref-other-product-backend.md          # 参考：其他产品后端<br>      ref-other-product-frontend.md         # 参考：其他产品前端<br>      ref-himarket.md               # 参考：HiMarket AI 开放平台<br>  reference-projects/               # 参考项目（git submodule，只读）<br>    higress/                        # 开源 Higress 网关内核源码<br>    nacos/                          # 开源 Nacos 源码<br>    pro-components/                 # 私域组件库源码<br>    other-product-backend/                  # 其他产品后端<br>    other-product-frontend/                 # 其他产品前端<br>    himarket/                       # 开源 HiMarket AI 开放平台<br>``` |

### [AGENTS.md 摘要](https://www.cnkirito.moe/ai-agents-md-practise/\#AGENTS-md-%E6%91%98%E8%A6%81 "AGENTS.md 摘要") AGENTS.md 摘要

以下是管控系统项目 AGENTS.md 的章节结构摘要，供参考：

|     |     |
| --- | --- |
| ```<br>1<br>2<br>3<br>4<br>5<br>6<br>7<br>8<br>9<br>10<br>11<br>12<br>13<br>14<br>15<br>16<br>17<br>18<br>19<br>20<br>21<br>22<br>23<br>24<br>25<br>26<br>27<br>28<br>29<br>30<br>31<br>32<br>33<br>34<br>35<br>36<br>37<br>38<br>39<br>``` | ```<br># AGENTS.md<br>## 1. 项目概述<br>  一段话：项目定位、技术栈（Spring Boot + React）、monorepo 结构<br>## 2. 快速命令<br>  构建、启动、格式化、质量检查命令速查表<br>  环境变量配置：~/.<project>_env 优先级说明<br>## 3. 后端架构<br>  包结构树（ASCII）+ 每个包的用途注释<br>  核心子系统简要说明<br>  → 详见 docs/architecture.md<br>## 4. 前端架构<br>  技术栈、路由方案、API 层约定、组件库规范<br>  → 详见 docs/design-docs/frontend-architecture.md<br>## 5. 关键约定<br>  - 异常统一用 BusinessException，禁止直接抛 RuntimeException<br>  - 响应体由框架统一包装，禁止手动构造<br>  - 分层架构禁止跨层依赖（make lint-arch 自动检查）<br>  - 代码风格：Spotless + Google Java Format<br>  - 安全：无状态 JWT<br>  → 每条规则附详细文档链接<br>## 6. 本地开发及验证流程<br>  「改 → 构建 → 启动 → 验证」完整闭环<br>  curl 验证模板、Token 获取、日志路径<br>  → 详见 docs/design-docs/api-verification.md<br>## 7. 质量检查<br>  make lint-arch / lint-format / format / build / test<br>## 8. 参考项目约定<br>  参考项目列表 + 优先级规则<br>## 9. 文档导航<br>  所有详细文档的索引表（architecture / design-docs / ref-*）<br>``` |

## [总结](https://www.cnkirito.moe/ai-agents-md-practise/\#%E6%80%BB%E7%BB%93 "总结") 总结

![打开即理解，改完即验证](https://image.cnkirito.cn/agentsmd-infographic-10.png)

[打开即理解，改完即验证](https://image.cnkirito.cn/agentsmd-infographic-10.png)

回顾这半年的实践，AGENTS.md 的本质是 **用最小的上下文成本，让 AI 工具获得最大的项目理解**。

写好它的关键不是写得多，而是写得准——把 AI 最容易犯错的地方堵住，把 AI 最需要的信息放在最容易找到的地方。配合自动化检查、验证闭环、统一环境配置，形成一套「打开即理解、改完即验证」的开发体验。

这套实践和我之前文章中提到的 Harness Engineering 是一脉相承的。AGENTS.md + 文档体系 + lint 脚本 + 启动脚本 + 验证规范，本质上就是在构建一个反馈回路：AI 读 AGENTS.md 理解项目 → 写代码 → 自动检查 → 启动验证 → 根据结果修正。人类的角色是设计这个回路，而不是在回路中的每一步都亲自操作。

一个有意思的观察是：AGENTS.md 的维护过程本身就是一种知识沉淀。过去团队的编码规范散落在 Wiki、聊天记录、口头约定里，新人入职要花很长时间才能摸清这些「潜规则」。现在这些知识被结构化地写进了 AGENTS.md 和配套文档中——虽然初衷是给 AI 看的，但人也能从中受益。某种意义上，为 AI 写好 AGENTS.md 的过程，也是在为团队做一次知识梳理。

如果你还没有为项目写 AGENTS.md，现在就可以开始——用 `/init` 生成一份初始版本，或者试试 [harness-creator](https://market.hiclaw.io/skills/product-69e4ee23e4b0491b51db30bd) 一键生成 AGENTS.md 及配套的 lint 脚本、Makefile 和验证基础设施。然后在日常使用中，每遇到一个 AI bad case，就补一条规则。用不了多久，你就会拥有一份真正有用的 AGENTS.md。

一个文件让 AI Coding 效率翻倍：AGENTS.md 实践指南

[https://www.cnkirito.moe/ai-agents-md-practise/](https://www.cnkirito.moe/ai-agents-md-practise/)

###### 作者

徐靖峰

###### 发布于

2026-04-19

###### 更新于

2026-04-19

###### 许可协议

[Creative Commons](https://creativecommons.org/ "Creative Commons")[Attribution](https://creativecommons.org/licenses/by/4.0/ "Attribution")[Noncommercial](https://creativecommons.org/licenses/by-nc/4.0/ "Noncommercial")

* * *

[AI Coding,](https://www.cnkirito.moe/tags/AI-Coding/) [AGENTS.md](https://www.cnkirito.moe/tags/AGENTS-md/)

### 喜欢这篇文章？打赏一下作者吧

支付宝![支付宝](https://www.cnkirito.moe/img/alipay.png)微信![微信](https://www.cnkirito.moe/img/wechatpay.png)

###### Your browser is out-of-date!

Update your browser to view this website correctly.&npsb; [Update my browser now](http://outdatedbrowser.com/)

[×](https://www.cnkirito.moe/ai-agents-md-practise/# "Close")

×

[一个 DDD 指导下的实体类设计案例\\
\\
1 引子项目开发中的工具类代码总是随着项目发展逐渐变大，在公司诸多的公用代码中，笔者发现了一个简单的，也是经常被使用的类：BaseDomain，引起了我的思考。在我们公司的开发习惯中，数据库实体类通常](https://www.cnkirito.moe/DDD-practice/) [Re：从零开始的领域驱动设计\\
\\
前言领域驱动的火爆程度不用我赘述，但是即便其如此得耳熟能详，但大多数人对其的认识，还只是停留在知道它的缩写是 DDD，知道它是一种软件思想，或者知道它和微服务有千丝万缕的关系。Eric Evans 对](https://www.cnkirito.moe/Re-DDD/) [Re：从零开始的 Spring Security OAuth2（二）\\
\\
本文开始从源码的层面，讲解一些 Spring Security Oauth2 的认证流程。本文较长，适合在空余时间段观看。且涉及了较多的源码，非关键性代码以… 代替。 准备工作首先开启 debug 信](https://www.cnkirito.moe/Spring-Security-OAuth2-2/) [Re：从零开始的 Spring Security OAuth2（一）\\
\\
前言今天来聊聊一个接口对接的场景，A 厂家有一套 HTTP 接口需要提供给 B 厂家使用，由于是外网环境，所以需要有一套安全机制保障，这个时候 oauth2 就可以作为一个方案。 关于 oauth2，](https://www.cnkirito.moe/Spring-Security-OAuth2-1/) [南京 IAS 架构师峰会观后感\\
\\
上周六，周日在南京举办了 IAS 架构师峰会，这么多人的技术分享会还是头一次参加，大佬云集，涨了不少姿势。特此一篇记录下印象深刻的几场分享。由于全凭记忆叙述，故只能以流水账的形式还原出现场的收获。 大](https://www.cnkirito.moe/NJIAS2017/)[Tags](https://www.cnkirito.moe/tags/index.html) [徐靖峰\\
\\
「技术分享」某种程度上，是让作者和读者，不那么孤独的东西。「Kirito的技术分享」致力于探讨 Java 生态的知识点，内容覆盖网关，分布式服务框架，微服务，性能调优，源码分析，技术杂谈。追求有深度并](https://www.cnkirito.moe/about/index.html) [Categories](https://www.cnkirito.moe/categories/index.html)[架构设计\\
(架构设计)](https://www.cnkirito.moe/categories/%E6%9E%B6%E6%9E%84%E8%AE%BE%E8%AE%A1/) [Spring\\
(Spring)](https://www.cnkirito.moe/categories/Spring/) [技术杂谈\\
(技术杂谈)](https://www.cnkirito.moe/categories/%E6%8A%80%E6%9C%AF%E6%9D%82%E8%B0%88/) [工具\\
(工具)](https://www.cnkirito.moe/categories/%E5%B7%A5%E5%85%B7/) [Java\\
(Java)](https://www.cnkirito.moe/categories/Java/)[DDD\\
(DDD)](https://www.cnkirito.moe/tags/DDD/) [Spring Security OAuth2\\
(Spring-Security-OAuth2)](https://www.cnkirito.moe/tags/Spring-Security-OAuth2/) [技术杂谈\\
(技术杂谈)](https://www.cnkirito.moe/tags/%E6%8A%80%E6%9C%AF%E6%9D%82%E8%B0%88/) [Arthas\\
(Arthas)](https://www.cnkirito.moe/tags/Arthas/) [字节序\\
(字节序)](https://www.cnkirito.moe/tags/%E5%AD%97%E8%8A%82%E5%BA%8F/)