# 16 Redis 向量存储与 RAG 应用

> Redis 7+ 借助 RediSearch 模块原生支持向量索引（HNSW/FLAT）与混合检索，结合 RedisVL 或 langchain_redis 可在分钟级搭建生产可用的 RAG Pipeline。

## 零基础前置认知

**这篇在讲什么**：把"切分好的文章 → 用 Embedding 模型变成向量 → 存进 Redis → 检索出相关的段落 → 喂给 LLM"全链路跑通，就是 **RAG**（检索增强生成，让 LLM 基于你的文档回答、减少幻觉）。Redis 在这里有两种玩法：**经典 RediSearch 向量索引**（FT.CREATE/FT.SEARCH + HNSW，配合 RedisVL 或 langchain_redis）与 **Redis 8 原生的 Vector Sets**（VADD/VSIM，更内建）。读完你能搭一个"企业知识库问答"的最小 RAG 服务，并说明 HNSW vs FLAT、Redis vs Milvus/pgvector 怎么选。*Embedding 用 OpenAI 或本地 BGE 均可；本文示例可用 FakeEmbeddings 免 Key 跑通（见 codes）。*

> 辅助类比：把 RAG 想成"图书馆查资料回答"——**Embedding** 是"把每篇文章按语义摆进多维书架"（相似语义放近处）；**向量索引（HNSW/FLAT）** 是"书架导航图"（HNSW=抄近道的索引卡、FLAT=一本本翻）；**相似度检索** 是"拿你的问题向量找最近的几本"；**混合检索** 是"既按语义、也按关键词（全文）一起找，取交集补集"（RRF 融合）；**Vector Sets（Redis 8）** 是"书架自带扫码枪"（原生命令 VADD/VSIM，免装扩展的更快路径）。

**基础名词集**：

| 名词 | 一句话定义 | 与相邻概念的关系 |
|------|-----------|----------------|
| RAG | 检索增强生成：检索→拼上下文→LLM 回答 | 与"向量检索 + 生成"一体；降幻觉、基于自有数据 |
| Embedding | 文本→固定维向量（语义相近→向量相近） | 由 OpenAI/BGE/SBERT 等生成；相似度用 COSINE/IP/L2 |
| HNSW | 图索引：层次化小世界近似检索 O(logN) | 大规模/低延迟首选；召回率由 M/EF 参数权衡 |
| FLAT | 暴力线性扫描（100% 召回） | 小规模（<10w）精确检索；无需建图 |
| RediSearch | Redis 的向量+全文模块（FT.* 命令） | Redis 8.0 起**内置**（旧版需 redis-stack）；支持 HNSW/FLAT + 过滤 + RRF |
| Vector Sets | Redis 8 原生向量集类型（VADD/VSIM） | 比"Hash+索引"更内建的新玩法（见 1.4） |
| RedisVL | Redis 官方 Python 向量库（redisvl） | 建索引/查询/过滤的高层封装；"langchain_redis.RedisVectorStore"亦官方 |
| 混合检索 / RRF | 向量+全文结果按排名融合 | 语义+关键词互补；Redis 原生 `FT.SEARCH` 可同时算两个 |

> 区分/注意：**"向量"是数据，不是 Redis 新类型**——经典做法把 embedding 存 Hash 字段 + FT.CREATE 建向量索引；**Redis 8 的 Vector Sets 才是新内建类型**（一条 `VADD` 直接管向量，比"Hash+模块索引"更简，见 1.4）。**RAG 不是向量库单独的事**：检索只是前半程，还有切分、prompt 拼装、LLM 生成与评估；别把"建了向量库"当"做完了 RAG"。**Redis 适合"中量级+内存检索+与业务缓存同库"**；海量（千万级）延迟敏感用 Milvus，Postgres 已有则可先试 pgvector（见七 对比）。

## 学习目标

- 理解 RediSearch 向量索引（HNSW / FLAT）原理与 FT.CREATE / FT.SEARCH / FT.AGGREGATE 命令体系
- 掌握 RedisVL（Redis Vector Library）创建文本 + 向量混合索引、相似度搜索与过滤器
- 能够使用 OpenAI / BGE / SBERT 三类 Embedding 模型生成向量
- 完整实现 LangChain v1.4 + Redis 的 RAG Pipeline（加载 → 分割 → Embedding → 存储 → 检索 → 生成）
- 对比 Redis 向量与 Chroma / Pinecone / Milvus / pgvector 的 11 维度差异
- 掌握 HNSW 调参、批量插入、RRF 混合检索等生产优化技巧
- 完成企业知识库 RAG 完整实战

## 前置知识

- [01 Redis 架构与核心概念](../01_Redis架构与核心概念/01_Redis架构与核心概念.md)：单线程模型、RESP3
- [02 数据类型与底层实现](../02_数据类型与底层实现/02_数据类型与底层实现.md)：Hash / Stream 编码
- [09 缓存模式与实战](../09_缓存模式与实战/09_缓存模式与实战.md)：缓存穿透 / 击穿 / 雪崩
- [14 Redis 与 Docker 容器化部署](../14_Redis与Docker容器化部署/14_Redis与Docker容器化部署.md)：redis-stack 镜像
- 熟悉 Python 异步、向量相似度（COSINE / L2 / IP）基础

## 一、Redis 向量能力总览

### 1.1 为什么用 Redis 做向量存储

Redis 7+ 通过 RediSearch 模块（redis-stack 内置）提供原生的向量索引与检索能力，相比专用向量库的优势在于：

