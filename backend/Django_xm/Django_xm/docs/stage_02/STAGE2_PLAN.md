# 第 2 阶段开发计划：RAG 知识库模块

## 📋 阶段目标

**第 2 阶段：RAG 知识库模块（向量库 + Retrievers + RAG Agent）**

实现一个完整的 RAG（Retrieval-Augmented Generation）系统，支持：
- 文档加载和处理（PDF、Markdown、TXT、HTML 等）
- 文本分块和向量化
- 向量存储和检索
- RAG Agent（基于检索的智能问答）
- HTTP API 接口
- CLI 工具

## 🎯 核心功能

### 1. 文档加载器（Document Loaders）
- **支持的格式**：
  - PDF 文档
  - Markdown/MDX 文件
  - 纯文本文件
  - HTML 文件
  - JSON 文件
  - 目录批量加载

- **功能特性**：
  - 自动格式检测
  - 元数据提取（文件名、路径、修改时间等）
  - 批量加载
  - 错误处理

### 2. 文本分块器（Text Splitters）
- **分块策略**：
  - RecursiveCharacterTextSplitter（递归字符分块）
  - MarkdownTextSplitter（Markdown 专用）
  - CharacterTextSplitter（字符分块）
  - TokenTextSplitter（Token 分块）

- **配置参数**：
  - chunk_size：分块大小（默认 1000）
  - chunk_overlap：重叠大小（默认 200）
  - 自定义分隔符

### 3. 向量存储（Vector Stores）
- **支持的向量库**：
  - InMemoryVectorStore（内存，开发测试用）
  - FAISS（本地持久化，推荐）
  - Chroma（可选）

- **Embedding 模型**：
  - OpenAI Embeddings（text-embedding-3-small/large）
  - 支持自定义 embedding 模型

### 4. 检索器（Retrievers）
- **检索策略**：
  - 相似度检索（Similarity Search）
  - MMR（最大边际相关性）检索
  - 相似度阈值过滤

- **配置参数**：
  - k：返回文档数量（默认 4）
  - score_threshold：相似度阈值
  - fetch_k：MMR 候选数量

### 5. RAG Agent
- **基于 LangChain 1.0.3 的 create_agent**
- **功能特性**：
  - 将 retriever 封装为 tool
  - 自动检索相关文档
  - 基于上下文生成回答
  - 引用来源文档
  - 支持流式输出

### 6. API 接口
- **索引管理**：
  - `POST /rag/index` - 创建索引
  - `GET /rag/index/list` - 列出所有索引
  - `DELETE /rag/index/{name}` - 删除索引
  - `GET /rag/index/{name}/stats` - 索引统计信息

- **文档管理**：
  - `POST /rag/documents/upload` - 上传文档
  - `POST /rag/documents/add-directory` - 添加目录
  - `GET /rag/documents/list` - 列出文档

- **查询接口**：
  - `POST /rag/query` - RAG 查询（非流式）
  - `POST /rag/query/stream` - RAG 查询（流式）
  - `POST /rag/search` - 纯检索（不生成回答）

### 7. CLI 工具
- **索引管理命令**：
  - `python scripts/rag_cli.py index create <name> <path>` - 创建索引
  - `python scripts/rag_cli.py index list` - 列出索引
  - `python scripts/rag_cli.py index delete <name>` - 删除索引

- **查询命令**：
  - `python scripts/rag_cli.py query <index_name> "<question>"` - 查询
  - `python scripts/rag_cli.py search <index_name> "<query>"` - 检索

- **交互模式**：
  - `python scripts/rag_cli.py interactive <index_name>` - 进入交互式问答

## 🏗️ 技术架构

### 模块结构
```
backend/rag/
├── __init__.py              # 模块初始化，导出核心接口
├── loaders.py               # 文档加载器
├── splitters.py             # 文本分块器
├── embeddings.py            # Embedding 模型封装
├── vector_stores.py         # 向量存储管理
├── retrievers.py            # 检索器封装
├── rag_agent.py             # RAG Agent 实现
├── index_manager.py         # 索引管理器
└── utils.py                 # 工具函数
```

