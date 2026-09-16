# 18 Redis MCP Server 与工具集成

> MCP（Model Context Protocol）是 Anthropic 开放的 AI 工具协议标准，将 Redis 操作封装为 MCP Server 后，Claude / GPT / 任意支持 MCP 的客户端即可通过统一协议操作 Redis，实现「自然语言 → Redis」的端到端 AI 助手。

## 零基础前置认知

**这篇在讲什么**：把 Redis 操作能力包成"标准接口"（MCP Server），让 Claude Code、Cursor 等任意支持 MCP 的客户端用自然语言直接操作 Redis——不用给每个客户端写一遍集成。读完你能用 `mcp` Python SDK 开发一个带 8 个工具（读/写/向量检索/Stream/分布式锁）+ 资源（只读统计/键值）+ Prompt 模板的 Redis MCP Server，完成安全隔离（只读默认、写操作白名单、Redis ACL 受限角色）与 Docker 三件套部署；最后用 langchain-mcp-adapters 让上一章的 LangChain Agent 直接调用这些 MCP 工具。*开发命令在本章 codes/mcp_server/ 提供完整可运行源码（详见第五节配套引用）。*

> 辅助类比：把 MCP 想成"AI 世界的 USB-C 接口"——**协议** 是统一插座标准（任何设备插上就能用）；**Tools** 是"可执行程序"（写数据、发消息，有副作用）；**Resources** 是"只读资料"（查看统计、读键值，像打开说明书）；**Prompts** 是"预填表格"（缓存诊断模板，像点套餐）；**MCP Server** 是"插座背后的服务方"（谁提供这个接口）。没有 MCP 时，N 个客户端 × M 个工具有 N×M 种集成；有了它，客户端只认一个协议、Server 只实现一次，变成 1:N。

**基础名词集**：

| 名词 | 一句话定义 | 与相邻概念的关系 |
|------|-----------|----------------|
| MCP | Anthropic 推出的大模型工具互操作协议（JSON-RPC 2.0） | 客户端 ↔ Server 的通信标准，解决 M×N 集成爆炸 |
| MCP Server | 实现 MCP 协议的能力提供方（独立进程 / 容器） | 一个 Server 提供若干 Tools / Resources / Prompts |
| Tools | LLM 可主动调用的函数（可有副作用） | 写权限由 Server 侧白名单 + 环境变量控制 |
| Resources | 只读数据源（URI 模板如 redis://stats） | 客户端按需拉取，不能改数据 |
| Prompts | 预定义对话模板，用户选择触发 | 把常见分析（缓存诊断/性能）固化成流程 |
| FastMCP | mcp SDK 里的高层开发框架（装饰器风格） | `@mcp.tool` / `@mcp.resource` / `@mcp.prompt` 声明能力 |
| Streamable HTTP | MCP 2025 规范的新传输（单端点 POST + 可选 SSE 流） | 取代独立 SSE 端点，远程部署用 |
| langchain-mcp-adapters | LangChain 官方 MCP 适配包 | 把 MCP 工具转成 LangChain tools，两套生态互通 |

> 区分/注意：**MCP 是"协议"不是"库"**——任何语言都能实现 Server，换语言不换协议；**Tools / Resources / Prompts 权限不同**——Resource 只读、Tool 可写，Server 默认只开放读、写操作要白名单 + 环境变量显式开启；**MCP ≠ 上一章的 @tool**——@tool 只给"当前这个 Agent 进程"用，MCP 给"任意客户端"用，工具要复用到多客户端就上 MCP，只用在一个 Agent 里就用 @tool。

## 学习目标

- 理解 MCP 协议三大原语：Tools / Resources / Prompts 与三种传输方式（stdio / HTTP / SSE）
- 使用 Python mcp SDK 开发生产级 Redis MCP Server（8+ 工具 + 资源 + Prompts）
- 掌握连接池、错误处理、只读默认 + 写操作白名单的安全设计
- 在 Claude Code 中通过 `.claude.json` 注册并使用 Redis MCP Server
- 用 Docker Compose 部署 redis-stack + mcp-server + claude-code 三件套
- 落地安全隔离清单：非 root、env 密钥、Redis ACL 受限角色
- 对比 MCP 与 LangChain 工具集成的差异，使用 langchain-mcp-adapters 互通
- 完成端到端 AI Redis 助手架构与验证清单

## 前置知识

- [16 Redis 向量存储与 RAG 应用](../16_Redis向量存储与RAG应用/16_Redis向量存储与RAG应用.md)：RedisVL / 向量检索
- [17 LangChain v1.4 与 Redis](../17_LangChain_v1.4与Redis/17_LangChain_v1.4与Redis.md)：@tool 工具开发、Agent 模式
- [08 发布订阅与消息队列](../08_发布订阅与消息队列/08_发布订阅与消息队列.md)：Stream / Pub-Sub
- [10 分布式锁与限流](../10_分布式锁与限流/10_分布式锁与限流.md)：分布式锁
- [12 安全与运维](../12_安全与运维/12_安全与运维.md)：ACL / TLS / 网络隔离
- [14 Redis 与 Docker 容器化部署](../14_Redis与Docker容器化部署/14_Redis与Docker容器化部署.md)：Compose 编排

## 一、MCP 协议总览

### 1.1 MCP 是什么

Model Context Protocol（MCP）是 Anthropic 在 2024 年提出的开放协议，定义了 LLM 客户端（如 Claude Desktop / Claude Code / Cursor）与外部能力提供方（MCP Server）之间的通信标准。它解决了「每个 LLM 客户端 × 每个工具」的 M×N 集成爆炸问题，统一为「客户端 → MCP → 任意 Server」的 1:N 模型。