| 维度 | 专用向量库（Milvus/Pinecone） | Redis 向量 |
|------|------------------------------|-----------|
| 部署复杂度 | 独立集群、依赖 etcd/MinIO | redis-stack 单容器 |
| 检索延迟 | 10~50ms | 1~5ms（内存） |
| 混合检索 | 多数需外挂 BM25 | 原生 FTS + Vector |
| 元数据过滤 | 各家语法不一 | 标准 RediSearch 过滤 |
| 缓存协同 | 需独立缓存层 | 与业务缓存同库 |
| 持久化 | 各家机制 | RDB + AOF 成熟 |

### 1.2 RediSearch 向量索引架构

```
┌─────────────────────────────────────────────────────────────┐
│                       Redis Stack                           │
│  ┌──────────────────────────────────────────────────────┐  │
│  │           RediSearch Module (FT.* commands)          │  │
│  │  ┌─────────────────┐    ┌─────────────────────────┐  │  │
│  │  │  HNSW Index     │    │  FLAT Index (brute)     │  │  │
│  │  │  (graph-based)  │    │  (linear scan)          │  │  │
│  │  └─────────────────┘    └─────────────────────────┘  │  │
│  └──────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  Hash / JSON documents                                │  │
│  │  {text: "...", embedding: [0.1, 0.2, ...], tag: ...} │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
        ▲                                ▲
        │ FT.CREATE / FT.SEARCH          │ Vector Data
        │                                │
  ┌─────┴─────┐                  ┌───────┴────────┐
  │ RedisVL   │                  │ LangChain      │
  │ Client    │                  │ RedisVectorStore│
  └───────────┘                  └────────────────┘
```

### 1.3 索引类型对比

| 维度 | HNSW | FLAT |
|------|------|------|
| 算法 | 层次化小世界图 | 暴力线性扫描 |
| 召回率 | 高（可调） | 100% 精确 |
| 延迟 | O(logN) | O(N) |
| 内存 | 额外图结构 | 仅向量 |
| 适用场景 | 大规模（>10w）、低延迟 | 小规模（<10w）、精确召回 |
| 参数 | M / EF_CONSTRUCTION / EF_RUNTIME | 无 |

### 1.4 Redis 8 原生 Vector Sets（VADD/VSIM，认知段）

Redis 8 引入**原生向量类型 Vector Set**，把"向量 + 相似度检索"收进内核，作为"Hash 字段 + RediSearch 索引"之外的更内建玩法（无需单独建索引）：

| 能力 | RediSearch 经典做法 | Redis 8 Vector Sets |
|------|--------------------|--------------------|
| 写入 | `HSET key embedding <vec>` + 预先 `FT.CREATE` | 一条 `VADD key DIMS 3 TYPE FLOAT32 <vec> [METADATA ...]` 即入集（免建索引） |
| 检索 | `FT.SEARCH idx "*=>[KNN 10 @vec $Q]"` | `VSIM key 10 KNN 3 <query_vec>` 直接返回 Top-K（COSINE） |
| 元数据 | Indexed tag/text 字段 | 每向量可带 `METADATA`（JSON）并支持 `FILTER` |
| 依赖 | 需要 FT 索引/模式 | 类型内建，命令更直白 |

```bash
# Redis 8：存入 3 维向量 + 元数据（一条命令）
VADD course:vec DIMS 3 TYPE FLOAT32 0.1 0.2 0.3 METADATA '{"title":"Redis 架构","src":"doc1"}'

# 检索与查询向量最近（COSINE）的 10 条
VSIM course:vec 10 KNN 3 0.15 0.25 0.35

# 支持按元数据过滤（如只查 src=doc1）
VSIM course:vec 10 KNN 3 0.15 0.25 0.35 FILTER 'src == "doc1"'
```

> 选型注：Vector Sets 面向"我要一个内存向量集、要快、要省事"的场景（免建索引、命令内建）；若需要**全文 + 向量混合检索（RRF）**、既有 RediSearch 体系或想用 RedisVL/langchain_redis 封装，走经典 FT 索引更合适——两者可并存，按你的查询形态选。完整命令与细节以 Redis 8 官方文档为准（写作时点为 2.x 示例，落地前核对）。

## 二、RediSearch 向量命令详解

### 2.1 FT.CREATE 创建向量索引

```bash
# HNSW 索引：COSINE 相似度，768 维（适配 BGE / SBERT）
FT.CREATE doc_idx ON HASH PREFIX 1 doc: SCHEMA
  title TEXT WEIGHT 2.0
  content TEXT
  tag TAG SEPARATOR ","
  created_at NUMERIC SORTABLE
  embedding VECTOR HNSW 6
    TYPE FLOAT32
    DIM 768
    DISTANCE_METRIC COSINE
    M 16
    EF_CONSTRUCTION 200
    EF_RUNTIME 10

# FLAT 索引：精确召回，1536 维（适配 OpenAI text-embedding-3-small）
FT.OPENAI_idx ON HASH PREFIX 1 openai: SCHEMA
  content TEXT
  embedding VECTOR FLAT 2
    TYPE FLOAT32
    DIM 1536
    DISTANCE_METRIC L2
```

**HNSW 参数详解：**

| 参数 | 默认 | 推荐范围 | 说明 |
|------|------|----------|------|
| M | 16 | 12~48 | 每节点最大邻居数，越大召回越高、内存↑ |
| EF_CONSTRUCTION | 200 | 100~500 | 建图候选数，越大索引质量越好、构建慢 |
| EF_RUNTIME | 10 | 10~100 | 查询候选数，越大召回越高、延迟↑ |
| EPSILON | 0.01 | 0.001~0.1 | 范围搜索半径系数 |

### 2.2 FT.SEARCH 向量检索

