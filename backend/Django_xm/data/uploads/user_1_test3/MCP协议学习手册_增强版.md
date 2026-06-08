# 零基础到进阶的MCP协议系统学习手册（增强版）

## 前言

本手册旨在帮助用户系统学习MCP（Model Context Protocol）协议，从协议原理到开发实践，循序渐进地掌握AI工具集成的核心知识点。手册中的所有实战案例均基于MCP最新规范，可直接复制运行。

**增强版特色**：
- **原理驱动**：每个知识点先讲协议原理，再给实战代码，知其然更知其所以然
- **场景导向**：结合真实业务场景，说明何时用、怎么用、为什么用
- **对比选型**：关键决策点提供对比表格，帮助做出正确选择
- **🔧 操作详解**：每个操作步骤附带详细执行说明、目的与预期效果
- **🔬 原理深挖**：深入解析每个技术点的底层工作原理、核心逻辑和应用场景
- **💡 背景扩展**：补充必要的背景知识和扩展信息，理解技术在整体体系中的价值

---

## 准备工作

### 操作步骤

```bash
conda activate Django_xm
pip install mcp>=1.0.0 httpx>=0.27.0 uvicorn>=0.29.0
```

### 执行说明

| 步骤 | 命令 | 目的 | 预期效果 |
|------|------|------|---------|
| 1 | `conda activate Django_xm` | 激活Conda虚拟环境 | 终端提示符前出现 `(Django_xm)` 前缀，表示已进入隔离的Python环境 |
| 2 | `pip install mcp>=1.0.0` | 安装MCP Python SDK | 安装MCP核心库，包含 `FastMCP`、`ClientSession` 等核心类 |
| 3 | `pip install httpx>=0.27.0` | 安装HTTP客户端库 | 为SSE/Streamable HTTP传输提供异步HTTP通信能力 |
| 4 | `pip install uvicorn>=0.29.0` | 安装ASGI服务器 | 为SSE模式的MCP Server提供HTTP服务运行时 |

### 技术原理

**为什么需要虚拟环境？** Conda虚拟环境通过创建独立的Python解释器和包目录，避免不同项目之间的依赖冲突。MCP SDK依赖特定版本的 `pydantic`、`httpx-sse` 等库，隔离环境确保版本一致性。

**依赖关系链**：
```
mcp (SDK核心)
├── pydantic >= 2.0        ← 数据模型验证，Tool的inputSchema基于此
├── httpx-sse              ← SSE传输层实现
├── anyio                  ← 异步IO抽象层，兼容asyncio/trio
├── starlette              ← SSE模式下的Web框架
└── httpx                  ← HTTP客户端（SSE Client使用）

uvicorn
├── uvloop (可选)          ← 高性能事件循环（Linux/macOS）
└── httptools              ← HTTP解析加速
```

**💡 扩展知识**：如果需要LangChain集成，还需额外安装 `langchain-mcp` 和 `langchain-openai`：
```bash
pip install langchain-mcp langchain-openai langgraph
```

---

## 模块一：MCP协议概述

### 1.1 什么是MCP

MCP（Model Context Protocol）是由Anthropic于2024年11月提出的开放协议，旨在标准化AI模型与外部数据源、工具之间的通信方式。

**💡 背景知识：MCP为什么诞生？**

在MCP出现之前，AI应用集成外部工具面临严重的碎片化问题：
- **OpenAI** 有自己的 Function Calling API
- **LangChain** 有自己的 Tool 抽象
- **各家IDE** 有自己的插件协议
- 每新增一个AI应用，就需要重新开发与每个数据源的集成代码

这类似于早期互联网时代，每个浏览器有自己的HTML渲染方式，直到W3C制定统一标准。MCP的角色正是AI工具集成领域的"W3C标准"。

```
MCP解决的核心问题：

传统AI应用集成方式：
┌──────────┐    ┌──────────┐    ┌──────────┐
│ AI应用A   │───▶│ 数据源X   │    │          │
│          │───▶│ 数据源Y   │    │ 每个应用  │
│          │───▶│ 工具Z     │    │ 独立集成  │
└──────────┘    └──────────┘    └──────────┘
┌──────────┐    ┌──────────┐
│ AI应用B   │───▶│ 数据源X   │ ← 重复开发集成代码
│          │───▶│ 数据源Y   │ ← 维护成本高
│          │───▶│ 工具Z     │ ← 标准不统一
└──────────┘    └──────────┘

MCP标准化方式：
┌──────────┐                    ┌──────────┐
│ AI应用A   │───┐               │ MCP服务器 │
│          │   │   ┌────────┐  │  数据源X  │
├──────────┤   ├──▶│ MCP协议 │◀─│  数据源Y  │
│ AI应用B   │   │   └────────┘  │  工具Z    │
│          │───┘               │          │
└──────────┘                    └──────────┘
  一次集成，所有应用可用              统一标准
```

**MCP核心价值**：

| 价值 | 说明 | 🔬 底层原理 |
|------|------|------------|
| 标准化 | 统一的协议规范，消除集成碎片化 | 基于JSON-RPC 2.0标准，定义了统一的请求/响应/通知消息格式 |
| 可复用 | MCP服务器一次开发，所有MCP客户端可用 | 通过 `tools/list`、`resources/list` 等发现机制，客户端无需预知服务端能力 |
| 安全性 | 内置权限控制和沙箱机制 | Tool Annotations标注操作属性，Host据此决定是否需要用户确认 |
| 可组合 | 多个MCP服务器可灵活组合 | Host管理多个Client，每个Client与一个Server建立1:1连接 |
| 可发现 | 客户端可动态发现服务器提供的工具和资源 | 初始化时交换能力信息，运行时通过list方法查询 |

**💡 设计哲学**：MCP的设计借鉴了多个成熟协议的思想：
- **LSP（Language Server Protocol）**：MCP的Host-Client-Server三层架构直接参考了LSP的设计，LSP解决了编辑器与语言服务器的标准化通信问题
- **JSON-RPC 2.0**：消息格式采用JSON-RPC 2.0，与LSP保持一致，降低学习成本
- **RESTful API**：Resource的URI设计借鉴了REST的统一资源标识理念

### 1.2 MCP与类似协议对比

| 维度 | MCP | OpenAI Function Calling | LangChain Tools | API网关 |
|------|-----|------------------------|----------------|---------|
| 类型 | 开放协议 | API特性 | 框架功能 | 基础设施 |
| 标准化 | 开放标准 | 厂商私有 | 框架私有 | 行业标准 |
| 传输层 | stdio/SSE/Streamable HTTP | HTTP API | Python函数 | HTTP |
| 发现机制 | 内置 | 无 | 无 | OpenAPI |
| 双向通信 | 支持 | 不支持 | 不支持 | 不支持 |
| 流式响应 | 支持 | 支持 | 部分支持 | 支持 |
| 安全模型 | 内置 | 依赖外部 | 依赖外部 | 内置 |
| 适用范围 | 任何AI应用 | OpenAI模型 | LangChain应用 | 通用 |

**🔬 深入对比分析**：

1. **MCP vs OpenAI Function Calling**：
   - Function Calling是OpenAI API的一个特性，工具定义嵌入在API请求中，只能在OpenAI模型调用时使用
   - MCP是独立于模型的协议，任何AI应用（无论使用GPT、Claude还是开源模型）都可以通过MCP Client连接MCP Server
   - 关键差异：Function Calling的"工具发现"发生在API调用时（每次请求都携带工具定义），MCP的"工具发现"发生在连接建立时（一次发现，多次使用）

2. **MCP vs LangChain Tools**：
   - LangChain Tools是Python函数级别的抽象，只能在LangChain框架内使用
   - MCP Server是独立进程，可以被任何语言的客户端调用
   - 关键差异：LangChain Tools是进程内调用（函数调用），MCP是进程间通信（IPC/RPC）

3. **MCP vs API网关**：
   - API网关解决的是服务间路由、限流、认证等基础设施问题
   - MCP解决的是AI模型如何"理解"和"调用"外部工具的问题
   - 关键差异：API网关关注"如何调用"，MCP关注"调用什么"和"如何描述"

### 1.3 MCP核心架构

```
MCP架构（客户端-服务器模型）：

┌─────────────────────────────────────────────────────────┐
│                    MCP架构图                              │
│                                                          │
│  ┌───────────────────────────────────────────────────┐  │
│  │                  MCP Host（宿主应用）               │  │
│  │                                                    │  │
│  │  ┌──────────────┐  ┌──────────────┐              │  │
│  │  │ MCP Client 1 │  │ MCP Client 2 │              │  │
│  │  │ (连接服务器A) │  │ (连接服务器B) │              │  │
│  │  └──────┬───────┘  └──────┬───────┘              │  │
│  └─────────┼─────────────────┼───────────────────────┘  │
│            │                 │                           │
│     MCP协议(stdio/SSE)  MCP协议(stdio/SSE)              │
│            │                 │                           │
│  ┌─────────▼─────────┐ ┌────▼──────────────┐           │
│  │  MCP Server A     │ │  MCP Server B     │           │
│  │  ┌─────────────┐  │ │  ┌─────────────┐  │           │
│  │  │ Tools       │  │ │  │ Tools       │  │           │
│  │  │ • search    │  │ │  │ • query_db  │  │           │
│  │  │ • analyze   │  │ │  │ • insert    │  │           │
│  │  ├─────────────┤  │ │  ├─────────────┤  │           │
│  │  │ Resources   │  │ │  │ Resources   │  │           │
│  │  │ • docs/     │  │ │  │ • schema/   │  │           │
│  │  ├─────────────┤  │ │  ├─────────────┤  │           │
│  │  │ Prompts     │  │ │  │ Prompts     │  │           │
│  │  │ • summarize │  │ │  │ • explain   │  │           │
│  │  └─────────────┘  │ │  └─────────────┘  │           │
│  │         │         │ │         │         │           │
│  └─────────┼─────────┘ └─────────┼─────────┘           │
│            │                     │                       │
│  ┌─────────▼─────────┐ ┌────────▼─────────┐           │
│  │  外部服务/数据源    │ │  外部服务/数据源   │           │
│  │  (GitHub/数据库等)  │ │  (文件系统/API等)  │           │
│  └───────────────────┘ └──────────────────┘           │
└─────────────────────────────────────────────────────────┘
```

**核心概念详解**：

| 概念 | 说明 | 🔬 架构角色 |
|------|------|------------|
| Host | 宿主应用（如Claude Desktop、IDE等），管理多个Client | **进程管理者**：负责创建和销毁Client实例，协调多个Client之间的资源分配 |
| Client | 协议客户端，与Server建立1:1连接 | **通信桥梁**：封装JSON-RPC消息的序列化/反序列化，管理连接生命周期 |
| Server | 协议服务端，提供Tools/Resources/Prompts能力 | **能力提供者**：注册和暴露可被AI调用的能力，处理Client的请求并返回结果 |
| Tool | 可被AI调用的函数/操作 | **动作单元**：具有副作用的操作（如查询数据库、发送消息），AI通过 `tools/call` 调用 |
| Resource | 可被AI读取的数据/文件 | **数据单元**：只读的上下文信息（如配置文件、日志），AI通过 `resources/read` 获取 |
| Prompt | 预定义的提示词模板 | **模板单元**：参数化的提示词生成器，确保AI交互的一致性和质量 |

**🔬 架构设计原理**：

1. **为什么是Host-Client-Server三层？**
   - Host层负责用户交互和AI模型调度，是"大脑"
   - Client层负责协议通信，是"翻译官"，将AI的意图转化为MCP协议消息
   - Server层负责能力实现，是"执行者"，将协议消息转化为具体操作
   - 三层解耦使得：Host可以同时管理多种类型的Server；Client与Server之间通过标准协议通信，互不依赖具体实现

2. **为什么Client与Server是1:1关系？**
   - 每个Client维护独立的连接状态（会话ID、能力信息等）
   - 1:1关系简化了状态管理，避免了多路复用带来的复杂性
   - Host通过管理多个Client来实现与多个Server的通信

---

## 模块二：MCP协议规范

### 2.1 传输机制

MCP支持三种传输方式：

```
1. stdio（标准输入输出）
┌──────────────┐    stdin/stdout    ┌──────────────┐
│ MCP Client   │◀══════════════════▶│ MCP Server   │
│              │   JSON-RPC消息     │ (子进程)      │
└──────────────┘                    └──────────────┘
适用场景：本地开发、CLI工具、IDE集成
特点：最简单、延迟最低、仅限本机

2. SSE（Server-Sent Events）+ HTTP POST
┌──────────────┐   POST /messages   ┌──────────────┐
│ MCP Client   │───────────────────▶│ MCP Server   │
│              │◀─── SSE /sse ──────│ (HTTP服务)   │
└──────────────┘                    └──────────────┘
适用场景：远程服务、Web应用
特点：支持远程、基于HTTP、服务端推送

3. Streamable HTTP（推荐，MCP 2025-03规范新增）
┌──────────────┐   POST /mcp        ┌──────────────┐
│ MCP Client   │───────────────────▶│ MCP Server   │
│              │◀─── SSE stream ────│ (HTTP服务)   │
└──────────────┘                    └──────────────┘
适用场景：远程服务、需要流式响应
特点：单端点、可选SSE升级、支持无状态
```

**🔬 传输机制底层原理详解**：

#### stdio传输

**工作原理**：
- Host通过操作系统API（如 `subprocess.Popen`）启动Server作为子进程
- Client通过子进程的 `stdin`（标准输入）发送JSON-RPC消息
- Server通过子进程的 `stdout`（标准输出）返回JSON-RPC消息
- 每条消息以换行符 `\n` 分隔，消息前缀为内容长度（类似LSP的帧格式）

**消息帧格式**：
```
Content-Length: 123\r\n
\r\n
{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}\n
```

**为什么stdio延迟最低？**
- 进程间通信走操作系统内核管道（pipe），无需网络协议栈
- 没有HTTP请求头、TCP握手等开销
- 数据在内核空间直接拷贝，不经过网络设备

**限制**：
- 只能本机通信，无法跨网络
- Server必须是Host的子进程，无法独立部署
- 依赖操作系统的进程管理能力

#### SSE传输

**工作原理**：
1. Client首先向Server的 `/sse` 端点发起GET请求，建立SSE连接
2. Server返回 `Content-Type: text/event-stream` 响应，连接保持长连接
3. Client通过向 `/messages` 端点发送POST请求来发送消息
4. Server通过SSE连接向Client推送响应和通知

**SSE协议细节**：
```
# Client → Server（HTTP POST）
POST /messages?sessionId=abc123
Content-Type: application/json

{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"search"}}

# Server → Client（SSE推送）
event: message
data: {"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text","text":"结果"}]}}
```

**SSE vs WebSocket**：MCP选择SSE而非WebSocket的原因：
- SSE基于标准HTTP，无需协议升级握手，防火墙友好
- SSE天然支持服务端推送，MCP的场景主要是"客户端请求→服务端响应"
- SSE连接断开后自动重连，内置 `Last-Event-ID` 机制

#### Streamable HTTP传输

**工作原理**：
1. Client向Server的 `/mcp` 端点发送POST请求
2. 如果请求需要流式响应，Server返回 `Content-Type: text/event-stream`
3. 如果请求不需要流式响应，Server直接返回JSON响应
4. 支持无状态模式：Server不需要维护会话状态，每个请求自包含

**与SSE的关键区别**：
- SSE需要两个端点（`/sse` + `/messages`），Streamable HTTP只需一个端点（`/mcp`）
- SSE必须建立长连接，Streamable HTTP可以无状态
- Streamable HTTP支持可选的SSE升级，只在需要流式响应时才升级

**传输方式对比**：

| 维度 | stdio | SSE | Streamable HTTP |
|------|-------|-----|-----------------|
| 部署方式 | 本地子进程 | 远程HTTP | 远程HTTP |
| 连接方向 | 双向管道 | 客户端→服务端POST + 服务端→客户端SSE | 客户端→服务端POST + 可选SSE |
| 服务发现 | 无需 | /sse端点 | /mcp端点 |
| 状态管理 | 有状态 | 有状态 | 可无状态 |
| 防火墙友好 | 否 | 是 | 是 |
| 复杂度 | 低 | 中 | 中 |
| 推荐场景 | 本地工具 | 遗留兼容 | 新项目推荐 |

**💡 选型建议**：
- 本地开发/IDE集成 → **stdio**（零配置，开箱即用）
- 已有SSE部署 → **SSE**（向后兼容）
- 新项目/远程部署 → **Streamable HTTP**（更简洁，支持无状态）

### 2.2 消息格式（JSON-RPC 2.0）

```
MCP基于JSON-RPC 2.0协议，消息分为三类：

1. Request（请求）
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "search",
    "arguments": {"query": "MCP协议"}
  }
}

2. Response（响应）
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "content": [
      {"type": "text", "text": "搜索结果..."}
    ]
  }
}

3. Notification（通知，无id，不需要响应）
{
  "jsonrpc": "2.0",
  "method": "notifications/progress",
  "params": {
    "progressToken": "token123",
    "progress": 50,
    "total": 100
  }
}
```

