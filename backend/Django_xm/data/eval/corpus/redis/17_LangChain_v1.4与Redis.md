# 17 LangChain v1.4 与 Redis

> **LangChain 1.x**（本文基于 v1.4 写作）重构后的包结构（langchain_core / langchain_redis / langgraph），Redis 作为 Agent 的会话记忆、向量检索、KV 存储、Checkpointer 与 HITL 状态中心，构成生产级 AI Agent 的核心数据底座。

## 零基础前置认知

**这篇在讲什么**：用 LangChain 1.x 写"带记忆的 AI Agent"——会话历史（RedisChatMessageHistory）、向量知识库（RedisVectorStore）、KV 状态与 Checkpointer（RedisStore / RedisSaver）三层数据都落在 Redis 上，最后用 LangGraph 把工具调用、多 Agent 路由与人工审批（HITL）串成生产级流程。读完你能用 langchain_redis 三件套搭一个"记得住你、查得了知识库、动得了工具"的 Agent，并说清 LCEL、create_react_agent、StateGraph、interrupt 各自解决什么问题。*本章 codes 提供 3 个免 API Key 的可运行示例（FakeEmbeddings / FakeListLLM），先跑通再接入真实模型。*

> 辅助类比：把 Agent 想成"有档案柜的客服专员"——**RedisChatMessageHistory** 是"本次通话记录本"（短期记忆，自动过期）；**RedisVectorStore** 是"资料库检索台"（长期记忆，按语义找文档）；**RedisStore / RedisSaver** 是"工单状态柜"（存到哪一步、可断点续办）；**@tool** 是"对外服务窗口"（缓存/队列/锁/检索）；**HITL 审批** 是"高风险操作需主管签字"（写操作人工放行）；**LangGraph** 负责"排班表"（谁先执行、下一步路由给谁）。

**基础名词集**：

| 名词 | 一句话定义 | 与相邻概念的关系 |
|------|-----------|----------------|
| LangChain 1.x | 重构后的包结构（langchain_core 抽象层 + langchain_\<partner\> 集成 + langgraph 编排） | 1.x 弃用 langchain.chains / langchain.agents 旧范式，改用 LCEL 与新版图式 Agent |
| LCEL | 用 `\|` 管道组合 Runnable 的表达式语言 | `prompt \| llm \| parser` 即一条链；统一 invoke / batch / stream / ainvoke |
| langchain_redis | Redis 官方集成包（三件套） | 含 RedisChatMessageHistory / RedisVectorStore / RedisStore / RedisCache |
| Agent Memory | 短期 + 长期 + 工作记忆分层 | 数据来自三件套，组装后注入 Prompt 上下文 |
| @tool | 函数转工具的装饰器（Pydantic schema 驱动参数校验） | 工具供 LLM 决策调用，由 LangGraph ToolNode 执行 |
| create_react_agent | LangGraph 预置的 ReAct 循环（Reason → Act → Observe） | 替代手写 AgentExecutor；thread_id 隔离会话、checkpointer 持久化 |
| StateGraph | 显式状态机式的图框架（节点 + 边 + 状态） | 多 Agent 通过 Command 路由；配合 checkpointer 断点续跑 |
| HITL / interrupt | 人工介入：遇写操作暂停，审批通过后 Command(resume=) 恢复 | 审批状态存 Redis Hash，可审计 |

> 区分/注意：**"LangChain 1.x"是架构切换而非版本号后缀**——本文基于 v1.4 写作，导入一律用 langchain_core / langchain_redis / langgraph 新结构，旧 `langchain.chains`、`langchain.agents` 已弃用；**三件套不是三个数据库**——是同一 Redis 上的三种用法（List 会话历史 / 向量索引 / KV 状态），靠 key 前缀与 index_name 隔离空间；**Agent 不是"自带知识"**——它只是 LLM + 工具 + 状态机，知识要靠向量库检索、状态要靠 store 存取，别把记忆/检索/审批当成默认自带能力。

## 学习目标

- 掌握 LangChain 1.x 包结构与 `langchain_core.*` / `langchain_redis.*` / `langgraph.prebuilt.*` 导入规范
- 熟练使用 LCEL 管道（`|`）与 Runnable 接口（invoke / stream / ainvoke）
- 掌握 `langchain_redis` 三大核心 API：RedisChatMessageHistory / RedisVectorStore / RedisStore
- 设计并实现 Agent Memory 架构：短期记忆 + 长期记忆 + 上下文注入
- 使用 `@tool` 装饰器封装 Redis 工具（cache / queue / lock / vector_search）
- 使用 `langgraph.prebuilt.create_react_agent` 构建带 Redis 记忆的 ReAct Agent
- 用 LangGraph StateGraph 实现 Supervisor 多 Agent 协作
- 实现 HITL（Human-in-the-Loop）工具审批流程
- 完成生产化配置：异步、重试、超时、LangSmith 监控

## 前置知识

- [16 Redis 向量存储与 RAG 应用](../16_Redis向量存储与RAG应用/16_Redis向量存储与RAG应用.md)：RedisVectorStore / Embedding / RAG Pipeline
- [08 发布订阅与消息队列](../08_发布订阅与消息队列/08_发布订阅与消息队列.md)：Stream / Pub-Sub
- [10 分布式锁与限流](../10_分布式锁与限流/10_分布式锁与限流.md)：SETNX / Redlock
- [09 缓存模式与实战](../09_缓存模式与实战/09_缓存模式与实战.md)：cache aside / TTL
- 熟悉 Python 异步、Pydantic v2、TypeScript 类型注解思维

## 一、LangChain v1.4 包结构

### 1.1 包结构总览

