# LangChain 框架研究笔记

## 一、LangChain 是什么

**LangChain** 是一个开源框架，2022年10月由 Harrison Chase 创建，旨在简化基于大语言模型（LLM）的应用程序开发。它提供了一套标准化的接口和工具，将 LLM 与外部数据源、API 和传统系统连接起来，使开发者能够快速构建复杂的 AI 应用。

### 核心理念
- **模块化**：将 LLM 应用的各个组件解耦为可复用模块
- **可组合性**：通过 Chain 将多个组件串联成复杂工作流
- **抽象统一**：为不同 LLM 提供商（OpenAI、Anthropic、Google 等）提供统一接口

### 发展历程
- 2022.10：由 Harrison Chase 在 GitHub 上开源发布
- 2023.02：获得 Benchmark 种子轮投资
- 2023.04：发布 LangChain v0.1，引入 LCEL
- 2023.10：发布 LangGraph 和 LangServe
- 2024.01：LangChain v0.2 重大更新，改进 LCEL 和 Tool Calling

## 二、核心功能模块

### 1. Models（模型封装）

LangChain 提供了三层模型抽象：

| 抽象层 | 描述 | 典型实现 |
|--------|------|---------|
| **LLMs** | 纯文本输入/输出模型 | OpenAI、LLaMA、ChatGLM |
| **Chat Models** | 基于消息列表的对话模型 | GPT-4、Claude、Gemini |
| **Embedding Models** | 文本→向量的嵌入模型 | OpenAIEmbeddings、HuggingFaceEmbeddings |

所有模型通过统一接口调用，切换供应商只需更改模型类名。

### 2. Prompts（提示词管理）

模板系统支持变量注入、少样本学习和输出格式化：

- **PromptTemplate**：基础模板，如 `"请翻译这句话：{text}"`
- **ChatPromptTemplate**：消息角色模板（system/human/ai）
- **FewShotPromptTemplate**：示例选择器+模板组合
- **PipelinePromptTemplate**：多模板流水线
- **Output Parsers**：格式化输出（JSON、Pydantic、Comma Separated List、Datetime 等）

### 3. Chains（链式调用）

Chain 是 LangChain 的核心抽象，将多个组件串联成处理流水线：

- **LLMChain**：最基础链，Prompt + LLM
- **SequentialChain**：顺序执行，前输出=后输入
- **RouterChain**：条件路由，根据输入内容分发到不同子链
- **TransformChain**：纯数据转换（无需 LLM）
- **LCEL（LangChain Expression Language）**：使用 `|` 操作符声明式组装链
  ```python
  chain = prompt_template | model | output_parser
  ```

### 4. Agents（智能代理）

Agent 实现了 ReAct（Reasoning + Acting）范式，LLM 自主推理并调用工具：

- **Tool**：工具抽象（计算器、搜索引擎、API 调用等）
- **Agent Types**：OpenAI Tools Agent、ReAct Agent、Conversational Agent
- **AgentExecutor**：循环执行器，推理→调用→观察→再推理
- **Toolkits**：预定义工具包（SQL、GitHub、文件系统等）

### 5. Memory（记忆/上下文管理）

（详见第三部分详细分析）

### 6. Retrieval（检索增强生成 RAG）

完整 RAG 管道：

```
文档 → 加载(Document Loader) → 分块(Text Splitter)
                                   ↓
用户查询 → 嵌入 → 向量检索(Vector Store) → 相关文档
                                   ↓
                          Prompt + LLM → 生成回答
```

关键组件：
- **Document Loaders**：PDF、HTML、CSV、Markdown、数据库等 100+ 格式
- **Text Splitters**：RecursiveCharacterTextSplitter、MarkdownHeaderSplitter 等
- **Vector Stores**：Chroma(本地)、FAISS(内存)、Pinecone(云)、Weaviate、Qdrant
- **Retrievers**：相似度检索、MMR(Max Marginal Relevance)、EnsembleRetriever
- **Indexing**：文档索引管道，支持增量更新

## 三、上下文管理（Memory）深入分析

### 为什么需要 Memory？

LLM 天然无状态——每次 API 调用相互独立。Memory 机制在 LLM 应用的两侧工作：

```
用户输入 → [Memory 注入历史] → LLM → [Memory 存储本轮对话] → 输出
```

### 主要 Memory 类型详解

#### 1. ConversationBufferMemory
**原理**：将所有对话历史以消息列表形式缓存，每次调用时注入完整历史。
```python
memory = ConversationBufferMemory(return_messages=True)
```
- ✅ 简单直接，无信息丢失
- ❌ 长对话后 token 开销巨大