**🔬 JSON-RPC 2.0 核心原理**：

**为什么选择JSON-RPC 2.0？**
1. **轻量级**：相比gRPC（需要protobuf定义和代码生成）、SOAP（XML格式冗余），JSON-RPC消息简洁易读
2. **语言无关**：JSON是几乎所有编程语言都支持的数据格式
3. **与LSP一致**：Language Server Protocol也使用JSON-RPC 2.0，开发者容易迁移
4. **双向通信**：JSON-RPC 2.0不限定请求方向，Server也可以向Client发请求（如 `sampling/createMessage`）

**消息类型详解**：

| 类型 | 特征 | 用途 | 是否需要响应 |
|------|------|------|------------|
| Request | 有 `id` 和 `method` | 客户端/服务端请求操作 | 是 |
| Response | 有 `id` 和 `result` 或 `error` | 对Request的应答 | - |
| Notification | 有 `method` 但无 `id` | 单向通知，如进度更新 | 否 |

**💡 关键细节**：
- `id` 字段用于匹配Request和Response，Client必须保证每个Request的id唯一
- Notification没有 `id`，因此不需要（也无法）回复
- 错误响应格式：`{"jsonrpc": "2.0", "id": 1, "error": {"code": -32600, "message": "Invalid Request"}}`
- MCP定义了专用的错误码范围：`-32000` 到 `-32099`（Server错误）

### 2.3 连接生命周期

```
MCP连接生命周期：

1. 初始化阶段（Initialize）
   Client                              Server
     │─── initialize ──────────────────▶│
     │◀── initialize result ───────────│
     │─── initialized (notification) ──▶│
     │                                  │
     │  交换能力信息：                    │
     │  • 客户端能力（采样等）            │
     │  • 服务端能力（工具/资源/提示词）   │
     │  • 协议版本协商                    │

2. 正常通信阶段
   Client                              Server
     │─── tools/list ──────────────────▶│
     │◀── tools list result ───────────│
     │─── tools/call ──────────────────▶│
     │◀── tools call result ───────────│
     │─── resources/list ──────────────▶│
     │◀── resources list result ───────│
     │─── resources/read ──────────────▶│
     │◀── resources read result ───────│
     │─── prompts/list ────────────────▶│
     │◀── prompts list result ─────────│
     │─── prompts/get ─────────────────▶│
     │◀── prompts get result ──────────│

3. 关闭阶段
   Client                              Server
     │  （关闭连接/终止子进程）            │
```

**🔬 生命周期状态机详解**：

MCP连接本质上是一个有限状态机，包含三个状态：

```
┌──────────┐  initialize  ┌───────────┐  initialized  ┌──────────┐
│  未连接   │─────────────▶│  初始化中  │──────────────▶│  已连接   │
│ (Uninit) │              │(Initing)  │               │(Ready)   │
└──────────┘              └───────────┘               └────┬─────┘
                                                        │
                                          close/timeout │
                                                        ▼
                                                     ┌──────────┐
                                                     │  已关闭   │
                                                     │ (Closed) │
                                                     └──────────┘
```

**初始化三步握手的意义**：

1. **`initialize` 请求**：Client告知Server自己的协议版本和能力
   - 目的：让Server知道Client支持什么功能，决定如何响应
   - 关键参数：`protocolVersion`（协议版本）、`capabilities`（客户端能力）、`clientInfo`（客户端信息）

2. **`initialize` 响应**：Server告知Client自己的协议版本和能力
   - 目的：让Client知道Server提供什么功能，决定如何使用
   - 关键参数：`protocolVersion`（协商后的版本）、`capabilities`（服务端能力）、`serverInfo`（服务端信息）

3. **`initialized` 通知**：Client确认初始化完成
   - 目的：这是一个"确认信号"，告诉Server可以开始正常通信
   - 为什么用通知而非请求？因为不需要Server再回复，减少一次网络往返

**💡 版本协商机制**：
- Client发送自己支持的最高版本号
- Server如果不支持该版本，返回错误；否则返回双方都支持的版本号
- 当前最新版本：`2025-03-26`

### 2.4 协议能力协商

```json
// 客户端初始化请求
{
  "protocolVersion": "2025-03-26",
  "capabilities": {
    "roots": {
      "listChanged": true
    },
    "sampling": {}
  },
  "clientInfo": {
    "name": "my-mcp-client",
    "version": "1.0.0"
  }
}

// 服务端初始化响应
{
  "protocolVersion": "2025-03-26",
  "capabilities": {
    "tools": {
      "listChanged": true
    },
    "resources": {
      "subscribe": true,
      "listChanged": true
    },
    "prompts": {
      "listChanged": true
    },
    "logging": {}
  },
  "serverInfo": {
    "name": "my-mcp-server",
    "version": "1.0.0"
  }
}
```

**🔬 能力协商原理详解**：

**为什么需要能力协商？**
- MCP协议是可扩展的，不是所有Client/Server都实现全部功能
- 能力协商让双方在通信前明确对方支持什么，避免调用不支持的方法
- 类似于HTTP的 `Accept` 头协商内容格式

**能力字段解析**：

| 能力 | 声明方 | 含义 | 子参数 |
|------|--------|------|--------|
| `roots` | Client | Client可以提供文件系统根目录信息 | `listChanged`：根目录列表变化时是否通知Server |
| `sampling` | Client | Client支持让Server请求LLM生成（反向调用） | 无子参数，声明即支持 |
| `tools` | Server | Server提供工具能力 | `listChanged`：工具列表变化时是否通知Client |
| `resources` | Server | Server提供资源能力 | `subscribe`：是否支持资源订阅；`listChanged`：资源列表变化时是否通知 |
| `prompts` | Server | Server提供提示词模板能力 | `listChanged`：模板列表变化时是否通知 |
| `logging` | Server | Server支持日志级别控制 | 无子参数，声明即支持 |

**💡 `listChanged` 的作用**：
- 当设为 `true` 时，表示能力列表可能动态变化
- 变化时发送 `notifications/tools/list_changed` 等通知
- Client收到通知后应重新调用 `tools/list` 获取最新列表
- 典型场景：插件热加载、配置动态更新

---

## 模块三：MCP三大核心能力

### 3.1 Tools（工具）

Tools是MCP最核心的能力，允许AI模型调用外部函数执行操作。

```
Tool定义结构：
┌──────────────────────────────────────────────┐
│  Tool                                         │
│  ├── name: string          ← 工具名称         │
│  ├── description: string   ← 工具描述         │
│  ├── inputSchema: JSON Schema ← 输入参数定义  │
│  └── annotations: object   ← 行为标注         │
│      ├── readOnlyHint      ← 是否只读         │
│      ├── destructiveHint   ← 是否破坏性操作   │
│      ├── idempotentHint    ← 是否幂等         │
│      └── openWorldHint     ← 是否访问外部     │
└──────────────────────────────────────────────┘

Tool调用流程：
AI模型 → 生成tool_use → MCP Client → MCP Server → 执行工具 → 返回结果 → AI模型继续生成
```

**🔬 Tool设计动机与原理**：

**为什么Tool是最核心的能力？**
- AI模型本身是"无状态的知识引擎"，无法直接操作外部世界
- Tool为AI模型提供了"手和脚"，使其能够执行实际操作
- 没有Tool的AI只能"说"，有了Tool的AI可以"做"

**Tool调用的完整链路**：

```
1. 用户提问："查询production数据库的用户数"
2. AI模型分析意图 → 决定调用 query_database 工具
3. AI模型生成 tool_use：
   {"name": "query_database", "arguments": {"sql": "SELECT COUNT(*) FROM users", "database": "production"}}
4. MCP Client 将 tool_use 封装为 JSON-RPC Request：
   {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "query_database", "arguments": {...}}}
5. MCP Server 接收请求 → 路由到对应的处理函数 → 执行SQL查询
6. MCP Server 返回结果：
   {"jsonrpc": "2.0", "id": 2, "result": {"content": [{"type": "text", "text": "用户数: 12345"}]}}
7. MCP Client 将结果返回给 AI模型
8. AI模型基于结果生成最终回答："production数据库中共有12345个用户"
```

**Tool定义示例**：

```json
{
  "name": "query_database",
  "description": "查询MySQL数据库，执行只读SQL查询",
  "inputSchema": {
    "type": "object",
    "properties": {
      "sql": {
        "type": "string",
        "description": "SQL查询语句（仅支持SELECT）"
      },
      "database": {
        "type": "string",
        "description": "数据库名称",
        "enum": ["production", "analytics", "staging"]
      },
      "limit": {
        "type": "integer",
        "description": "返回行数限制",
        "default": 100,
        "maximum": 1000
      }
    },
    "required": ["sql", "database"]
  },
  "annotations": {
    "readOnlyHint": true,
    "destructiveHint": false,
    "idempotentHint": true,
    "openWorldHint": false
  }
}
```

**🔬 inputSchema 深度解析**：

`inputSchema` 使用 JSON Schema 定义工具的输入参数，其作用不仅是"文档"，更是AI模型理解如何调用工具的关键：

1. **AI模型如何选择工具？** → 依赖 `name` + `description`
2. **AI模型如何构造参数？** → 依赖 `inputSchema` 中的 `properties` 和 `description`
3. **如何验证参数合法性？** → 依赖 `required`、`enum`、`maximum` 等约束

**💡 JSON Schema 常用约束**：

| 约束 | 适用类型 | 作用 | 示例 |
|------|---------|------|------|
| `enum` | string/integer | 限定可选值 | `"enum": ["production", "staging"]` |
| `default` | 任意 | 默认值 | `"default": 100` |
| `maximum` | integer/number | 最大值 | `"maximum": 1000` |
| `minimum` | integer/number | 最小值 | `"minimum": 1` |
| `pattern` | string | 正则校验 | `"pattern": "^SELECT"` |
| `maxLength` | string | 最大长度 | `"maxLength": 5000` |

**Annotations 详解**：

| 注解 | 含义 | Host行为 | 典型场景 |
|------|------|---------|---------|
| `readOnlyHint: true` | 只读操作，无副作用 | 可自动执行，无需确认 | 查询数据库、读取文件 |
| `destructiveHint: true` | 破坏性操作 | 必须用户确认后执行 | 删除文件、DROP表 |
| `idempotentHint: true` | 幂等操作，可安全重试 | 失败后可自动重试 | 创建目录（已存在不报错） |
| `openWorldHint: true` | 访问外部系统 | 需审计日志 | HTTP请求、发送邮件 |

### 3.2 Resources（资源）

Resources是MCP提供的只读数据访问机制，让AI模型可以获取上下文信息。

```
Resource类型：

1. 文本资源（text/plain）
   ├── 代码文件
   ├── 配置文件
   ├── 日志文件
   └── 文档内容

2. 二进制资源（application/octet-stream）
   ├── 图片
   ├── PDF文件
   └── 其他二进制数据

Resource URI格式：
  file:///path/to/file          ← 本地文件
  db://database/table/row       ← 数据库记录
  api://service/endpoint        ← API数据
  git://repo/path/file          ← Git仓库文件
  custom://scheme/resource      ← 自定义资源
```

**🔬 Resources vs Tools 的设计哲学**：

| 维度 | Resources | Tools |
|------|-----------|-------|
| 语义 | "这是什么"（数据） | "做什么"（操作） |
| 副作用 | 无（只读） | 可能有（可写） |
| 调用方式 | `resources/read` | `tools/call` |
| 缓存 | 可缓存（幂等） | 不宜缓存 |
| 订阅 | 支持变更订阅 | 不支持 |
| AI行为 | 主动获取上下文 | 被动响应调用 |

**为什么需要Resources？**
- 如果只有Tools，AI每次获取配置信息都需要"调用工具"，语义上不自然
- Resources提供了"数据源"的抽象，AI可以像读取文件一样获取上下文
- Resources支持订阅机制，数据变化时主动通知AI，实现实时感知

```
Resource订阅机制：

Client                              Server
  │─── resources/subscribe ────────▶│
  │    {"uri": "file:///app/log"}   │
  │                                 │
  │◀── notifications/resources/    │  （文件变化时主动推送）
  │    updated                      │
  │    {"uri": "file:///app/log"}   │
  │                                 │
  │─── resources/read ────────────▶│  （读取最新内容）
  │◀── resources/read result ─────│
```

**🔬 订阅机制原理**：
- 订阅基于URI，Client订阅特定资源的变化
- Server通过文件监视（如 `watchdog` 库）、数据库触发器等机制检测变化
- 变化时Server发送 `notifications/resources/updated` 通知
- Client收到通知后主动读取最新内容
- 这是一种"推拉结合"模式：推送通知 + 拉取内容

**💡 Resource URI模板**：
- 静态资源：`config://app/database` → 固定URI，返回固定内容
- 动态资源：`config://app/{name}` → URI模板，`{name}` 是参数
- Client通过 `resources/templates/list` 获取URI模板列表
- 模板机制使得一个Resource函数可以处理多种URI

### 3.3 Prompts（提示词模板）

Prompts是MCP提供的预定义提示词模板，支持参数化生成。

```
Prompt定义结构：
┌──────────────────────────────────────────────┐
│  Prompt                                       │
│  ├── name: string          ← 模板名称         │
│  ├── description: string   ← 模板描述         │
│  ├── arguments: array      ← 模板参数         │
│  │   ├── name: string      ← 参数名           │
│  │   ├── description: string ← 参数描述       │
│  │   └── required: boolean ← 是否必填         │
│  └── messages: array       ← 生成的消息序列   │
└──────────────────────────────────────────────┘

Prompt使用流程：
1. 客户端调用 prompts/list 获取可用模板
2. 客户端调用 prompts/get 获取具体模板内容
3. 服务端返回参数化后的消息序列
4. 客户端将消息序列发送给AI模型
```

**🔬 Prompts的设计动机**：

**为什么需要Prompts？**
1. **一致性**：确保不同用户、不同场景下使用相同的提示词结构
2. **可复用**：将常用的提示词模式封装为模板，避免重复编写
3. **可发现**：AI应用可以通过 `prompts/list` 发现可用的提示词模板
4. **参数化**：通过参数定制提示词内容，无需修改模板代码

**Prompt与Tool的区别**：
- Tool返回的是"操作结果"（数据）
- Prompt返回的是"消息序列"（指令），用于指导AI模型的行为
- Prompt的输出直接作为AI模型的输入，Tool的输出需要AI模型进一步处理

**💡 Prompt返回的消息序列**：
```json
{
  "messages": [
    {
      "role": "user",
      "content": {
        "type": "text",
        "text": "请审查以下python代码：\n```python\ndef add(a, b): return a + b\n```"
      }
    }
  ]
}
```
- 消息序列可以包含多条消息（如 system + user）
- 每条消息有 `role`（user/assistant）和 `content`
- 这种设计使得Prompt可以构建复杂的多轮对话上下文

---

## 模块四：MCP Server开发

### 4.1 使用Python SDK开发Server

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("my-tools-server")


@mcp.tool()
def search_docs(query: str, max_results: int = 5) -> str:
    """搜索技术文档，返回相关文档摘要

    Args:
        query: 搜索关键词
        max_results: 最大返回结果数
    """
    results = [
        {"title": f"文档{i}", "content": f"关于{query}的内容{i}"}
        for i in range(min(max_results, 5))
    ]
    return "\n".join(f"## {r['title']}\n{r['content']}" for r in results)


@mcp.tool()
def query_database(sql: str, database: str = "production") -> str:
    """执行只读SQL查询

    Args:
        sql: SQL查询语句（仅支持SELECT）
        database: 数据库名称
    """
    if not sql.strip().upper().startswith("SELECT"):
        return "错误：仅支持SELECT查询"
    return f"在{database}库执行查询: {sql}\n结果: ..."


@mcp.resource("config://app/{name}")
def get_config(name: str) -> str:
    """获取应用配置"""
    configs = {
        "database": "host=localhost;port=3306;db=myapp",
        "redis": "host=localhost;port=6379;db=0",
        "api": "base_url=https://api.example.com;timeout=30",
    }
    return configs.get(name, f"配置 {name} 不存在")


@mcp.resource("docs://api/{endpoint}")
def get_api_doc(endpoint: str) -> str:
    """获取API文档"""
    return f"# API文档: {endpoint}\n\n## 请求\nGET /api/{endpoint}\n\n## 响应\n200 OK"


@mcp.prompt()
def code_review(code: str, language: str = "python") -> str:
    """代码审查提示词模板

    Args:
        code: 待审查的代码
        language: 编程语言
    """
    return f"""请审查以下{language}代码，关注：
1. 代码质量和可读性
2. 潜在的Bug和安全问题
3. 性能优化建议
4. 最佳实践建议

代码：
```{language}
{code}
```"""


@mcp.prompt()
def explain_concept(concept: str, level: str = "intermediate") -> str:
    """概念解释提示词模板

    Args:
        concept: 要解释的概念
        level: 解释深度（beginner/intermediate/advanced）
    """
    level_desc = {
        "beginner": "用简单的语言和类比，适合初学者",
        "intermediate": "包含技术细节和实际应用",
        "advanced": "深入底层原理和高级用法",
    }
    return f"请{level_desc.get(level, '')}解释：{concept}"