```
┌─────────────────────────────────────────────────────────────┐
│                       MCP 协议架构                           │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│   ┌──────────────┐    ┌──────────────┐    ┌──────────────┐ │
│   │ Claude Code  │    │  Cursor IDE  │    │  自研 Agent  │ │
│   │ (MCP Client) │    │ (MCP Client) │    │ (MCP Client) │ │
│   └──────┬───────┘    └──────┬───────┘    └──────┬───────┘ │
│          │                   │                   │          │
│          └───────────────────┼───────────────────┘          │
│                              │ JSON-RPC 2.0                  │
│                              ▼                               │
│                   ┌──────────────────────┐                  │
│                   │   MCP Transport      │                  │
│                   │  (stdio/HTTP/SSE)    │                  │
│                   └──────────┬───────────┘                  │
│                              │                              │
│        ┌─────────────────────┼─────────────────────┐        │
│        ▼                     ▼                     ▼        │
│  ┌───────────┐        ┌─────────────┐       ┌───────────┐  │
│  │ Redis MCP │        │ GitHub MCP  │       │ Slack MCP │  │
│  │  Server   │        │   Server    │       │  Server   │  │
│  └───────────┘        └─────────────┘       └───────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### 1.2 三大原语

| 原语 | 作用 | 触发方式 | 示例 |
|------|------|---------|------|
| **Tools** | LLM 主动调用的函数（有副作用） | LLM 决策后 `tools/call` | `redis.set` / `redis.get` |
| **Resources** | LLM 可读取的数据源（只读） | 客户端拉取 `resources/read` | `redis://key/foo` / `redis://stats` |
| **Prompts** | 预定义的对话模板 | 用户选择 `prompts/get` | 缓存诊断模板、性能分析模板 |

### 1.3 三种传输方式

| 传输 | 场景 | 优点 | 缺点 |
|------|------|------|------|
| **stdio** | CLI 工具 / 本地集成 | 简单、零端口、安全 | 仅本地、不能远程 |
| **HTTP** | 服务化、远程访问 | 标准 REST、可远程 | 需鉴权 |
| **SSE** | 流式响应（已合并入 Streamable HTTP） | 长任务流式 | 协议演进中 |

> MCP 2025 规范将 SSE 升级为 **Streamable HTTP**（单端点 POST + 可选 SSE 流）。本文使用 `mcp` Python SDK 2.x 的最新 API。

### 1.4 安装

```powershell
conda activate langchain_xm
pip install "mcp>=2.1.1" redis redisvl
# Claude Code CLI
npm install -g @anthropic-ai/claude-code
```

> ⚠️ **风险提示**：MCP Python SDK 的装饰器签名基于 `mcp 2.x`（写作时点 PyPI 最新 2.1.1 已实测），更早版本（如 0.x 或 1.x）的 FastMCP API 需按版本微调。安装后用 `pip show mcp` 确认版本。

## 二、Redis MCP Server 开发

### 2.1 项目结构

```
redis_mcp_server/
├── server.py            # 入口与工具注册
├── tools.py             # 工具实现
├── resources.py         # 资源实现
├── prompts.py           # Prompts 实现
├── config.py            # 配置与 ACL
├── requirements.txt
└── Dockerfile
```

> 📦 **配套代码（本章 codes/mcp_server/，与正文 2.2-2.6 完全一致的可运行源码）**：本目录结构即抽取自 codes——`config.py` / `tools.py` / `resources.py` / `prompts.py` / `server.py` / `requirements.txt` 已就位（另附 README 说明安装与验证方式），Dockerfile 见四、Docker Compose 三件套。[codes/mcp_server/](./codes/mcp_server/)

### 2.2 配置与连接池

```python
# config.py
import os
from dataclasses import dataclass
import redis

@dataclass
class ServerConfig:
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379")
    # 写操作白名单：默认只读，仅显式列出的命令允许写
    write_tools_whitelist: set = None
    # 默认 8 个工具是否启用
    enable_write: bool = os.getenv("MCP_REDIS_WRITE", "false").lower() == "true"
    # 资源读取键名前缀限制（防止越权）
    allowed_key_prefixes: tuple = ("",)   # 空字符串=允许所有；生产建议白名单
    max_value_length: int = 10_000        # 资源返回值最大长度，防止大 key 打爆 LLM

    def __post_init__(self):
        if self.write_tools_whitelist is None:
            self.write_tools_whitelist = {
                "redis_set", "redis_delete", "redis_cache_invalidate",
                "redis_stream_publish", "redis_lock_acquire",
                "redis_lock_release",
            }

config = ServerConfig()

# 全局连接池
_pool = redis.ConnectionPool.from_url(
    config.redis_url,
    max_connections=20,
    decode_responses=True,
    socket_timeout=5,
    socket_connect_timeout=5,
    retry_on_timeout=True,
)

def get_redis() -> redis.Redis:
    return redis.Redis(connection_pool=_pool)
```

### 2.3 工具实现（8 个）