```bash
# 1. 准备查询向量（需 base64 编码的 float32，doc_idx 维度 768）
# Python 中用 numpy.tobytes() 后 base64 编码，这里生成 768 维全 0 向量作示例
QUERY_VEC=$(python3 -c "import numpy as np, base64; print(base64.b64encode(np.zeros(768, dtype=np.float32).tobytes()).decode())")

# 2. KNN 查询：找最相似的 5 个
redis-cli FT.SEARCH doc_idx "*=>[KNN 5 @embedding \$query_vec EF_RUNTIME 10]" \
  PARAMS 2 query_vec "$QUERY_VEC" \
  DIALECT 2

# 3. 混合检索：tag 过滤 + 向量 KNN
redis-cli FT.SEARCH doc_idx "@tag:{tech,ai}=>[KNN 5 @embedding \$query_vec]" \
  PARAMS 2 query_vec "$QUERY_VEC" \
  RETURN 3 title content __vector_score \
  DIALECT 2

# 4. 范围搜索：距离 < 0.5 的全部返回
redis-cli FT.SEARCH doc_idx "*=>[VECTOR_RANGE 0.5 @embedding \$query_vec]" \
  PARAMS 2 query_vec "$QUERY_VEC" \
  DIALECT 2
```

### 2.3 FT.AGGREGATE 聚合分析

```bash
# 按 tag 聚合，统计每个分类的平均向量距离（Q 复用上面的 QUERY_VEC）
redis-cli FT.AGGREGATE doc_idx "@tag:{tech,ai}=>[KNN 100 @embedding \$q]" \
  PARAMS 2 q "$QUERY_VEC" \
  LOAD 2 tag __vector_score \
  GROUPBY 1 @tag \
  REDUCE 2 AVG 1 __vector_score AS avg_dist \
  SORTBY 2 @avg_dist ASC \
  DIALECT 2
```

### 2.4 原生命令 Python 示例

```python
import base64
import numpy as np
import redis

r = redis.Redis(host="localhost", port=6379, decode_responses=False)

def vec_to_blob(vec: list[float]) -> bytes:
    arr = np.array(vec, dtype=np.float32)
    return base64.b64encode(arr.tobytes())

# 创建 HNSW 索引
r.execute_command(
    "FT.CREATE", "doc_idx", "ON", "HASH", "PREFIX", "1", "doc:",
    "SCHEMA",
    "title", "TEXT", "WEIGHT", "2.0",
    "content", "TEXT",
    "tag", "TAG", "SEPARATOR", ",",
    "embedding", "VECTOR", "HNSW", "6",
    "TYPE", "FLOAT32", "DIM", "768",
    "DISTANCE_METRIC", "COSINE",
    "M", "16", "EF_CONSTRUCTION", "200", "EF_RUNTIME", "10",
)

# 插入文档
import hashlib
def add_doc(text: str, vec: list[float], tag: str = "tech"):
    key = "doc:" + hashlib.md5(text.encode()).hexdigest()
    r.hset(key, mapping={
        "content": text,
        "tag": tag,
        "embedding": np.array(vec, dtype=np.float32).tobytes(),
    })

# KNN 检索
def knn_search(query_vec: list[float], k: int = 5):
    blob = vec_to_blob(query_vec)
    return r.execute_command(
        "FT.SEARCH", "doc_idx",
        "*=>[KNN 5 @embedding $query_vec EF_RUNTIME 10]",
        "PARAMS", "2", "query_vec", blob,
        "RETURN", "3", "content", "tag", "__vector_score",
        "DIALECT", "2",
    )
```

## 三、RedisVL（Redis Vector Library）

### 3.1 安装与连接

```powershell
conda activate langchain_xm
pip install "redisvl>=0.4" numpy
```

> ⚠️ **风险提示**：RedisVL 的 schema 语法基于 redisvl 0.x，升级后可能变化。安装后执行 `pip show redisvl` 确认版本。

### 3.2 VectorIndex 创建（混合索引）

```python
from redisvl.index import SearchIndex
from redisvl.schema import IndexSchema

schema = IndexSchema.from_dict({
    "index": {
        "name": "knowledge_base",
        "prefix": "kb",
        "storage_type": "hash",
    },
    "fields": [
        {"name": "title", "type": "text", "attrs": {"weight": 2.0}},
        {"name": "content", "type": "text"},
        {"name": "category", "type": "tag", "attrs": {"separator": ","}},
        {"name": "created_at", "type": "numeric", "attrs": {"sortable": True}},
        {
            "name": "embedding",
            "type": "vector",
            "attrs": {
                "dims": 768,
                "algorithm": "hnsw",
                "distance_metric": "cosine",
                "datatype": "float32",
                "hnsw": {"m": 16, "ef_construction": 200, "ef_runtime": 10},
            },
        },
    ],
})

index = SearchIndex(schema, redis_url="redis://localhost:6379")
index.create(overwrite=True)
```

### 3.3 文档插入与批量加载

```python
import numpy as np
from redisvl.index import SearchIndex

index = SearchIndex.from_yaml("schema.yaml", redis_url="redis://localhost:6379")

# 单条插入
data = [{
    "title": "Redis 向量检索简介",
    "content": "RediSearch 模块支持 HNSW 与 FLAT 向量索引...",
    "category": "tech,ai",
    "created_at": 1721692800,
    "embedding": np.random.rand(768).astype(np.float32).tolist(),
}]
index.load(data)

# 批量插入（推荐 pipeline + 分块）
def batch_load(index, docs: list[dict], batch_size: int = 500):
    import time
    for i in range(0, len(docs), batch_size):
        chunk = docs[i:i + batch_size]
        index.load(chunk)
        print(f"已写入 {min(i + batch_size, len(docs))}/{len(docs)}")
        # 避免压垮 Redis
        if i + batch_size < len(docs):
            time.sleep(0.1)
```

### 3.4 相似度搜索

