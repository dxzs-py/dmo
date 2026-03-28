# 🔄 LangChain V1.0.0 重构说明

## 📅 重构信息

- **日期：** 2025-11-05
- **原因：** 适配 LangChain V1.0.0 的全新 API
- **参考文档：**
  - https://docs.langchain.com/oss/python/langchain/agents
  - https://reference.langchain.com/python/langchain/agents/
  - https://reference.langchain.com/python/langchain/models/

## 🎯 重大变更

### 1. Agent 创建方式完全改变

#### 旧方式（V0.x）
```python
from langchain.agents import create_tool_calling_agent, AgentExecutor
from langchain_core.prompts import ChatPromptTemplate

# 创建 prompt
prompt = ChatPromptTemplate.from_messages([...])

# 创建 agent
agent = create_tool_calling_agent(llm=model, tools=tools, prompt=prompt)

# 创建 executor
agent_executor = AgentExecutor(agent=agent, tools=tools, ...)
```

#### 新方式（V1.0.0）✅
```python
from langchain.agents import create_agent

# 一步创建，返回 CompiledStateGraph
graph = create_agent(
    model="openai:gpt-4o",  # 字符串标识符或 BaseChatModel 实例
    tools=tools,
    system_prompt="你是一个助手",
    debug=False,
)
```

**关键区别：**
- ✅ 不再需要 `create_tool_calling_agent`
- ✅ 不再需要 `AgentExecutor`
- ✅ 不再需要手动创建 `ChatPromptTemplate`
- ✅ `create_agent` 直接返回 `CompiledStateGraph`（基于 LangGraph）
- ✅ 模型可以用字符串标识符（如 "openai:gpt-4o"）

### 2. 输入格式变化

#### 旧方式
```python
agent_executor.invoke({
    "input": "你好",
    "chat_history": [...],
})
```

#### 新方式✅
```python
graph.invoke({
    "messages": [
        HumanMessage(content="你好"),
        # ... 其他消息
    ],
})
```

**关键区别：**
- ✅ 使用 `messages` 键而不是 `input` 和 `chat_history`
- ✅ 直接传递消息列表

### 3. 输出格式变化

#### 旧方式
```python
result = agent_executor.invoke(...)
output = result["output"]  # 字符串
```

#### 新方式✅
```python
result = graph.invoke(...)
messages = result["messages"]  # 消息列表
# 需要提取最后一条 AI 消息
for msg in reversed(messages):
    if isinstance(msg, AIMessage):
        output = msg.content
        break
```

### 4. 流式输出变化

#### 旧方式
```python
for chunk in agent_executor.stream(...):
    if "output" in chunk:
        print(chunk["output"])
```

#### 新方式✅
```python
for chunk in graph.stream(..., stream_mode="messages"):
    # chunk 是 (message, metadata) 元组
    if isinstance(chunk, tuple):
        message, metadata = chunk
        if isinstance(message, AIMessage):
            print(message.content)
```

**流式模式：**
- `"messages"` - 流式返回消息（推荐）
- `"updates"` - 返回状态更新
- `"values"` - 返回完整状态值

## 📝 已重构的文件

### 1. `agents/base_agent.py` ✅

**主要变更：**
- 使用 `langchain.agents.create_agent` 替代 `create_tool_calling_agent` + `AgentExecutor`
- `__init__` 方法简化，直接调用 `create_agent`
- `model` 参数支持字符串标识符（如 "openai:gpt-4o"）
- 移除 `streaming`、`max_iterations`、`max_execution_time`、`verbose` 参数
- 添加 `debug` 参数（对应 `create_agent` 的 debug）
- `invoke`/`stream`/`ainvoke`/`astream` 方法适配新的输入/输出格式

**新增属性：**
```python
self.graph  # CompiledStateGraph 实例（替代 agent_executor）
```

**示例：**
```python
# 创建 Agent
agent = BaseAgent(
    model="openai:gpt-4o",  # 或 None 使用默认配置
    tools=[get_current_time, calculator],
    prompt_mode="default",
    debug=False,
)

# 调用
response = agent.invoke("现在几点？")

# 流式调用
for chunk in agent.stream("讲个笑话"):
    print(chunk, end="", flush=True)
```

### 2. `core/models.py` ✅

**新增函数：**
```python
def get_model_string(
    model_name: Optional[str] = None,
    provider: str = "openai",
) -> str:
    """
    获取模型标识符字符串
    
    返回格式：" provider:model_name"
    例如："openai:gpt-4o"
    """
```

**用途：**
- 为 `create_agent` 生成正确的模型标识符字符串
- 支持多个提供商（openai, anthropic, etc.）

## 🔧 待重构的文件

### 3. `api/routers/chat.py` ⏳

**需要变更：**
- 适配新的 Agent 调用方式
- 更新流式输出处理逻辑