```
langchain-core          # 抽象层（Runnable / BaseMessage / BaseRetriever）
├── langchain_core.runnables
├── langchain_core.prompts
├── langchain_core.messages
├── langchain_core.tools          # @tool / BaseTool
├── langchain_core.documents
└── langchain_core.outputs

langchain              # 主包（兼容层，少用）
├── langchain.chains   # ⚠️ v1.4 已弃用，改用 LCEL
└── langchain.agents   # ⚠️ 1.x 已弃用，改用 langgraph

langchain-<partner>    # 合作伙伴集成
├── langchain_openai          # ChatOpenAI / OpenAIEmbeddings
├── langchain_redis           # ⭐ Redis 全家桶
├── langchain_huggingface     # HuggingFaceEmbeddings
├── langchain_community       # 社区适配（旧集成）
└── langchain_mcp_adapters    # MCP 工具适配

langgraph              # Agent 编排框架
├── langgraph.graph           # StateGraph
├── langgraph.prebuilt        # create_react_agent / ToolNode
├── langgraph.checkpoint      # Checkpointer
└── langgraph.errors          # GraphRecursionError
```

### 1.2 导入规范（v1.4 强制）

```python
# ✅ 正确：v1.4 新结构
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain_core.tools import tool, BaseTool
from langchain_core.runnables import RunnablePassthrough, RunnableLambda
from langchain_core.documents import Document
from langchain_core.output_parsers import StrOutputParser
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_redis import RedisChatMessageHistory, RedisVectorStore, RedisStore
from langgraph.prebuilt import create_react_agent, ToolNode
from langgraph.graph import StateGraph, END, START
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.redis import RedisSaver  # 需 langgraph-checkpoint-redis 包（见下方安装命令）

# ❌ 错误：1.x 已弃用，禁止使用
# from langchain.chains import RetrievalQA, LLMChain
# from langchain.agents import AgentExecutor, initialize_agent
# from langchain.agents.agent_types import AgentType
# from langchain.memory import ConversationBufferMemory
# from langchain.vectorstores import Redis
```

> ⚠️ **风险提示**：`langchain_redis` 包的 API 在不同版本有差异，安装后用 `pip show langchain-redis` 确认版本号，本文基于 `langchain-redis >= 0.2.5`（PyPI 当前最新稳定）撰写。

### 1.3 环境准备

```powershell
conda activate langchain_xm
pip install "langchain>=1.4" "langchain-core>=1.4" "langchain-redis>=0.2.5" `
            "langchain-openai>=1.0" "langgraph>=1.0" "redis>=8.1" redisvl `
            pydantic
# 可选：异步支持
pip install "langgraph-checkpoint-redis>=0.1"
```

## 二、LCEL（LangChain Expression Language）

### 2.1 LCEL 三大特性

| 特性 | 说明 | 示例 |
|------|------|------|
| 管道 `\|` | 顺序组合 Runnable | `prompt \| llm \| parser` |
| 统一接口 | invoke / batch / stream / ainvoke | 全 Runnable 实现 |
| 自动并行 | dict 内 Runnable 并发执行 | `{"a": r1, "b": r2} \| merge` |

### 2.2 Runnable 接口

```python
from langchain_core.runnables import RunnableLambda, RunnablePassthrough
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)  # 示例模型（按实际可替换其它型号）
prompt = ChatPromptTemplate.from_template("用 50 字解释 {topic}")
parser = StrOutputParser()

chain = prompt | llm | parser

# 1. 同步调用
print(chain.invoke({"topic": "Redis 向量检索"}))

# 2. 批量（并发上限可配）
results = chain.batch([{"topic": "Redis"}, {"topic": "LangGraph"}],
                     config={"max_concurrency": 2})

# 3. 流式
for chunk in chain.stream({"topic": "Redis 向量检索"}):
    print(chunk, end="", flush=True)

# 4. 异步
import asyncio
async def amain():
    answer = await chain.ainvoke({"topic": "Redis 向量检索"})
    print(answer)
    async for c in chain.astream({"topic": "Redis"}):
        print(c, end="", flush=True)

asyncio.run(amain())
```

### 2.3 数据流与 RunnablePassthrough

```python
from langchain_core.runnables import RunnablePassthrough, RunnableParallel

# RunnablePassthrough：原样透传输入
# RunnableParallel：并行执行多个 Runnable

chain = RunnableParallel({
    "original": RunnablePassthrough(),
    "uppercased": RunnableLambda(lambda x: x.upper()),
    "length": RunnableLambda(lambda x: len(x)),
})
print(chain.invoke("hello"))
# {'original': 'hello', 'uppercased': 'HELLO', 'length': 5}
```

### 2.4 LCEL 执行模型

```
        input
          │
          ▼
┌─────────────────────┐
│  Runnable 1 (prompt)│ ── invoke ──> prompt_value
└─────────────────────┘
          │ pipe
          ▼
┌─────────────────────┐
│  Runnable 2 (llm)   │ ── invoke ──> AIMessage
└─────────────────────┘
          │ pipe
          ▼
┌─────────────────────┐
│  Runnable 3 (parser)│ ── invoke ──> str
└─────────────────────┘
          │
          ▼
        output

并发执行由 batch / astream 自动处理
错误传播：任一环节抛异常，整个链路短路
```

## 三、langchain_redis 核心三件套

### 3.1 RedisChatMessageHistory（会话历史）

```python
from langchain_redis import RedisChatMessageHistory

history = RedisChatMessageHistory(
    session_id="user_001_session_1",
    redis_url="redis://localhost:6379",
    key_prefix="chat:",        # 实际 key: chat:user_001_session_1
    ttl=86400,                 # 24h 自动过期
)

from langchain_core.messages import HumanMessage, AIMessage
history.add_message(HumanMessage(content="你好"))
history.add_message(AIMessage(content="你好！有什么可以帮你？"))

# 读取全部历史
msgs = history.messages
for m in msgs:
    print(f"[{m.type}] {m.content}")

# 清空
history.clear()
```

**多会话管理：**