```python
from redisvl.query import VectorQuery

# 基础 KNN 查询
vq = VectorQuery(
    vector=[0.1] * 768,                # 查询向量
    vector_field_name="embedding",
    query_type="KNN",                  # 或 RANGE
    num_results=5,
    return_fields=["title", "content", "category"],
    return_score=True,
)
results = index.query(vq)
for r in results:
    print(r["title"], r["vector_distance"])
```

### 3.5 过滤器：Numeric / Tag / Text

```python
from redisvl.query import VectorQuery
from redisvl.query.filter import Tag, Numeric, Text

# Tag 过滤：category 必须是 tech 或 ai
tag_filter = Tag("category") == ["tech", "ai"]

# Numeric 过滤：created_at > 1700000000
num_filter = Numeric("created_at") > 1700000000

# Text 过滤：title 中包含 "Redis"
text_filter = Text("title") % "Redis"

# 组合过滤
combined = (tag_filter & num_filter) | text_filter

vq = VectorQuery(
    vector=[0.1] * 768,
    vector_field_name="embedding",
    query_type="KNN",
    num_results=10,
    filter_expression=combined,
)
results = index.query(vq)
```

### 3.6 三种相似度度量对比

| 度量 | 公式 | 适用 | Redis 配置 |
|------|------|------|-----------|
| COSINE | 1 - (A·B)/(\|A\|·\|B\|) | 文本语义（最常用） | `DISTANCE_METRIC COSINE` |
| L2 | √Σ(aᵢ-bᵢ)² | 图像 / 通用 | `DISTANCE_METRIC L2` |
| IP | -Σ(aᵢ·bᵢ) | 已归一化向量 | `DISTANCE_METRIC IP` |

## 四、Embedding 生成

### 4.1 OpenAI text-embedding-3-small

```python
import os
from langchain_openai import OpenAIEmbeddings

os.environ["OPENAI_API_KEY"] = "sk-..."

embeddings = OpenAIEmbeddings(
    model="text-embedding-3-small",   # 1536 维，价格 $0.02/M
    # dimensions=768,                 # v3 支持降维，可省成本
)
vec = embeddings.embed_query("Redis 向量检索实战")
print(len(vec))   # 1536
```

### 4.2 BGE（中文场景首选）

```python
# 本地 BGE 模型，无需 API
from langchain_huggingface import HuggingFaceEmbeddings

embeddings = HuggingFaceEmbeddings(
    model_name="BAAI/bge-large-zh-v1.5",   # 1024 维
    model_kwargs={"device": "cpu"},        # 或 "cuda"
    encode_kwargs={"normalize_embeddings": True},
)
vec = embeddings.embed_query("Redis 向量检索实战")
print(len(vec))   # 1024
```

### 4.3 SBERT（轻量级）

```python
from langchain_huggingface import HuggingFaceEmbeddings

embeddings = HuggingFaceEmbeddings(
    model_name="sentence-transformers/all-MiniLM-L6-v2",  # 384 维
    model_kwargs={"device": "cpu"},
)
vec = embeddings.embed_query("Redis 向量 retrieval")
print(len(vec))   # 384
```

### 4.4 三类 Embedding 对比

| 模型 | 维度 | 中文 | 成本 | 延迟 | 适用 |
|------|------|------|------|------|------|
| OpenAI text-embedding-3-small | 1536 | 一般 | $0.02/M tokens | 100ms | 国际化 / 快速 PoC |
| BGE-large-zh-v1.5 | 1024 | 优秀 | 免费（自部署） | 50ms（GPU） | 中文生产场景 |
| SBERT MiniLM-L6 | 384 | 一般 | 免费 | 10ms | 边缘 / 离线 |

## 五、完整 RAG Pipeline（LangChain v1.4 + Redis）

### 5.1 RAG 流程图

```
┌────────────┐   ┌────────────┐   ┌────────────┐   ┌──────────────┐
│ 文档加载    │ → │ 分割       │ → │ Embedding  │ → │ Redis 向量库 │
│ PyPDFLoader│   │ Recursive  │   │ OpenAI/BGE │   │ RedisVector  │
│ WebLoader  │   │ Splitter   │   │            │   │ Store        │
└────────────┘   └────────────┘   └────────────┘   └──────┬───────┘
                                                          │
                                                          ▼
┌────────────┐   ┌────────────┐   ┌────────────┐   ┌──────────────┐
│ 答案生成    │ ← │ RAG Chain │ ← │ Retriever  │ ← │ 用户问题     │
│ ChatOpenAI │   │ LCEL       │   │ MMR / KNN  │   │ Embedding    │
└────────────┘   └────────────┘   └────────────┘   └──────────────┘
```

### 5.2 文档加载与分割

```python
# langchain_community 的 document_loaders 属 1.x 过渡设计（官方陆续迁移至专包，loaders 尚在其间，用法稳定）
from langchain_community.document_loaders import PyPDFLoader, WebBaseLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter

# 1. 加载 PDF
pdf_loader = PyPDFLoader("knowledge.pdf")
pdf_docs = pdf_loader.load()

# 2. 加载网页
web_loader = WebBaseLoader(["https://redis.io/docs/latest/develop/interact/search-and-query/"])
web_docs = web_loader.load()

all_docs = pdf_docs + web_docs

# 3. 分割：chunk_size 500，overlap 50（中文场景可调大）
splitter = RecursiveCharacterTextSplitter(
    chunk_size=500,
    chunk_overlap=50,
    separators=["\n\n", "\n", "。", "！", "？", "；", " "],
)
chunks = splitter.split_documents(all_docs)
print(f"原始文档 {len(all_docs)} → 分块后 {len(chunks)}")
```

### 5.3 Embedding + 存储（RedisVectorStore）