### 数据流程
```
文档文件
  ↓
Document Loader（加载）
  ↓
Documents（文档对象）
  ↓
Text Splitter（分块）
  ↓
Chunks（文本块）
  ↓
Embeddings（向量化）
  ↓
Vector Store（存储）
  ↓
Retriever（检索）
  ↓
RAG Agent（生成回答）
  ↓
用户回答
```

## 📝 开发任务拆分

### 任务 1: 基础设施准备（30 分钟）
- [x] 创建 `rag/` 目录结构
- [x] 更新 `requirements.txt`，添加 RAG 相关依赖
- [x] 更新 `config/settings.py`，添加 RAG 配置
- [x] 创建测试数据目录 `backend/data/`

### 任务 2: 文档加载器实现（45 分钟）
- [x] 实现 `loaders.py`
  - [x] `load_document()` - 加载单个文档
  - [x] `load_directory()` - 加载目录
  - [x] `get_loader_for_file()` - 根据文件类型选择加载器
  - [x] 支持 PDF、Markdown、TXT、HTML、JSON
- [x] 编写单元测试
- [x] 创建测试文档

### 任务 3: 文本分块器实现（30 分钟）
- [x] 实现 `splitters.py`
  - [x] `get_text_splitter()` - 获取分块器
  - [x] `split_documents()` - 分块文档
  - [x] 支持多种分块策略
- [x] 编写单元测试

### 任务 4: Embeddings 封装（20 分钟）
- [x] 实现 `embeddings.py`
  - [x] `get_embeddings()` - 获取 embedding 模型
  - [x] 支持 OpenAI embeddings
  - [x] 缓存机制
- [x] 编写单元测试

### 任务 5: 向量存储实现（45 分钟）
- [x] 实现 `vector_stores.py`
  - [x] `create_vector_store()` - 创建向量库
  - [x] `load_vector_store()` - 加载向量库
  - [x] `save_vector_store()` - 保存向量库
  - [x] 支持 InMemory、FAISS
- [x] 实现 `index_manager.py`
  - [x] 索引的 CRUD 操作
  - [x] 索引元数据管理
  - [x] 索引持久化
- [x] 编写单元测试

### 任务 6: 检索器实现（30 分钟）
- [x] 实现 `retrievers.py`
  - [x] `create_retriever()` - 创建检索器
  - [x] `retriever_tool()` - 将检索器封装为工具
  - [x] 支持多种检索策略
- [x] 编写单元测试

### 任务 7: RAG Agent 实现（60 分钟）
- [x] 实现 `rag_agent.py`
  - [x] `create_rag_agent()` - 创建 RAG Agent
  - [x] 集成 retriever tool
  - [x] 支持流式输出
  - [x] 引用来源文档
  - [x] 对话历史管理
- [x] 编写单元测试

### 任务 8: API 接口实现（60 分钟）
- [x] 实现 `api/routers/rag.py`
  - [x] 索引管理接口
  - [x] 文档管理接口
  - [x] 查询接口
  - [x] 流式查询接口
- [x] 集成到 `http_server.py`
- [x] 编写 API 测试

### 任务 9: CLI 工具实现（45 分钟）
- [x] 实现 `scripts/rag_cli.py`
  - [x] 索引管理命令
  - [x] 查询命令
  - [x] 交互模式
- [x] 创建启动脚本 `start_rag_cli.sh`

### 任务 10: 测试和文档（60 分钟）
- [x] 创建测试数据集
- [x] 端到端测试
- [x] 编写使用文档
- [x] 编写 README
- [x] 编写学习总结

## 🔧 技术细节

### 1. LangChain 1.0.3 RAG 核心 API

#### Document Loaders
```python
from langchain_community.document_loaders import (
    PyPDFLoader,
    TextLoader,
    UnstructuredMarkdownLoader,
    DirectoryLoader,
)

# 加载 PDF
loader = PyPDFLoader("document.pdf")
documents = loader.load()

# 加载目录
loader = DirectoryLoader("./docs", glob="**/*.md")
documents = loader.load()
```