if __name__ == "__main__":
    mcp.run()
```

**🔬 逐行原理解析**：

#### FastMCP 初始化

```python
mcp = FastMCP("my-tools-server")
```

**底层发生了什么？**
1. `FastMCP` 是MCP Python SDK提供的高层封装，底层是 `mcp.server.Server`
2. 参数 `"my-tools-server"` 是Server名称，会在初始化响应的 `serverInfo.name` 中返回给Client
3. `FastMCP` 内部维护三个注册表：
   - `_tools`：工具函数注册表（字典，key为工具名）
   - `_resources`：资源函数注册表（字典，key为URI模板）
   - `_prompts`：提示词模板注册表（字典，key为模板名）

#### @mcp.tool() 装饰器

```python
@mcp.tool()
def search_docs(query: str, max_results: int = 5) -> str:
    """搜索技术文档，返回相关文档摘要

    Args:
        query: 搜索关键词
        max_results: 最大返回结果数
    """
```

**装饰器工作原理**：
1. `@mcp.tool()` 将函数注册到 `_tools` 注册表
2. 自动从函数签名提取 `inputSchema`：
   - `query: str` → `{"type": "string", "description": "搜索关键词"}`
   - `max_results: int = 5` → `{"type": "integer", "default": 5, "description": "最大返回结果数"}`
3. 自动从docstring提取 `description`：
   - 函数docstring的第一行作为工具描述
   - `Args:` 部分作为参数描述
4. 返回值 `str` 会被自动包装为 `{"content": [{"type": "text", "text": 返回值}]}`

**💡 docstring规范很重要**：
- AI模型依赖 `description` 来理解工具用途，docstring写得好坏直接影响AI是否能正确选择工具
- 建议遵循Google风格的docstring格式

#### @mcp.resource() 装饰器

```python
@mcp.resource("config://app/{name}")
def get_config(name: str) -> str:
```

**URI模板原理**：
1. `"config://app/{name}"` 是URI模板，`{name}` 是路径参数
2. 当Client请求 `resources/read("config://app/database")` 时：
   - SDK从URI中提取 `name = "database"`
   - 调用 `get_config(name="database")`
   - 返回结果
3. URI模板使得一个函数可以处理多种URI请求

#### @mcp.prompt() 装饰器

```python
@mcp.prompt()
def code_review(code: str, language: str = "python") -> str:
```

**工作原理**：
1. `@mcp.prompt()` 将函数注册到 `_prompts` 注册表
2. 当Client调用 `prompts/get("code_review", arguments={"code": "...", "language": "python"})` 时：
   - SDK调用 `code_review(code="...", language="python")`
   - 返回值被包装为消息序列：`{"messages": [{"role": "user", "content": {"type": "text", "text": 返回值}}]}`

#### mcp.run()

```python
if __name__ == "__main__":
    mcp.run()
```

**run()方法做了什么？**
1. 默认使用stdio传输模式
2. 创建 `stdio_server` 上下文管理器
3. 从 `sys.stdin` 读取JSON-RPC消息
4. 路由消息到对应的处理方法（`tools/call` → 调用注册的工具函数）
5. 将处理结果写入 `sys.stdout`

### 4.2 Server配置与运行

#### 🔧 操作步骤

```bash
# stdio模式（默认）
python server.py

# SSE模式
python -c "from server import mcp; mcp.run(transport='sse')"

# 指定SSE端口
MCP_SSE_PORT=8080 python -c "from server import mcp; mcp.run(transport='sse')"
```

**执行说明**：

| 命令 | 目的 | 预期效果 |
|------|------|---------|
| `python server.py` | 以stdio模式启动Server | Server等待从stdin读取JSON-RPC消息，不会主动输出任何内容 |
| `mcp.run(transport='sse')` | 以SSE模式启动Server | Server启动HTTP服务，默认监听 `localhost:8000`，输出 `Uvicorn running on ...` |
| `MCP_SSE_PORT=8080` | 指定SSE端口 | Server监听 `localhost:8080` |

**🔬 stdio模式运行原理**：
- Server作为子进程运行，stdin/stdout被Host（如Claude Desktop）接管
- Server不会在终端输出任何内容（所有输出都通过stdout发送给Host）
- 如果直接在终端运行 `python server.py`，看起来会"卡住"，这是正常的——它在等待stdin输入

**Claude Desktop配置**：

```json
// ~/AppData/Roaming/Claude/claude_desktop_config.json (Windows)
// ~/.claude/claude_desktop_config.json (macOS/Linux)
{
  "mcpServers": {
    "my-tools": {
      "command": "python",
      "args": ["D:/Project/study_zl/mcp_server.py"],
      "env": {
        "DB_HOST": "localhost",
        "DB_PORT": "3306"
      }
    },
    "remote-api": {
      "url": "http://localhost:8080/sse"
    }
  }
}
```

**配置字段解析**：

| 字段 | 含义 | 适用传输 |
|------|------|---------|
| `command` | 启动Server的命令 | stdio |
| `args` | 命令参数 | stdio |
| `env` | 环境变量（注入到Server进程） | stdio |
| `url` | Server的SSE端点URL | SSE |

**🔬 Claude Desktop如何使用此配置**：
1. Claude Desktop启动时读取配置文件
2. 对于stdio配置：通过 `subprocess.Popen` 启动子进程，将stdin/stdout连接到MCP Client
3. 对于SSE配置：创建SSE Client连接到指定URL
4. 每个配置项创建一个独立的MCP Client实例

### 4.3 高级Server开发

```python
from mcp.server.fastmcp import FastMCP, Context
from typing import Any

mcp = FastMCP("advanced-server")


@mcp.tool()
async def long_running_task(task_name: str, ctx: Context) -> str:
    """长时间运行的任务，支持进度报告

    Args:
        task_name: 任务名称
    """
    total = 100
    for i in range(total):
        await ctx.report_progress(i, total, f"正在处理 {task_name}: {i}/{total}")
        import asyncio
        await asyncio.sleep(0.1)
    return f"任务 {task_name} 完成"


@mcp.tool()
def create_issue(title: str, body: str, labels: list[str] | None = None) -> dict[str, Any]:
    """创建GitHub Issue

    Args:
        title: Issue标题
        body: Issue内容
        labels: 标签列表
    """
    return {
        "id": 123,
        "title": title,
        "body": body,
        "labels": labels or [],
        "status": "created",
    }


@mcp.tool()
def search_with_pagination(
    query: str,
    page: int = 1,
    page_size: int = 10,
) -> dict[str, Any]:
    """分页搜索

    Args:
        query: 搜索关键词
        page: 页码（从1开始）
        page_size: 每页数量
    """
    return {
        "query": query,
        "page": page,
        "page_size": page_size,
        "total": 100,
        "results": [f"结果{i}" for i in range((page - 1) * page_size, page * page_size)],
    }


@mcp.resource("log://app/{date}")
def get_app_log(date: str) -> str:
    """获取应用日志

    Args:
        date: 日期（YYYY-MM-DD格式）
    """
    return f"[{date}] 应用运行日志内容..."


@mcp.resource("schema://database/{table}")
def get_table_schema(table: str) -> str:
    """获取数据库表结构"""
    schemas = {
        "users": "CREATE TABLE users (id INT PRIMARY KEY, name VARCHAR(50), email VARCHAR(100))",
        "orders": "CREATE TABLE orders (id INT PRIMARY KEY, user_id INT, amount DECIMAL(10,2))",
    }
    return schemas.get(table, f"表 {table} 不存在")


@mcp.prompt()
def debug_error(error_message: str, context: str = "") -> str:
    """调试错误提示词模板

    Args:
        error_message: 错误信息
        context: 上下文信息
    """
    prompt = f"""请帮我分析以下错误：

错误信息：
{error_message}"""

    if context:
        prompt += f"""

上下文信息：
{context}"""

    prompt += """

请提供：
1. 错误原因分析
2. 可能的解决方案
3. 预防措施"""
    return prompt


if __name__ == "__main__":
    mcp.run()