```python
import os
from langchain_openai import OpenAIEmbeddings
from langchain_redis import RedisConfig, RedisVectorStore

os.environ["OPENAI_API_KEY"] = "sk-..."

embeddings = OpenAIEmbeddings(model="text-embedding-3-small")

config = RedisConfig(
    index_name="rag_kb",
    redis_url="redis://localhost:6379",
    key_prefix="rag",
    metadata_schema=[                        # 元数据字段（用于过滤）
        {"name": "source", "type": "text"},
        {"name": "page", "type": "numeric"},
    ],
)

vector_store = RedisVectorStore.from_documents(
    documents=chunks,
    embedding=embeddings,
    config=config,
)
```

### 5.4 检索：similarity_search 与 MMR

```python
# 1. 基础相似度检索
results = vector_store.similarity_search(
    query="Redis 如何实现向量检索？",
    k=5,
)

# 2. 带元数据过滤
from redisvl.query.filter import Tag, Text
filter_expr = Text("source") % "redis.io"
filtered = vector_store.similarity_search(
    query="Redis 如何实现向量检索？",
    k=5,
    filter=filter_expr,
)

# 3. MMR（最大边际相关性）：去重 + 多样性
mmr_results = vector_store.max_marginal_relevance_search(
    query="Redis 如何实现向量检索？",
    k=5,
    fetch_k=20,         # 先召回 20 条
    lambda_mult=0.5,    # 0=最大多样性，1=最大相关性
)

# 4. 带分数的检索（带 __vector_score）
scored = vector_store.similarity_search_with_score(
    query="Redis 如何实现向量检索？",
    k=5,
)
for doc, score in scored:
    print(f"[{score:.4f}] {doc.page_content[:80]}")
```

### 5.5 生成：ChatOpenAI + RAG Chain

```python
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)

prompt = ChatPromptTemplate.from_template("""
你是 Redis 技术专家，基于以下检索到的上下文回答问题。

上下文：
{context}

问题：{question}

要求：
1. 仅基于上下文回答，不要编造
2. 若上下文不足，明确说明"信息不足"
3. 用中文回答，结构化输出

回答：
""")

retriever = vector_store.as_retriever(
    search_type="mmr",
    search_kwargs={"k": 5, "fetch_k": 20, "lambda_mult": 0.5},
)

def format_docs(docs):
    return "\n\n".join(
        f"[来源: {d.metadata.get('source', 'unknown')}]\n{d.page_content}"
        for d in docs
    )

# LCEL 管道
rag_chain = (
    {"context": retriever | format_docs, "question": RunnablePassthrough()}
    | prompt
    | llm
    | StrOutputParser()
)

# 同步调用
answer = rag_chain.invoke("Redis HNSW 索引有哪些参数？")
print(answer)

# 异步流式
import asyncio
async def stream_answer(q: str):
    async for chunk in rag_chain.astream(q):
        print(chunk, end="", flush=True)

asyncio.run(stream_answer("Redis HNSW 索引有哪些参数？"))
```

## 六、RedisVL 方案 vs langchain_redis.RedisVectorStore

| 维度 | 手动 RedisVL | langchain_redis.RedisVectorStore |
|------|-------------|--------------------------------|
| 抽象层级 | 底层（Index/Query） | 高层（VectorStore 抽象） |
| LangChain 集成 | 需手动适配 | 原生 BaseRetriever |
| 灵活性 | 高（任意 schema） | 中（约定字段） |
| 学习曲线 | 陡 | 平 |
| 过滤语法 | Python DSL | RedisVL 同款 |
| 推荐场景 | 定制化检索 / 复杂 schema | 标准 RAG / Agent |

### 6.1 RedisVL 方案完整示例

```python
from redisvl.index import SearchIndex
from redisvl.query import VectorQuery
from redisvl.query.filter import Tag
from langchain_openai import OpenAIEmbeddings
from langchain_core.documents import Document

embeddings = OpenAIEmbeddings(model="text-embedding-3-small")

index = SearchIndex.from_dict({
    "index": {"name": "kb_vl", "prefix": "kvl", "storage_type": "hash"},
    "fields": [
        {"name": "content", "type": "text"},
        {"name": "source", "type": "text"},
        {"name": "category", "type": "tag"},
        {"name": "embedding", "type": "vector", "attrs": {
            "dims": 1536, "algorithm": "hnsw", "distance_metric": "cosine",
            "datatype": "float32",
        }},
    ],
}, redis_url="redis://localhost:6379")
index.create(overwrite=True)

def add(doc: Document, category: str = "general"):
    vec = embeddings.embed_query(doc.page_content)
    index.load([{
        "content": doc.page_content,
        "source": doc.metadata.get("source", "unknown"),
        "category": category,
        "embedding": vec,
    }])

def search(query: str, k: int = 5, category: str | None = None):
    vec = embeddings.embed_query(query)
    f = Tag("category") == [category] if category else None
    vq = VectorQuery(
        vector=vec, vector_field_name="embedding",
        query_type="KNN", num_results=k,
        filter_expression=f,
        return_fields=["content", "source", "category"],
        return_score=True,
    )
    return [Document(page_content=r["content"], metadata={
        "source": r["source"], "score": r["vector_distance"]
    }) for r in index.query(vq)]
```

## 七、Redis 向量 vs 主流向量库对比（11 维度）