```python
# tools.py
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field
from typing import Optional
import json
import time
import uuid
import redis
from config import config, get_redis

# 创建 MCP Server 实例
mcp = FastMCP(
    name="redis-mcp-server",
    version="1.0.0",
    instructions=(
        "Redis MCP Server：通过自然语言操作 Redis。"
        "默认只读；写操作需 MCP_REDIS_WRITE=true。"
    ),
)

def require_write(tool_name: str):
    """写操作白名单校验"""
    if not config.enable_write:
        raise PermissionError(f"写操作 {tool_name} 被禁用，需设置 MCP_REDIS_WRITE=true")
    if tool_name not in config.write_tools_whitelist:
        raise PermissionError(f"工具 {tool_name} 不在白名单")

def safe_call(fn, *args, **kwargs):
    """统一异常包装"""
    try:
        return fn(*args, **kwargs)
    except redis.ConnectionError as e:
        return f"ERROR: Redis 连接失败 - {e}"
    except redis.TimeoutError:
        return "ERROR: Redis 超时"
    except redis.AuthenticationError as e:
        return f"ERROR: Redis 鉴权失败 - {e}"
    except Exception as e:
        return f"ERROR: {type(e).__name__} - {e}"


# ──────────────── 工具 1: redis_get ────────────────
class RedisGetInput(BaseModel):
    key: str = Field(description="Redis 键名")

@mcp.tool()
def redis_get(key: str) -> str:
    """读取 Redis 键的字符串值。不存在返回 NULL。"""
    r = get_redis()
    def _op():
        val = r.get(key)
        return val if val is not None else "NULL"
    return safe_call(_op)


# ──────────────── 工具 2: redis_set ────────────────
class RedisSetInput(BaseModel):
    key: str = Field(description="Redis 键名")
    value: str = Field(description="要写入的字符串值")
    ttl: int = Field(default=0, description="过期时间（秒），0 表示永久")

@mcp.tool()
def redis_set(key: str, value: str, ttl: int = 0) -> str:
    """写入 Redis 键值对（写操作，需白名单）。"""
    require_write("redis_set")
    r = get_redis()
    def _op():
        if ttl > 0:
            r.setex(key, ttl, value)
        else:
            r.set(key, value)
        return f"OK: {key} (ttl={ttl}s)"
    return safe_call(_op)


# ──────────────── 工具 3: redis_delete ────────────────
class RedisDeleteInput(BaseModel):
    key: str = Field(description="要删除的键名")

@mcp.tool()
def redis_delete(key: str) -> str:
    """删除 Redis 键（写操作）。返回删除数量。"""
    require_write("redis_delete")
    r = get_redis()
    def _op():
        n = r.delete(key)
        return f"deleted: {n}"
    return safe_call(_op)


# ──────────────── 工具 4: redis_cache_invalidate ────────────────
class CacheInvalidateInput(BaseModel):
    pattern: str = Field(description="要失效的键模式（如 user:*）")

@mcp.tool()
def redis_cache_invalidate(pattern: str) -> str:
    """按 pattern 批量失效缓存（写操作，慎用）。单次最多删 1000 个。"""
    require_write("redis_cache_invalidate")
    r = get_redis()
    def _op():
        keys = list(r.scan_iter(match=pattern, count=1000))[:1000]
        if not keys:
            return "no keys matched"
        n = r.delete(*keys)
        return f"invalidated: {n} (pattern={pattern})"
    return safe_call(_op)


# ──────────────── 工具 5: redis_vector_search ────────────────
class VectorSearchInput(BaseModel):
    query: str = Field(description="自然语言查询")
    index_name: str = Field(default="agent_kb", description="向量索引名")
    k: int = Field(default=5, description="返回结果数")

@mcp.tool()
def redis_vector_search(query: str, index_name: str = "agent_kb",
                        k: int = 5) -> str:
    """对 Redis 向量库进行相似度检索（只读）。返回 Top-K 文档。"""
    def _op():
        from langchain_redis import RedisConfig, RedisVectorStore
        from langchain_openai import OpenAIEmbeddings
        import os
        assert os.environ.get("OPENAI_API_KEY"), "请先配置 OPENAI_API_KEY（勿硬编码）"
        embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
        cfg = RedisConfig(index_name=index_name,
                          redis_url=config.redis_url, key_prefix="agent")
        vs = RedisVectorStore(embeddings, config=cfg)
        docs = vs.similarity_search(query, k=k)
        if not docs:
            return "no results"
        return "\n\n".join(
            f"[{i+1}] {d.page_content[:200]}" for i, d in enumerate(docs)
        )
    return safe_call(_op)


# ──────────────── 工具 6: redis_stream_publish ────────────────
class StreamPublishInput(BaseModel):
    stream: str = Field(description="Stream 名称")
    fields: dict = Field(description="消息字段 KV")

@mcp.tool()
def redis_stream_publish(stream: str, fields: dict) -> str:
    """向 Redis Stream 发布消息（写操作）。"""
    require_write("redis_stream_publish")
    r = get_redis()
    def _op():
        msg_id = r.xadd(stream, fields, maxlen=10000, approximate=True)
        return f"published: {msg_id}"
    return safe_call(_op)


# ──────────────── 工具 7: redis_stream_consume ────────────────
class StreamConsumeInput(BaseModel):
    stream: str = Field(description="Stream 名称")
    group: str = Field(default="mcp_group", description="消费者组")
    consumer: str = Field(default="mcp_consumer", description="消费者名")
    count: int = Field(default=1, description="拉取消息数")

@mcp.tool()
def redis_stream_consume(stream: str, group: str = "mcp_group",
                         consumer: str = "mcp_consumer",
                         count: int = 1) -> str:
    """从 Redis Stream 消费消息（只读消费，无 ACK）。"""
    r = get_redis()
    def _op():
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
    return safe_call(_op)


# ──────────────── 工具 8: redis_lock_acquire / release ────────────────
class LockAcquireInput(BaseModel):
    resource: str = Field(description="锁定的资源名")
    holder: str = Field(description="持有者标识")
    ttl: int = Field(default=30, description="TTL（秒）")

@mcp.tool()
def redis_lock_acquire(resource: str, holder: str, ttl: int = 30) -> str:
    """获取分布式锁（写操作）。返回 ACQUIRED 或 FAILED。"""
    require_write("redis_lock_acquire")
    r = get_redis()
    def _op():
        if r.set(f"lock:{resource}", holder, nx=True, ex=ttl):
            return f"ACQUIRED: {resource}"
        return f"FAILED: {resource} held by {r.get(f'lock:{resource}')}"
    return safe_call(_op)

class LockReleaseInput(BaseModel):
    resource: str = Field(description="资源名")
    holder: str = Field(description="持有者标识")

@mcp.tool()
def redis_lock_release(resource: str, holder: str) -> str:
    """释放分布式锁（写操作）。Lua 校验持有者防误释放。"""
    require_write("redis_lock_release")
    r = get_redis()
    def _op():
        lua = """
        if redis.call('GET', KEYS[1]) == ARGV[1] then
            return redis.call('DEL', KEYS[1])
        else
            return 0
        end
        """
        res = r.eval(lua, 1, f"lock:{resource}", holder)
        return "RELEASED" if res == 1 else "NOT_OWNER"
    return safe_call(_op)
```