```python
def get_history(session_id: str) -> RedisChatMessageHistory:
    """工厂函数：按需创建会话历史"""
    return RedisChatMessageHistory(
        session_id=session_id,
        redis_url="redis://localhost:6379",
        ttl=86400,
    )

# 不同用户/会话隔离
user1_history = get_history("user_001:conv_1")
user2_history = get_history("user_002:conv_1")
```

### 3.2 RedisVectorStore（向量存储）

详见 [16 Redis 向量存储与 RAG 应用](../16_Redis向量存储与RAG应用/16_Redis向量存储与RAG应用.md)，这里给出在 Agent 场景的快速用法。

```python
from langchain_redis import RedisConfig, RedisVectorStore
from langchain_openai import OpenAIEmbeddings

embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
config = RedisConfig(
    index_name="agent_kb",
    redis_url="redis://localhost:6379",
    key_prefix="agent",
)

# 写入
vs = RedisVectorStore(embeddings, config=config)
from langchain_core.documents import Document
vs.add_documents([
    Document(page_content="Redis 7 支持 HNSW", metadata={"cat": "tech"}),
    Document(page_content="LangGraph 用于 Agent 编排", metadata={"cat": "ai"}),
])

# 检索（as_retriever）
retriever = vs.as_retriever(search_type="mmr", search_kwargs={"k": 5})
docs = retriever.invoke("Redis 向量")
```

### 3.3 RedisStore（通用 KV 存储）

```python
from langchain_redis import RedisStore

store = RedisStore(redis_url="redis://localhost:6379",
                   namespace="agent_state")

# BaseStore 接口：MSET / MGET / MDELETE
store.mset([("task:1", "running"), ("task:2", "pending")])
print(store.mget(["task:1", "task:2"]))   # ['running', 'pending']
store.mdelete(["task:1"])

# 异步接口
import asyncio
async def amain():
    await store.amset([("k1", "v1")])
    vals = await store.amget(["k1"])
    print(vals)   # ['v1']

asyncio.run(amain())
```

### 3.4 三件套对比

| API | 用途 | 数据结构 | 典型场景 |
|-----|------|---------|---------|
| RedisChatMessageHistory | 会话上下文 | List（按时间序） | 短期记忆 |
| RedisVectorStore | 语义检索 | HNSW 向量索引 | 长期记忆 / RAG |
| RedisStore | KV 状态 | String / Hash | Checkpointer / 工具状态 |

> 📦 **配套代码（本章 codes/，免 API Key 可直接运行）**：
>
> - [redis_chat_history.py](./codes/redis_chat_history.py)：`RedisChatMessageHistory` 会话历史读写 + 多会话隔离演示
> - [redis_vector_rag.py](./codes/redis_vector_rag.py)：`RedisVectorStore` + `FakeEmbeddings` 文档写入 / 相似度检索 / retriever 全流程（生产替换真实 Embedding 模型）
> - [redis_llm_cache.py](./codes/redis_llm_cache.py)：`RedisCache` LLM 缓存，相同 prompt 命中缓存（用 `FakeListLLM` 验证 LLM 调用次数不增加）
>
> 运行：`conda activate langchain_xm && pip install "langchain>=1.4" langchain-redis>=0.2.5 redis`，再分别 `python redis_chat_history.py` 等（需本地 Redis 已启动，默认 `redis://127.0.0.1:6379`，可用 `REDIS_URL` 覆盖）。注意 langchain-redis 0.2.5 的构造参数为 `redis_url` / `redis_client`，且多会话多实例需独立 `index_name`（详见文件内注释）。

## 四、Agent Memory 架构

### 4.1 记忆分层模型

```
┌─────────────────────────────────────────────────────────────┐
│                    Agent Memory 架构                         │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌────────────────┐  ┌────────────────┐  ┌───────────────┐ │
│  │ 短期记忆        │  │ 长期记忆        │  │ 工作记忆       │ │
│  │ (Session)      │  │ (Semantic)     │  │ (Working)     │ │
│  ├────────────────┤  ├────────────────┤  ├───────────────┤ │
│  │ RedisChatMsg   │  │ RedisVector    │  │ RedisStore    │ │
│  │ History        │  │ Store          │  │ (scratchpad)  │ │
│  ├────────────────┤  ├────────────────┤  ├───────────────┤ │
│  │ 当前会话上下文 │  │ 历史语义检索   │  │ 中间状态       │ │
│  │ TTL=24h        │  │ 永久或长 TTL   │  │ TTL=1h        │ │
│  └────────────────┘  └────────────────┘  └───────────────┘ │
│         │                    │                    │          │
│         └────────────────────┼────────────────────┘          │
│                              ▼                               │
│                  [Prompt Context Assembly]                   │
│                              │                               │
│                              ▼                               │
│                      [LLM Decision]                          │
└─────────────────────────────────────────────────────────────┘
```

### 4.2 短期记忆实现

```python
from langchain_redis import RedisChatMessageHistory
from langchain_core.messages import (
    HumanMessage, AIMessage, SystemMessage, trim_messages,
)

class ShortTermMemory:
    def __init__(self, session_id: str, redis_url: str,
                 max_tokens: int = 2000):
        self.history = RedisChatMessageHistory(
            session_id=session_id, redis_url=redis_url, ttl=86400,
        )
        self.max_tokens = max_tokens

    def add_user(self, content: str):
        self.history.add_message(HumanMessage(content=content))

    def add_ai(self, content: str):
        self.history.add_message(AIMessage(content=content))

    def get_context(self) -> list:
        """按 token 预算裁剪历史，保留 SystemMessage"""
        return trim_messages(
            self.history.messages,
            max_tokens=self.max_tokens,
            strategy="last",          # 保留最近消息
            token_counter=len,        # 简化：按字符数
            include_system=True,      # System 不裁剪
            start_on="human",         # 必须以 human 开头
        )

    def summary(self, llm) -> str:
        """长会话摘要：当历史超过预算时压缩"""
        if sum(len(m.content) for m in self.history.messages) <= self.max_tokens:
            return ""
        recent = self.history.messages[-10:]
        prompt = f"将以下对话压缩为 200 字摘要：\n{recent}"
        return llm.invoke(prompt).content
```

