# DeepAgents 与 LangChain 的关系研究报告

## 摘要

DeepAgents（Deep Agents / `deepagents`）与 LangChain 并不是竞争或替代关系，而是**同一生态（LangChain 官方）内、技术栈分层上的"上下级"关系**。DeepAgents 是 LangChain 公司（langchain-ai）官方开发的开源"智能体套件"（Agent Harness），它**明确构建在 LangChain 框架之上，并以 LangGraph 作为底层运行时**。三者共同构成 LangChain 官方所称的"开源 Agent 技术栈的三层"。

---

## 一、DeepAgents 是什么

**Deep Agents（`deepagents`）是 LangChain 团队开发并维护的开源"智能体工具链/框架"（Agent Harness）**，官方定位为 **"The batteries-included agent harness"（开箱即用的智能体框架）**。

- **"Deep Agents" 这一术语由 LangChain 官方提出**，用于描述能够处理复杂、开放式任务、在更长时间跨度内运行的 AI 智能体。LangChain 认为这类智能体有四大关键要素：**规划工具（planning tool）、文件系统访问（filesystem）、子智能体（subagents）、详尽的提示词（detailed prompts）**。
- 设计灵感来自 **Claude Code、Deep Research、Manus** 等工具的架构模式，LangChain 将其抽象、标准化并打包成库，使其**模型无关、可定制、生产就绪**。
- 时间线：概念约 **2025年8月** 首次提出，2025年10月发布 CLI 工具；截至 2026年8月，GitHub 仓库已获约 **28,000 stars / 3,900 forks**，并已发布 TypeScript/JavaScript 版本（`deepagentsjs`）。

**核心能力（四大块）：**

| 能力模块 | 具体内容 |
|---|---|
| **执行环境** | 工具调用、虚拟文件系统（`ls`/`read_file`/`write_file`/`edit_file`/`glob`/`grep` 等）、可选沙箱（Modal、Daytona、Deno 等）、代码解释器（QuickJS） |
| **上下文管理** | 技能（Skills）按需加载、记忆（Memory/AGENTS.md）、历史摘要与上下文卸载、prompt 缓存 |
| **委派（Delegation）** | 子智能体（Sub-agents）——通过内置 `task` 工具生成隔离上下文窗口的子代理；可选任务规划（`write_todos`） |
| **引导/控制（Steering）** | 人在环路（Human-in-the-loop）：敏感工具调用前暂停等待人工审批/修改/拒绝 |

其他关键特性：模型无关（支持任何支持工具调用的 LLM）、完全支持 MCP、底层基于 LangGraph（流式、持久化、检查点）、配合 LangSmith 做追踪与评估、附带 CLI 工具（`deepagents` / `dcode`）。

---

## 二、LangChain 是什么

**LangChain 是一个开源软件框架（orchestration framework），用于简化基于大语言模型（LLM）的应用程序开发。**

- **创始人**：Harrison Chase
- **诞生时间**：2022 年 10 月；**开发组织**：LangChain Inc.（langchain-ai），2023 年 1 月注册成立
- **许可证**：MIT 开源协议；**语言**：Python 和 JavaScript/TypeScript
- **融资**：2023 年种子轮（Benchmark + 红杉）；2024 年 B 轮 **1.25 亿美元、估值 12.5 亿美元**，目标是成为企业部署 AI Agent 的"基础构建模块"

**核心功能与定位：** LangChain 是"抽象库"，将使用语言模型所需的常见步骤模块化，开发者可"链"（chain）在一起，实现统一 API 对接几乎所有主流 LLM、连接外部数据源和工具（RAG、文档分析、聊天机器人等）。

**当前生态定位（官方定义，三者互补协同）：**

| 产品 | 定位 | 作用 |
|---|---|---|
| **LangChain** | Agent 框架 | 模型、工具、Agent 循环的抽象与集成层（构建层） |
| **LangGraph** | 编排运行时 | 低层 agent 编排框架：持久化执行、流式输出、human-in-the-loop、持久化记忆 |
| **LangSmith** | 可观测平台 | 追踪、评估（evals）、提示词管理、调试与部署 |
| **Deep Agents** | Agent harness（高层封装） | 基于 LangGraph，内置规划、子代理、文件系统等能力 |

**地位：** GitHub 星数约 **7 万+**，2025 年多份"最佳开源 AI Agent 框架"榜单中位列第一，被视为 AI Agent 赛道的基础设施级玩家；其原生支持并深度集成 agent 框架（包括官方低层编排框架 LangGraph）。

---

## 三、两者关系：分层叠加，而非替代

### 3.1 核心结论

**DeepAgents 不是独立的框架，而是 LangChain 官方生态体系中的一个组成部分——具体说是"站在最顶层的 Agent Harness（智能体套件）"。**