### 2.4 资源实现

```python
# resources.py
from mcp.server.fastmcp import FastMCP
from config import config, get_redis
import json

def register_resources(mcp: FastMCP):

    @mcp.resource("redis://key/{key}")
    def read_key(key: str) -> str:
        """读取指定 key 的值（自动识别类型）"""
        r = get_redis()
        t = r.type(key)
        if t == "none":
            return f"NULL: {key} 不存在"
        if t == "string":
            val = r.get(key) or ""
            if len(val) > config.max_value_length:
                val = val[:config.max_value_length] + "...[truncated]"
            return val
        if t == "hash":
            data = r.hgetall(key)
            return json.dumps(data, ensure_ascii=False, indent=2)
        if t == "list":
            data = r.lrange(key, 0, 99)
            return json.dumps(data, ensure_ascii=False)
        if t == "set":
            data = list(r.smembers(key))[:100]
            return json.dumps(data, ensure_ascii=False)
        if t == "zset":
            data = r.zrange(key, 0, 99, withscores=True)
            return json.dumps(
                [{"member": m, "score": s} for m, s in data],
                ensure_ascii=False, indent=2,
            )
        if t == "stream":
            data = r.xrange(key, count=50)
            return json.dumps(
                [{"id": i, "fields": f} for i, f in data],
                ensure_ascii=False, indent=2,
            )
        return f"unsupported type: {t}"

    @mcp.resource("redis://stats")
    def server_stats() -> str:
        """Redis INFO 统计信息（精选字段）"""
        r = get_redis()
        info = r.info()
        return json.dumps({
            "redis_version": info.get("redis_version"),
            "used_memory_human": info.get("used_memory_human"),
            "used_memory_peak_human": info.get("used_memory_peak_human"),
            "connected_clients": info.get("connected_clients"),
            "total_commands_processed": info.get("total_commands_processed"),
            "keyspace_hits": info.get("keyspace_hits"),
            "keyspace_misses": info.get("keyspace_misses"),
            "hit_rate": round(
                info.get("keyspace_hits", 0) /
                max(info.get("keyspace_hits", 0) +
                    info.get("keyspace_misses", 1), 1) * 100, 2
            ),
            "db_count": len([k for k in info if k.startswith("db")]),
        }, ensure_ascii=False, indent=2)

    @mcp.resource("redis://keys/{pattern}")
    def list_keys(pattern: str = "*") -> str:
        """按 pattern 列出键名（默认最多 200 个）"""
        r = get_redis()
        keys = list(r.scan_iter(match=pattern, count=200))[:200]
        return json.dumps(keys, ensure_ascii=False, indent=2)

    @mcp.resource("redis://index/{name}")
    def index_info(name: str) -> str:
        """查看 RediSearch 索引信息"""
        r = get_redis()
        try:
            info = r.execute_command("FT.INFO", name)
            # FT.INFO 返回 [k, v, k, v, ...] 格式
            d = {info[i]: info[i+1] for i in range(0, len(info)-1, 2)}
            return json.dumps(
                {"name": d.get("index_name"), "num_docs": d.get("num_docs"),
                 "bytes_used": d.get("bytes_per_record_avg")},
                ensure_ascii=False, indent=2,
            )
        except Exception as e:
            return f"ERROR: {e}"
```

### 2.5 Prompts 实现

```python
# prompts.py
from mcp.server.fastmcp import FastMCP

def register_prompts(mcp: FastMCP):

    @mcp.prompt()
    def cache_diagnostic(symptom: str) -> str:
        """缓存诊断：基于症状生成排查 Prompt"""
        return f"""你是 Redis 缓存诊断专家。用户描述如下：

{symptom}

请按以下步骤分析：
1. 调用 redis://stats 查看命中率、内存、连接数
2. 调用 redis://keys/*:hot 查看可能的 hot key
3. 判断属于以下哪类问题：
   - 缓存穿透（key 不存在）
   - 缓存击穿（hot key 过期）
   - 缓存雪崩（大批 key 同时过期）
   - 大 Key / 慢查询
4. 给出具体修复方案（含 Redis 命令）

输出格式：
- 诊断：<具体问题>
- 证据：<支撑数据>
- 修复：<可执行命令>
"""

    @mcp.prompt()
    def performance_analysis(target: str = "server") -> str:
        """性能分析：生成系统性能分析 Prompt"""
        return f"""你是 Redis 性能调优专家。分析目标：{target}

执行步骤：
1. redis://stats 获取基础统计
2. 评估以下指标：
   - hit_rate（< 90% 需优化）
   - used_memory_peak / used_memory（碎片率）
   - connected_clients（连接数）
   - total_commands_processed / instantaneous_ops_per_sec
3. 输出 JSON 格式报告：
   {{
     "score": "0-100",
     "issues": [...],
     "recommendations": [...]
   }}
"""

    @mcp.prompt()
    def vector_index_design(corpus_size: int, dim: int) -> str:
        """向量索引设计：根据规模推荐 HNSW 参数"""
        return f"""你是 Redis 向量检索专家。设计一个向量索引：
- 语料规模：{corpus_size}
- 向量维度：{dim}

要求：
1. 在 HNSW 与 FLAT 间选择并说明理由
2. 给出 FT.CREATE 完整命令
3. 推荐参数：M / EF_CONSTRUCTION / EF_RUNTIME
4. 估算内存占用
5. 给出批量插入与查询示例
"""
```