```

**🔬 高级特性原理解析**：

#### Context对象与进度报告

```python
async def long_running_task(task_name: str, ctx: Context) -> str:
```

**Context是什么？**
- `Context` 是MCP SDK注入的工具执行上下文对象
- 通过函数参数 `ctx: Context` 声明，SDK会自动注入
- 提供以下能力：
  - `ctx.report_progress(progress, total, message)` → 发送进度通知
  - `ctx.request_id` → 当前请求ID
  - `ctx.session` → 底层ServerSession对象

**进度报告原理**：
```python
await ctx.report_progress(i, total, f"正在处理 {task_name}: {i}/{total}")
```
1. SDK发送 `notifications/progress` 通知给Client
2. 通知格式：`{"method": "notifications/progress", "params": {"progressToken": "xxx", "progress": 50, "total": 100}}`
3. Client收到通知后可以展示进度条
4. `progressToken` 来自原始 `tools/call` 请求中的 `_meta.progressToken`

**💡 异步工具函数**：
- 工具函数可以是 `async def`（异步）或 `def`（同步）
- SDK内部使用 `anyio` 统一处理，同步函数会在线程池中执行
- 长时间运行的任务建议使用 `async def`，避免阻塞事件循环

#### 复杂返回类型

```python
def create_issue(...) -> dict[str, Any]:
```

**返回类型处理**：
- `str` → 自动包装为 `{"type": "text", "text": 返回值}`
- `dict` / `list` → 自动序列化为JSON字符串，再包装为text内容
- `bytes` → 包装为 `{"type": "image", "data": base64编码, "mimeType": "..."}`
- 也可以直接返回 `list[Content]` 来构造多内容响应

---

## 模块五：MCP Client开发

### 5.1 基础Client

```python
import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    server_params = StdioServerParameters(
        command="python",
        args=["mcp_server.py"],
        env=None
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            print("可用工具:")
            for tool in tools.tools:
                print(f"  - {tool.name}: {tool.description}")

            result = await session.call_tool(
                "search_docs",
                arguments={"query": "MCP协议", "max_results": 3}
            )
            print(f"\n搜索结果:\n{result.content[0].text}")

            resources = await session.list_resources()
            print("\n可用资源:")
            for res in resources.resources:
                print(f"  - {res.uri}: {res.name}")

            resource_content = await session.read_resource("config://app/database")
            print(f"\n配置内容: {resource_content.contents[0].text}")

            prompts = await session.list_prompts()
            print("\n可用提示词模板:")
            for prompt in prompts.prompts:
                print(f"  - {prompt.name}: {prompt.description}")

            prompt_result = await session.get_prompt(
                "code_review",
                arguments={"code": "def add(a, b): return a + b", "language": "python"}
            )
            print(f"\n生成的提示词:\n{prompt_result.messages[0].content.text}")


asyncio.run(main())
```

**🔬 Client连接建立过程详解**：

#### 第一步：配置Server参数

```python
server_params = StdioServerParameters(
    command="python",
    args=["mcp_server.py"],
    env=None
)
```

**参数解析**：
- `command`：启动Server进程的命令，SDK会通过 `anyio.open_process` 启动子进程
- `args`：命令行参数列表
- `env`：环境变量字典，`None` 表示继承当前进程的环境变量

#### 第二步：建立stdio连接

```python
async with stdio_client(server_params) as (read, write):
```

**底层发生了什么？**
1. SDK通过 `anyio.open_process(command, args=args, env=env)` 启动Server子进程
2. 获取子进程的 `stdout` 作为 `read` 流（Client读取Server的输出）
3. 获取子进程的 `stdin` 作为 `write` 流（Client向Server写入消息）
4. `async with` 确保退出时正确关闭子进程

#### 第三步：创建会话并初始化

```python
async with ClientSession(read, write) as session:
    await session.initialize()
```

**initialize()的完整流程**：
1. Client发送 `initialize` 请求，携带协议版本和客户端能力
2. Server返回 `initialize` 响应，携带协议版本和服务端能力
3. Client发送 `initialized` 通知，确认初始化完成
4. 会话进入Ready状态，可以正常通信

#### 第四步：发现和调用能力

```python
tools = await session.list_tools()
result = await session.call_tool("search_docs", arguments={"query": "MCP协议", "max_results": 3})
```

**list_tools()原理**：
1. Client发送 `{"method": "tools/list"}` 请求
2. Server遍历 `_tools` 注册表，返回所有工具的定义
3. Client缓存工具列表，后续调用时使用

**call_tool()原理**：
1. Client发送 `{"method": "tools/call", "params": {"name": "search_docs", "arguments": {...}}}` 请求
2. Server根据 `name` 查找注册的处理函数
3. Server调用处理函数，传入 `arguments` 作为参数
4. Server将返回值包装为标准格式返回

**💡 异步上下文管理器的嵌套**：
```python
async with stdio_client(...) as (read, write):    # 管理子进程生命周期
    async with ClientSession(read, write) as session:  # 管理会话生命周期
```
- 外层 `async with` 确保子进程正确启动和关闭
- 内层 `async with` 确保会话正确初始化和清理
- 嵌套使用保证资源的安全释放

### 5.2 SSE Client

```python
import asyncio
from mcp import ClientSession
from mcp.client.sse import sse_client


async def main():
    async with sse_client("http://localhost:8080/sse") as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            for tool in tools.tools:
                print(f"工具: {tool.name}")

            result = await session.call_tool(
                "query_database",
                arguments={"sql": "SELECT * FROM users LIMIT 5", "database": "production"}
            )
            print(f"查询结果: {result.content[0].text}")


asyncio.run(main())
```

**🔬 SSE Client连接原理**：

1. `sse_client("http://localhost:8080/sse")` 做了什么？
   - 向 `http://localhost:8080/sse` 发起GET请求，建立SSE连接
   - Server返回一个 `sessionId`，后续POST请求携带此ID
   - SSE连接用于接收Server的响应和通知
   - 返回的 `write` 函数用于向Server的 `/messages` 端点发送POST请求

2. SSE模式 vs stdio模式：
   - stdio模式：`read` = 子进程stdout，`write` = 子进程stdin
   - SSE模式：`read` = SSE事件流，`write` = HTTP POST函数
   - 两者对 `ClientSession` 来说接口一致，只是底层传输不同

### 5.3 集成LangChain

```python
from langchain_mcp import MCPToolkit
from langchain_openai import ChatOpenAI
from langchain.agents import initialize_agent, AgentType
import asyncio


async def create_agent():
    toolkit = MCPToolkit(
        server_params=StdioServerParameters(
            command="python",
            args=["mcp_server.py"]
        )
    )

    async with toolkit.session() as session:
        tools = toolkit.get_tools()
        llm = ChatOpenAI(model="gpt-4o", temperature=0)
        agent = initialize_agent(
            tools,
            llm,
            agent=AgentType.OPENAI_FUNCTIONS,
            verbose=True
        )
        result = await agent.ainvoke("查询production数据库中最近5条用户记录")
        print(result)


asyncio.run(create_agent())
```

**🔬 MCPToolkit工作原理**：

1. **工具转换**：`MCPToolkit.get_tools()` 将MCP Tool转换为LangChain Tool
   - MCP Tool的 `name` → LangChain Tool的 `name`
   - MCP Tool的 `inputSchema` → LangChain Tool的 `args_schema`（Pydantic Model）
   - MCP Tool的 `description` → LangChain Tool的 `description`
   - 调用LangChain Tool时，内部调用 `session.call_tool(name, arguments)`

2. **Agent执行流程**：
   ```
   用户提问 → LLM分析 → 选择Tool → 调用MCP Server → 获取结果 → LLM生成回答
   ```
   - `AgentType.OPENAI_FUNCTIONS` 使用OpenAI的Function Calling机制
   - LLM决定何时调用哪个工具，MCPToolkit负责实际的工具调用

---

## 模块六：MCP与Django集成

### 6.1 Django MCP Server

```python
# mcp_server/django_server.py
import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
django.setup()

from mcp.server.fastmcp import FastMCP, Context
from myapp.models import Article, User
from django.db import connection

mcp = FastMCP("django-app-server")


@mcp.tool()
def query_articles(keyword: str, limit: int = 10) -> str:
    """搜索文章

    Args:
        keyword: 搜索关键词
        limit: 返回数量限制
    """
    articles = Article.objects.filter(title__icontains=keyword)[:limit]
    if not articles:
        return f"未找到包含'{keyword}'的文章"
    results = []
    for a in articles:
        results.append(f"## {a.title}\n作者: {a.author.username}\n摘要: {a.summary}\n发布: {a.created_at}")
    return "\n\n---\n\n".join(results)


@mcp.tool()
def get_user_stats(user_id: int) -> str:
    """获取用户统计信息

    Args:
        user_id: 用户ID
    """
    try:
        user = User.objects.get(id=user_id)
        article_count = user.articles.count()
        return f"用户: {user.username}\n文章数: {article_count}\n注册时间: {user.date_joined}"
    except User.DoesNotExist:
        return f"用户ID {user_id} 不存在"


@mcp.tool()
def execute_readonly_query(sql: str) -> str:
    """执行只读SQL查询（仅SELECT）

    Args:
        sql: SQL查询语句
    """
    if not sql.strip().upper().startswith("SELECT"):
        return "错误：仅支持SELECT查询"
    if any(kw in sql.upper() for kw in ["DROP", "DELETE", "UPDATE", "INSERT", "ALTER"]):
        return "错误：检测到不允许的SQL操作"
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql)
            columns = [col[0] for col in cursor.description]
            rows = cursor.fetchall()
        result = " | ".join(columns) + "\n" + "-" * 40 + "\n"
        for row in rows[:100]:
            result += " | ".join(str(v) for v in row) + "\n"
        return result
    except Exception as e:
        return f"查询错误: {str(e)}"


@mcp.resource("schema://django/{model_name}")
def get_model_schema(model_name: str) -> str:
    """获取Django模型结构"""
    models_map = {
        "article": Article,
        "user": User,
    }
    model = models_map.get(model_name.lower())
    if not model:
        return f"模型 {model_name} 不存在"
    fields = []
    for field in model._meta.get_fields():
        fields.append(f"  {field.name}: {field.__class__.__name__}")
    return f"模型: {model.__name__}\n字段:\n" + "\n".join(fields)


@mcp.prompt()
def write_article(topic: str, style: str = "technical") -> str:
    """撰写文章提示词

    Args:
        topic: 文章主题
        style: 写作风格（technical/casual/formal）
    """
    style_guide = {
        "technical": "技术深度、代码示例、架构图",
        "casual": "通俗易懂、生活类比、幽默风趣",
        "formal": "严谨正式、数据支撑、引用来源",
    }
    return f"""请撰写一篇关于"{topic}"的文章。

写作风格要求：{style_guide.get(style, '')}

请包含：
1. 标题和摘要
2. 背景介绍
3. 核心内容（分章节）
4. 实践建议
5. 总结"""


if __name__ == "__main__":
    mcp.run()
```

**🔬 Django与MCP桥接原理**：

#### Django初始化的关键步骤

```python
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
django.setup()
```

**为什么必须在导入Model之前调用 `django.setup()`？**
1. Django的ORM依赖 `django.apps.apps` 注册表来管理Model
2. `django.setup()` 完成以下工作：
   - 读取 `DJANGO_SETTINGS_MODULE` 指定的配置文件
   - 初始化 `django.apps.apps` 注册表
   - 导入所有 `INSTALLED_APPS` 中的models模块
   - 设置数据库连接
3. 如果不调用 `django.setup()`，`from myapp.models import Article` 会抛出 `AppRegistryNotReady` 异常

**💡 MCP Server作为独立进程**：
- MCP Server是独立于Django Web服务的Python进程
- 它需要自己的Django初始化，不能依赖Web服务的运行状态
- 这意味着MCP Server可以独立启动，不需要Django Web服务运行

#### Django ORM与MCP Tool的映射模式

```
Django ORM操作          →  MCP Tool
─────────────────────────────────────
Article.objects.filter() → query_articles()
User.objects.get()       → get_user_stats()
connection.cursor()      → execute_readonly_query()

Django Model元信息       →  MCP Resource
─────────────────────────────────────
Model._meta.get_fields() → get_model_schema()
```

**设计原则**：
- **查询操作** → 封装为Tool（需要参数，有处理逻辑）
- **元数据信息** → 封装为Resource（只读，提供上下文）
- **提示词模板** → 封装为Prompt（标准化AI交互）

### 6.2 Django视图调用MCP

```python
# views.py
import asyncio
from rest_framework.views import APIView
from rest_framework.response import Response
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class MCPToolCallView(APIView):
    """通过Django API调用MCP工具"""

    def post(self, request):
        tool_name = request.data.get("tool_name")
        arguments = request.data.get("arguments", {})

        result = asyncio.run(self._call_mcp_tool(tool_name, arguments))
        return Response(result)

    async def _call_mcp_tool(self, tool_name: str, arguments: dict):
        server_params = StdioServerParameters(
            command="python",
            args=["mcp_server/django_server.py"]
        )
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
                return {
                    "tool": tool_name,
                    "result": result.content[0].text if result.content else None,
                }
```

**🔬 Django视图中的异步处理原理**：

**`asyncio.run()` 在Django同步视图中的使用**：
1. Django的 `APIView.post()` 是同步方法，运行在WSGI线程中
2. MCP Client是异步的，需要在事件循环中运行
3. `asyncio.run()` 创建一个新的事件循环，运行异步函数，然后关闭循环
4. **注意**：每次请求都创建新的事件循环和MCP连接，性能开销较大

**💡 生产环境优化建议**：
- 使用Django的异步视图（`async def post()`）+ ASGI服务器
- 使用连接池复用MCP Client会话
- 考虑使用SSE模式的MCP Server，避免每次请求启动子进程

```python
# 优化版本：异步视图 + SSE连接复用
class MCPToolCallView(APIView):
    async def post(self, request):
        tool_name = request.data.get("tool_name")
        arguments = request.data.get("arguments", {})
        async with sse_client("http://localhost:9000/sse") as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
                return Response({"tool": tool_name, "result": result.content[0].text})
```

### 6.3 Django中启动MCP SSE服务

```python
# mcp_server/sse_server.py
import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
django.setup()

from mcp_server.django_server import mcp

if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=9000)
```

**🔧 操作步骤**：

```bash
python mcp_server/sse_server.py
```

**执行说明**：
- 启动一个独立的HTTP服务，监听 `0.0.0.0:9000`
- 任何MCP Client都可以通过 `http://<server-ip>:9000/sse` 连接
- `host="0.0.0.0"` 允许远程连接（生产环境应改为具体IP或配合防火墙）

**🔬 SSE模式运行原理**：
1. `mcp.run(transport="sse")` 内部使用 `starlette` + `uvicorn` 启动HTTP服务
2. 注册两个路由：
   - `GET /sse` → SSE连接端点
   - `POST /messages` → 消息接收端点
3. 每个SSE连接创建一个独立的 `ClientSession` 实例
4. 消息通过SSE事件流推送回Client

---

## 模块七：MCP安全模型

### 7.1 安全架构

```
MCP安全层次：

┌──────────────────────────────────────────────────────┐
│  第一层：传输安全                                      │
│  ├── stdio：本地进程通信，信任边界为操作系统             │
│  ├── SSE：HTTPS加密，需认证                            │
│  └── Streamable HTTP：HTTPS + OAuth2.1认证            │
├──────────────────────────────────────────────────────┤
│  第二层：权限控制                                      │
│  ├── Tool Annotations（行为标注）                      │
│  │   ├── readOnlyHint：只读操作，无副作用              │
│  │   ├── destructiveHint：破坏性操作，需确认           │
│  │   ├── idempotentHint：幂等操作，可重试              │
│  │   └── openWorldHint：访问外部系统，需审计           │
│  ├── 用户授权：Host在调用工具前请求用户确认             │
│  └── 沙箱隔离：限制文件系统/网络访问范围                │
├──────────────────────────────────────────────────────┤
│  第三层：数据安全                                      │
│  ├── 输入验证：防止注入攻击                            │
│  ├── 输出过滤：防止敏感信息泄露                        │
│  └── 审计日志：记录所有工具调用                        │
└──────────────────────────────────────────────────────┘
```

**🔬 安全模型深度解析**：

#### 第一层：传输安全

**stdio模式的安全假设**：
- 信任边界是操作系统本身
- 只有能登录本机的用户才能启动和访问MCP Server
- 不需要额外的认证机制
- **风险**：如果本机被入侵，MCP Server可被直接访问

**SSE/Streamable HTTP的安全要求**：
- 必须使用HTTPS加密传输，防止中间人攻击
- Streamable HTTP支持OAuth2.1认证，验证Client身份
- **风险**：暴露在网络上，需要严格的认证和授权

#### 第二层：权限控制

**Tool Annotations如何影响安全决策？**

```
AI模型请求调用工具 → Host检查Annotations → 决策
                                              │
                    readOnlyHint=true  ────────┤→ 自动执行
                    destructiveHint=true ──────┤→ 弹出确认对话框
                    openWorldHint=true ────────┤→ 记录审计日志 + 确认
```

- Host（如Claude Desktop）根据Annotations决定是否需要用户确认
- 这是一种"声明式安全"：Server声明行为特征，Host据此做决策
- **注意**：Annotations是"建议"而非"强制"，恶意Server可能伪造Annotations

#### 第三层：数据安全

**SQL注入防御**：
```python
# 危险：直接拼接SQL
cursor.execute(f"SELECT * FROM {table} WHERE id = {user_id}")

# 安全：参数化查询
cursor.execute("SELECT * FROM %s WHERE id = %s", [table, user_id])
```

**路径遍历防御**：
```python
# 危险：直接使用用户输入的路径
with open(user_path) as f: ...

# 安全：验证路径在允许范围内
abs_path = os.path.abspath(user_path)
if not abs_path.startswith("/app/data"):
    raise ValueError("路径不在允许范围内")
```

### 7.2 安全最佳实践

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("secure-server")

ALLOWED_SQL_KEYWORDS = {"SELECT", "SHOW", "DESCRIBE", "EXPLAIN"}
BLOCKED_PATTERNS = ["DROP", "DELETE", "UPDATE", "INSERT", "ALTER", "CREATE", "GRANT"]


@mcp.tool()
def safe_query(sql: str, database: str = "readonly_db") -> str:
    """安全的数据库查询（只读）

    Args:
        sql: SQL查询语句
        database: 数据库名称
    """
    sql_upper = sql.strip().upper()

    first_keyword = sql_upper.split()[0] if sql_upper.split() else ""
    if first_keyword not in ALLOWED_SQL_KEYWORDS:
        return f"错误：不允许的SQL操作类型 '{first_keyword}'"

    for pattern in BLOCKED_PATTERNS:
        if pattern in sql_upper:
            return f"错误：检测到不允许的SQL关键字 '{pattern}'"

    if ";" in sql.rstrip(";"):
        return "错误：不允许执行多条SQL语句"

    return f"执行查询: {sql[:100]}..."


@mcp.tool()
def read_file(path: str) -> str:
    """读取允许目录下的文件

    Args:
        path: 文件路径
    """
    import os
    allowed_dirs = ["/app/data", "/app/logs", "/app/config"]
    abs_path = os.path.abspath(path)
    if not any(abs_path.startswith(d) for d in allowed_dirs):
        return f"错误：路径 '{path}' 不在允许的目录范围内"
    try:
        with open(abs_path, 'r', encoding='utf-8') as f:
            content = f.read(10000)
        return content
    except FileNotFoundError:
        return f"错误：文件 '{path}' 不存在"
    except PermissionError:
        return f"错误：无权限读取文件 '{path}'"


if __name__ == "__main__":
    mcp.run()
```

**🔬 安全实践原理详解**：

#### SQL注入防御的多层策略

```python
first_keyword = sql_upper.split()[0] if sql_upper.split() else ""
if first_keyword not in ALLOWED_SQL_KEYWORDS:
    return f"错误：不允许的SQL操作类型 '{first_keyword}'"
```

**第一层：关键词白名单**
- 只允许 `SELECT`、`SHOW`、`DESCRIBE`、`EXPLAIN` 开头的SQL
- 拒绝所有写操作（INSERT、UPDATE、DELETE、DROP等）

```python
for pattern in BLOCKED_PATTERNS:
    if pattern in sql_upper:
        return f"错误：检测到不允许的SQL关键字 '{pattern}'"
```

**第二层：关键词黑名单**
- 即使SQL以SELECT开头，也检查是否包含危险关键词
- 防止 `SELECT ... INTO OUTFILE` 等绕过方式

```python
if ";" in sql.rstrip(";"):
    return "错误：不允许执行多条SQL语句"
```

**第三层：多语句防御**
- 防止SQL注入中常见的多语句攻击：`SELECT 1; DROP TABLE users`
- 分号是SQL语句分隔符，禁止分号可以防止多语句执行

**💡 更安全的做法**：
- 使用只读数据库账号连接（数据库层面限制）
- 使用参数化查询而非字符串拼接
- 限制返回行数，防止大量数据泄露

#### 路径遍历防御

```python
abs_path = os.path.abspath(path)
if not any(abs_path.startswith(d) for d in allowed_dirs):
    return f"错误：路径 '{path}' 不在允许的目录范围内"
```

**防御原理**：
1. `os.path.abspath(path)` 将相对路径转为绝对路径，消除 `../` 等遍历符号
2. 检查绝对路径是否以允许的目录开头
3. 防止攻击：`path = "/app/data/../../etc/passwd"` → `abs_path = "/etc/passwd"` → 不在允许范围内

**💡 潜在绕过与加固**：
- 符号链接绕过：攻击者可能创建指向敏感目录的符号链接
- 加固方案：使用 `os.path.realpath(path)` 替代 `os.path.abspath()`，解析符号链接

### 7.3 OAuth2.1认证（Streamable HTTP）

```
OAuth2.1认证流程（远程MCP服务器）：

┌──────────┐                    ┌──────────┐     ┌──────────┐
│  Client  │                    │ MCP Server│     │ Auth     │
│          │                    │          │     │ Server   │
└────┬─────┘                    └────┬─────┘     └────┬─────┘
     │                               │                │
     │ 1. GET /mcp (无认证)          │                │
     │──────────────────────────────▶│                │
     │◀─ 401 + WWW-Authenticate ────│                │
     │   (OAuth2 metadata)           │                │
     │                               │                │
     │ 2. GET /.well-known/oauth-    │                │
     │    authorization-server       │                │
     │──────────────────────────────────────────────▶│
     │◀─ OAuth2 metadata ────────────────────────────│
     │                               │                │
     │ 3. Authorization Code Flow    │                │
     │──────────────────────────────────────────────▶│
     │◀─ Authorization Code ─────────────────────────│
     │                               │                │
     │ 4. Token Exchange             │                │
     │──────────────────────────────────────────────▶│
     │◀─ Access Token ──────────────────────────────│
     │                               │                │
     │ 5. POST /mcp (Bearer Token)   │                │
     │──────────────────────────────▶│                │
     │◀─ MCP Response ──────────────│                │
```

**🔬 OAuth2.1流程详解**：

**步骤1：未认证请求**
- Client首次请求 `/mcp` 端点，不携带认证信息
- Server返回 `401 Unauthorized`，响应头包含 `WWW-Authenticate: Bearer`
- 同时返回OAuth2元数据URL，告诉Client去哪里认证

**步骤2：发现认证服务器**
- Client访问 `/.well-known/oauth-authorization-server` 获取OAuth2配置
- 配置包含：授权端点URL、令牌端点URL、支持的授权类型等
- 这是OAuth2.0 Authorization Server Metadata（RFC8414）标准

**步骤3-4：Authorization Code Flow**
- 标准的OAuth2.1授权码流程
- Client重定向用户到授权页面，用户登录并授权
- 授权服务器返回授权码，Client用授权码交换访问令牌

**步骤5：携带令牌访问**
- Client在后续请求中携带 `Authorization: Bearer <access_token>`
- Server验证令牌有效性后处理请求

**💡 OAuth2.1 vs OAuth2.0**：
- OAuth2.1强制使用PKCE（Proof Key for Code Exchange），防止授权码截获攻击
- 禁止隐式授权（Implicit Grant）和密码授权（Resource Owner Password Credentials）
- 更严格的重定向URI匹配规则

---

## 模块八：MCP与AI Agent集成

### 8.1 MCP在Agent架构中的位置

```
AI Agent架构中的MCP：

┌─────────────────────────────────────────────────────────┐
│                    AI Agent                               │
│                                                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────────┐ │
│  │ LLM      │  │ Planner  │  │ MCP Client           │ │
│  │ (推理)    │  │ (规划)    │  │ (工具调用)            │ │
│  └────┬─────┘  └────┬─────┘  └──────┬───────────────┘ │
│       │              │               │                   │
│       └──────────────┼───────────────┘                   │
│                      │                                   │
│              ┌───────▼───────┐                           │
│              │  Memory       │                           │
│              │  (上下文管理)  │                           │
│              └───────────────┘                           │
└──────────────────────────────────────────────────────────┘
                       │
              MCP协议（标准化接口）
                       │
       ┌───────────────┼───────────────┐
       │               │               │
┌──────▼──────┐ ┌──────▼──────┐ ┌──────▼──────┐
│ MCP Server  │ │ MCP Server  │ │ MCP Server  │
│ (数据库)    │ │ (文件系统)   │ │ (API服务)   │
└─────────────┘ └─────────────┘ └─────────────┘
```

**🔬 Agent核心原理：ReAct循环**

AI Agent的核心执行模式是 **ReAct（Reasoning + Acting）循环**，MCP在"Acting"阶段发挥关键作用：

```
ReAct循环详解：

┌─────────────────────────────────────────────────────────┐
│  Reason（推理）                                           │
│  LLM分析用户问题，决定下一步行动                           │
│  输出：tool_calls 或 最终回答                              │
│       │                                                  │
│       ▼                                                  │
│  Act（行动）                                              │
│  如果LLM决定调用工具 → MCP Client调用MCP Server            │
│  MCP Server执行操作 → 返回结果                             │
│       │                                                  │
│       ▼                                                  │
│  Observe（观察）                                          │
│  LLM接收工具执行结果，更新推理状态                          │
│  决定是否需要继续调用工具或生成最终回答                      │
│       │                                                  │
│       └──────▶ 回到Reason（如果需要继续）                   │
└─────────────────────────────────────────────────────────┘
```

**ReAct vs 纯推理的区别**：
- 纯推理（Chain-of-Thought）：LLM只能基于已有知识推理，无法获取新信息
- ReAct：LLM可以通过工具获取实时数据，推理基于真实信息而非猜测

**💡 MCP在ReAct中的独特价值**：
1. **动态工具发现**：Agent启动时通过 `tools/list` 发现可用工具，无需硬编码
2. **标准化调用**：所有工具遵循统一的MCP协议，Agent无需为每个工具编写适配代码
3. **可扩展性**：新增MCP Server无需修改Agent代码，Agent自动发现新工具

### 8.2 LangGraph + MCP Agent

```python
import asyncio
from typing import Annotated
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import MemorySaver
from langchain_mcp import MCPToolkit
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def create_mcp_agent():
    server_params = StdioServerParameters(
        command="python",
        args=["mcp_server/django_server.py"]
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            toolkit = MCPToolkit(session=session)
            tools = toolkit.get_tools()

            llm = ChatOpenAI(model="gpt-4o", temperature=0)
            agent = create_react_agent(
                llm,
                tools,
                checkpointer=MemorySaver()
            )

            result = await agent.ainvoke(
                {"messages": [HumanMessage(content="查询最近发布的5篇关于Django的文章")]},
                config={"configurable": {"thread_id": "1"}}
            )
            for msg in result["messages"]:
                print(f"{msg.type}: {msg.content}")


asyncio.run(create_mcp_agent())
```

**🔬 代码执行流程详解**：

```
执行流程：

1. stdio_client(server_params)
   │── 启动Django MCP Server子进程
   │── 获取stdin/stdout流
   ▼
2. ClientSession(read, write)
   │── 创建MCP会话
   ▼
3. session.initialize()
   │── 发送initialize请求 → 接收响应 → 发送initialized通知
   │── 交换能力信息（Client告知支持sampling，Server告知提供tools/resources/prompts）
   ▼
4. MCPToolkit(session=session)
   │── 将MCP会话封装为LangChain工具包
   ▼
5. toolkit.get_tools()
   │── 内部调用session.list_tools()
   │── 将每个MCP Tool转换为LangChain BaseTool
   ▼
6. create_react_agent(llm, tools, checkpointer=MemorySaver())
   │── 创建ReAct Agent
   │── MemorySaver用于保存对话状态（支持多轮对话）
   ▼
7. agent.ainvoke({"messages": [...]}, config={"thread_id": "1"})
   │── LLM推理 → 选择工具 → 调用MCP Server → 获取结果 → 生成回答
   │── thread_id="1" 标识对话，后续同一thread_id可继续对话
```

**🔧 操作说明**：

| 步骤 | 操作 | 目的 | 预期效果 |
|------|------|------|---------|
| 1 | 配置 `StdioServerParameters` | 指定MCP Server启动方式 | SDK知道如何启动和连接Server |
| 2 | `async with stdio_client(...)` | 建立stdio连接 | 启动子进程，获取通信流 |
| 3 | `session.initialize()` | 初始化MCP会话 | 完成能力协商，进入Ready状态 |
| 4 | `toolkit.get_tools()` | 获取MCP工具列表 | 返回LangChain Tool列表 |
| 5 | `create_react_agent(...)` | 创建Agent | 构建具备工具调用能力的Agent |
| 6 | `agent.ainvoke(...)` | 执行查询 | Agent自动推理、调用工具、生成回答 |

**💡 MemorySaver的作用**：
- `MemorySaver()` 是LangGraph提供的内存检查点存储
- 每次Agent执行后，对话状态（messages列表）被保存
- 同一 `thread_id` 的后续请求可以恢复之前的对话上下文
- 生产环境应替换为 `AsyncPostgresSaver` 实现持久化

### 8.3 自定义Agent集成MCP

```python
import asyncio
import json
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from openai import OpenAI


class MCPAgent:
    def __init__(self, mcp_server_command: list[str], model: str = "gpt-4o"):
        self.server_params = StdioServerParameters(
            command=mcp_server_command[0],
            args=mcp_server_command[1:]
        )
        self.model = model
        self.client = OpenAI()
        self.tools = []
        self.session = None

    async def connect(self):
        self._read, self._write = await stdio_client(self.server_params).__aenter__()
        self.session = await ClientSession(self._read, self._write).__aenter__()
        await self.session.initialize()
        tools_result = await self.session.list_tools()
        self.tools = self._convert_tools(tools_result.tools)

    def _convert_tools(self, mcp_tools):
        openai_tools = []
        for tool in mcp_tools:
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.inputSchema,
                }
            })
        return openai_tools

    async def run(self, user_message: str) -> str:
        messages = [{"role": "user", "content": user_message}]

        while True:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=self.tools if self.tools else None,
                tool_choice="auto"
            )

            msg = response.choices[0].message
            messages.append(msg)

            if not msg.tool_calls:
                return msg.content

            for tool_call in msg.tool_calls:
                result = await self.session.call_tool(
                    tool_call.function.name,
                    json.loads(tool_call.function.arguments)
                )
                tool_response = result.content[0].text if result.content else ""
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": tool_response,
                })

    async def close(self):
        if self.session:
            await self.session.__aexit__(None, None, None)