| 维度 | Redis | Chroma | Pinecone | Milvus | pgvector |
|------|-------|--------|----------|--------|----------|
| 部署 | Docker 单容器 | Docker / 嵌入式 | SaaS 托管 | 集群 + 依赖 | PG 扩展 |
| 索引算法 | HNSW / FLAT | HNSW | HNSW / IVF | HNSW / IVF / DiskANN | HNSW / IVFFlat |
| 混合检索 | 原生 FTS+Vector | 元数据过滤 | 元数据过滤 | 标量过滤 | 全文 + 向量 |
| 实时性 | 内存级 ms | 内存级 | 10~50ms | 5~30ms | 5~50ms |
| 持久化 | RDB+AOF | DuckDB | 托管 | 多级存储 | WAL |
| 规模上限 | 千万级（受内存） | 百万级 | 十亿级 | 十亿级 | 千万级 |
| 一致性 | 强一致 | 强一致 | 最终一致 | 强一致 | 强一致 |
| 多租户 | 库/索引隔离 | 集合隔离 | namespace | collection | schema |
| 运维成本 | 低 | 低 | 极低（托管） | 高 | 低（PG 内） |
| 生态集成 | LangChain/LlamaIndex | LangChain/LlamaIndex | 全生态 | 全生态 | LangChain |
| 成本 | 自部署硬件 | 自部署硬件 | 按 PU/s | 自部署硬件 | 与 PG 共享 |

**选型建议：**

- 数据规模 < 1000w 且对延迟敏感 → Redis
- 极大规模（>1亿）+ 分布式 → Milvus / Pinecone
- 已有 PostgreSQL 想加向量能力 → pgvector
- 快速 PoC / 本地开发 → Chroma

## 八、性能优化

### 8.1 HNSW 调参决策表

| 现象 | 调整方向 | 影响 |
|------|---------|------|
| 召回率低 | ↑ EF_RUNTIME (10→50) | 延迟↑ |
| 召回率低 | ↑ M (16→32) | 内存↑、建图慢 |
| 召回率低 | ↑ EF_CONSTRUCTION (200→400) | 构建慢、内存↑ |
| 延迟高 | ↓ EF_RUNTIME | 召回↓ |
| 内存高 | ↓ M | 召回↓ |
| 构建慢 | ↓ EF_CONSTRUCTION | 索引质量↓ |

### 8.2 批量插入优化

```python
import asyncio
import numpy as np
from redis.asyncio import Redis as AsyncRedis

async def batch_insert_pipeline(
    client: AsyncRedis, docs: list[dict], batch: int = 1000
):
    """使用 pipeline 批量 HSET，比逐条快 10x+"""
    total = len(docs)
    for i in range(0, total, batch):
        async with client.pipeline(transaction=False) as pipe:
            for j, doc in enumerate(docs[i:i+batch], start=i):
                key = f"rag:{j}"
                mapping = {
                    "content": doc["content"],
                    "source": doc.get("source", ""),
                    "embedding": np.array(
                        doc["embedding"], dtype=np.float32
                    ).tobytes(),
                }
                pipe.hset(key, mapping=mapping)
            await pipe.execute()
        if i + batch < total:
            print(f"progress: {i+batch}/{total}")

# 使用
async def main():
    client = AsyncRedis.from_url("redis://localhost:6379")
    docs = [{"content": f"doc {i}", "embedding": [0.1]*1536} for i in range(10000)]
    await batch_insert_pipeline(client, docs, batch=500)

asyncio.run(main())
```

### 8.3 混合检索（FTS + Vector RRF）

```python
from redisvl.query import VectorQuery, TextQuery
from redisvl.index import SearchIndex

def hybrid_search(index: SearchIndex, query_text: str, query_vec: list[float],
                  k: int = 5, alpha: float = 0.5):
    """RRF (Reciprocal Rank Fusion) 融合 BM25 与向量结果"""
    # 1. 全文检索
    text_q = TextQuery(text=query_text, text_field_name="content",
                       num_results=k*2, return_fields=["content"])
    text_results = index.query(text_q)

    # 2. 向量检索
    vec_q = VectorQuery(vector=query_vec, vector_field_name="embedding",
                        query_type="KNN", num_results=k*2,
                        return_fields=["content"])
    vec_results = index.query(vec_q)

    # 3. RRF 融合：score = Σ 1/(rank + 60)
    scores: dict[str, float] = {}
    for rank, r in enumerate(text_results, 1):
        key = r.get("id", "")
        scores[key] = scores.get(key, 0) + alpha * (1 / (rank + 60))
    for rank, r in enumerate(vec_results, 1):
        key = r.get("id", "")
        scores[key] = scores.get(key, 0) + (1 - alpha) * (1 / (rank + 60))

    # 4. 排序取 Top-K
    sorted_keys = sorted(scores, key=lambda x: -scores[x])[:k]
    all_map = {r.get("id"): r for r in text_results + vec_results}
    return [all_map[k] for k in sorted_keys if k in all_map]
```

### 8.4 索引监控

```bash
# 查看索引信息
FT.INFO doc_idx

# 关键指标：
# - num_docs: 已索引文档数
# - num_records: 总记录数
# - bytes_used: 索引内存占用
# - vector_index_spec: 向量参数
```

```python
# Python 监控
def index_stats(client, index_name: str):
    info = client.execute_command("FT.INFO", index_name)
    return {
        "num_docs": info[info.index("num_docs") + 1],
        "bytes_used": info[info.index("bytes_used") + 1],
        "vector_dims": info[info.index("vector_index_spec") + 1],
    }
```

## 九、实战：企业知识库 RAG 完整实现

### 9.1 项目结构

```
rag_kb/
├── config.py          # 配置
├── ingest.py          # 文档摄入
├── retriever.py       # 检索层
├── chain.py           # RAG Chain
├── main.py            # 入口
└── data/              # 知识库原始文档
    ├── handbook.pdf
    └── faq.md
```

### 9.2 配置文件

```python
# config.py
import os
from dataclasses import dataclass

@dataclass
class Settings:
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    index_name: str = "enterprise_kb"
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536
    llm_model: str = "gpt-4o-mini"
    chunk_size: int = 500
    chunk_overlap: int = 50
    top_k: int = 5

settings = Settings()
```

