# 研究笔记：DeepAgents 与 LangChain 的关系

> 研究时间：2026-08-20
> 方法：三个 web-researcher 子代理并行网络搜索 + 官方文档/博客原文抓取 + 社区讨论交叉验证

## 核心结论
DeepAgents 不是独立框架，而是 LangChain 官方生态体系中的组成部分——是"站在最顶层的 Agent Harness（智能体套件）"。它构建在 LangChain 之上，以 LangGraph 为运行时。

## 一、DeepAgents 是什么
- **Deep Agents（deepagents）是 LangChain 团队（langchain-ai）开发维护的开源"智能体工具链/框架"（Agent Harness）**，官方定位 "The batteries-included agent harness"（开箱即用的智能体框架）。
- "Deep Agents" 术语由 LangChain 官方提出，描述能处理复杂开放式任务、长时运行的 AI 智能体，四大要素：规划工具、文件系统、子智能体、详尽提示词。
- 灵感来自 Claude Code、Deep Research、Manus 的架构，抽象标准化为模型无关、可定制、生产就绪的库。
- 时间线：概念约 2025-08 提出，2025-10 发布 CLI；2026-08 时 GitHub 约 28k stars/3.9k forks，版本 v0.7+，已发布 JS 版（deepagentsjs）。
- 核心能力：执行环境（工具调用、虚拟文件系统、沙箱、代码解释器）、上下文管理（技能、记忆、摘要）、委派（子智能体、任务规划）、人在环路。
- 模型无关，支持 MCP，底层基于 LangGraph，配合 LangSmith。

## 二、LangChain 是什么
- LangChain 是开源 LLM 应用开发框架，创始人 Harrison Chase，2022-10 发布，2023-01 成立公司 LangChain Inc.（langchain-ai），MIT 协议，Python + JS/TS。
- 融资：2023 种子轮（Benchmark 1000万 + 红杉 2000万），2024 B 轮 1.25 亿美元估值 12.5 亿美元。
- 生态三件套：LangChain（Agent 框架/抽象层）、LangGraph（编排运行时）、LangSmith（可观测平台）。
- GitHub 星数约 7 万+，2025 多份榜单位列最佳开源 AI Agent 框架第一。
- 原生支持并深度集成 agent 框架（LangGraph）。

## 三、两者关系（重点）
- **是 LangChain 生态的一部分**：代码托管于 github.com/langchain-ai/deepagents。
- **明确构建在 LangChain 之上**：官方文档 "a standalone library built on top of LangChain's core building blocks for agents. It uses the LangGraph runtime"。
- **分层叠加（集成）关系，非对比或替代**：官方 "three layers of our open source agent stack"。

### 依赖链条
```
DeepAgents（Agent Harness，最高层，开箱即用）
    ↑ 构建于
LangChain create_agent（Agent Framework，中间层）
    ↑ 构建于
LangGraph（Agent Runtime，最底层，图运行时）
```

### 官方原文
- 官方博客（2025-10-28）："deepagents is built on top of langchain's agent abstraction, which in turn is built on top of langgraph's agent runtime."
- GitHub FAQ："LangGraph is the graph runtime. LangChain's create_agent is a minimal agent harness on top of it. Deep Agents is a more opinionated harness on top of create_agent."
- 官方博客（2026-08-06）："Fun fact: Deep Agents is actually just the core LangChain agent plus a bunch of middleware!"

### 选择建议（官方）
| 场景 | 建议 |
|---|---|
| 大多数构建者、开箱即用 | 先试 DeepAgents |
| 极简循环、自己搭提示词 | LangChain create_agent |
| 完全自定义图、复杂工作流 | LangGraph |

## 四、参考来源
- GitHub：https://github.com/langchain-ai/deepagents 、https://github.com/langchain-ai/deepagentsjs 、https://github.com/langchain-ai/langchain 、https://github.com/langchain-ai/langgraph
- 官方文档：https://docs.langchain.com/oss/python/deepagents/overview 、https://docs.langchain.com/oss/python/langgraph/overview
- 官方博客：https://www.langchain.com/blog/doubling-down-on-deepagents 、https://www.langchain.com/blog/introducing-deepagents-cli
- Wikipedia：https://en.wikipedia.org/wiki/LangChain
- 其他：https://deepwiki.com/langchain-ai/deepagents 、https://www.youtube.com/watch?v=IVts6ztrkFg