#### Text Splitters
```python
from langchain_text_splitters import (
    RecursiveCharacterTextSplitter,
    MarkdownTextSplitter,
)

# 递归字符分块
splitter = RecursiveCharacterTextSplitter(
    chunk_size=1000,
    chunk_overlap=200,
)
chunks = splitter.split_documents(documents)
```

#### Embeddings
```python
from langchain_openai import OpenAIEmbeddings

embeddings = OpenAIEmbeddings(
    model="text-embedding-3-small",
    api_key=settings.openai_api_key,
)
```

#### Vector Stores
```python
from langchain_community.vectorstores import FAISS
from langchain_core.vectorstores import InMemoryVectorStore

# 创建 FAISS 向量库
vector_store = FAISS.from_documents(
    documents=chunks,
    embedding=embeddings,
)

# 保存
vector_store.save_local("./indexes/my_index")

# 加载
vector_store = FAISS.load_local(
    "./indexes/my_index",
    embeddings=embeddings,
    allow_dangerous_deserialization=True,
)
```

#### Retrievers
```python
# 基本检索器
retriever = vector_store.as_retriever(
    search_type="similarity",
    search_kwargs={"k": 4},
)

# MMR 检索器
retriever = vector_store.as_retriever(
    search_type="mmr",
    search_kwargs={"k": 4, "fetch_k": 20},
)

# 相似度阈值检索器
retriever = vector_store.as_retriever(
    search_type="similarity_score_threshold",
    search_kwargs={"score_threshold": 0.5, "k": 4},
)
```

#### RAG Agent
```python
from langchain.agents import create_tool_calling_agent, AgentExecutor
from langchain.tools.retriever import create_retriever_tool

# 将 retriever 封装为 tool
retriever_tool = create_retriever_tool(
    retriever=retriever,
    name="knowledge_base",
    description="搜索知识库中的相关信息。用于回答关于文档内容的问题。",
)

# 创建 RAG Agent
agent = create_tool_calling_agent(
    llm=model,
    tools=[retriever_tool],
    prompt=prompt,
)

agent_executor = AgentExecutor(
    agent=agent,
    tools=[retriever_tool],
    verbose=True,
)
```

### 2. 配置参数

#### RAG 配置（添加到 settings.py）
```python
# ==================== RAG 配置 ====================
# Embedding 配置
embedding_model: str = "text-embedding-3-small"
embedding_batch_size: int = 100

# 文本分块配置
chunk_size: int = 1000
chunk_overlap: int = 200

# 向量库配置
vector_store_type: str = "faiss"  # faiss, inmemory, chroma
vector_store_path: str = "data/indexes"

# 检索配置
retriever_search_type: str = "similarity"  # similarity, mmr, similarity_score_threshold
retriever_k: int = 4
retriever_score_threshold: float = 0.5
retriever_fetch_k: int = 20

# RAG Agent 配置
rag_agent_max_iterations: int = 10
rag_agent_return_source_documents: bool = True
```

### 3. 数据目录结构
```
backend/data/
├── documents/           # 原始文档
│   ├── test/           # 测试文档
│   └── production/     # 生产文档
├── indexes/            # 向量索引
│   ├── test_index/
│   │   ├── index.faiss
│   │   └── metadata.json
│   └── production_index/
└── uploads/            # 用户上传的文档
```

## 📦 依赖包更新

需要添加到 `requirements.txt`：
```
# RAG 相关
langchain-text-splitters==0.4.1
faiss-cpu==1.9.0.post1          # FAISS 向量库（CPU 版本）
# faiss-gpu==1.9.0.post1        # FAISS GPU 版本（可选）

# 文档加载器
pypdf==5.1.0                    # PDF 加载
unstructured==0.17.3            # 通用文档加载
markdown==3.7                   # Markdown 支持
beautifulsoup4==4.12.3          # HTML 解析
lxml==5.3.0                     # XML 解析

# 文件处理
python-multipart==0.0.20        # 文件上传支持
aiofiles==24.1.0                # 异步文件操作
```

## 🎓 学习目标