### 4.3 长期记忆实现

```python
from langchain_redis import RedisConfig, RedisVectorStore
from langchain_openai import OpenAIEmbeddings
from langchain_core.documents import Document
import hashlib
from datetime import datetime

class LongTermMemory:
    def __init__(self, user_id: str, redis_url: str):
        embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
        config = RedisConfig(
            index_name=f"memory_{user_id}",
            redis_url=redis_url,
            key_prefix="mem",
            metadata_schema=[
                {"name": "timestamp", "type": "numeric"},
                {"name": "topic", "type": "tag"},
            ],
        )
        self.vs = RedisVectorStore(embeddings, config=config)
        self.retriever = self.vs.as_retriever(search_kwargs={"k": 5})

    def remember(self, content: str, topic: str = "general"):
        """持久化记忆"""
        doc = Document(
            page_content=content,
            metadata={
                "timestamp": int(datetime.now().timestamp()),
                "topic": topic,
                "hash": hashlib.md5(content.encode()).hexdigest(),
            },
        )
        self.vs.add_documents([doc])

    def recall(self, query: str, k: int = 5) -> list[str]:
        """语义召回历史记忆"""
        docs = self.vs.similarity_search(query, k=k)
        return [d.page_content for d in docs]

    def recall_with_filter(self, query: str, topic: str, k: int = 5):
        from redisvl.query.filter import Tag
        f = Tag("topic") == [topic]
        return self.vs.similarity_search(query, k=k, filter=f)
```

### 4.4 完整 Memory 实现（摘要 + 向量检索 + 上下文注入）

```python
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
from typing import Any

class AgentMemory:
    """组合短期 + 长期 + 摘要的完整 Memory"""

    def __init__(self, user_id: str, session_id: str,
                 redis_url: str = "redis://localhost:6379"):
        self.user_id = user_id
        self.session_id = session_id
        self.short_term = ShortTermMemory(session_id, redis_url)
        self.long_term = LongTermMemory(user_id, redis_url)
        self.llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)  # 示例模型（按实际可替换其它型号）

    def assemble_context(self, query: str) -> str:
        """组装完整上下文：摘要 + 长期记忆 + 短期记忆"""
        # 1. 长期记忆语义召回
        long_term_hits = self.long_term.recall(query, k=3)
        long_term_block = "\n".join(f"- {m}" for m in long_term_hits)

        # 2. 短期记忆摘要（历史过长时触发）
        summary = self.short_term.summary(self.llm)

        # 3. 短期记忆最近上下文
        recent = self.short_term.get_context()[-6:]

        parts = []
        if long_term_hits:
            parts.append(f"## 用户历史记忆\n{long_term_block}")
        if summary:
            parts.append(f"## 会话摘要\n{summary}")
        if recent:
            recent_text = "\n".join(
                f"{'用户' if isinstance(m, HumanMessage) else '助手'}: {m.content}"
                for m in recent if isinstance(m, (HumanMessage, AIMessage))
            )
            parts.append(f"## 最近对话\n{recent_text}")

        return "\n\n".join(parts)

    def remember_turn(self, user_msg: str, ai_msg: str,
                      important: bool = False):
        """记录一轮对话"""
        self.short_term.add_user(user_msg)
        self.short_term.add_ai(ai_msg)
        if important:
            # 重要内容写入长期记忆
            self.long_term.remember(
                content=f"用户曾问：{user_msg}\n助手答：{ai_msg}",
                topic="conversation",
            )

# 使用
memory = AgentMemory(user_id="u001", session_id="s_2025")
memory.remember_turn("Redis 向量检索用什么算法？", "HNSW 或 FLAT",
                     important=True)
context = memory.assemble_context("Redis 向量检索参数？")
```

## 五、@tool 装饰器开发 Redis 工具

### 5.1 工具开发规范

`@tool` 装饰器配合 Pydantic schema 是 v1.4 推荐的工具开发方式。

```python
from langchain_core.tools import tool, BaseTool
from pydantic import BaseModel, Field
import redis
import json
import time
import uuid

# 全局连接池
_redis_pool = redis.ConnectionPool.from_url(
    "redis://localhost:6379", max_connections=20, decode_responses=True,
)
def get_redis() -> redis.Redis:
    return redis.Redis(connection_pool=_redis_pool)
```

### 5.2 cache_get / cache_set

```python
class CacheSetInput(BaseModel):
    key: str = Field(description="缓存键名")
    value: str = Field(description="缓存值（字符串）")
    ttl: int = Field(default=3600, description="过期时间（秒），0 表示永久")

@tool("cache_set", args_schema=CacheSetInput)
def cache_set(key: str, value: str, ttl: int = 3600) -> str:
    """将键值对写入 Redis 缓存，支持 TTL 过期"""
    r = get_redis()
    if ttl > 0:
        r.setex(key, ttl, value)
    else:
        r.set(key, value)
    return f"OK: {key} (ttl={ttl}s)"

class CacheGetInput(BaseModel):
    key: str = Field(description="缓存键名")

@tool("cache_get", args_schema=CacheGetInput)
def cache_get(key: str) -> str:
    """从 Redis 读取缓存值，不存在返回 NULL"""
    r = get_redis()
    val = r.get(key)
    return val if val is not None else "NULL"
```

### 5.3 queue_publish / queue_consume（基于 Stream）