async def main():
    agent = MCPAgent(["python", "mcp_server/django_server.py"])
    await agent.connect()

    result = await agent.run("查询production数据库中users表的表结构")
    print(result)

    result = await agent.run("搜索关于Django的文章")
    print(result)

    await agent.close()


asyncio.run(main())
```

**🔬 自定义Agent核心原理**：

#### MCP Tool → OpenAI Function Calling 转换

```python
def _convert_tools(self, mcp_tools):
    openai_tools = []
    for tool in mcp_tools:
        openai_tools.append({
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.inputSchema,
            }
        })
    return openai_tools
```

**转换映射关系**：

| MCP Tool字段 | OpenAI Function字段 | 说明 |
|-------------|---------------------|------|
| `tool.name` | `function.name` | 工具名称，直接映射 |
| `tool.description` | `function.description` | 工具描述，AI模型据此选择工具 |
| `tool.inputSchema` | `function.parameters` | 参数定义，JSON Schema格式兼容 |

**💡 为什么可以直接映射？**
- MCP的 `inputSchema` 使用标准JSON Schema格式
- OpenAI的Function Calling也使用JSON Schema定义参数
- 两者格式高度兼容，无需额外转换

#### ReAct循环的实现

```python
while True:
    response = self.client.chat.completions.create(
        model=self.model,
        messages=messages,
        tools=self.tools,
        tool_choice="auto"
    )

    msg = response.choices[0].message
    messages.append(msg)

    if not msg.tool_calls:
        return msg.content

    for tool_call in msg.tool_calls:
        result = await self.session.call_tool(...)
        messages.append({"role": "tool", ...})
```

**循环逻辑**：
1. **Reason**：LLM分析messages，决定是否调用工具
2. **Act**：如果有 `tool_calls`，通过MCP Client调用对应工具
3. **Observe**：将工具结果添加到messages，回到步骤1
4. **终止条件**：LLM不再生成 `tool_calls`，返回最终文本回答

**🔬 `tool_choice="auto"` 的含义**：
- `auto`：LLM自主决定是否调用工具（推荐）
- `required`：LLM必须调用至少一个工具
- `none`：LLM不允许调用工具
- `{"type": "function", "function": {"name": "xxx"}}`：强制调用指定工具

---

## 模块九：MCP Server实战案例

### 9.1 数据库MCP Server

```python
from mcp.server.fastmcp import FastMCP
import pymysql

mcp = FastMCP("mysql-server")

def get_connection(database: str = "production"):
    return pymysql.connect(
        host="localhost",
        port=3306,
        user="readonly",
        password="readonly_pass",
        database=database,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor
    )


@mcp.tool()
def list_databases() -> str:
    """列出所有可访问的数据库"""
    conn = get_connection()
    with conn.cursor() as cursor:
        cursor.execute("SHOW DATABASES")
        dbs = [row["Database"] for row in cursor.fetchall()]
    conn.close()
    return "可用数据库:\n" + "\n".join(f"  - {db}" for db in dbs)


@mcp.tool()
def list_tables(database: str = "production") -> str:
    """列出数据库中的所有表

    Args:
        database: 数据库名称
    """
    conn = get_connection(database)
    with conn.cursor() as cursor:
        cursor.execute("SHOW TABLES")
        tables = list(cursor.fetchall()[0].values())
    conn.close()
    return f"数据库 {database} 中的表:\n" + "\n".join(f"  - {t}" for t in tables)


@mcp.tool()
def describe_table(table: str, database: str = "production") -> str:
    """查看表结构

    Args:
        table: 表名
        database: 数据库名称
    """
    conn = get_connection(database)
    with conn.cursor() as cursor:
        cursor.execute(f"DESCRIBE `{table}`")
        rows = cursor.fetchall()
    conn.close()
    result = f"表 {database}.{table} 结构:\n"
    result += f"{'字段':<20} {'类型':<20} {'允许空':<8} {'键':<8} {'默认值':<15}\n"
    result += "-" * 75 + "\n"
    for row in rows:
        result += f"{row['Field']:<20} {row['Type']:<20} {row['Null']:<8} {row['Key']:<8} {str(row['Default']):<15}\n"
    return result


@mcp.tool()
def run_select_query(sql: str, database: str = "production", limit: int = 50) -> str:
    """执行SELECT查询

    Args:
        sql: SELECT查询语句
        database: 数据库名称
        limit: 返回行数限制
    """
    if not sql.strip().upper().startswith("SELECT"):
        return "错误：仅支持SELECT查询"
    sql = sql.rstrip(";") + f" LIMIT {limit}"
    conn = get_connection(database)
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql)
            rows = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description]
        if not rows:
            return "查询结果为空"
        result = " | ".join(columns) + "\n" + "-" * 60 + "\n"
        for row in rows:
            result += " | ".join(str(v) for v in row.values()) + "\n"
        result += f"\n共 {len(rows)} 行"
        return result
    except Exception as e:
        return f"查询错误: {str(e)}"
    finally:
        conn.close()


@mcp.resource("schema://mysql/{database}/{table}")
def get_table_schema_resource(database: str, table: str) -> str:
    """获取表结构作为资源"""
    return describe_table(table, database)


if __name__ == "__main__":
    mcp.run()
```

**🔬 数据库Server设计原理**：

#### 工具分层设计：发现→探索→理解→操作

```
AI探索数据库的递进路径：

第一层：发现（Discovery）
┌──────────────────────────────────────┐
│ list_databases()                     │
│ 目的：让AI知道有哪些数据库可用         │
│ 返回：数据库名称列表                   │
│ 安全：无参数，只读操作                 │
└──────────────────────────────────────┘
                    │
                    ▼
第二层：探索（Exploration）
┌──────────────────────────────────────┐
│ list_tables(database)                │
│ 目的：让AI了解数据库中有哪些表         │
│ 返回：表名列表                        │
│ 安全：参数为枚举值，只读操作            │
└──────────────────────────────────────┘
                    │
                    ▼
第三层：理解（Understanding）
┌──────────────────────────────────────┐
│ describe_table(table, database)      │
│ 目的：让AI理解表的结构和字段含义        │
│ 返回：字段名、类型、约束等详细信息       │
│ 安全：只读元数据，不涉及数据            │
└──────────────────────────────────────┘
                    │
                    ▼
第四层：操作（Operation）
┌──────────────────────────────────────┐
│ run_select_query(sql, database)      │
│ 目的：让AI执行实际的数据查询           │
│ 返回：查询结果数据                     │
│ 安全：仅允许SELECT，自动添加LIMIT      │
└──────────────────────────────────────┘
```

**💡 为什么需要分层？**
- AI模型需要逐步理解数据库结构才能构造正确的SQL
- 直接让AI写SQL容易出错，分层设计让AI先"了解"再"操作"
- 每层的安全风险递增，分层便于实施差异化的安全策略

#### 安全机制详解

```python
def get_connection(database: str = "production"):
    return pymysql.connect(
        user="readonly",          # 只读账号
        password="readonly_pass",
        ...
    )
```

**数据库层面安全**：
- 使用 `readonly` 账号连接，数据库层面限制只能执行SELECT
- 即使MCP Server的安全检查被绕过，数据库也会拒绝写操作

```python
if not sql.strip().upper().startswith("SELECT"):
    return "错误：仅支持SELECT查询"
sql = sql.rstrip(";") + f" LIMIT {limit}"
```

**应用层面安全**：
- 白名单检查：只允许SELECT开头的SQL
- 自动添加LIMIT：防止返回过多数据导致性能问题
- 去除末尾分号：防止多语句注入

**💡 双层安全策略**：应用层检查 + 数据库层限制，即使一层被绕过，另一层仍能保护

### 9.2 文件系统MCP Server

```python
from mcp.server.fastmcp import FastMCP
import os

mcp = FastMCP("filesystem-server")

ALLOWED_DIRS = ["/app/data", "/app/config", "/app/logs"]


def _validate_path(path: str) -> str:
    abs_path = os.path.abspath(path)
    if not any(abs_path.startswith(d) for d in ALLOWED_DIRS):
        raise ValueError(f"路径 '{path}' 不在允许的目录范围内")
    return abs_path


@mcp.tool()
def list_directory(path: str) -> str:
    """列出目录内容

    Args:
        path: 目录路径
    """
    try:
        abs_path = _validate_path(path)
        entries = os.listdir(abs_path)
        result = []
        for entry in sorted(entries):
            full = os.path.join(abs_path, entry)
            size = os.path.getsize(full) if os.path.isfile(full) else "-"
            result.append(f"{'[DIR]' if os.path.isdir(full) else '     '} {entry:<40} {size}")
        return "\n".join(result)
    except ValueError as e:
        return str(e)
    except FileNotFoundError:
        return f"目录 '{path}' 不存在"


@mcp.tool()
def read_file(path: str, encoding: str = "utf-8") -> str:
    """读取文件内容

    Args:
        path: 文件路径
        encoding: 文件编码
    """
    try:
        abs_path = _validate_path(path)
        with open(abs_path, 'r', encoding=encoding) as f:
            return f.read(50000)
    except ValueError as e:
        return str(e)
    except FileNotFoundError:
        return f"文件 '{path}' 不存在"


@mcp.tool()
def search_in_files(path: str, pattern: str, file_ext: str = "") -> str:
    """在文件中搜索内容

    Args:
        path: 搜索目录
        pattern: 搜索关键词
        file_ext: 文件扩展名过滤（如.py, .log）
    """
    try:
        abs_path = _validate_path(path)
        matches = []
        for root, dirs, files in os.walk(abs_path):
            for fname in files:
                if file_ext and not fname.endswith(file_ext):
                    continue
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                        for i, line in enumerate(f, 1):
                            if pattern in line:
                                matches.append(f"{fpath}:{i}: {line.strip()}")
                                if len(matches) >= 50:
                                    return "\n".join(matches) + "\n... (结果过多，已截断)"
                except Exception:
                    continue
        if not matches:
            return f"未找到包含 '{pattern}' 的内容"
        return "\n".join(matches)
    except ValueError as e:
        return str(e)


@mcp.resource("file://{path}")
def get_file_resource(path: str) -> str:
    """获取文件内容作为资源"""
    return read_file(path)


if __name__ == "__main__":
    mcp.run()
```

**🔬 文件系统Server安全原理**：

#### 路径验证：防御路径遍历攻击

```python
ALLOWED_DIRS = ["/app/data", "/app/config", "/app/logs"]

def _validate_path(path: str) -> str:
    abs_path = os.path.abspath(path)
    if not any(abs_path.startswith(d) for d in ALLOWED_DIRS):
        raise ValueError(f"路径 '{path}' 不在允许的目录范围内")
    return abs_path
```

**攻击场景与防御**：

| 攻击方式 | 恶意输入 | `abspath`结果 | 是否被拦截 |
|---------|---------|--------------|-----------|
| 路径遍历 | `/app/data/../../etc/passwd` | `/etc/passwd` | ✅ 不在ALLOWED_DIRS内 |
| 绝对路径 | `/etc/shadow` | `/etc/shadow` | ✅ 不在ALLOWED_DIRS内 |
| 相对路径 | `../../etc/passwd` | `/etc/passwd` | ✅ 不在ALLOWED_DIRS内 |
| 正常路径 | `/app/data/log.txt` | `/app/data/log.txt` | ❌ 允许访问 |

**💡 `os.path.abspath()` 的作用**：
- 将相对路径转为绝对路径
- 解析 `..` 和 `.` 符号
- 使得路径遍历攻击在比较前被"还原"为真实路径

**⚠️ 潜在风险：符号链接**：
- 如果 `/app/data/link` 是指向 `/etc` 的符号链接
- `os.path.abspath()` 不会解析符号链接
- 加固方案：使用 `os.path.realpath()` 替代 `os.path.abspath()`

#### 读取限制

```python
with open(abs_path, 'r', encoding=encoding) as f:
    return f.read(50000)