### 4. `scripts/demo_cli.py` ⏳

**需要变更：**
- 适配新的 Agent 创建参数
- 移除 `streaming` 参数相关逻辑

### 5. `scripts/test_basic.py` ⏳

**需要变更：**
- 更新测试用例以适配新 API

## 📚 新 API 参考

### `create_agent` 参数

根据 [官方文档](https://reference.langchain.com/python/langchain/agents/#langchain.agents.create_agent)：

```python
create_agent(
    model: str | BaseChatModel,           # 模型标识符或实例
    tools: Sequence[BaseTool] | None,     # 工具列表
    system_prompt: str | None = None,     # 系统提示词
    middleware: Sequence[...] = (),       # 中间件
    response_format: ... | None = None,   # 响应格式
    state_schema: type[...] | None = None,# 状态 schema
    context_schema: type[...] | None = None, # 上下文 schema
    checkpointer: Checkpointer | None = None, # 状态持久化
    store: BaseStore | None = None,       # 跨线程存储
    interrupt_before: list[str] | None = None, # 前置中断点
    interrupt_after: list[str] | None = None,  # 后置中断点
    debug: bool = False,                  # 调试模式
    name: str | None = None,              # Agent 名称
    cache: BaseCache | None = None,       # 缓存
) -> CompiledStateGraph
```

### 模型标识符格式

支持的格式：
- `"openai:gpt-4o"` - OpenAI GPT-4o
- `"openai:gpt-4o-mini"` - OpenAI GPT-4o Mini
- `"anthropic:claude-3-5-sonnet-20241022"` - Anthropic Claude
- `"google:gemini-pro"` - Google Gemini
- 或直接传递 `BaseChatModel` 实例

### CompiledStateGraph 方法

```python
# 同步调用
result = graph.invoke({"messages": [...]})

# 异步调用
result = await graph.ainvoke({"messages": [...]})

# 流式调用
for chunk in graph.stream({"messages": [...]}, stream_mode="messages"):
    # 处理 chunk

# 异步流式调用
async for chunk in graph.astream({"messages": [...]}, stream_mode="messages"):
    # 处理 chunk
```

## 🎯 迁移检查清单

- [x] 更新 `agents/base_agent.py`
  - [x] 使用 `create_agent` 替代旧 API
  - [x] 适配新的输入/输出格式
  - [x] 更新流式输出处理
  - [x] 更新文档字符串

- [x] 更新 `core/models.py`
  - [x] 添加 `get_model_string` 函数
  - [x] 更新文档说明

- [ ] 更新 `api/routers/chat.py`
  - [ ] 适配新的 Agent 接口
  - [ ] 更新流式响应处理

- [ ] 更新 `scripts/demo_cli.py`
  - [ ] 移除 `streaming` 参数
  - [ ] 适配新的 Agent 创建方式

- [ ] 更新 `scripts/test_basic.py`
  - [ ] 更新测试用例

- [ ] 更新文档
  - [ ] README.md
  - [ ] QUICKSTART.md
  - [ ] STAGE1_COMPLETION.md

## 💡 最佳实践

### 1. 使用字符串标识符

**推荐：**
```python
agent = BaseAgent(model="openai:gpt-4o")
```

**原因：**
- 自动从环境变量读取 API Key
- 简化配置管理
- 支持多个提供商

### 2. 利用 debug 模式

```python
agent = BaseAgent(debug=True)
```

**用途：**
- 查看详细的执行日志
- 调试工具调用
- 理解 Agent 执行流程

### 3. 使用 checkpointer 实现记忆

```python
from langgraph.checkpoint.memory import MemorySaver

agent = BaseAgent(
    checkpointer=MemorySaver(),
)
```

**用途：**
- 持久化对话状态
- 实现多轮对话记忆
- 支持对话恢复

## 🔗 参考资源

- [LangChain Agents 文档](https://docs.langchain.com/oss/python/langchain/agents)
- [create_agent API 参考](https://reference.langchain.com/python/langchain/agents/)
- [LangChain Models 文档](https://docs.langchain.com/oss/python/langchain/models)
- [LangGraph 文档](https://docs.langchain.com/oss/python/langgraph)

## 🎉 总结

LangChain V1.0.0 的新 `create_agent` API 大大简化了 Agent 的创建过程：

**优点：**
- ✅ 更简洁的 API
- ✅ 基于 LangGraph 的强大功能
- ✅ 更好的状态管理
- ✅ 内置的流式支持
- ✅ 支持中间件和扩展

**注意事项：**
- ⚠️ 输入/输出格式完全不同
- ⚠️ 需要适配所有调用代码
- ⚠️ 流式输出处理逻辑改变

---

**最后更新：** 2025-11-05
**状态：** 进行中（3/5 完成）