```python
class QueuePublishInput(BaseModel):
    stream: str = Field(description="Stream 名称")
    fields: dict = Field(description="消息字段 KV")

@tool("queue_publish", args_schema=QueuePublishInput)
def queue_publish(stream: str, fields: dict) -> str:
    """向 Redis Stream 发布消息，返回消息 ID"""
    r = get_redis()
    msg_id = r.xadd(stream, fields, maxlen=10000, approximate=True)
    return f"published: {msg_id}"

class QueueConsumeInput(BaseModel):
    stream: str = Field(description="Stream 名称")
    group: str = Field(default="agent_group", description="消费者组")
    consumer: str = Field(default="agent_1", description="消费者名")
    count: int = Field(default=1, description="拉取消息数")

@tool("queue_consume", args_schema=QueueConsumeInput)
def queue_consume(stream: str, group: str = "agent_group",
                  consumer: str = "agent_1", count: int = 1) -> str:
    """从 Redis Stream 消费消息，自动创建消费者组"""
    r = get_redis()
    try:
        r.xgroup_create(stream, group, id="0", mkstream=True)
    except redis.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise
    msgs = r.xreadgroup(group, consumer, {stream: ">"}, count=count)
    if not msgs:
        return "empty"
    return json.dumps(
        [{"id": mid, "fields": f} for _, lst in msgs for mid, f in lst],
        ensure_ascii=False,
    )
```

### 5.4 lock_acquire / lock_release

```python
class LockAcquireInput(BaseModel):
    resource: str = Field(description="锁定的资源名")
    holder: str = Field(description="持有者标识（如 agent_id）")
    ttl: int = Field(default=30, description="锁 TTL（秒），防止死锁")

@tool("lock_acquire", args_schema=LockAcquireInput)
def lock_acquire(resource: str, holder: str, ttl: int = 30) -> str:
    """获取分布式锁，返回 ACQUIRED 或 FAILED"""
    r = get_redis()
    key = f"lock:{resource}"
    # SET NX EX 原子加锁
    if r.set(key, holder, nx=True, ex=ttl):
        return f"ACQUIRED: {resource}"
    return f"FAILED: {resource} (held by {r.get(key)})"

class LockReleaseInput(BaseModel):
    resource: str = Field(description="资源名")
    holder: str = Field(description="持有者标识")

@tool("lock_release", args_schema=LockReleaseInput)
def lock_release(resource: str, holder: str) -> str:
    """释放分布式锁（Lua 校验持有者，防止误释放）"""
    r = get_redis()
    lua = """
    if redis.call('GET', KEYS[1]) == ARGV[1] then
        return redis.call('DEL', KEYS[1])
    else
        return 0
    end
    """
    res = r.eval(lua, 1, f"lock:{resource}", holder)
    return "RELEASED" if res == 1 else "NOT_OWNER"
```

### 5.5 vector_search

```python
class VectorSearchInput(BaseModel):
    query: str = Field(description="自然语言查询")
    k: int = Field(default=5, description="返回结果数")
    index_name: str = Field(default="agent_kb", description="向量索引名")

@tool("vector_search", args_schema=VectorSearchInput)
def vector_search(query: str, k: int = 5,
                  index_name: str = "agent_kb") -> str:
    """从 Redis 向量库检索知识，返回 Top-K 相关文档"""
    from langchain_redis import RedisConfig, RedisVectorStore
    from langchain_openai import OpenAIEmbeddings
    embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
    config = RedisConfig(index_name=index_name,
                         redis_url="redis://localhost:6379",
                         key_prefix="agent")
    vs = RedisVectorStore(embeddings, config=config)
    docs = vs.similarity_search(query, k=k)
    if not docs:
        return "no results"
    return "\n\n".join(
        f"[{i+1}] {d.page_content[:200]}" for i, d in enumerate(docs)
    )
```

### 5.6 工具集合

```python
REDIS_TOOLS: list[BaseTool] = [
    cache_get, cache_set,
    queue_publish, queue_consume,
    lock_acquire, lock_release,
    vector_search,
]
```

## 六、create_react_agent + Redis 记忆

### 6.1 ReAct Agent 基础

```python
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import HumanMessage

llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)  # 示例模型（按实际可替换其它型号）

# 使用 Redis Checkpointer（生产推荐）
try:
    from langgraph.checkpoint.redis import RedisSaver
    checkpointer = RedisSaver.from_conn_string("redis://localhost:6379")
except ImportError:
    # 兜底：内存版（仅 PoC）
    checkpointer = MemorySaver()

agent = create_react_agent(
    model=llm,
    tools=REDIS_TOOLS,
    checkpointer=checkpointer,
)

# 调用：thread_id 标识会话
config = {"configurable": {"thread_id": "user_001_conv_1"}}
response = agent.invoke(
    {"messages": [HumanMessage(content="把 'hello world' 写入缓存键 greet，TTL 60 秒")]},
    config=config,
)
print(response["messages"][-1].content)

# 后续对话保留上下文
response2 = agent.invoke(
    {"messages": [HumanMessage(content="读出 greet 键的值")]},
    config=config,   # 同一 thread_id 共享上下文
)
print(response2["messages"][-1].content)
```

### 6.2 ReAct 执行流程

```
┌──────────────────────────────────────────────────────────────┐
│                  create_react_agent 循环                      │
├──────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌──────────┐                                                │
│  │  User    │ → HumanMessage                                 │
│  │  Input   │                                                │
│  └──────────┘                                                │
│       │                                                       │
│       ▼                                                       │
│  ┌──────────┐    tool_calls     ┌────────────┐              │
│  │   LLM    │ ─────────────────>│ ToolNode   │              │
│  │ (Reason) │                   │ (Execute)  │              │
│  └──────────┘                   └────────────┘              │
│       ▲                              │                       │
│       │      ToolMessage             │                       │
│       └──────────────────────────────┘                       │
│       │                                                       │
│       ▼                                                       │
│  ┌──────────┐                                                │
│  │   LLM    │ → 最终 AIMessage（无 tool_calls）              │
│  │ (Answer) │                                                │
│  └──────────┘                                                │
│       │                                                       │
│       ▼                                                       │
│  ┌──────────────┐                                            │
│  │ Checkpointer │ → RedisSaver 持久化消息序列              │
│  │  (Redis)     │                                            │
│  └──────────────┘                                            │
└──────────────────────────────────────────────────────────────┘
```