```

- `f.read(50000)` 限制最多读取50000字节，防止读取超大文件导致内存溢出
- `encoding=encoding` 允许指定编码，默认UTF-8

### 9.3 Redis MCP Server

```python
from mcp.server.fastmcp import FastMCP
import redis

mcp = FastMCP("redis-server")

def get_redis_client(db: int = 0):
    return redis.Redis(host="localhost", port=6379, db=db, decode_responses=True)


@mcp.tool()
def redis_get(key: str, db: int = 0) -> str:
    """获取Redis键值

    Args:
        key: Redis键名
        db: 数据库编号(0-15)
    """
    r = get_redis_client(db)
    value = r.get(key)
    if value is None:
        return f"键 '{key}' 不存在"
    return str(value)


@mcp.tool()
def redis_keys(pattern: str = "*", db: int = 0) -> str:
    """搜索Redis键名

    Args:
        pattern: 键名模式（支持通配符*）
        db: 数据库编号(0-15)
    """
    r = get_redis_client(db)
    keys = r.keys(pattern)
    if not keys:
        return f"未找到匹配 '{pattern}' 的键"
    return "\n".join(f"  {key}" for key in keys[:100])


@mcp.tool()
def redis_type(key: str, db: int = 0) -> str:
    """获取Redis键的类型

    Args:
        key: Redis键名
        db: 数据库编号(0-15)
    """
    r = get_redis_client(db)
    key_type = r.type(key)
    if key_type == "none":
        return f"键 '{key}' 不存在"
    ttl = r.ttl(key)
    ttl_str = f"{ttl}秒" if ttl > 0 else ("永不过期" if ttl == -1 else "键不存在")
    return f"键: {key}\n类型: {key_type}\nTTL: {ttl_str}"


@mcp.tool()
def redis_info(section: str = "default") -> str:
    """获取Redis服务器信息

    Args:
        section: 信息类别（server/clients/memory/stats/replication/cpu/keyspace）
    """
    r = get_redis_client()
    info = r.info(section)
    result = []
    for k, v in info.items():
        result.append(f"  {k}: {v}")
    return "\n".join(result)


@mcp.tool()
def redis_hgetall(key: str, db: int = 0) -> str:
    """获取Redis Hash的所有字段

    Args:
        key: Hash键名
        db: 数据库编号(0-15)
    """
    r = get_redis_client(db)
    data = r.hgetall(key)
    if not data:
        return f"Hash '{key}' 不存在或为空"
    return "\n".join(f"  {k}: {v}" for k, v in data.items())


if __name__ == "__main__":
    mcp.run()
```

**🔬 Redis Server设计原理**：

#### 只暴露读操作的安全策略

```
Redis MCP Server工具设计：

只读工具（安全）                    未暴露的写操作（危险）
─────────────────                  ──────────────────
redis_get()     → 读取键值          SET/SETNX        → 写入键值
redis_keys()    → 搜索键名          DEL              → 删除键
redis_type()    → 查看类型          EXPIRE           → 设置过期
redis_info()    → 服务器信息         FLUSHDB/FLUSHALL → 清空数据库
redis_hgetall() → 读取Hash          HSET/HDEL        → 修改Hash
```

**💡 为什么只暴露读操作？**
- Redis是内存数据库，写操作的影响是即时的且不可逆的
- AI模型可能误判而执行危险的写操作（如 `FLUSHALL`）
- 只读设计确保AI只能"看"不能"改"

#### `redis_keys()` 的性能陷阱

```python
keys = r.keys(pattern)
return "\n".join(f"  {key}" for key in keys[:100])
```

**⚠️ `KEYS` 命令的危险性**：
- `KEYS *` 会遍历整个键空间，时间复杂度O(N)
- 在生产环境的大数据库上执行可能导致Redis阻塞
- **生产环境建议**：使用 `SCAN` 命令替代 `KEYS`，分批迭代

```python
# 生产环境安全版本
@mcp.tool()
def redis_scan_keys(pattern: str = "*", db: int = 0, count: int = 100) -> str:
    """安全搜索Redis键名（使用SCAN）
    Args:
        pattern: 键名模式
        db: 数据库编号
        count: 每批扫描数量
    """
    r = get_redis_client(db)
    keys = []
    cursor = 0
    while True:
        cursor, batch = r.scan(cursor=cursor, match=pattern, count=count)
        keys.extend(batch)
        if cursor == 0 or len(keys) >= 100:
            break
    return "\n".join(f"  {key}" for key in keys[:100])
```

---

## 模块十：MCP调试与测试

### 10.1 MCP Inspector调试工具

```bash
npx @modelcontextprotocol/inspector python mcp_server.py
```

```
Inspector功能：
┌──────────────────────────────────────────────────────┐
│  MCP Inspector (Web UI)                              │
│                                                      │
│  ┌────────────────────────────────────────────────┐ │
│  │ 连接配置                                        │ │
│  │  传输方式: stdio / sse                          │ │
│  │  命令/URL: python mcp_server.py                 │ │
│  │  [连接]                                         │ │
│  └────────────────────────────────────────────────┘ │
│                                                      │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐            │
│  │ Tools    │ │Resources │ │ Prompts  │            │
│  │          │ │          │ │          │            │
│  │ search   │ │ config://│ │ code_    │            │
│  │ query_db │ │ docs://  │ │ review   │            │
│  │          │ │ schema://│ │ explain  │            │
│  └──────────┘ └──────────┘ └──────────┘            │
│                                                      │
│  ┌────────────────────────────────────────────────┐ │
│  │ 工具测试                                        │ │
│  │  工具: search_docs                              │ │
│  │  参数: {"query": "test"}                        │ │
│  │  [执行]                                         │ │
│  │                                                 │ │
│  │  结果: ...                                      │ │
│  └────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────┘
```

**🔬 Inspector工作原理**：

```
Inspector内部架构：

┌─────────────────────────────────────────────────────┐
│  浏览器 (React前端)                                   │
│  ├── 连接配置面板                                     │
│  ├── 工具/资源/提示词列表                              │
│  └── 测试面板                                         │
│       │                                              │
│       │ HTTP API                                     │
│       ▼                                              │
│  ┌─────────────────────────────────────────────────┐│
│  │  Inspector Server (Node.js后端)                   ││
│  │  ├── MCP Client (连接MCP Server)                  ││
│  │  ├── REST API (供前端调用)                         ││
│  │  └── WebSocket (实时推送日志)                      ││
│  │       │                                           ││
│  │       │ stdio/SSE                                 ││
│  │       ▼                                           ││
│  │  ┌─────────────────────────────────────────────┐ ││
│  │  │  MCP Server (被调试的Server)                  │ ││
│  │  └─────────────────────────────────────────────┘ ││
│  └─────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────┘
```

**Inspector的核心能力**：
1. **连接管理**：自动启动MCP Server子进程并建立stdio连接
2. **能力浏览**：调用 `tools/list`、`resources/list`、`prompts/list` 展示Server能力
3. **交互测试**：手动构造参数调用工具，查看返回结果
4. **日志查看**：实时显示JSON-RPC消息流，便于调试协议问题

**🔧 操作步骤**：

| 步骤 | 操作 | 目的 | 预期效果 |
|------|------|------|---------|
| 1 | `npx @modelcontextprotocol/inspector python mcp_server.py` | 启动Inspector | 自动安装依赖并启动Web服务 |
| 2 | 浏览器打开 `http://localhost:6274` | 访问Inspector界面 | 显示连接配置面板 |
| 3 | 点击"连接" | 建立与MCP Server的连接 | 显示Server提供的工具/资源/提示词列表 |
| 4 | 选择工具并填写参数 | 测试工具调用 | 显示工具执行结果 |

**💡 调试技巧**：
- 如果连接失败，检查Server脚本路径是否正确
- 使用Inspector的"日志"标签查看完整的JSON-RPC消息流
- 如果工具参数报错，对比Inspector显示的inputSchema和实际传入的参数

### 10.2 单元测试

```python
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import asyncio


@pytest.fixture
async def mcp_session():
    server_params = StdioServerParameters(
        command="python",
        args=["mcp_server/django_server.py"]
    )
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


@pytest.mark.asyncio
async def test_list_tools(mcp_session):
    tools = await mcp_session.list_tools()
    tool_names = [t.name for t in tools.tools]
    assert "search_docs" in tool_names
    assert "query_database" in tool_names


@pytest.mark.asyncio
async def test_call_tool(mcp_session):
    result = await mcp_session.call_tool(
        "search_docs",
        arguments={"query": "test", "max_results": 3}
    )
    assert result.content
    assert len(result.content) > 0


@pytest.mark.asyncio
async def test_list_resources(mcp_session):
    resources = await mcp_session.list_resources()
    assert len(resources.resources) > 0


@pytest.mark.asyncio
async def test_read_resource(mcp_session):
    content = await mcp_session.read_resource("config://app/database")
    assert content.contents
    assert "host" in content.contents[0].text.lower()


@pytest.mark.asyncio
async def test_get_prompt(mcp_session):
    result = await mcp_session.get_prompt(
        "code_review",
        arguments={"code": "print('hello')", "language": "python"}
    )
    assert result.messages
    assert "审查" in result.messages[0].content.text or "review" in result.messages[0].content.text.lower()
```

**🔬 测试策略详解**：

#### pytest异步fixture管理MCP会话

```python
@pytest.fixture
async def mcp_session():
    server_params = StdioServerParameters(
        command="python",
        args=["mcp_server/django_server.py"]
    )
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session
```

**fixture工作原理**：
1. 测试开始前：启动MCP Server子进程，建立连接，初始化会话
2. `yield session`：将会话对象传递给测试函数
3. 测试结束后：自动关闭会话和子进程（`async with` 的清理机制）

**💡 为什么用 `yield` 而不是 `return`？**
- `yield` 使得fixture可以在测试后执行清理代码
- `async with` 的退出代码在 `yield` 之后的代码中执行
- 确保每个测试后MCP Server子进程被正确关闭

#### 测试覆盖策略

| 测试类型 | 测试内容 | 验证目标 |
|---------|---------|---------|
| 能力发现 | `test_list_tools` | Server正确注册和暴露工具 |
| 工具调用 | `test_call_tool` | 工具可被正确调用并返回结果 |
| 资源访问 | `test_list_resources` / `test_read_resource` | 资源URI正确映射和返回 |
| 提示词 | `test_get_prompt` | 提示词模板正确参数化 |

**🔬 集成测试 vs 单元测试**：
- 上述测试是**集成测试**：测试完整的Client→Server通信链路
- 如果需要**单元测试**（只测试Tool函数逻辑），可以直接调用函数：

```python
# 单元测试：直接测试Tool函数（不经过MCP协议）
def test_search_docs():
    from mcp_server import search_docs
    result = search_docs(query="test", max_results=3)
    assert "文档" in result

def test_query_database_rejects_non_select():
    from mcp_server import query_database
    result = query_database(sql="DROP TABLE users")
    assert "错误" in result
```

---

## 模块十一：应用场景分析

### 11.1 场景一：IDE智能编码助手

```
IDE + MCP架构：

┌──────────────────────────────────────────────────────┐
│  IDE (VS Code / Cursor)                              │
│  ┌────────────────────────────────────────────────┐ │
│  │ MCP Host                                       │ │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐      │ │
│  │  │ Client   │ │ Client   │ │ Client   │      │ │
│  │  │ (Git)    │ │ (FS)     │ │ (DB)     │      │ │
│  │  └────┬─────┘ └────┬─────┘ └────┬─────┘      │ │
│  └───────┼─────────────┼────────────┼────────────┘ │
│          │             │            │               │
│  ┌───────▼─────┐ ┌────▼──────┐ ┌───▼──────────┐  │
│  │ Git MCP     │ │ FS MCP    │ │ DB MCP       │  │
│  │ • diff      │ │ • read    │ │ • query      │  │
│  │ • commit    │ │ • search  │ │ • schema     │  │
│  │ • log       │ │ • write   │ │ • explain    │  │
│  │ • branch    │ │ • tree    │ │ • optimize   │  │
│  └─────────────┘ └───────────┘ └──────────────┘  │
└──────────────────────────────────────────────────────┘

AI能力：
  "帮我查看最近的Git提交" → 调用Git MCP的log工具
  "搜索项目中所有TODO" → 调用FS MCP的search工具
  "优化这个SQL查询" → 调用DB MCP的query + explain工具
```

**🔬 IDE场景技术架构解析**：

**多MCP Server协作原理**：
- IDE作为MCP Host，管理多个MCP Client实例
- 每个Client连接一个独立的MCP Server（Git/FS/DB）
- AI模型通过 `tools/list` 发现所有Server的工具
- 工具名通过Server名前缀区分（如 `git_log`、`fs_read`、`db_query`）

**跨Server协作示例**：
```
用户："这个Bug是什么时候引入的？"

AI推理链：
1. 调用 git_log → 找到相关提交
2. 调用 git_diff → 查看提交变更
3. 调用 fs_read → 读取变更后的文件
4. 调用 db_query → 查询Bug相关的数据库记录
5. 综合分析 → 生成回答
```

**💡 为什么IDE场景特别适合MCP？**
- IDE需要集成多种外部工具（Git、文件系统、数据库、API等）
- 传统方式：每个工具写一个插件，维护成本高
- MCP方式：每个工具一个MCP Server，标准化集成，一次开发多IDE可用

### 11.2 场景二：企业知识库助手

```
企业知识库 + MCP架构：

┌──────────────────────────────────────────────────────┐
│  企业知识库助手                                        │
│                                                      │
│  MCP Server集群：                                     │
│  ├── Confluence MCP → 读取Wiki文档                    │
│  ├── Slack MCP → 搜索聊天记录                         │
│  ├── Jira MCP → 查询Issue和项目状态                   │
│  ├── GitHub MCP → 搜索代码仓库                        │
│  ├── MySQL MCP → 查询业务数据                         │
│  └── S3 MCP → 读取文档文件                            │
│                                                      │
│  AI能力：                                             │
│  "项目X的进度如何？" → Jira MCP查询项目状态            │
│  "这个API怎么用？" → Confluence MCP搜索文档            │
│  "上周的会议决定了什么？" → Slack MCP搜索会议频道      │
│  "这个Bug的根因是什么？" → GitHub MCP + Jira MCP联动  │
└──────────────────────────────────────────────────────┘
```

**🔬 企业知识库场景核心价值**：

**解决数据孤岛问题**：
- 企业数据分散在Confluence、Slack、Jira、GitHub等系统中
- 传统方式：每个系统有独立的搜索入口，用户需要分别查询
- MCP方式：AI通过统一的MCP协议访问所有系统，一次查询跨系统关联

**安全考量**：
- 每个MCP Server使用对应系统的只读API
- Tool Annotations标记 `readOnlyHint: true`，确保不修改企业数据
- `openWorldHint: true` 标记访问外部系统的工具，便于审计

### 11.3 场景三：DevOps自动化

```
DevOps + MCP架构：

┌──────────────────────────────────────────────────────┐
│  DevOps自动化助手                                      │
│                                                      │
│  MCP Server集群：                                     │
│  ├── Kubernetes MCP → 管理Pod/Service/Deployment     │
│  ├── Docker MCP → 构建镜像/管理容器                   │
│  ├── Prometheus MCP → 查询监控指标                    │
│  ├── Grafana MCP → 读取Dashboard数据                 │
│  ├── Ansible MCP → 执行运维剧本                      │
│  └── PagerDuty MCP → 管理告警                        │
│                                                      │
│  AI能力：                                             │
│  "服务X的CPU使用率异常" → Prometheus MCP查询指标       │
│  "重启服务Y" → K8s MCP执行滚动重启                    │
│  "查看最近的告警" → PagerDuty MCP查询                 │
│  "扩容服务Z到5个副本" → K8s MCP执行扩容               │
└──────────────────────────────────────────────────────┘
```

**🔬 DevOps场景安全设计**：

**破坏性操作的确认机制**：
- `kubectl rollout restart` → `destructiveHint: true`，必须用户确认
- `kubectl scale --replicas=5` → `destructiveHint: true`，必须用户确认
- `kubectl get pods` → `readOnlyHint: true`，可自动执行

```
AI请求："重启服务Y"
    │
    ▼
Host检查Tool Annotations
    │
    ├── destructiveHint=true → 弹出确认对话框
    │   "即将执行：kubectl rollout restart deployment/Y
    │    此操作将重启服务，是否继续？"
    │       │
    │       ├── 用户确认 → 执行操作
    │       └── 用户拒绝 → 取消操作
    │
    └── readOnlyHint=true → 自动执行（无需确认）
```

**💡 DevOps场景的审计需求**：
- 所有 `openWorldHint: true` 的工具调用应记录审计日志
- 记录：谁（用户）、何时（时间）、做了什么（工具+参数）、结果如何
- 审计日志可用于事后追溯和合规检查

### 11.4 场景四：数据分析助手