- ✅ **是 LangChain 生态的一部分**：由 LangChain 公司于 2025 年发布，代码托管在官方仓库 `github.com/langchain-ai/deepagents`。
- ✅ **明确构建在 LangChain 之上**：官方文档原话 *"a standalone library built on top of LangChain's core building blocks for agents. It uses the LangGraph runtime"*。
- ✅ **与 LangChain/LangGraph 是"分层叠加"（集成）关系，而非对比或替代关系**：三者构成官方所谓的 "three layers of our open source agent stack"（开源 Agent 技术栈的三层）。
- ⚠️ 唯一需要澄清的"独立性"：DeepAgents **以独立包分发**（`pip install deepagents`），但它的身份、代码库、文档、技术支持全部属于 LangChain 生态，**并不脱离 LangChain 独立存在**。

### 3.2 依赖链条

```
DeepAgents（Agent Harness，最高层，开箱即用）
    ↑ 构建于
LangChain create_agent（Agent Framework，中间层，极简抽象）
    ↑ 构建于
LangGraph（Agent Runtime，最底层，图运行时/引擎）
```

### 3.3 官方原文佐证

1. **官方文档（Deep Agents 概览）**：*"`deepagents` is a standalone library built on top of LangChain's core building blocks for agents. It uses the LangGraph runtime for durable execution, streaming, human-in-the-loop, and other features."*
2. **官方博客《Doubling down on Deep Agents》**（2025-10-28）：*"deepagents is built on top of langchain's agent abstraction, which in turn is built on top of langgraph's agent runtime."*
3. **GitHub 仓库 FAQ**：*"LangGraph is the graph runtime. LangChain's `create_agent` is a minimal agent harness on top of it. Deep Agents is a more opinionated harness on top of `create_agent` — same building blocks, but with filesystem, sub-agents, context management, and skills bundled in."*
4. **官方博客《Deep Agents vs LangChain vs LangGraph》**（2026-08-06）：*"Fun fact: Deep Agents is actually just the core LangChain agent plus a bunch of middleware!"*

### 3.4 不是替代关系

- 官方强调："All three are fully composable, so you can move between layers instead of picking one."（三者完全可组合，可层间切换，而非二选一。）
- 第三方分析（Towards AI）：*"Deep Agents is not a replacement for LangGraph — it uses LangGraph under the hood for everything."*
- 社区（LinkedIn）：*"They are not really competitors... Deep Agents sits above them as a more opinionated harness for complex agentic tasks."*

### 3.5 官方选择建议

| 场景 | 官方建议 |
|---|---|
| 大多数构建者、开箱即用、长时程自主任务 | **先试 DeepAgents** |
| 想要极简循环、自己搭建提示词与工具 | 用 LangChain 的 `create_agent` |
| 需要完全自定义图、确定性流程/复杂工作流 | 下沉到 LangGraph |

---

## 四、结论与建议

1. **一句话总结**：DeepAgents 是 LangChain 生态栈的最高层"智能体套件"，构建在 LangChain 框架之上、以 LangGraph 为运行时，是 LangChain 官方对"深度智能体"架构（规划、文件系统、子智能体、上下文管理）的开箱即用封装，**不是替代品，而是同一技术栈的组成部分**。
2. **技术选型建议**：若追求开箱即用、快速构建长时程自主 agent，推荐 DeepAgents；若需要精细控制 agent 循环与提示词，用 LangChain `create_agent`；若需高度定制、复杂有状态工作流，则用 LangGraph。
3. **注意事项**：主流语境下 "DeepAgents" 特指 LangChain 的 `deepagents` 项目；但"深度智能体（deep agent）"作为一种架构概念也被业界广泛使用（如 OpenAI Deep Research 类产品），两者概念相关但来源不同，需注意区分。

---

## 参考文献

1. GitHub 仓库（DeepAgents Python）：https://github.com/langchain-ai/deepagents
2. GitHub 仓库（DeepAgents JS/TS）：https://github.com/langchain-ai/deepagentsjs
3. GitHub 仓库（LangChain）：https://github.com/langchain-ai/langchain
4. GitHub 仓库（LangGraph）：https://github.com/langchain-ai/langgraph
5. 官方文档（Deep Agents Overview）：https://docs.langchain.com/oss/python/deepagents/overview
6. 官方文档（LangGraph Overview）：https://docs.langchain.com/oss/python/langgraph/overview
7. 官方博客《Doubling down on Deep Agents》：https://www.langchain.com/blog/doubling-down-on-deepagents
8. 官方博客《Introducing Deep Agents CLI》：https://www.langchain.com/blog/introducing-deepagents-cli
9. Wikipedia – LangChain：https://en.wikipedia.org/wiki/LangChain
10. DeepWiki 代码解析：https://deepwiki.com/langchain-ai/deepagents
11. LangChain 官方 YouTube 讲解：https://www.youtube.com/watch?v=IVts6ztrkFg