## 七、LangGraph StateGraph 多 Agent 协作

### 7.1 Supervisor 模式架构

```
                 ┌──────────────────┐
                 │   Coordinator    │
                 │   (Supervisor)   │
                 └────────┬─────────┘
                          │ route
        ┌─────────────────┼─────────────────┐
        ▼                 ▼                 ▼
┌───────────────┐  ┌───────────────┐  ┌───────────────┐
│ cache_agent   │  │ search_agent  │  │ summary_agent │
│ (cache tools) │  │ (vector tools)│  │ (LLM only)    │
└───────────────┘  └───────────────┘  └───────────────┘
        │                 │                 │
        └─────────────────┼─────────────────┘
                          ▼
                 ┌──────────────────┐
                 │  Coordinator     │
                 │  (汇总 + 终答)   │
                 └──────────────────┘
```

### 7.2 状态定义

```python
from typing import Annotated, Literal
from typing_extensions import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.types import Command
from langchain_core.messages import HumanMessage, AIMessage

class AgentState(TypedDict):
    messages: Annotated[list, "对话历史"]
    next: str          # 路由目标
    cache_result: str  # cache_agent 输出
    search_result: str # search_agent 输出
    final: str         # 最终答案
```

### 7.3 子 Agent 构建

```python
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)  # 示例模型（按实际可替换其它型号）

# cache_agent：仅 cache 工具
cache_agent = create_react_agent(
    model=llm,
    tools=[cache_get, cache_set],
    prompt="你是缓存专家，只处理读写缓存的任务。",
)

# search_agent：仅 vector_search
search_agent = create_react_agent(
    model=llm,
    tools=[vector_search],
    prompt="你是检索专家，从知识库召回相关文档。",
)
```

### 7.4 Supervisor 节点

```python
from pydantic import BaseModel, Field
from typing import Literal

class RouteDecision(BaseModel):
    next: Literal["cache_agent", "search_agent", "FINISH"] = Field(
        description="下一个执行节点"
    )

def coordinator(state: AgentState) -> Command:
    """Supervisor：决定路由"""
    last_msg = state["messages"][-1].content
    structured_llm = llm.with_structured_output(RouteDecision)
    decision = structured_llm.invoke(f"""
    根据用户问题决定路由：
    - 涉及缓存读写 → cache_agent
    - 涉及知识查询 → search_agent
    - 已有足够信息可总结 → FINISH

    用户问题：{last_msg}
    """)
    if decision.next == "FINISH":
        return Command(goto=END, update={"final": last_msg})
    return Command(goto=decision.next)

def cache_node(state: AgentState) -> Command:
    res = cache_agent.invoke({"messages": state["messages"]})
    return Command(goto="coordinator", update={
        "cache_result": res["messages"][-1].content,
        "messages": res["messages"],
    })

def search_node(state: AgentState) -> Command:
    res = search_agent.invoke({"messages": state["messages"]})
    return Command(goto="coordinator", update={
        "search_result": res["messages"][-1].content,
        "messages": res["messages"],
    })
```

### 7.5 组装图

```python
builder = StateGraph(AgentState)
builder.add_node("coordinator", coordinator)
builder.add_node("cache_agent", cache_node)
builder.add_node("search_agent", search_node)

builder.add_edge(START, "coordinator")
# 子节点回 coordinator 后由 supervisor 再决策

from langgraph.checkpoint.redis import RedisSaver
checkpointer = RedisSaver.from_conn_string("redis://localhost:6379")
graph = builder.compile(checkpointer=checkpointer)

# 运行
config = {"configurable": {"thread_id": "multi_agent_1"}}
result = graph.invoke(
    {"messages": [HumanMessage(content="把 'redis-ok' 写入键 status，并查询 HNSW 文档")]},
    config=config,
)
print(result.get("final"))
```

## 八、HITL（Human-in-the-Loop）工具审批

### 8.1 审批流程

```
Agent ──> tool_call (写操作) ──> [PENDING]
                                  │
                                  ▼
                          ┌────────────────┐
                          │  Redis 审批队列 │
                          │  approval:<id> │
                          └────────┬───────┘
                                   │
                          ┌────────▼────────┐
                          │  Human Review   │
                          │  (approve/reject)│
                          └────────┬────────┘
                                   │
                          ┌────────▼────────┐
                          │  Agent Resume   │
                          │  (continue)     │
                          └─────────────────┘
```

### 8.2 审批状态存 Redis

```python
import json
from langgraph.types import interrupt
from langchain_core.tools import tool

# 写操作工具白名单：需人工审批
WRITE_TOOLS_NEEDING_APPROVAL = {"cache_set", "queue_publish", "lock_acquire"}

def request_approval(tool_name: str, tool_args: dict) -> bool:
    """通过 Redis 队列请求审批，阻塞等待结果"""
    r = get_redis()
    approval_id = f"approval:{uuid.uuid4().hex}"
    r.hset(approval_id, mapping={
        "tool": tool_name,
        "args": json.dumps(tool_args, ensure_ascii=False),
        "status": "PENDING",
        "created_at": str(time.time()),
    })
    r.lpush("approval_queue", approval_id)
    # 阻塞等待结果（实际生产用 BRPOP 或异步轮询）
    print(f"[HITL] 等待审批 {approval_id} ...")
    # 在 LangGraph 中用 interrupt 暂停
    approved = interrupt({"approval_id": approval_id, "tool": tool_name})
    return approved

# 包装写工具
_original_cache_set = cache_set
def cache_set_with_approval(key: str, value: str, ttl: int = 3600) -> str:
    approved = request_approval("cache_set", {"key": key, "value": value, "ttl": ttl})
    if not approved:
        return "REJECTED by human"
    return _original_cache_set.invoke({"key": key, "value": value, "ttl": ttl})

cache_set_with_approval_tool = tool("cache_set")(cache_set_with_approval)
```