```
数据分析 + MCP架构：

┌──────────────────────────────────────────────────────┐
│  数据分析助手                                          │
│                                                      │
│  MCP Server集群：                                     │
│  ├── MySQL MCP → 查询业务数据库                       │
│  ├── ClickHouse MCP → 查询分析数据库                  │
│  ├── Redis MCP → 查询缓存数据                        │
│  ├── Python MCP → 执行数据分析脚本                    │
│  └── Chart MCP → 生成可视化图表                      │
│                                                      │
│  AI能力：                                             │
│  "本月GMV趋势" → MySQL MCP查询 + Chart MCP可视化      │
│  "用户留存率分析" → ClickHouse MCP + Python MCP计算   │
│  "实时在线用户数" → Redis MCP查询                     │
│  "生成周报" → 多MCP联动汇总数据                       │
└──────────────────────────────────────────────────────┘
```

**🔬 数据分析场景的多MCP联动**：

**"本月GMV趋势"的完整执行链路**：
```
1. AI分析意图 → 需要查询MySQL获取订单数据
2. 调用 MySQL MCP 的 run_select_query
   SQL: SELECT DATE(created_at), SUM(amount) FROM orders WHERE ... GROUP BY DATE(created_at)
3. 获取查询结果 → 需要可视化
4. 调用 Chart MCP 的 generate_line_chart
   参数: {data: 查询结果, title: "本月GMV趋势", x_axis: "日期", y_axis: "GMV"}
5. 返回图表URL或Base64图片
```

**💡 Python MCP的设计考量**：
- Python MCP允许AI执行任意Python代码，风险极高
- 安全方案：
  - 运行在Docker沙箱中，隔离执行环境
  - 限制可导入的库（只允许pandas、numpy等数据分析库）
  - 设置执行超时（如30秒）
  - 限制内存使用（如1GB）

---

## 模块十二：MCP生态与未来

### 12.1 MCP生态现状

```
MCP生态组件：

官方参考实现：
├── Python SDK (mcp) → 服务端+客户端开发
├── TypeScript SDK (@modelcontextprotocol/sdk) → Node.js开发
└── Inspector → 调试工具

官方MCP Server：
├── filesystem → 文件系统操作
├── github → GitHub API集成
├── gitlab → GitLab API集成
├── postgres → PostgreSQL数据库
├── sqlite → SQLite数据库
├── slack → Slack消息集成
├── google-drive → Google Drive文件
├── puppeteer → 浏览器自动化
├── brave-search → Brave搜索引擎
├── sequential-thinking → 思维链推理
└── memory → 持久化记忆

Host应用：
├── Claude Desktop → Anthropic官方客户端
├── Cursor → AI IDE
├── Continue → VS Code插件
├── Zed → 代码编辑器
└── Sourcegraph Cody → 代码搜索助手
```

**🔬 生态架构解析**：

**三层生态结构**：

```
┌─────────────────────────────────────────────────────┐
│  应用层（Host）                                       │
│  Claude Desktop / Cursor / Continue / Zed            │
│  价值：提供用户界面，管理MCP Client                     │
├─────────────────────────────────────────────────────┤
│  协议层（MCP规范）                                    │
│  JSON-RPC 2.0 + Tools/Resources/Prompts              │
│  价值：标准化通信，消除集成碎片化                       │
├─────────────────────────────────────────────────────┤
│  能力层（Server）                                     │
│  filesystem / github / postgres / slack / ...         │
│  价值：封装外部系统能力，一次开发多应用可用              │
└─────────────────────────────────────────────────────┘
```

**💡 官方Server的设计模式**：
- 每个Server专注一个领域（单一职责原则）
- Server之间无依赖，可独立部署和升级
- 通过Host的多Client管理实现Server组合

### 12.2 MCP vs Function Calling选型

| 场景 | 推荐方案 | 原因 |
|------|---------|------|
| 单一AI应用+少量工具 | OpenAI Function Calling | 简单直接，无需额外协议 |
| 多AI应用+共享工具 | MCP | 工具一次开发，多应用复用 |
| 需要动态发现工具 | MCP | 内置发现机制 |
| 需要双向通信 | MCP | 支持服务端推送 |
| 需要资源订阅 | MCP | 内置订阅机制 |
| 快速原型开发 | Function Calling | 无需搭建Server |
| 企业级AI平台 | MCP | 标准化、安全、可管理 |

**🔬 选型决策树**：

```
你的项目需要工具集成吗？
    │
    ├── 否 → 不需要MCP或Function Calling
    │
    └── 是 → 只用一个AI模型吗？
              │
              ├── 是 → 工具数量少（<5个）吗？
              │         │
              │         ├── 是 → Function Calling（简单直接）
              │         └── 否 → 需要动态发现工具吗？
              │                   │
              │                   ├── 是 → MCP
              │                   └── 否 → Function Calling
              │
              └── 否（多AI应用）→ MCP（工具复用+标准化）
```

**💡 选型核心判断标准**：
1. **是否需要跨应用复用？** → 是则MCP
2. **是否需要动态发现？** → 是则MCP
3. **是否需要双向通信？** → 是则MCP
4. **是否需要快速原型？** → 是则Function Calling
5. **是否是企业级部署？** → 是则MCP

### 12.3 未来发展方向

```
MCP协议演进方向：

1. 协议增强
   ├── Streamable HTTP传输（已发布）
   ├── OAuth2.1认证（已发布）
   ├── 结构化工具输出
   └── 流式资源支持

2. 生态扩展
   ├── 更多官方Server
   ├── Server市场/注册中心
   ├── 跨语言SDK（Go, Rust, Java）
   └── 标准化测试套件

3. 企业特性
   ├── 细粒度权限控制
   ├── 审计日志标准
   ├── 多租户支持
   └── 服务网格集成

4. AI能力增强
   ├── Agent间协作协议
   ├── 多模态资源支持
   ├── 长时任务编排
   └── 分布式工具调用
```

**🔬 演进方向技术解析**：

#### 结构化工具输出

当前MCP Tool的返回值是 `list[Content]`（文本/图片内容列表），未来将支持结构化输出：

```json
// 当前：非结构化文本返回
{"content": [{"type": "text", "text": "用户数: 12345"}]}

// 未来：结构化数据返回
{"content": [{"type": "resource_link", "resource": {"mimeType": "application/json", "data": {"user_count": 12345}}}]}
```

**价值**：AI模型可以直接解析结构化数据，而非从文本中提取信息

#### Agent间协作协议

```
当前：单个Agent + 多个MCP Server

┌──────────┐
│  Agent   │──┬──▶ MCP Server A
│          │──┼──▶ MCP Server B
└──────────┘  └──▶ MCP Server C

未来：多Agent协作 + MCP Server共享

┌──────────┐     ┌──────────┐
│ Agent A  │────▶│ Agent B  │
│ (规划)    │     │ (执行)    │
└────┬─────┘     └────┬─────┘
     │                │
     └───────┬────────┘
             │
     ┌───────┼───────┐
     ▼       ▼       ▼
  Server A Server B Server C
```

**价值**：复杂任务可拆分为多个子任务，由专门的Agent协作完成

#### Server市场/注册中心

```
当前：手动配置每个MCP Server
{
  "mcpServers": {
    "my-tools": {"command": "python", "args": ["server.py"]},
    "remote-api": {"url": "http://localhost:8080/sse"}
  }
}

未来：从注册中心发现和安装MCP Server
$ mcp install @official/github
$ mcp install @community/jira
$ mcp search "database"
```

**价值**：降低MCP Server的发现和使用成本，促进生态繁荣

---

## 模块十三：LangGraph深度集成MCP

### 13.1 LangGraph + MCP架构

```
LangGraph + MCP Agent架构：

┌─────────────────────────────────────────────────────────┐
│  LangGraph StateGraph                                   │
│                                                          │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐         │
│  │ Agent节点 │───▶│ MCP工具   │───▶│ 观察节点  │         │
│  │ (LLM推理) │    │ 动态发现  │    │ (结果评估) │         │
│  └──────────┘    └────┬─────┘    └────┬─────┘         │
│                       │               │                  │
│                       ▼               │                  │
│  ┌──────────────────────────────────┐ │                 │
│  │  MCP Client                      │ │                 │
│  │  ├── discover_tools()            │ │                 │
│  │  ├── call_tool(name, args)       │◀│                 │
│  │  └── list_resources()            │                   │
│  └──────────┬───────────────────────┘                   │
│             │                                            │
│     ┌───────┼───────┐                                   │
│     ▼       ▼       ▼                                   │
│  ┌──────┐┌──────┐┌──────┐                              │
│  │MySQL ││Redis ││File  │                              │
│  │Server││Server││Server│                              │
│  └──────┘└──────┘└──────┘                              │
└─────────────────────────────────────────────────────────┘

核心优势：
  1. 工具动态发现：Agent运行时发现MCP Server提供的工具
  2. 标准化协议：所有工具遵循MCP规范，统一调用方式
  3. 有状态工作流：LangGraph管理对话历史和Agent状态
  4. 可扩展：新增MCP Server无需修改Agent代码
```

**🔬 LangGraph核心概念**：

**StateGraph（状态图）**：
- LangGraph的核心抽象，将Agent建模为有限状态机
- 每个节点是一个处理函数，接收当前状态，返回状态更新
- 边定义节点之间的转移条件
- 状态是TypedDict，在整个图中共享

**与MCP的结合点**：
- MCP提供"工具能力"（Tool），LangGraph提供"编排能力"（Workflow）
- MCP的动态发现机制使得LangGraph Agent可以在运行时获取可用工具
- LangGraph的检查点机制保证Agent状态的可恢复性

### 13.2 MCP工具转换为LangChain Tool

```python
import asyncio
import json
from typing import Any
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class MCPToolWrapper(BaseTool):
    name: str = ""
    description: str = ""
    args_schema: type[BaseModel] | None = None
    session: Any = None
    tool_name: str = ""

    def _run(self, **kwargs) -> str:
        return asyncio.run(self._arun(**kwargs))

    async def _arun(self, **kwargs) -> str:
        result = await self.session.call_tool(self.tool_name, arguments=kwargs)
        if result.content:
            return "\n".join(
                item.text if hasattr(item, "text") else str(item)
                for item in result.content
            )
        return "工具执行完成，无返回内容"


class MCPToolDiscovery:
    def __init__(self):
        self.sessions: dict[str, ClientSession] = {}
        self.server_configs: dict[str, StdioServerParameters] = {}

    def register_server(self, name: str, command: str, args: list[str] = None, env: dict = None):
        self.server_configs[name] = StdioServerParameters(
            command=command,
            args=args or [],
            env=env,
        )

    async def discover_tools(self) -> list[MCPToolWrapper]:
        tools = []
        for server_name, params in self.server_configs.items():
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.list_tools()
                    self.sessions[server_name] = session

                    for mcp_tool in result.tools:
                        schema = self._build_pydantic_schema(mcp_tool)
                        wrapper = MCPToolWrapper(
                            name=f"mcp_{server_name}_{mcp_tool.name}",
                            description=mcp_tool.description or "",
                            args_schema=schema,
                            session=session,
                            tool_name=mcp_tool.name,
                        )
                        tools.append(wrapper)
        return tools

    @staticmethod
    def _build_pydantic_schema(mcp_tool) -> type[BaseModel]:
        properties = mcp_tool.inputSchema.get("properties", {})
        required = mcp_tool.inputSchema.get("required", [])
        fields = {}
        for prop_name, prop_def in properties.items():
            prop_type = str
            if prop_def.get("type") == "integer":
                prop_type = int
            elif prop_def.get("type") == "number":
                prop_type = float
            elif prop_def.get("type") == "boolean":
                prop_type = bool
            default = ... if prop_name in required else None
            fields[prop_name] = (prop_type, Field(default=default, description=prop_def.get("description", "")))
        return type(f"{mcp_tool.name}Schema", (BaseModel,), fields)


discovery = MCPToolDiscovery()
discovery.register_server("mysql", "python", ["mcp_server_mysql.py"])
discovery.register_server("redis", "python", ["mcp_server_redis.py"])
discovery.register_server("filesystem", "python", ["mcp_server_fs.py"])
```

**🔬 工具转换原理详解**：

#### MCPToolWrapper：MCP Tool → LangChain Tool 的适配器

```python
class MCPToolWrapper(BaseTool):
    name: str = ""
    description: str = ""
    args_schema: type[BaseModel] | None = None
    session: Any = None
    tool_name: str = ""
```

**适配器模式**：
- `BaseTool` 是LangChain的工具基类，要求实现 `_run` 和 `_arun` 方法
- `MCPToolWrapper` 将MCP的工具调用委托给 `session.call_tool()`
- `name` 使用 `mcp_{server_name}_{tool_name}` 格式，避免不同Server的工具名冲突
- `tool_name` 保存原始MCP工具名，调用时使用

**类型映射**：

| JSON Schema类型 | Python类型 | 说明 |
|----------------|-----------|------|
| `string` | `str` | 字符串 |
| `integer` | `int` | 整数 |
| `number` | `float` | 浮点数 |
| `boolean` | `bool` | 布尔值 |
| `array` | `list` | 数组（未处理） |
| `object` | `dict` | 对象（未处理） |

#### MCPToolDiscovery：多Server工具发现器

**工作流程**：
1. `register_server()` 注册MCP Server配置
2. `discover_tools()` 遍历所有注册的Server：
   - 建立stdio连接
   - 初始化会话
   - 获取工具列表
   - 将每个MCP Tool转换为LangChain Tool
3. 返回所有工具的列表

**💡 工具名前缀的作用**：
- `name=f"mcp_{server_name}_{mcp_tool.name}"` 添加了Server名前缀
- 防止不同Server的同名工具冲突（如MySQL和Redis都有 `get` 工具）
- 调用时需要去掉前缀：`tc["name"].replace(f"mcp_{server_name}_", "")`

### 13.3 LangGraph + MCP ReAct Agent

```python
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import HumanMessage

async def create_mcp_agent():
    tools = await discovery.discover_tools()

    llm = ChatOpenAI(
        base_url="http://localhost:8000/v1",
        api_key="empty",
        model="./merged-for-vllm",
        temperature=0,
    )

    system_prompt = f"""你是一个专业的AI助手，可以通过MCP工具访问外部系统。

可用工具：
{chr(10).join(f"- {t.name}: {t.description}" for t in tools)}

使用规则：
1. 先分析用户意图，选择合适的工具
2. 调用工具获取数据
3. 基于工具返回结果回答用户
4. 不要编造数据，如果工具返回空结果请如实说明
"""

    agent = create_react_agent(
        llm,
        tools,
        checkpointer=MemorySaver(),
        prompt=system_prompt,
    )
    return agent


agent = asyncio.run(create_mcp_agent())

result = agent.invoke(
    {"messages": [HumanMessage(content="查询最近7天的订单数据，并统计每日订单量")]},
    config={"configurable": {"thread_id": "mcp-session-001"}}
)
for msg in result["messages"]:
    print(f"[{msg.type}] {msg.content}")
```

**🔬 ReAct Agent执行流程**：

```
用户："查询最近7天的订单数据，并统计每日订单量"
                    │
                    ▼
┌─────────────────────────────────────────────────────┐
│ Agent节点（LLM推理）                                  │
│ LLM分析：需要调用MySQL MCP的run_select_query工具      │
│ 生成tool_calls: [{"name": "mcp_mysql_run_select_    │
│ query", "arguments": {"sql": "SELECT DATE(...)..."}}]│
└──────────────────────┬──────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────┐
│ Tools节点（工具执行）                                  │
│ 调用MCPToolWrapper._arun()                           │
│ → session.call_tool("run_select_query", {...})       │
│ → MCP Server执行SQL查询                              │
│ → 返回查询结果                                        │
└──────────────────────┬──────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────┐
│ Agent节点（LLM推理）                                  │
│ LLM基于查询结果生成最终回答                            │
│ "最近7天的每日订单量如下：..."                         │
└─────────────────────────────────────────────────────┘
```

**💡 system_prompt中的工具列表**：
- 将可用工具列表注入system_prompt，帮助LLM理解可用的工具
- 这是一种"提示词增强"策略，提高LLM选择工具的准确性

### 13.4 LangGraph自定义工作流 + MCP