#### 2. ConversationBufferWindowMemory
**原理**：只保留最近 k 轮对话，超出部分丢弃。
```python
memory = ConversationBufferWindowMemory(k=5, return_messages=True)
```
- ✅ Token 消耗可控
- ⚠️ k=5~10 适合多数场景
- ❌ 丢失早期关键信息

#### 3. ConversationSummaryMemory
**原理**：每次对话后自动调用 LLM 对历史进行总结，只保留摘要。
```python
memory = ConversationSummaryMemory(llm=llm)
```
- ✅ 极大节省 token
- ❌ 依赖 LLM 总结质量，有额外 API 调用
- ❌ 摘要可能丢失细节

#### 4. ConversationSummaryBufferMemory（推荐）
**原理**：混合策略——近期对话保留精确记录，早期对话压缩为摘要。当 token 数超过 max_token_limit 时触发压缩。
```python
memory = ConversationSummaryBufferMemory(
    llm=llm,
    max_token_limit=2000,
    return_messages=True
)
```
- ✅ 精确+摘要的平衡方案
- ✅ 适合复杂长对话

#### 5. ConversationTokenBufferMemory
**原理**：按 token 数量而非轮次截断，超过阈值丢弃最早消息。
```python
memory = ConversationTokenBufferMemory(
    llm=llm,
    max_token_limit=2000
)
```
- ✅ 精确控制 token 预算
- ❌ 可能截断不完整的对话轮次

#### 6. VectorStoreRetrieverMemory
**原理**：将对话历史存入向量数据库，每次从历史中语义检索最相关部分。
```python
memory = VectorStoreRetrieverMemory(
    retriever=vectorstore.as_retriever(),
    memory_key="chat_history",
    return_messages=True
)
```
- ✅ 支持超长/跨会话记忆
- ✅ 语义检索比滑动窗口更智能
- ❌ 需要维护向量数据库

#### 7. PostgresChatMessageHistory
**原理**：将消息持久化到 PostgreSQL 数据库。
```python
history = PostgresChatMessageHistory(
    session_id="session_001",
    connection_string="postgresql://..."
)
```
- ✅ 生产级持久化
- ✅ 支持会话管理

### Memory 组合使用策略

```
┌─ 短期（当前会话）──┐
│ BufferMemory      │  ← 精确历史
│ WindowMemory      │  ← 滑动窗口
└───────────────────┘
        ↓ 会话结束时压缩
┌─ 中期（用户画像）──┐
│ SummaryMemory     │  ← 会话摘要
│ EntityMemory      │  ← 实体信息
└───────────────────┘
        ↓ 持久化存储
┌─ 长期（知识库）───┐
│ VectorStoreMemory│  ← 语义检索
│ 数据库持久化      │  ← 完整归档
└───────────────────┘
```

### 最佳实践建议

| 对话复杂度 | 推荐 Memory 配置 |
|-----------|----------------|
| 简单问答（≤3轮） | ConversationBufferMemory |
| 常规客服（5-15轮） | ConversationBufferWindowMemory(k=10) |
| 深度讨论（>20轮） | ConversationSummaryBufferMemory(max_token_limit=2000) |
| 知识密集型 | VectorStoreRetrieverMemory |
| 生产系统 | BufferMemory + 数据库持久化 |

## 四、生态系统工具

### LangSmith
- LLM 应用可观测性平台
- **Tracing**：完整记录每次 LLM 调用链
- **Evaluation**：自动评估、回归测试
- **Hub**：共享 Prompt 和 Chain

### LangServe
- 将 Chain 部署为 REST API
- 自动生成 FastAPI 路由
- 支持异步流式响应

### LangGraph
- 图状态机编排 Agent
- 支持循环、分支、条件
- 适合复杂多 Agent 工作流

## 五、主要应用场景

1. **智能客服/对话机器人**：Memory + RAG + 多轮对话
2. **企业知识库问答**：RAG Pipeline
3. **数据分析助手**：Agent + SQL/Pandas Tool
4. **代码生成与审查**：Agent + 代码解释器
5. **自动化报告生成**：SequentialChain + 模板
6. **多语言翻译系统**：Chain + 自定义 Prompt
7. **信息抽取清洗**：LLMChain + Output Parser
8. **教学辅导**：Memory + 自适应 Chain

## 参考来源
- LangChain 官方文档：https://python.langchain.com/docs
- LangChain GitHub：https://github.com/langchain-ai/langchain
- LangSmith 文档：https://docs.smith.langchain.com
- LangGraph 文档：https://langchain-ai.github.io/langgraph