### 8.3 审批 Webhook（生产实现）

```python
# 外部审批服务（FastAPI）调用此函数回写审批结果
def submit_approval(approval_id: str, approved: bool, reviewer: str):
    r = get_redis()
    r.hset(approval_id, mapping={
        "status": "APPROVED" if approved else "REJECTED",
        "reviewer": reviewer,
        "reviewed_at": str(time.time()),
    })
    # 通过 LangGraph 的 Command 恢复执行
    return {"approval_id": approval_id, "approved": approved}

# 恢复执行（在 graph 调用方）
def resume_after_approval(thread_id: str, approved: bool):
    for chunk in graph.stream(
        Command(resume=approved),
        config={"configurable": {"thread_id": thread_id}},
    ):
        print(chunk)
```

## 九、完整 RAG + Agent 示例

### 9.1 整合：Redis 向量检索 + 工具调用 + 对话历史

```python
import os
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_redis import RedisChatMessageHistory, RedisConfig, RedisVectorStore
from langchain_core.documents import Document
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.redis import RedisSaver

# 从环境变量读 Key（勿硬编码）：export OPENAI_API_KEY=sk-...（或 .env + python-dotenv 加载）
assert os.environ.get("OPENAI_API_KEY"), "请先配置 OPENAI_API_KEY（见 .env.example）"

REDIS_URL = "redis://localhost:6379"

# 1. 准备向量库（首次运行写入示例数据）
def init_kb():
    embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
    config = RedisConfig(index_name="rag_agent_kb", redis_url=REDIS_URL,
                         key_prefix="rag_agent")
    vs = RedisVectorStore(embeddings, config=config)
    if vs.similarity_search("test", k=1):
        return vs
    docs = [
        Document(page_content="Redis HNSW 参数：M/EF_CONSTRUCTION/EF_RUNTIME",
                 metadata={"cat": "redis"}),
        Document(page_content="LangGraph 用 StateGraph 编排多 Agent",
                 metadata={"cat": "langgraph"}),
    ]
    vs.add_documents(docs)
    return vs

# 2. 工具：检索知识库
@tool
def kb_search(query: str) -> str:
    """从企业知识库检索相关内容"""
    vs = init_kb()
    docs = vs.similarity_search(query, k=3)
    return "\n\n".join(d.page_content for d in docs) or "无相关结果"

# 3. 工具：写入会话笔记（带审批的写操作）
@tool
def save_note(content: str) -> str:
    """把重要内容保存到会话笔记"""
    r = get_redis()
    r.rpush("agent_notes", content)
    return f"saved: {content[:50]}..."

# 4. 构建 Agent
llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)  # 示例模型（按实际可替换其它型号）
checkpointer = RedisSaver.from_conn_string(REDIS_URL)

agent = create_react_agent(
    model=llm,
    tools=[kb_search, save_note, cache_get, cache_set],
    checkpointer=checkpointer,
    prompt="""你是 Redis & LangGraph 技术助手。
可用工具：
- kb_search：检索知识库
- save_note：保存重要笔记
- cache_get/cache_set：读写缓存

回答流程：
1. 若问题涉及技术细节，先调用 kb_search
2. 关键结论用 save_note 保存
3. 用户偏好可缓存到 Redis
""",
)

# 5. 多轮对话
def chat(thread_id: str):
    config = {"configurable": {"thread_id": thread_id}}
    print("Agent 就绪（输入 exit 退出）")
    while True:
        q = input("\n> ").strip()
        if q.lower() in {"exit", "quit"}:
            break
        res = agent.invoke(
            {"messages": [HumanMessage(content=q)]}, config=config,
        )
        print(res["messages"][-1].content)

if __name__ == "__main__":
    init_kb()
    chat("user_001_session_1")
```

## 十、生产化配置

### 10.1 异步与流式

```python
import asyncio
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

async def stream_agent(agent, query: str, thread_id: str):
    """异步流式输出 Agent 响应"""
    config = {"configurable": {"thread_id": thread_id}}
    async for event in agent.astream_events(
        {"messages": [HumanMessage(content=query)]},
        config=config,
        version="v2",
    ):
        kind = event["event"]
        if kind == "on_chat_model_stream":
            print(event["data"]["chunk"].content, end="", flush=True)
        elif kind == "on_tool_start":
            print(f"\n[tool] {event['name']} ...")
        elif kind == "on_tool_end":
            print(f"[tool] {event['name']} -> {event['data'].get('output', '')[:80]}")

asyncio.run(stream_agent(agent, "解释 HNSW", "s1"))
```

### 10.2 重试与超时

```python
from langchain_core.runnables import RunnableConfig
from tenacity import retry, stop_after_attempt, wait_exponential

# LLM 级别：OpenAI SDK 内置 retry
llm = ChatOpenAI(
    model="gpt-4o-mini",
    timeout=30,                # 单次请求 30s 超时
    max_retries=3,             # 自动重试 3 次
)

# 工具级别：tenacity
@retry(stop=stop_after_attempt(3),
       wait=wait_exponential(multiplier=1, min=1, max=10))
def redis_op_with_retry(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except redis.ConnectionError:
        raise   # 触发重试

# 调用级超时
config = RunnableConfig(
    timeout=60,                # 整个 invoke 链 60s 超时
    max_concurrency=4,
    recursion_limit=25,        # LangGraph 递归上限
)
res = agent.invoke({"messages": [HumanMessage("hello")]}, config=config)
```

### 10.3 LangSmith 监控