### 9.3 文档摄入

```python
# ingest.py
import hashlib
from langchain_community.document_loaders import (
    PyPDFLoader, TextLoader, DirectoryLoader
)
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_openai import OpenAIEmbeddings
from langchain_redis import RedisConfig, RedisVectorStore
from config import settings

def load_directory(path: str):
    loader = DirectoryLoader(path, glob="**/*.{pdf,txt,md}",
                             show_progress=True)
    return loader.load()

def split_docs(docs):
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        separators=["\n\n", "\n", "。", "！", "？", "；", " ", ""],
    )
    return splitter.split_documents(docs)

def build_vector_store(docs):
    embeddings = OpenAIEmbeddings(
        model=settings.embedding_model,
        api_key=settings.openai_api_key,
    )
    config = RedisConfig(
        index_name=settings.index_name,
        redis_url=settings.redis_url,
        key_prefix="ent_kb",
        metadata_schema=[
            {"name": "source", "type": "text"},
            {"name": "page", "type": "numeric"},
            {"name": "doc_hash", "type": "tag"},
        ],
    )
    # 添加 doc_hash 防止重复
    for d in docs:
        d.metadata["doc_hash"] = hashlib.md5(
            d.page_content.encode()
        ).hexdigest()
    return RedisVectorStore.from_documents(
        documents=docs, embedding=embeddings, config=config,
    )

def ingest(path: str = "./data"):
    docs = load_directory(path)
    chunks = split_docs(docs)
    print(f"加载 {len(docs)} 文档 → 分块 {len(chunks)}")
    vs = build_vector_store(chunks)
    print(f"已写入 Redis 索引 {settings.index_name}")
    return vs

if __name__ == "__main__":
    ingest()
```

### 9.4 检索层（带重排）

```python
# retriever.py
from langchain_redis import RedisConfig, RedisVectorStore
from langchain_openai import OpenAIEmbeddings
from langchain_core.documents import Document
from config import settings

def get_vector_store():
    embeddings = OpenAIEmbeddings(
        model=settings.embedding_model, api_key=settings.openai_api_key,
    )
    config = RedisConfig(
        index_name=settings.index_name,
        redis_url=settings.redis_url,
        key_prefix="ent_kb",
    )
    return RedisVectorStore(embeddings, config=config)

def retrieve(query: str, k: int = None):
    vs = get_vector_store()
    # MMR 检索，多样性
    docs = vs.max_marginal_relevance_search(
        query=query, k=k or settings.top_k, fetch_k=20, lambda_mult=0.5,
    )
    return docs

def retrieve_with_score(query: str, k: int = None):
    vs = get_vector_store()
    return vs.similarity_search_with_score(query, k=k or settings.top_k)
```

### 9.5 RAG Chain

```python
# chain.py
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough, RunnableLambda
from langchain_openai import ChatOpenAI
from retriever import retrieve
from config import settings

SYSTEM_PROMPT = """你是企业知识库助手。基于下方上下文回答用户问题。

规则：
1. 仅基于上下文，禁止编造
2. 引用来源：[来源: filename.pdf, page N]
3. 上下文不足时回答"信息不足以回答该问题"
4. 中文回答，结构化（要点 + 简要展开）
"""

prompt = ChatPromptTemplate.from_messages([
    ("system", SYSTEM_PROMPT),
    ("human", "上下文：\n{context}\n\n问题：{question}"),
])

def format_docs(docs):
    parts = []
    for i, d in enumerate(docs, 1):
        src = d.metadata.get("source", "unknown")
        page = d.metadata.get("page", "?")
        parts.append(f"[{i}] 来源: {src}, page {page}\n{d.page_content}")
    return "\n\n".join(parts)

def build_rag_chain():
    llm = ChatOpenAI(
        model=settings.llm_model, temperature=0,
        api_key=settings.openai_api_key,
    )
    return (
        {
            "context": RunnableLambda(lambda q: retrieve(q)) | format_docs,
            "question": RunnablePassthrough(),
        }
        | prompt
        | llm
        | StrOutputParser()
    )

rag = build_rag_chain()
```

### 9.6 入口

```python
# main.py
import sys
from chain import rag

def main():
    if len(sys.argv) > 1 and sys.argv[1] == "ingest":
        from ingest import ingest
        ingest(sys.argv[2] if len(sys.argv) > 2 else "./data")
        return

    print("企业知识库助手（输入 exit 退出）")
    while True:
        q = input("\n用户 > ").strip()
        if q.lower() in {"exit", "quit", "q"}:
            break
        if not q:
            continue
        print("\n助手 > ", end="", flush=True)
        for chunk in rag.stream(q):
            print(chunk, end="", flush=True)
        print()

if __name__ == "__main__":
    main()
```

### 9.7 运行

```powershell
conda activate langchain_xm
# 1. 摄入文档
python main.py ingest ./data

# 2. 交互问答
python main.py
# 用户 > Redis HNSW 的 EF_RUNTIME 参数如何调整？
```

### 9.8 验证清单

- [ ] Redis 中 `FT.INFO enterprise_kb` 显示 `num_docs > 0`
- [ ] `vector_store.similarity_search("测试", k=3)` 返回 3 条结果
- [ ] RAG 答案中含 `[来源: ...]` 引用
- [ ] 流式输出正常（字符逐个返回）
- [ ] 输入无关问题时回答"信息不足"

## 十、常见问题与排错