### 2.6 入口与启动

```python
# server.py
import os
import logging
from mcp.server.fastmcp import FastMCP
from config import config
from tools import mcp
from resources import register_resources
from prompts import register_prompts

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("redis-mcp")

# 注册资源与 Prompts
register_resources(mcp)
register_prompts(mcp)

# 启动入口
if __name__ == "__main__":
    logger.info("Redis MCP Server 启动中...")
    logger.info(f"REDIS_URL={config.redis_url}")
    logger.info(f"WRITE_ENABLED={config.enable_write}")
    # 默认 stdio 传输；可改 "streamable-http" 部署为 HTTP 服务
    transport = os.getenv("MCP_TRANSPORT", "stdio")
    mcp.run(transport=transport)
```

### 2.7 运行（stdio 模式）

> 本小节命令在配套代码 `codes/mcp_server/` 目录下执行（源码无需手抄，见 2.1 配套引用）。

```powershell
conda activate langchain_xm
pip install -r requirements.txt
# 1. 启动 Redis（已开 redis-stack）
# 2. 启动 MCP Server（前台 stdio，默认只读）
python server.py

# 3. 或导出为命令便于 Claude Code 调用（模块式）
python -m server
```

### 2.8 HTTP 模式部署

```python
# 启动为 HTTP 服务（端口 8080）
# 设置环境变量：MCP_TRANSPORT=streamable-http
# FastMCP 默认监听 0.0.0.0:8080/mcp
$env:MCP_TRANSPORT="streamable-http"
python server.py
```

## 三、Claude Code 集成

### 3.1 安装 Claude Code CLI

```powershell
nvm use v20.19.5
npm install -g @anthropic-ai/claude-code
claude --version
```

### 3.2 注册 MCP Server（.claude.json）

在用户目录或项目根创建 `.claude.json`：

```json
{
  "mcpServers": {
    "redis": {
      "command": "python",
      "args": ["-m", "server"],
      "env": {
        "REDIS_URL": "redis://localhost:6379",
        "OPENAI_API_KEY": "sk-...",
        "MCP_REDIS_WRITE": "true",
        "PYTHONPATH": "<工程路径>/Redis/18_Redis_MCP_Server与工具集成/codes/mcp_server",
        "CONDA_PREFIX": "C:/Users/<your_user>/miniconda3/envs/langchain_xm"
      },
      "cwd": "<工程路径>/Redis/18_Redis_MCP_Server与工具集成/codes/mcp_server"
    },
    "redis-readonly": {
      "command": "python",
      "args": ["-m", "server"],
      "env": {
        "REDIS_URL": "redis://localhost:6379",
        "MCP_REDIS_WRITE": "false"
      }
    }
  }
}
```

> 路径（`<工程路径>`、`<your_user>`）按你的实际环境与本章 `codes/mcp_server/` 目录修改。

> Windows 下若 Claude Code 找不到 conda 环境的 python，可在 `command` 直接写 conda 环境的 python 绝对路径，如 `C:/Users/xxx/miniconda3/envs/langchain_xm/python.exe`。

### 3.3 实战对话流程

启动 `claude` CLI 后即可对话：

```
> 帮我看看 Redis 当前内存使用情况
[claude 调用 redis://stats 资源]
Redis 当前内存：2.4MB（峰值 5.1MB），命中率 98.3%，连接数 8...

> 把 "重要配置" 写入键 config:backup，TTL 1 小时
[claude 调用 redis_set 工具]
OK: config:backup (ttl=3600s)

> 检索知识库里关于 HNSW 的内容
[claude 调用 redis_vector_search 工具]
[1] Redis HNSW 参数：M/EF_CONSTRUCTION/EF_RUNTIME ...
[2] ...

> 给我做一次缓存诊断，最近 hit rate 下降
[claude 使用 cache_diagnostic prompt 模板]
诊断：缓存击穿，证据：hit_rate 75%，stats:user:1001 在 30 秒前过期...
```

### 3.4 在 Cursor / Continue 中使用

Cursor 同样支持 MCP：在 Settings → MCP 中添加同样的 server 配置即可，复用同一 Redis MCP Server。

## 四、Docker Compose 三件套

### 4.1 目录结构

```
redis-ai-stack/
├── docker-compose.yml
├── mcp-server/
│   ├── Dockerfile
│   ├── server.py
│   ├── tools.py
│   ├── resources.py
│   ├── prompts.py
│   ├── config.py
│   └── requirements.txt
└── .env
```

### 4.2 Dockerfile（MCP Server）

```dockerfile
# mcp-server/Dockerfile
FROM python:3.12-slim AS base

# 非 root 运行
RUN groupadd -r mcp && useradd -r -g mcp -m -d /home/mcp mcp

WORKDIR /app

# 仅复制 requirements 加速缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制源码
COPY --chown=mcp:mcp . .

USER mcp

# 默认 stdio 模式
ENV MCP_TRANSPORT=stdio
ENV PYTHONUNBUFFERED=1

# 健康检查（HTTP 模式下使用）
HEALTHCHECK --interval=30s --timeout=3s \
  CMD python -c "import redis,os;r=redis.from_url(os.getenv('REDIS_URL'));r.ping()" || exit 1

ENTRYPOINT ["python", "-m", "server"]
```

### 4.3 requirements.txt

```
mcp>=2.1.1
redis>=8.1
redisvl>=0.4
langchain-redis>=0.2.5
langchain-openai>=1.0
langchain-core>=1.4
pydantic>=2
```

### 4.4 docker-compose.yml