```python
import os

os.environ["LANGSMITH_TRACING"] = "true"
# 生产从环境变量/密钥管理读取，勿硬编码（setdefault：环境已有则优先）
os.environ.setdefault("LANGSMITH_API_KEY", "ls__your_key_here")
os.environ["LANGSMITH_PROJECT"] = "redis-agent-prod"
os.environ["LANGSMITH_ENDPOINT"] = "https://api.smith.langchain.com"

# 启用 tracing 后，所有 invoke / astream 自动上报
# 在 LangSmith UI 可看到：
#   - 每次 LLM 调用（token / 延迟 / 费用）
#   - 工具调用链
#   - 完整消息历史
#   - 错误堆栈

# 自定义 metadata 便于追踪
config = {
    "configurable": {"thread_id": "u_001"},
    "metadata": {
        "user_id": "u_001",
        "env": "prod",
        "version": "1.0.3",
    },
    "tags": ["redis-agent", "rag"],
}
res = agent.invoke({"messages": [HumanMessage("hi")]}, config=config)
```

### 10.4 异常处理清单

```python
from langgraph.errors import GraphRecursionError
import redis

def safe_invoke(agent, query: str, thread_id: str):
    try:
        return agent.invoke(
            {"messages": [HumanMessage(content=query)]},
            config={"configurable": {"thread_id": thread_id},
                    "recursion_limit": 25},
        )
    except GraphRecursionError:
        return {"error": "Agent 思考次数超限，请简化问题"}
    except redis.ConnectionError:
        return {"error": "Redis 不可用，请稍后重试"}
    except redis.TimeoutError:
        return {"error": "Redis 超时"}
    except Exception as e:
        return {"error": f"未知错误: {type(e).__name__}: {e}"}
```

## 十一、LangChain Redis vs PostgreSQL Memory

| 维度 | LangChain Redis | PostgreSQL（含 pgvector） |
|------|----------------|--------------------------|
| 短期记忆（ChatHistory） | List 原生、TTL 自动 | 需表 + 定时清理 |
| 长期记忆（向量） | HNSW 内存级 | pgvector HNSW（磁盘） |
| 检索延迟 | 1~5ms | 5~50ms |
| 持久化 | RDB + AOF | WAL（强一致） |
| Checkpointer | RedisSaver（高频更新友好） | PostgresSaver（事务一致） |
| TTL 管理 | 原生 EX 命令 | 需定时任务 |
| 多会话隔离 | key prefix | schema / row 级 |
| 复杂查询 | RediSearch 有限 | SQL 全功能 |
| 事务支持 | 弱（仅 MULTI/EXEC） | 强（ACID） |
| 运维成本 | 低（单容器） | 中（独立 DB） |
| 适用场景 | 高频短会话、实时 Agent | 强一致、复杂查询、长期归档 |

**选型建议：**

- 实时聊天 / 高频工具调用 / 短期记忆 → Redis
- 长期归档 / 复杂报表 / 强一致 → PostgreSQL
- 生产混合架构：Redis 做热数据 + PG 做冷归档

## 十二、常见问题

| 现象 | 原因 | 解决 |
|------|------|------|
| `ImportError: langgraph.checkpoint.redis` | 未装包 | `pip install langgraph-checkpoint-redis` |
| `create_react_agent` 报 recursion | Agent 陷入死循环 | 调小 `recursion_limit`、优化 prompt |
| RedisSaver 写入失败 | 版本不匹配 | 确认 langgraph-checkpoint-redis >= 0.1 |
| 工具 schema 解析失败 | Pydantic v1/v2 混用 | 强制 v2：`pip install "pydantic>=2"` |
| 多 Agent 状态丢失 | thread_id 不一致 | 严格固定 thread_id |
| HITL 卡死 | interrupt 未 resume | 用 `Command(resume=...)` 恢复 |

## 📖 深入阅读

- [LangChain 官方文档](https://python.langchain.com/docs/)
- [LangGraph 文档](https://langchain-ai.github.io/langgraph/)
- [langchain-redis 集成](https://python.langchain.com/docs/integrations/providers/redis/)
- [LangGraph Checkpointer Redis](https://langchain-ai.github.io/langgraph/reference/checkpoints/#redis)
- [LangSmith 监控](https://docs.smith.langchain.com/)
- [ReAct 论文](https://arxiv.org/abs/2210.03629)

## 本章小结

- LangChain v1.4 强制新导入结构：`langchain_core.*` / `langchain_redis.*` / `langgraph.prebuilt.*`，弃用 `langchain.chains` / `langchain.agents`
- LCEL 用 `|` 管道组合 Runnable，统一 invoke / stream / ainvoke 接口
- langchain_redis 三件套：RedisChatMessageHistory（短期）/ RedisVectorStore（长期）/ RedisStore（KV 状态）
- Agent Memory 分层：短期 + 长期 + 工作记忆，通过上下文组装注入 Prompt
- `@tool` + Pydantic 是 v1.4 工具开发推荐方式，可封装 cache / queue / lock / vector_search
- `create_react_agent` + RedisSaver 构成生产级 ReAct Agent，thread_id 标识会话
- LangGraph StateGraph 实现 Supervisor 多 Agent 协作，子 Agent 通过 Command 路由
- HITL 用 interrupt 暂停 + Command(resume=) 恢复，审批状态存 Redis Hash
- 生产化四件套：异步 astream / tenacity 重试 / recursion_limit / LangSmith 追踪

## 下一步导航

- 探索 MCP 协议与 Redis 工具集成 → [18 Redis MCP Server 与工具集成](../18_Redis_MCP_Server与工具集成/18_Redis_MCP_Server与工具集成.md)
- 回顾向量存储基础 → [16 Redis 向量存储与 RAG 应用](../16_Redis向量存储与RAG应用/16_Redis向量存储与RAG应用.md)
- 理解 Redis Stream 用于队列 → [08 发布订阅与消息队列](../08_发布订阅与消息队列/08_发布订阅与消息队列.md)
- 复习分布式锁实现 → [10 分布式锁与限流](../10_分布式锁与限流/10_分布式锁与限流.md)