```python
from langgraph.graph import StateGraph, END, START
from typing import Annotated, TypedDict
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage
from langgraph.graph.message import add_messages
from langgraph.checkpoint.memory import MemorySaver


class MCPAgentState(TypedDict):
    messages: Annotated[list, add_messages]
    mcp_tools: list[dict]
    next_action: str
    tool_results: list[str]
    iteration: int


llm = ChatOpenAI(
    base_url="http://localhost:8000/v1",
    api_key="empty", model="./merged-for-vllm", temperature=0
)


async def discover_mcp_tools_node(state: MCPAgentState) -> dict:
    tools = await discovery.discover_tools()
    tool_defs = []
    for t in tools:
        tool_defs.append({
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.args_schema.schema() if t.args_schema else {},
            }
        })
    return {"mcp_tools": tool_defs}


def plan_node(state: MCPAgentState) -> dict:
    tools_desc = "\n".join(f"- {t['function']['name']}: {t['function']['description']}" for t in state["mcp_tools"])
    messages = [
        SystemMessage(content=f"""你是专业助手，可使用以下MCP工具：
{tools_desc}

请分析用户问题，决定是否需要调用工具。如需调用，返回工具调用。"""),
    ] + state["messages"]

    llm_with_tools = llm.bind_tools(state["mcp_tools"])
    response = llm_with_tools.invoke(messages)
    next_action = "execute" if response.tool_calls else "respond"
    return {"messages": [response], "next_action": next_action, "iteration": state.get("iteration", 0) + 1}


async def execute_node(state: MCPAgentState) -> dict:
    last_msg = state["messages"][-1]
    results = []
    for tc in last_msg.tool_calls:
        for server_name, session in discovery.sessions.items():
            try:
                result = await session.call_tool(tc["name"].replace(f"mcp_{server_name}_", ""), arguments=tc["args"])
                text = "\n".join(item.text if hasattr(item, "text") else str(item) for item in result.content)
                results.append(text)
                break
            except Exception:
                continue
    return {"tool_results": results, "next_action": "observe"}


def observe_node(state: MCPAgentState) -> dict:
    tool_results_text = "\n\n".join(state["tool_results"])
    messages = state["messages"] + [
        HumanMessage(content=f"工具执行结果：\n{tool_results_text}\n\n请根据结果回答用户问题。")
    ]
    response = llm.invoke(messages)
    return {"messages": [response], "next_action": "end"}


def respond_node(state: MCPAgentState) -> dict:
    return {"next_action": "end"}


def route_action(state: MCPAgentState) -> str:
    if state["next_action"] == "execute" and state.get("iteration", 0) < 5:
        return "execute"
    elif state["next_action"] == "observe":
        return "observe"
    return "respond"


workflow = StateGraph(MCPAgentState)
workflow.add_node("discover", discover_mcp_tools_node)
workflow.add_node("plan", plan_node)
workflow.add_node("execute", execute_node)
workflow.add_node("observe", observe_node)
workflow.add_node("respond", respond_node)

workflow.add_edge(START, "discover")
workflow.add_edge("discover", "plan")
workflow.add_conditional_edges("plan", route_action, {
    "execute": "execute",
    "observe": "observe",
    "respond": "respond",
})
workflow.add_edge("execute", "observe")
workflow.add_conditional_edges("observe", lambda s: "plan" if s.get("iteration", 0) < 5 else "respond", {
    "plan": "plan",
    "respond": "respond",
})
workflow.add_edge("respond", END)

app = workflow.compile(checkpointer=MemorySaver())

result = asyncio.run(app.ainvoke(
    {"messages": [HumanMessage(content="查询Redis中用户session的key数量")], "iteration": 0},
    config={"configurable": {"thread_id": "mcp-workflow-001"}}
))
```

**🔬 自定义工作流原理详解**：

**状态定义**：

```python
class MCPAgentState(TypedDict):
    messages: Annotated[list, add_messages]  # 对话历史，add_messages自动合并
    mcp_tools: list[dict]                    # MCP工具定义列表
    next_action: str                         # 下一步动作
    tool_results: list[str]                  # 工具执行结果
    iteration: int                           # 迭代次数（防止无限循环）
```

**节点职责**：

| 节点 | 职责 | 输入 | 输出 |
|------|------|------|------|
| `discover` | 发现MCP工具 | 无 | `mcp_tools` |
| `plan` | LLM分析意图，决定是否调用工具 | `messages` + `mcp_tools` | `messages` + `next_action` |
| `execute` | 执行MCP工具调用 | `messages`（含tool_calls） | `tool_results` |
| `observe` | LLM基于工具结果生成回答 | `messages` + `tool_results` | `messages` |
| `respond` | 直接回答（无需工具） | `messages` | `next_action="end"` |

**条件路由**：

```python
def route_action(state: MCPAgentState) -> str:
    if state["next_action"] == "execute" and state.get("iteration", 0) < 5:
        return "execute"
    elif state["next_action"] == "observe":
        return "observe"
    return "respond"
```

- `iteration < 5`：防止无限循环，最多5轮工具调用
- 如果LLM决定调用工具 → 走 `execute` → `observe` → 可能回到 `plan`
- 如果LLM决定不调用工具 → 走 `respond` → 结束

**💡 与 `create_react_agent` 的区别**：
- `create_react_agent` 是预构建的ReAct Agent，开箱即用但灵活性有限
- 自定义工作流可以添加更多节点（如"验证"、"反思"、"规划"等）
- 自定义工作流可以实现更复杂的条件路由逻辑

### 13.5 LangGraph + MCP + Django集成

```python
# ai_service/mcp_agent.py
import os
import asyncio
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import HumanMessage


class MCPAgentService:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._agent = None
        return cls._instance

    async def _ensure_agent(self):
        if self._agent is None:
            from .mcp_discovery import discovery
            tools = await discovery.discover_tools()
            llm = ChatOpenAI(
                base_url=os.environ.get("VLLM_URL", "http://localhost:8000/v1"),
                api_key="empty",
                model=os.environ.get("MODEL_NAME", "./merged-for-vllm"),
                temperature=0,
            )
            self._agent = create_react_agent(
                llm, tools, checkpointer=MemorySaver()
            )

    async def query(self, question: str, session_id: str = "default") -> str:
        await self._ensure_agent()
        result = await self._agent.ainvoke(
            {"messages": [HumanMessage(content=question)]},
            config={"configurable": {"thread_id": session_id}}
        )
        return result["messages"][-1].content
```

```python
# ai_service/views.py
from rest_framework.views import APIView
from rest_framework.response import Response
from .mcp_agent import MCPAgentService
import asyncio


class MCPAgentView(APIView):
    def post(self, request):
        question = request.data.get("question", "")
        session_id = request.data.get("session_id", "default")
        if not question:
            return Response({"error": "问题不能为空"}, status=400)

        agent = MCPAgentService()
        answer = asyncio.run(agent.query(question, session_id))
        return Response({"question": question, "answer": answer})
```

```python
# ai_service/urls.py
from django.urls import path
from .views import MCPAgentView

urlpatterns = [
    path("mcp-agent/query/", MCPAgentView.as_view(), name="mcp-agent-query"),
]
```

**🔬 Django集成架构解析**：

**单例模式 `MCPAgentService`**：
- 使用 `__new__` 实现单例，确保全局只有一个Agent实例
- `_ensure_agent()` 延迟初始化，首次调用时才创建Agent
- Agent创建后缓存在 `_instance._agent` 中，后续请求复用

**API请求流程**：
```
POST /mcp-agent/query/
    │
    ▼
MCPAgentView.post()
    │
    ▼
MCPAgentService.query(question, session_id)
    │
    ├── _ensure_agent() → 创建Agent（首次）
    │
    ▼
agent.ainvoke({"messages": [HumanMessage(content=question)]})
    │
    ├── LLM推理 → 选择MCP工具 → 调用MCP Server → 获取结果 → 生成回答
    │
    ▼
返回回答
```

**💡 session_id的作用**：
- LangGraph的checkpointer通过 `thread_id` 管理对话状态
- 不同的 `session_id` 对应不同的对话历史
- 同一 `session_id` 的多次请求共享对话上下文，实现多轮对话

### 13.6 LangGraph + MCP持久化检查点

```python
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool

async def create_persistent_agent():
    pool = AsyncConnectionPool(
        conninfo=os.environ.get("DATABASE_URL", "postgresql://user:pass@localhost:5432/agent_db"),
        max_size=10,
    )
    checkpointer = AsyncPostgresSaver(pool)
    await checkpointer.setup()

    tools = await discovery.discover_tools()
    llm = ChatOpenAI(
        base_url="http://localhost:8000/v1",
        api_key="empty", model="./merged-for-vllm", temperature=0
    )

    agent = create_react_agent(llm, tools, checkpointer=checkpointer)
    return agent
```

**🔬 持久化检查点原理**：

**MemorySaver vs AsyncPostgresSaver**：

| 维度 | MemorySaver | AsyncPostgresSaver |
|------|-------------|-------------------|
| 存储位置 | 内存 | PostgreSQL数据库 |
| 持久性 | 进程重启后丢失 | 永久保存 |
| 跨进程 | 不支持 | 支持 |
| 适用场景 | 开发调试 | 生产环境 |

**AsyncPostgresSaver工作原理**：
1. 每次Agent执行后，将状态（messages、tool_results等）序列化为JSON
2. 存储到PostgreSQL的checkpoint表中，key为 `thread_id`
3. 下次同一 `thread_id` 的请求，从数据库恢复状态
4. `checkpointer.setup()` 创建必要的数据库表

**💡 连接池配置**：
- `max_size=10`：最多10个数据库连接
- 连接池复用连接，避免频繁创建/销毁连接的开销
- 适合高并发场景

---

## 附录A：MCP协议方法速查

| 方法 | 方向 | 说明 | 🔬 调用示例/使用场景 |
|------|------|------|---------------------|
| initialize | C→S | 初始化连接 | `{"method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {...}}}` |
| ping | C↔S | 心跳检测 | 保持连接活跃，检测对端是否可达，超时未响应则判定连接断开 |
| tools/list | C→S | 列出可用工具 | Agent启动时调用，获取Server提供的所有工具定义 |
| tools/call | C→S | 调用工具 | `{"method": "tools/call", "params": {"name": "search_docs", "arguments": {"query": "MCP"}}}` |
| resources/list | C→S | 列出可用资源 | 获取Server提供的所有静态资源URI |
| resources/read | C→S | 读取资源内容 | `{"method": "resources/read", "params": {"uri": "config://app/database"}}` |
| resources/subscribe | C→S | 订阅资源变更 | 监控配置文件/日志变化，变化时收到 `notifications/resources/updated` |
| resources/templates/list | C→S | 列出资源模板 | 获取动态资源URI模板（如 `config://app/{name}`） |
| prompts/list | C→S | 列出提示词模板 | 获取Server提供的所有提示词模板 |
| prompts/get | C→S | 获取提示词内容 | `{"method": "prompts/get", "params": {"name": "code_review", "arguments": {"code": "..."}}}` |
| logging/setLevel | C→S | 设置日志级别 | 控制Server的日志输出级别（debug/info/warning/error） |
| completion/complete | C→S | 参数自动补全 | 为工具参数提供自动补全建议（如枚举值列表） |
| sampling/createMessage | S→C | 请求LLM生成 | **反向调用**：Server请求Client的LLM生成文本，用于Agent的推理能力 |
| roots/list | S→C | 获取根目录列表 | Server了解Client的文件系统根目录，便于提供相对路径资源 |

**🔬 关键方法详解**：

### `sampling/createMessage`：Server→Client的反向调用

这是MCP协议中最独特的方法，允许Server向Client请求LLM生成：

```
使用场景：
1. Server需要AI判断如何处理数据（如智能分类）
2. Server需要AI生成摘要（如文档摘要工具）
3. Server需要AI做决策（如自动化工作流中的条件判断）

调用流程：
Server                          Client
  │─── sampling/createMessage ──▶│
  │    {"messages": [...],       │
  │     "maxTokens": 1000}       │
  │◀── sampling result ─────────│
  │    {"content": "...",        │
  │     "model": "gpt-4o"}      │
```

**💡 安全注意**：
- Client可以选择拒绝sampling请求
- Client应检查 `maxTokens` 限制，防止过度消耗
- Server不应依赖sampling结果做关键决策（可能被拒绝）

### `tools/call` 的 `_meta` 扩展

```json
{
  "method": "tools/call",
  "params": {
    "name": "long_running_task",
    "arguments": {"task_name": "数据导入"},
    "_meta": {
      "progressToken": "task-123"
    }
  }
}
```

- `_meta.progressToken`：Client通过此字段请求进度报告
- Server收到后，在执行过程中发送 `notifications/progress` 通知
- 进度通知格式：`{"method": "notifications/progress", "params": {"progressToken": "task-123", "progress": 50, "total": 100}}`

---

## 附录B：Tool Annotations速查

| 注解 | 类型 | 说明 | 示例 | 🔬 Host行为与安全决策 |
|------|------|------|------|----------------------|
| readOnlyHint | boolean | 只读操作，无副作用 | 查询数据库 | 可自动执行，无需用户确认；适合缓存结果 |
| destructiveHint | boolean | 破坏性操作，需确认 | 删除文件 | 必须用户确认后执行；不可缓存结果 |
| idempotentHint | boolean | 幂等操作，可安全重试 | 创建目录 | 失败后可自动重试；多次执行结果一致 |
| openWorldHint | boolean | 访问外部系统 | HTTP请求 | 需审计日志；结果可能不可预测 |

**🔬 Annotations组合场景分析**：

| 组合 | 含义 | 典型工具 | Host策略 |
|------|------|---------|---------|
| readOnly=true, destructive=false, idempotent=true, openWorld=false | 纯只读本地操作 | `list_files`、`read_config` | 自动执行，可缓存 |
| readOnly=true, openWorld=true | 只读但访问外部 | `fetch_api_data`、`search_web` | 自动执行但记录审计，不可缓存 |
| readOnly=false, destructive=true | 破坏性写操作 | `delete_file`、`drop_table` | 必须用户确认，不可重试 |
| readOnly=false, destructive=false, idempotent=true | 非破坏性幂等写 | `create_directory`、`set_config` | 可自动执行，失败可重试 |
| readOnly=false, destructive=false, idempotent=false | 非幂等写操作 | `send_email`、`create_order` | 需确认，不可重试（避免重复发送） |

**💡 Annotations的"声明式安全"本质**：
- Annotations是Server对工具行为的**声明**，而非强制约束
- 恶意Server可能伪造Annotations（如标记 `readOnlyHint=true` 但实际执行写操作）
- 因此，Annotations应作为**辅助决策信息**，而非唯一安全防线
- 生产环境应结合：传输安全 + Annotations + 用户确认 + 沙箱隔离 + 审计日志

---

## 附录C：常见问题排查

| 问题 | 原因 | 解决方案 | 🔬 根因分析 |
|------|------|---------|------------|
| 连接超时 | Server未启动/端口错误 | 检查Server进程和端口 | Client发送initialize请求后，在超时时间内未收到Server响应 |
| 工具调用失败 | 参数类型不匹配 | 检查inputSchema定义 | AI模型生成的参数类型与JSON Schema定义不一致（如传了字符串给integer字段） |
| 权限被拒 | 安全策略限制 | 检查Annotations和用户授权 | Host根据Annotations判断操作需要用户确认，但用户拒绝了 |
| 中文乱码 | 编码问题 | 确保UTF-8编码 | stdio传输中消息未使用UTF-8编码，或文件读取未指定encoding |
| Server崩溃 | 未捕获异常 | 添加try/except和日志 | Tool函数内部抛出未捕获的异常，导致Server进程终止 |
| 工具不可见 | 未注册或listChanged未触发 | 检查装饰器和初始化 | @mcp.tool()装饰器未正确应用，或Server未在初始化响应中声明tools能力 |

## 附录D：面试高频问题

1. **MCP是什么？** → AI模型与外部工具/数据源的标准化通信协议
   - 🔬 补充：MCP解决了AI工具集成的碎片化问题，类似于LSP解决了编辑器与语言服务器的通信标准化

2. **MCP和Function Calling的区别？** → MCP是开放标准，支持发现/订阅/双向通信；Function Calling是API特性
   - 🔬 补充：关键差异在于"工具发现"时机——MCP在连接建立时发现，Function Calling在每次API调用时携带

3. **MCP的三大核心能力？** → Tools（工具调用）、Resources（资源访问）、Prompts（提示词模板）
   - 🔬 补充：Tools是"做"（操作），Resources是"读"（数据），Prompts是"说"（模板）

4. **MCP的传输方式？** → stdio（本地）、SSE（远程HTTP）、Streamable HTTP（推荐）
   - 🔬 补充：stdio走操作系统管道，延迟最低；SSE/Streamable HTTP走网络，支持远程部署

5. **MCP如何保证安全？** → 传输加密 + Tool Annotations + 用户授权 + OAuth2.1 + 沙箱
   - 🔬 补充：三层安全模型——传输安全（加密）、权限控制（Annotations+确认）、数据安全（验证+过滤）

6. **MCP Server如何开发？** → Python SDK (FastMCP) 或 TypeScript SDK
   - 🔬 补充：FastMCP通过装饰器自动提取inputSchema和description，底层是JSON-RPC消息路由

7. **MCP如何与Django集成？** → Django ORM操作封装为MCP Tool，或Django视图调用MCP Client
   - 🔬 补充：MCP Server作为独立进程，需要调用django.setup()初始化ORM

8. **MCP的连接生命周期？** → Initialize → 正常通信 → 关闭
   - 🔬 补充：三步握手（initialize请求→响应→initialized通知），类似TCP三次握手

9. **Tool Annotations有什么用？** → 标注工具行为特征，帮助Host做安全决策
   - 🔬 补充：是"声明式安全"，Server声明行为，Host据此决定是否自动执行或需要确认

10. **MCP适合什么场景？** → 多AI应用共享工具、需要动态发现、企业级AI平台
    - 🔬 补充：核心判断标准——是否需要"一次开发，多应用复用"的工具标准化