| 现象 | 原因 | 解决 |
|------|------|------|
| `FT.CREATE` 报 unknown command | 未启用 RediSearch | 使用 `redis-stack` 镜像 |
| 索引创建后查不到数据 | prefix 不匹配 | 检查 `key_prefix` 与 hash key |
| 向量维度不匹配 | Embedding 模型与 schema DIM 不一致 | 重新创建索引或换模型 |
| 召回率低 | EF_RUNTIME 太小或 chunk 太大 | 调参 + 优化分割策略 |
| 插入慢 | 单条写入 | 改用 pipeline 批量 |
| 内存暴涨 | M 太大或 chunk 过多 | 调小 M，重切 chunk |

## 小思考题帮你巩固

1. **HNSW 和 FLAT 分别适合什么规模/场景？召回率与延迟怎么权衡？**（提示：FLAT=暴力精确 100% 召回但 O(N)；HNSW=近似但 O(logN)，M/EF 调召回/延迟，见 1.3。）
2. **Redis 向量存储（内存）与 Milvus/Pinecone（外部）的边界在哪？什么情况该迁专用库？**（提示：部署简单、混合检索/缓存同库是 Redis 优势；千万级/超低延迟/大规模分布式检索要专用向量库，见七 对比。）
3. **"向量检索"与"关键词检索"为什么常常要混合？RRF 做了什么？**（提示：语义（同义/口语）与关键词（专名/编号）互补；RRF 按两路排名融合取并集，见性能优化。）
4. **Redis 8 的 Vector Sets（VADD/VSIM）与经典 RediSearch 向量索引的使用差别是什么？**（提示：VADD 免建索引、命令内建直接管理向量集；FT 索引支持全文+向量混合与 RedisVL 封装，两者按需选，见 1.4。）
5. **RAG 链路里 Embedding 用 OpenAI 还是本地 BGE，对最终效果的关键差异是什么？**（提示：OpenAI 需 Key/联网、维度大；本地 BGE/SBERT 免 Key、可离线；效果差异主要来自"检索召回"与"与文档语言/领域匹配"，见四。）

<details>
<summary>参考答案（点击展开）</summary>

1. FLAT 线性扫描、100% 精确召回，适合 <10w 向量的小规模（建库无参数、快）；HNSW 层次化图索引近似检索 O(logN)，适合 >10w 大规模与低延迟场景，召回率由 M（连接数）、EF_CONSTRUCTION（建图）、EF_RUNTIME（查询）权衡——对"差一点召回可以接受、要快"的业务选 HNSW，对"必须全部召回"选 FLAT。
2. Redis 向量适合"中量级（百万内）+ 与业务缓存/其他数据同库 + 内存检索毫秒级 + 部署简单"；超出（千万级、跨机房、需分布式搜索/向量专属优化）转 Milvus/Pinecone/Weaviate；若已在用 PostgreSQL 且量不大，pgvector 免新组件也是备选（见 1.2/七）。
3. 向量检索擅长"语义近似"（同义词、口语化提问），关键词（全文/专名/编号/型号）有时更准；混合检索两路结果做融合（Redis 原生 RRF）取"你中有我、我中有你"的并集，通常召回明显好于单路——这就是为什么生产 RAG 常"向量 + 全文双路"。
4. Vector Sets：一条 `VADD` 就入集（可带 METADATA），`VSIM` 直接 K 近邻 + FILTER——免建索引、命令内建，适合"纯向量、要省事"；RediSearch 经典做法要先 `FT.CREATE` 声明 schema 再插 Hash，优势是能同时做全文 + 向量（RRF 混合）且封装成熟（RedisVL/langchain_redis）。小项目/原型走 VADD 最快；要混合检索走 FT。
5. OpenAI embedding 质量稳、但要 Key/联网/有成本与延迟；本地 BGE/SBERT 免 Key、离线可用、可针对领域微调。关键差异不是模型"好/差"，而是"你的文档语言/领域与 embedding 模型是否匹配、切分质量、检索阈值"，以及团队是否有外网 Key。工程上先跑通用任意一个，再在 BGE/OpenAI 间做检索质量对比。

</details>

## 📖 深入阅读

- [Redis 官方文档 - Vector Search](https://redis.io/docs/latest/develop/interact/search-and-query/query/vector-search/)
- [RedisVL GitHub](https://github.com/redis/redis-vl-python)
- [LangChain Redis 集成](https://python.langchain.com/docs/integrations/vectorstores/redis/)
- [HNSW 原始论文](https://arxiv.org/abs/1603.09320)
- [RRF 论文 - Reciprocal Rank Fusion](https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf)

## 本章小结

- Redis 7+ 通过 RediSearch 模块原生支持 HNSW / FLAT 向量索引，延迟毫秒级
- RedisVL 提供低层灵活控制，langchain_redis.RedisVectorStore 提供高层 RAG 抽象
- 三类 Embedding（OpenAI / BGE / SBERT）适配不同场景，中文生产首选 BGE-large-zh
- 完整 RAG Pipeline 六步：加载 → 分割 → Embedding → 存储 → 检索 → 生成
- HNSW 调参三要素：M / EF_CONSTRUCTION / EF_RUNTIME，需在召回 / 延迟 / 内存间权衡
- 生产优化：pipeline 批量插入、RRF 混合检索、MMR 多样性去重

## 下一步导航

- 深入 Agent 记忆与工具调用 → [17 LangChain v1.4 与 Redis](../17_LangChain_v1.4与Redis/17_LangChain_v1.4与Redis.md)
- 探索 MCP 协议与工具集成 → [18 Redis MCP Server 与工具集成](../18_Redis_MCP_Server与工具集成/18_Redis_MCP_Server与工具集成.md)
- 回顾缓存模式与一致性 → [09 缓存模式与实战](../09_缓存模式与实战/09_缓存模式与实战.md)
- 容器化部署 redis-stack → [14 Redis 与 Docker 容器化部署](../14_Redis与Docker容器化部署/14_Redis与Docker容器化部署.md)
