# 智能天气查询 + 上下文记忆功能

## 问题背景

在原有实现中存在两个问题：

1. **工具调用错误**：`'StructuredTool' object is not callable`
   - 原因：在 `@tool` 装饰的函数内部直接调用了另一个 `@tool` 装饰的函数
   - 被 `@tool` 装饰后，函数变成了 `StructuredTool` 对象，不能直接作为函数调用

2. **天气查询不智能**：
   - 用户问"明天天气"时，返回了未来3-4天的预报，而不是只返回明天一天
   - 用户问"后天呢？"时，Agent 无法记住之前问的是哪个城市

## 解决方案

### 1. 修复工具调用错误

**问题代码**（`core/tools/weather.py`）：
```python
@tool
def get_weather(city: str, extensions: str = "base") -> str:
    # ... 实现代码 ...
    return result

@tool
def get_weather_forecast(city: str) -> str:
    # ❌ 错误：直接调用 @tool 装饰的函数
    return get_weather(city=city, extensions="all")
```

**解决方案**：
- 提取底层实现函数 `_get_weather_impl()`，不加 `@tool` 装饰器
- 让所有工具函数都调用这个底层函数

```python
# ✅ 底层实现函数（不加装饰器）
def _get_weather_impl(city: str, extensions: str = "base") -> str:
    # ... 实现代码 ...
    return result

# ✅ 工具函数调用底层实现
@tool
def get_weather(city: str, extensions: str = "base") -> str:
    return _get_weather_impl(city, extensions)

@tool
def get_weather_forecast(city: str) -> str:
    return _get_weather_impl(city=city, extensions="all")
```

### 2. 新增智能天气查询工具

新增 `get_daily_weather` 工具，支持精准查询某一天的天气：

```python
@tool
def get_daily_weather(
    city: str,
    day: Literal["today", "tomorrow", "day_after_tomorrow"] = "tomorrow"
) -> str:
    """
    查询指定城市某一天的天气预报
    
    这是最智能的天气查询工具，可以精确查询某一天的天气。
    当用户问"明天天气"、"后天天气"时，应该使用这个工具。
    """
    # ... 实现代码 ...
```

**特性**：
- 只返回指定那一天的天气，不返回多天预报
- 更节省 token，适合用户只问某一天天气的场景
- 支持今天、明天、后天三个时间点

### 3. 优化格式化函数

修改 `_format_forecast_weather()` 函数，支持按天过滤：

```python
def _format_forecast_weather(data: dict, day_offset: Optional[int] = None) -> str:
    """
    格式化预报天气数据
    
    Args:
        data: API 返回的 JSON 数据
        day_offset: 指定查询第几天的天气（0=今天，1=明天，2=后天，None=所有天）
    """
    # 如果指定了 day_offset，只返回那一天的数据
    if day_offset is not None:
        # 只格式化指定那一天
        # 显示为：今天/明天/后天（日期 星期X）
        pass
    
    # 否则返回所有天的预报
    # 每天都显示：今天/明天/后天（日期 星期X）
```

### 4. 实现上下文记忆

**在 CLI 中保存对话历史**（`scripts/demo_cli.py`）：

```python
async def chat(self, message: str) -> str:
    """发送消息并获取回复"""
    from langchain_core.messages import HumanMessage, AIMessage
    
    # 调用 Agent
    response = await self.agent.ainvoke(
        input_text=message,
        chat_history=self.chat_history,  # ✅ 传递对话历史
    )
    
    # ✅ 更新对话历史
    self.chat_history.append(HumanMessage(content=message))
    self.chat_history.append(AIMessage(content=response))
    
    return response
```

**在系统提示词中指导模型**（`core/prompts.py`）：

```python
TOOL_USAGE_INSTRUCTIONS = """
...
天气查询的上下文记忆：
- 当用户第一次问某个城市的天气时，记住这个城市
- 如果用户接着问"后天呢？"、"大后天呢？"，应该查询之前提到的同一个城市
- 从对话历史中提取城市名称和时间信息
...
"""
```