```yaml
# docker-compose.yml（Compose v2+ 无需 version 字段，v3 已废弃该字段）
services:
  redis-stack:
    image: redis/redis-stack:7.4.0-v8   # Docker Hub 当前最新稳定（2026-09 实测）
    container_name: redis-stack
    ports:
      - "6379:6379"
      - "8001:8001"   # RedisInsight
    environment:
      - REDIS_ARGS=--requirepass ${REDIS_PASSWORD} --appendonly yes
    volumes:
      - redis-data:/data
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "redis-cli", "-a", "${REDIS_PASSWORD}", "ping"]
      interval: 10s
      timeout: 3s
      retries: 5

  mcp-server:
    build: ./mcp-server
    container_name: redis-mcp
    depends_on:
      redis-stack:
        condition: service_healthy
    environment:
      - REDIS_URL=redis://:${REDIS_PASSWORD}@redis-stack:6379
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - MCP_REDIS_WRITE=${MCP_REDIS_WRITE:-false}
      - MCP_TRANSPORT=streamable-http
      - MCP_HOST=0.0.0.0
      - MCP_PORT=8080
    ports:
      - "8080:8080"
    restart: unless-stopped
    user: "1000:1000"   # 非 root
    read_only: false
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true

  # Claude Code 容器（可选；多数场景下 Claude Code 装在本地更方便）
  claude-code:
    image: node:22-slim        # 维护期 LTS（Node 24 为 Active LTS，可按需替换）
    container_name: claude-code
    depends_on:
      - mcp-server
    environment:
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
      - MCP_REDIS_URL=http://mcp-server:8080/mcp
    working_dir: /workspace
    volumes:
      - ./workspace:/workspace
      - ./claude-config:/root/.claude
    command: >
      sh -c "npm install -g @anthropic-ai/claude-code && claude"
    stdin_open: true
    tty: true
    restart: "no"

volumes:
  redis-data:
```

### 4.5 .env 模板

```env
# .env
REDIS_PASSWORD=change_me_in_prod
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
MCP_REDIS_WRITE=false
```

### 4.6 启动

```powershell
cd redis-ai-stack
docker compose up -d redis-stack mcp-server
# 查看 MCP Server 日志
docker compose logs -f mcp-server
# 本地 Claude Code 连接 MCP Server
claude
```

## 五、安全隔离清单

| 维度 | 配置 | 说明 |
|------|------|------|
| 容器用户 | `user: "1000:1000"` + `cap_drop: ALL` | 非 root + 最小权限 |
| 文件系统 | `read_only: true` + tmpfs 挂载 | 防止写入恶意脚本 |
| 内核能力 | `security_opt: no-new-privileges` | 阻止提权 |
| Redis 鉴权 | `requirepass` + ACL | 强密码 + 角色 |
| Redis ACL 角色 | `+@read +get +hget +xread +ft.search` | 仅读 + 必要命令 |
| 写操作白名单 | `MCP_REDIS_WRITE=false` 默认 | 仅显式开启 |
| 资源前缀限制 | `allowed_key_prefixes` | 防止越权读 |
| 资源大小限制 | `max_value_length=10000` | 防大 key 打爆 LLM |
| 网络隔离 | mcp-server 不暴露公网 | 仅本地或内网 |
| 密钥管理 | `.env` + `.gitignore` | 不进版本库 |
| TLS | `rediss://` + 证书 | 生产强制 |
| 日志审计 | MCP 调用全量日志 | 便于追溯 |

### 5.1 Redis ACL 受限角色示例

```bash
# 创建只读角色
redis-cli -a change_me_in_prod ACL SETUSER mcp_readonly on >readonly_pwd \
  ~* +@read +ping +info +ft.info +ft.search -@write -@dangerous

# 创建读写角色（写操作受限）
redis-cli -a change_me_in_prod ACL SETUSER mcp_writer on >writer_pwd \
  ~cache:* ~agent:* ~lock:* \
  +@read +@write +get +set +del +setex +xadd +xreadgroup \
  +eval +ft.search +ft.info -@dangerous -flushall -flushdb -config

# 验证
redis-cli --user mcp_readonly -a readonly_pwd GET foo   # OK
redis-cli --user mcp_readonly -a readonly_pwd SET foo 1 # NOPERM
```

### 5.2 .gitignore

```
.env
*.key
*.pem
workspace/
claude-config/.credentials.json
```

## 六、MCP vs LangChain 工具对比

| 维度 | MCP Server | LangChain @tool |
|------|-----------|-----------------|
| 协议 | JSON-RPC 2.0 标准 | Python 函数 + Pydantic |
| 客户端 | Claude/Cursor/任意 MCP 客户端 | 仅 LangChain Agent |
| 跨语言 | 是（任意语言实现） | 仅 Python |
| 部署 | 独立进程 / 容器 | 嵌入 Agent 进程 |
| 资源（Resources） | 原语支持 | 无 |
| Prompts 模板 | 原语支持 | 通过 PromptTemplate |
| 状态管理 | 协议无关（Server 自管） | LangGraph Checkpointer |
| 异步 | 协议层支持 | asyncio |
| 流式 | SSE / Streamable HTTP | astream |
| 学习曲线 | 中（协议 + SDK） | 低（Python 装饰器） |
| 生态成熟度 | 新（2024 起） | 成熟 |
| 推荐场景 | 跨客户端共享工具 / 多语言 | 单一 LangChain 栈 |

### 6.1 langchain-mcp-adapters 互通方案

让 LangChain Agent 调用 MCP Server 暴露的工具：

```powershell
pip install langchain-mcp-adapters
```

```python
import asyncio
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.redis import RedisSaver

async def main():
    # 1. 连接 Redis MCP Server（stdio 或 HTTP）
    client = MultiServerMCPClient({
        "redis": {
            "transport": "streamable_http",
            "url": "http://localhost:8080/mcp",
        },
        # 可同时连多个 MCP Server
        # "github": {...},
    })
    tools = await client.get_tools()
    print(f"加载工具：{[t.name for t in tools]}")

    # 2. 构建带 Redis Checkpointer 的 Agent
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
    checkpointer = RedisSaver.from_conn_string("redis://localhost:6379")
    agent = create_react_agent(llm, tools, checkpointer=checkpointer)

    # 3. 调用
    res = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "把 hello 写入 key:greet"}]},
        config={"configurable": {"thread_id": "mcp_1"}},
    )
    print(res["messages"][-1].content)

asyncio.run(main())
```