### LangChain 核心概念
1. **Document Loaders** - 文档加载器的使用和自定义
2. **Text Splitters** - 文本分块策略和参数调优
3. **Embeddings** - 向量化模型的选择和使用
4. **Vector Stores** - 向量数据库的操作和持久化
5. **Retrievers** - 检索策略和优化
6. **RAG Pattern** - RAG 模式的实现和最佳实践
7. **Tool Integration** - 将 Retriever 集成到 Agent

### RAG 最佳实践
1. **文本分块策略** - 如何选择合适的 chunk_size 和 overlap
2. **Embedding 选择** - 不同 embedding 模型的对比
3. **检索优化** - 相似度搜索 vs MMR vs 阈值过滤
4. **上下文管理** - 如何控制检索到的上下文数量
5. **来源引用** - 如何在回答中引用来源文档
6. **性能优化** - 索引构建和查询的性能优化

### 工程实践
1. **索引管理** - 索引的创建、更新、删除
2. **元数据管理** - 文档元数据的提取和使用
3. **错误处理** - RAG 系统的错误处理策略
4. **API 设计** - RESTful API 的设计原则
5. **CLI 工具** - 命令行工具的设计和实现

## 📊 验收标准

### 功能完整性
- [x] 支持至少 3 种文档格式（PDF、Markdown、TXT）
- [x] 支持目录批量加载
- [x] 支持多种文本分块策略
- [x] 支持 FAISS 向量库
- [x] 支持多种检索策略
- [x] RAG Agent 能正确回答问题并引用来源
- [x] 提供完整的 HTTP API
- [x] 提供易用的 CLI 工具

### 代码质量
- [x] 所有代码有详细中文注释
- [x] 遵循 PEP 8 规范
- [x] 完整的类型提示
- [x] 完善的错误处理
- [x] 详细的日志记录
- [x] 单元测试覆盖核心功能

### 文档完整性
- [x] 完整的 README
- [x] API 文档
- [x] 使用示例
- [x] 学习总结
- [x] 最佳实践指南

### 性能要求
- [x] 索引构建速度合理（1000 文档 < 5 分钟）
- [x] 查询响应时间 < 2 秒
- [x] 支持流式输出，用户体验良好

## 🚀 开发流程

### 第 1 天：基础设施 + 文档加载（任务 1-2）
1. 创建目录结构
2. 更新依赖和配置
3. 实现文档加载器
4. 创建测试数据

### 第 2 天：文本处理 + 向量化（任务 3-5）
1. 实现文本分块器
2. 实现 Embeddings 封装
3. 实现向量存储
4. 实现索引管理器

### 第 3 天：检索 + RAG Agent（任务 6-7）
1. 实现检索器
2. 实现 RAG Agent
3. 端到端测试

### 第 4 天：API + CLI + 文档（任务 8-10）
1. 实现 HTTP API
2. 实现 CLI 工具
3. 编写文档
4. 完整测试

## 📚 参考资料

### LangChain 官方文档
- Retrieval: https://docs.langchain.com/oss/python/langchain/retrieval
- Document Loaders: https://reference.langchain.com/python/langchain_core/document_loaders/
- Text Splitters: https://reference.langchain.com/python/langchain_text_splitters/
- Embeddings: https://reference.langchain.com/python/langchain_core/embeddings/
- Vector Stores: https://reference.langchain.com/python/langchain_core/vectorstores/
- Retrievers: https://reference.langchain.com/python/langchain_core/retrievers/

### 最佳实践
- RAG 系统设计模式
- 向量数据库选择指南
- Embedding 模型对比
- 检索优化技巧

## 🎯 成功标准

完成第 2 阶段后，应该能够：

1. ✅ 从本地文件夹加载文档并创建向量索引
2. ✅ 通过 CLI 或 API 查询知识库
3. ✅ RAG Agent 能基于文档内容回答问题
4. ✅ 回答中包含来源文档引用
5. ✅ 支持流式输出，用户体验良好
6. ✅ 理解 RAG 的核心原理和最佳实践
7. ✅ 掌握 LangChain 的 RAG 相关 API

让我们开始吧！🚀