## 测试结果

### 测试场景 1：上下文记忆

```
👤 用户: 帮我查询一下明天深圳的天气
🤖 助手: 深圳明天（11月6日）天气多云，白天气温约28°C...
         [✅ 只返回明天一天的天气]

👤 用户: 后天呢？
🤖 助手: 深圳后天（11月7日）天气多云，白天预计26°C...
         [✅ 自动记住了"深圳"，查询深圳后天的天气]

👤 用户: 那今天怎么样？
🤖 助手: 今天（11月5日）深圳天气晴朗，白天气温约27°C...
         [✅ 继续记住了"深圳"，查询深圳今天的天气]
```

### 测试场景 2：单日查询准确性

```
👤 用户: 明天北京天气怎么样？
🤖 助手: 明天北京的天气预报显示，白天多云阴天，气温约为13°C...
         [✅ 只返回明天一天的天气，不返回3-4天预报]

👤 用户: 后天上海会下雨吗？
🤖 助手: 根据上海的天气预报，后天（2025-11-07），白天和夜间都将有小雨...
         [✅ 准确回答了"后天会不会下雨"的问题]
```

## 工具优先级

在 `core/tools/__init__.py` 中，按优先级排列天气工具：

```python
ADVANCED_TOOLS = [
    web_search,
    web_search_simple,
    get_daily_weather,      # ⭐ 智能天气查询（推荐，优先使用）
    get_weather_forecast,   # 多天预报
    get_weather,           # 通用天气查询
]
```

模型会优先选择 `get_daily_weather`，因为它在提示词中被标记为"推荐"。

## 技术要点

### 1. @tool 装饰器的正确使用

```python
# ❌ 错误：在 @tool 函数内调用另一个 @tool 函数
@tool
def func_a():
    return "result"

@tool
def func_b():
    return func_a()  # 错误！func_a 已经是 StructuredTool 对象

# ✅ 正确：提取底层实现
def _impl():
    return "result"

@tool
def func_a():
    return _impl()

@tool
def func_b():
    return _impl()  # 正确！调用的是普通函数
```

### 2. LangChain 对话历史管理

```python
from langchain_core.messages import HumanMessage, AIMessage

# 维护对话历史
chat_history = []

# 每次对话后更新
chat_history.append(HumanMessage(content=user_input))
chat_history.append(AIMessage(content=ai_response))

# 传递给 Agent
agent.ainvoke(input_text=message, chat_history=chat_history)
```

### 3. 系统提示词指导模型行为

通过提示词明确告诉模型：
- 什么时候使用哪个工具
- 如何从对话历史中提取信息
- 如何记忆上下文

这比完全依赖模型的"智能"更可靠。

## 文件变更清单

### 修改的文件
1. `core/tools/weather.py` - 修复工具调用错误，新增智能查询工具
2. `core/tools/__init__.py` - 导出新工具，调整工具优先级
3. `core/prompts.py` - 更新工具使用说明，加入上下文记忆指导
4. `scripts/demo_cli.py` - 实现对话历史保存

### 新增的文件
1. `scripts/test_weather_智能.py` - 智能天气查询测试脚本
2. `docs/stage_01/WEATHER_CONTEXT_MEMORY.md` - 本文档

## 下一步优化方向

1. **长期记忆**：使用 LangChain 的 Memory 机制，持久化对话历史
2. **更智能的时间理解**：支持"这周末"、"下周二"等更复杂的时间表达
3. **多轮对话状态管理**：使用 LangGraph 的 State 机制，更好地管理对话状态
4. **工具链路追踪**：记录工具调用日志，方便 debug

## 总结

通过以下三个关键改进：

1. ✅ **修复工具调用错误**：正确使用 `@tool` 装饰器
2. ✅ **智能时间范围识别**：只返回用户问的那一天的天气
3. ✅ **上下文记忆**：Agent 能够记住之前的对话，理解"后天呢？"指的是哪个城市

现在的天气查询功能已经非常智能和实用了！🎉

