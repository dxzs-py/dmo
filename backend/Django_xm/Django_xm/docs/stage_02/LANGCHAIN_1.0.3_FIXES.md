# LangChain 1.0.3 API 修复说明

## 问题描述

在使用 LangChain 1.0.3 时，遇到了以下导入错误：

1. `ModuleNotFoundError: No module named 'langchain.tools.retriever'`
2. `ImportError: cannot import name 'create_tool_calling_agent' from 'langchain.agents'`

## 原因分析

LangChain 1.0.3 对 API 进行了重大重构：

1. **工具相关 API 移动**：
   - 旧路径：`langchain.tools.retriever`
   - 新路径：`langchain_core.tools.retriever`

2. **Agent API 简化**：
   - 移除了 `create_tool_calling_agent` 和 `AgentExecutor`
   - 统一使用 `create_agent` API

## 修复方案

### 1. 修复 `retrievers.py` 的导入

**修改前**：
```python
from langchain.tools.retriever import create_retriever_tool as lc_create_retriever_tool
```

**修改后**：
```python
from langchain_core.tools.retriever import create_retriever_tool as lc_create_retriever_tool
```

### 2. 重写 `rag_agent.py` 使用新的 API

**修改前**：
```python
from langchain.agents import create_tool_calling_agent, AgentExecutor

def create_rag_agent(
    retriever: BaseRetriever,
    model: Optional[BaseChatModel] = None,
    ...
) -> AgentExecutor:
    # 创建 Agent
    agent = create_tool_calling_agent(
        llm=model,
        tools=tools,
        prompt=prompt,
    )
    
    # 创建 AgentExecutor
    agent_executor = AgentExecutor(
        agent=agent,
        tools=tools,
        max_iterations=max_iterations,
        verbose=verbose,
        ...
    )
    
    return agent_executor
```

**修改后**：
```python
from langchain.agents import create_agent

def create_rag_agent(
    retriever: BaseRetriever,
    model: Optional[str] = None,  # 改为字符串
    ...
):
    # 使用 LangChain 1.0.3 的 create_agent API
    agent = create_agent(
        model=model,  # 直接传入模型字符串，如 "openai:gpt-4o"
        tools=tools,
        system_prompt=system_prompt,
        **kwargs,
    )
    
    return agent
```

### 3. 更新 Agent 调用方式

**修改前**：
```python
# 查询
result = agent.invoke({"input": query})
print(result["output"])

# 流式
async for chunk in agent.astream({"input": query}):
    if "output" in chunk:
        print(chunk["output"], end="")
```

**修改后**：
```python
# 查询
result = agent.invoke(query)  # 直接传入字符串
print(result)

# 流式
async for chunk in agent.astream(query):
    print(chunk, end="")  # chunk 直接是内容
```

## 修改的文件列表

1. `rag/retrievers.py` - 修复 `create_retriever_tool` 导入
2. `rag/rag_agent.py` - 重写为使用 `create_agent` API
3. `api/routers/rag.py` - 更新 API 路由中的调用方式
4. `scripts/rag_cli.py` - 更新 CLI 工具中的调用方式

## 新的 API 使用示例

### 创建 RAG Agent

```python
from rag import create_rag_agent, create_retriever

# 创建检索器
retriever = create_retriever(vector_store)

# 创建 RAG Agent（使用默认模型）
agent = create_rag_agent(retriever)

# 或指定模型
agent = create_rag_agent(retriever, model="openai:gpt-4o")
```

### 查询

```python
# 同步查询
result = agent.invoke("什么是机器学习？")
print(result)

# 异步查询
result = await agent.ainvoke("什么是机器学习？")
print(result)
```

### 流式输出

```python
# 同步流式
for chunk in agent.stream("解释深度学习"):
    print(chunk, end="", flush=True)

# 异步流式
async for chunk in agent.astream("解释深度学习"):
    print(chunk, end="", flush=True)
```

## LangChain 1.0.3 API 参考

### create_agent

```python
from langchain.agents import create_agent

agent = create_agent(
    model="openai:gpt-4o",  # 模型字符串
    tools=[tool1, tool2],    # 工具列表
    system_prompt="...",     # 系统提示词
    # 其他可选参数
)
```

### create_retriever_tool

```python
from langchain_core.tools.retriever import create_retriever_tool

tool = create_retriever_tool(
    retriever=retriever,
    name="knowledge_base",
    description="搜索知识库"
)
```

## 优势

新的 API 更加简洁：

1. **统一的 Agent 创建**：不再区分 `create_tool_calling_agent` 等多种方式
2. **简化的调用**：直接传入字符串，不需要构建复杂的输入字典
3. **更好的流式支持**：流式输出直接返回内容，不需要额外处理

## 注意事项

1. **模型参数**：现在使用字符串格式，如 `"openai:gpt-4o"`
2. **输入格式**：直接传入字符串，不再使用 `{"input": query}` 格式
3. **输出格式**：返回值直接是字符串，不再是 `{"output": ...}` 字典
4. **流式输出**：chunk 直接是内容字符串

## 测试验证

修复后，可以正常导入和使用：

```bash
# 测试导入
python -c "from rag import create_rag_agent; print('✅ 导入成功')"

# 测试创建索引
python scripts/rag_cli.py index create test_docs data/documents/test

# 测试查询
python scripts/rag_cli.py query test_docs "什么是机器学习？"
```

## 下一步

需要安装额外的依赖：

```bash
# 安装 CLI 工具依赖
pip install rich click

# 安装 RAG 相关依赖
pip install -r requirements.txt
```

## 参考资料

- [LangChain 1.0.3 Agents 文档](https://docs.langchain.com/oss/python/langchain/agents)
- [LangChain Core Tools 参考](https://reference.langchain.com/python/langchain_core/tools/)
- [create_agent API 参考](https://reference.langchain.com/python/langchain/agents/)