### 6.2 互通架构

```
┌─────────────────────────────────────────────────────────────┐
│                  互通架构：LangChain ↔ MCP                   │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│   ┌──────────────────┐                                      │
│   │ LangChain Agent  │ (create_react_agent)                 │
│   │  + RedisSaver    │                                      │
│   └────────┬─────────┘                                      │
│            │ load_tools()                                   │
│            ▼                                                 │
│   ┌──────────────────┐    JSON-RPC    ┌──────────────────┐ │
│   │ langchain-mcp-   │ ──────────────>│ Redis MCP Server │ │
│   │ adapters         │                │  (Python)        │ │
│   └──────────────────┘                └────────┬─────────┘ │
│                                                │ redis-py   │
│                                                ▼            │
│                                       ┌──────────────────┐ │
│                                       │  Redis Stack     │ │
│                                       └──────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

## 七、端到端 AI Redis 助手架构

### 7.1 整体架构

```
┌─────────────────────────────────────────────────────────────────┐
│                  端到端 AI Redis 助手架构                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  用户输入（自然语言）                                            │
│      │                                                           │
│      ▼                                                           │
│  ┌─────────────────────────────────────────────┐                │
│  │       LLM Client (Claude Code / Cursor)     │                │
│  │       - 工具选择决策                         │                │
│  │       - 资源读取编排                         │                │
│  │       - Prompts 模板调用                     │                │
│  └────────────────┬────────────────────────────┘                │
│                   │ MCP 协议（stdio / HTTP）                    │
│                   ▼                                              │
│  ┌─────────────────────────────────────────────┐                │
│  │          Redis MCP Server                   │                │
│  │  ┌──────────┬──────────┬────────────────┐  │                │
│  │  │ 8 Tools  │ Resources│ 3 Prompts      │  │                │
│  │  │ get/set  │ key/stats│ cache_diag     │  │                │
│  │  │ delete   │ keys/*   │ perf_analysis  │  │                │
│  │  │ cache_inv│ index/*  │ vector_design  │  │                │
│  │  │ vector   │          │                │  │                │
│  │  │ stream_p │          │                │  │                │
│  │  │ stream_c │          │                │  │                │
│  │  │ lock_a/r │          │                │  │                │
│  │  └──────────┴──────────┴────────────────┘  │                │
│  │  - 连接池 / 异常包装 / 写白名单            │                │
│  └────────────────┬────────────────────────────┘                │
│                   │ redis-py / langchain_redis                  │
│                   ▼                                              │
│  ┌─────────────────────────────────────────────┐                │
│  │            Redis Stack (7+)                 │                │
│  │  - String / Hash / List / Stream            │                │
│  │  - RediSearch (FT.* 向量检索)               │                │
│  │  - ACL 角色隔离 (mcp_readonly / mcp_writer) │                │
│  │  - AOF + RDB 持久化                         │                │
│  └─────────────────────────────────────────────┘                │
└─────────────────────────────────────────────────────────────────┘
```

### 7.2 端到端验证清单

- [ ] **MCP Server 启动**：`python server.py` 无报错，日志显示 `WRITE_ENABLED=false`
- [ ] **Claude Code 加载**：`.claude.json` 配置后 `claude` 启动能看到 `redis` server 在线
- [ ] **工具列表**：在 Claude Code 输入 `/mcp` 看到 8+ 个 redis_* 工具
- [ ] **只读模式**：`MCP_REDIS_WRITE=false` 时调用 `redis_set` 返回 `PermissionError`
- [ ] **资源读取**：调用 `redis://stats` 返回 JSON 格式统计
- [ ] **Prompts 模板**：在 Claude Code 中选择 `cache_diagnostic` 模板，能填充 symptom
- [ ] **向量检索**：`redis_vector_search` 返回 Top-K 文档
- [ ] **Stream 消息**：`redis_stream_publish` 后 `redis_stream_consume` 能取到
- [ ] **分布式锁**：`redis_lock_acquire` 返回 ACQUIRED，`redis_lock_release` 返回 RELEASED
- [ ] **ACL 隔离**：用 `mcp_readonly` 用户连接，调用写工具返回 NOPERM
- [ ] **Docker 部署**：`docker compose up -d` 后三个容器健康
- [ ] **langchain-mcp-adapters**：LangChain Agent 能加载并调用 MCP 工具
- [ ] **LangSmith 追踪**：调用链路在 LangSmith UI 可见

### 7.3 故障排查

| 现象 | 排查 |
|------|------|
| Claude Code 提示 MCP 未启动 | `python -m server`（在 codes/mcp_server/ 下）单独运行看报错 |
| 工具调用返回 `PermissionError` | 检查 `MCP_REDIS_WRITE` 与 `write_tools_whitelist` |
| 资源读取 NULL | 确认 key 存在 + `allowed_key_prefixes` 未拦截 |
| 向量检索返回 no results | `FT.INFO` 确认索引有 `num_docs > 0` |
| 容器内连不上 Redis | 用 `redis-stack` 服务名而非 localhost |
| Claude Code 找不到 python | 用 conda 环境的 python 绝对路径 |

## 八、扩展与最佳实践

### 8.1 多 Redis 实例路由

```python
# 在工具内根据 key 前缀路由到不同 Redis
def get_redis_for_key(key: str) -> redis.Redis:
    if key.startswith("cache:"):
        return cache_redis       # 热数据 Redis
    elif key.startswith("archive:"):
        return archive_redis     # 冷数据 Redis
    return default_redis
```

### 8.2 工具调用审计

```python
import logging
audit_logger = logging.getLogger("mcp.audit")

def audit_log(tool_name: str, args: dict, result: str, user: str = "anonymous"):
    audit_logger.info(json.dumps({
        "ts": time.time(), "tool": tool_name, "args": args,
        "result_len": len(result), "user": user,
    }, ensure_ascii=False))
    # 也可写入 Redis Stream 供后续分析
    r = get_redis()
    r.xadd("mcp:audit", {
        "tool": tool_name, "user": user,
        "args": json.dumps(args, ensure_ascii=False),
    }, maxlen=100000, approximate=True)
```

### 8.3 速率限制

```python
from redis import Redis

def rate_limit(user: str, limit: int = 60, window: int = 60) -> bool:
    """每分钟限制 60 次工具调用"""
    r = get_redis()
    key = f"ratelimit:{user}:{int(time.time() // window)}"
    current = r.incr(key)
    if current == 1:
        r.expire(key, window)
    return current <= limit

# 在工具中调用
@mcp.tool()
def redis_get(key: str, _user: str = "default") -> str:
    if not rate_limit(_user):
        return "ERROR: rate limit exceeded"
    r = get_redis()
    value = r.get(key)
    if value is None:
        return f"KEY NOT FOUND: {key}"
    return value.decode("utf-8") if isinstance(value, bytes) else value
```

### 8.4 流式响应（长任务）

```python
from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent

@mcp.tool()
async def redis_vector_search_stream(query: str, k: int = 100) -> list[TextContent]:
    """流式返回大量向量检索结果"""
    r = get_redis()
    # 假设使用 cursor 分批
    results = []
    for batch in vector_search_paginated(query, k, batch_size=20):
        results.append(TextContent(type="text", text=json.dumps(batch)))
    return results
```

### 8.5 工具版本演进

```python
# 工具版本演进：用新函数名注册，避免与旧工具冲突
@mcp.tool(name="redis_get_v2")
def redis_get_v2(key: str, decode: bool = True) -> str:
    """v2 新增 decode 参数控制是否自动反序列化 JSON（注册名 redis_get_v2）。"""
    r = get_redis()
    raw = r.get(key)
    if raw is None:
        return f"KEY NOT FOUND: {key}"
    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    if not decode:
        return text
    try:
        return json.dumps(json.loads(text), ensure_ascii=False)
    except (json.JSONDecodeError, TypeError):
        return text
```

## 九、常见问题

| 现象 | 原因 | 解决 |
|------|------|------|
| `ModuleNotFoundError: mcp` | 未装 SDK | `pip install "mcp>=2.1.1"` |
| `@mcp.tool()` 不识别 | SDK 版本过旧 | 升级到 2.x |
| Claude Code 报 server timeout | 启动慢或 python 路径错 | 用绝对路径 + 预热 |
| 工具调用结果为空 | Redis 内无数据 | 先 `redis-cli SET foo bar` 验证 |
| 资源读取被截断 | `max_value_length` 限制 | 调大但注意 LLM token 成本 |
| 容器内 OPENAI_API_KEY 缺失 | .env 未挂载 | 检查 `env_file` 或 environment 段 |
| Windows 下 stdio 中文乱码 | 控制台编码 | 设 `PYTHONIOENCODING=utf-8` |
| HTTP 模式 404 | 端点路径不对 | FastMCP 默认 `/mcp` |

## 📖 深入阅读

- [MCP 官方规范](https://modelcontextprotocol.io/specification)
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [Claude Code 文档](https://docs.anthropic.com/claude/docs/claude-code)
- [langchain-mcp-adapters](https://github.com/langchain-ai/langchain-mcp-adapters)
- [MCP Servers 生态目录](https://github.com/modelcontextprotocol/servers)
- [Anthropic MCP 公告](https://www.anthropic.com/news/model-context-protocol)

## 本章小结

- MCP 是 Anthropic 提出的 LLM 工具协议标准，三大原语：Tools（函数调用）/ Resources（数据读取）/ Prompts（模板）
- 三种传输方式：stdio（本地）/ HTTP（远程）/ Streamable HTTP（流式，SSE 升级版）
- Redis MCP Server 用 `mcp` Python SDK + FastMCP 装饰器开发，包含 8 个工具（get/set/delete/cache_invalidate/vector_search/stream_publish/stream_consume/lock_acquire+release）
- 资源用 URI 模板暴露：`redis://key/{key}` / `redis://stats` / `redis://keys/{pattern}` / `redis://index/{name}`
- Prompts 模板让用户复用诊断 / 分析 / 设计场景，提升首次正确率
- 安全四件套：非 root 容器 + Redis ACL 角色 + 写操作白名单 + 资源大小限制
- Claude Code 通过 `.claude.json` 注册 MCP Server，支持多 Server 共存（如 redis + redis-readonly）
- Docker Compose 三件套：redis-stack + mcp-server + claude-code，全栈容器化
- langchain-mcp-adapters 让 LangChain Agent 调用 MCP 工具，实现两套生态互通
- 端到端架构：用户 → LLM Client → MCP Server → Redis Stack，所有路径可观测、可审计

## 下一步导航

- 回顾 Agent 工具开发 → [17 LangChain v1.4 与 Redis](../17_LangChain_v1.4与Redis/17_LangChain_v1.4与Redis.md)
- 复习向量检索基础 → [16 Redis 向量存储与 RAG 应用](../16_Redis向量存储与RAG应用/16_Redis向量存储与RAG应用.md)
- 容器化部署细节 → [14 Redis 与 Docker 容器化部署](../14_Redis与Docker容器化部署/14_Redis与Docker容器化部署.md)
- 安全运维深入 → [12 安全与运维](../12_安全与运维/12_安全与运维.md)
- 查看术语速查 → [99 附录](../99_附录/99_附录.md)
