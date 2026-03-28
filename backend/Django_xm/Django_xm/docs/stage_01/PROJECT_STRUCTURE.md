# 📁 项目结构说明

## 完整目录树

```
backend/
│
├── 📄 配置和文档
│   ├── .env                        # 环境变量配置（需要创建）
│   ├── .gitignore                  # Git 忽略规则
│   ├── env.example                 # 配置示例文件
│   ├── requirements.txt            # Python 依赖（LangChain 1.0.3）
│   ├── pyproject.toml              # 项目元数据
│   ├── README.md                   # 完整项目文档
│   ├── QUICKSTART.md               # 5 分钟快速开始
│   ├── STAGE1_COMPLETION.md        # 第 1 阶段完成报告
│   └── PROJECT_STRUCTURE.md        # 本文档
│
├── 🚀 启动脚本
│   ├── start_server.sh             # HTTP 服务器启动脚本
│   └── start_cli.sh                # CLI 工具启动脚本
│
├── ⚙️ config/ - 配置管理
│   ├── __init__.py                 # 导出 settings, setup_logging, get_logger
│   ├── settings.py                 # Pydantic Settings 统一配置
│   └── logging.py                  # Loguru 日志配置
│
├── 🧠 core/ - 核心功能
│   ├── __init__.py
│   ├── models.py                   # LLM 模型封装
│   ├── prompts.py                  # 系统提示词模板
│   │
│   └── tools/ - 工具集合
│       ├── __init__.py             # 导出所有工具
│       ├── time_tools.py           # 时间工具
│       ├── calculator.py           # 计算器工具
│       └── web_search.py           # 网络搜索工具（Tavily）
│
├── 🤖 agents/ - 智能体实现
│   ├── __init__.py                 # 导出 BaseAgent, create_base_agent
│   └── base_agent.py               # 基础 Agent（核心实现）
│
├── 🌐 api/ - HTTP 接口
│   ├── __init__.py
│   ├── http_server.py              # FastAPI 应用主入口
│   │
│   └── routers/ - API 路由
│       ├── __init__.py             # 导出所有路由
│       └── chat.py                 # 聊天接口（流式/非流式）
│
├── 📜 scripts/ - 脚本工具
│   ├── __init__.py
│   ├── demo_cli.py                 # CLI 交互式演示工具
│   └── test_basic.py               # 基础功能测试脚本
│
└── 📊 logs/ - 日志文件（自动创建）
    └── app.log                     # 应用日志
```

## 📦 模块说明

### 1. config/ - 配置管理模块

#### settings.py
**作用：** 统一的配置管理，使用 Pydantic Settings

**主要类：**
- `Settings` - 配置类，包含所有配置项

**配置项：**
- OpenAI API 配置（必需）
- Tavily 搜索配置（可选）
- 服务器配置
- 日志配置
- Agent 配置

**使用方式：**
```python
from config import settings

# 访问配置
api_key = settings.openai_api_key
model = settings.openai_model

# 获取配置字典
openai_config = settings.get_openai_config()
```

#### logging.py
**作用：** 日志系统配置，使用 Loguru

**主要函数：**
- `setup_logging()` - 初始化日志系统
- `get_logger(name)` - 获取 logger 实例

**特性：**
- 彩色控制台输出
- 文件日志轮转
- 异常追踪
- 异步写入

**使用方式：**
```python
from config import get_logger

logger = get_logger(__name__)
logger.info("这是一条日志")
```

---

### 2. core/ - 核心功能模块

#### models.py
**作用：** LLM 模型封装

**主要函数：**
- `get_chat_model()` - 获取聊天模型
- `get_streaming_model()` - 获取流式模型
- `get_structured_output_model()` - 获取结构化输出模型
- `get_model_by_preset()` - 根据预设获取模型

**预设配置：**
- `default` - 默认模型（gpt-4o, temp=0.7）
- `fast` - 快速模型（gpt-4o-mini, temp=0.7）
- `precise` - 精确模型（gpt-4o, temp=0.3）
- `creative` - 创意模型（gpt-4o, temp=1.0）

**使用方式：**
```python
from core.models import get_chat_model, get_model_by_preset

# 默认模型
model = get_chat_model()

# 快速模型
model = get_model_by_preset("fast")
```

#### prompts.py
**作用：** 系统提示词模板管理

**主要函数：**
- `get_system_prompt(mode)` - 获取系统提示词
- `get_prompt_with_tools(mode)` - 获取带工具说明的提示词
- `create_custom_prompt()` - 创建自定义提示词

**提示词模式：**
- `default` - 默认学习助手
- `coding` - 编程学习助手
- `research` - 研究助手
- `concise` - 简洁模式
- `detailed` - 详细解释模式

**使用方式：**
```python
from core.prompts import get_system_prompt

prompt = get_system_prompt(mode="coding")
```

#### tools/ - 工具模块

##### time_tools.py
**工具：**
- `get_current_time()` - 获取当前时间
- `get_current_date()` - 获取当前日期（含星期）

##### calculator.py
**工具：**
- `calculator(expression)` - 安全的数学表达式计算

**特性：**
- 防止代码注入
- 支持基本运算和括号
- 错误处理

##### web_search.py
**工具：**
- `web_search(query)` - 网络搜索（Tavily）
- `web_search_simple(query)` - 快速搜索
- `create_tavily_search_tool()` - 创建 Tavily 工具实例

**使用方式：**
```python
from core.tools import get_current_time, calculator, web_search

# 直接调用
time = get_current_time.invoke({})
result = calculator.invoke({"expression": "2 + 2"})
```

---

### 3. agents/ - 智能体模块

#### base_agent.py
**作用：** 基础 Agent 实现（第 1 阶段核心）

**主要类：**
- `BaseAgent` - 基础 Agent 类

**主要方法：**
- `invoke()` - 同步调用
- `stream()` - 流式调用
- `ainvoke()` - 异步调用
- `astream()` - 异步流式调用

**工厂函数：**
- `create_base_agent()` - 创建 Agent 的便捷函数

**特性：**
- 基于 LangChain 1.0.3 的 `create_tool_calling_agent`
- 支持工具调用
- 支持流式输出
- 对话历史管理
- 错误处理

**使用方式：**
```python
from agents import create_base_agent

# 创建 Agent
agent = create_base_agent(
    prompt_mode="default",
    streaming=True
)

# 同步调用
response = agent.invoke("你好")

# 流式调用
for chunk in agent.stream("讲个笑话"):
    print(chunk, end="", flush=True)
```

---

### 4. api/ - HTTP 接口模块

#### http_server.py
**作用：** FastAPI 应用主入口

**主要功能：**
- FastAPI 应用初始化
- 生命周期管理
- 中间件配置（CORS、日志）
- 全局异常处理
- 路由注册

**端点：**
- `GET /` - 根路径，返回 API 信息
- `GET /health` - 健康检查
- `GET /info` - 系统信息

**启动方式：**
```bash
python api/http_server.py
# 或
uvicorn api.http_server:app --reload
```

#### routers/chat.py
**作用：** 聊天接口路由

**端点：**
- `POST /chat` - 非流式聊天
- `POST /chat/stream` - 流式聊天（SSE）
- `GET /chat/modes` - 获取可用模式
- `GET /chat/health` - 健康检查

**请求模型：**
- `ChatRequest` - 聊天请求
  - `message` - 用户消息
  - `chat_history` - 对话历史
  - `mode` - Agent 模式
  - `use_tools` - 是否使用工具
  - `use_advanced_tools` - 是否使用高级工具

**响应模型：**
- `ChatResponse` - 聊天响应
  - `message` - AI 回复
  - `mode` - 使用的模式
  - `tools_used` - 使用的工具列表
  - `success` - 是否成功
  - `error` - 错误信息

**使用方式：**
```bash
# 非流式
curl -X POST "http://localhost:8000/chat" \
  -H "Content-Type: application/json" \
  -d '{"message": "你好"}'

# 流式
curl -X POST "http://localhost:8000/chat/stream" \
  -H "Content-Type: application/json" \
  -d '{"message": "讲个笑话"}'
```

---

### 5. scripts/ - 脚本工具模块

#### demo_cli.py
**作用：** CLI 交互式演示工具

**功能：**
- 交互式命令行界面
- 彩色输出
- 命令系统
- 会话管理
- 实时流式输出

**命令：**
- `/help` - 显示帮助
- `/mode <模式>` - 切换模式
- `/stream` - 切换流式输出
- `/tools` - 切换工具
- `/clear` - 清空历史
- `/info` - 显示配置
- `/quit` - 退出

**启动方式：**
```bash
python scripts/demo_cli.py
# 或
./start_cli.sh
```

#### test_basic.py
**作用：** 基础功能测试脚本

**测试内容：**
1. 配置加载测试
2. 模型创建测试
3. 工具调用测试
4. Agent 功能测试

**运行方式：**
```bash
python scripts/test_basic.py
```

---

## 🔄 数据流

### 1. CLI 调用流程
```
用户输入
  ↓
demo_cli.py (ChatSession)
  ↓
create_base_agent()
  ↓
BaseAgent.stream() / invoke()
  ↓
AgentExecutor
  ↓
LLM + Tools
  ↓
输出到终端
```

### 2. API 调用流程
```
HTTP 请求
  ↓
FastAPI (http_server.py)
  ↓
chat.py (router)
  ↓
create_base_agent()
  ↓
BaseAgent.astream() / ainvoke()
  ↓
AgentExecutor
  ↓
LLM + Tools
  ↓
HTTP 响应（JSON / SSE）
```

### 3. Agent 执行流程
```
用户消息
  ↓
BaseAgent
  ↓
AgentExecutor
  ↓
create_tool_calling_agent
  ↓
LLM 决策
  ↓
需要工具？
  ├─ 是 → 调用工具 → 获取结果 → 继续思考
  └─ 否 → 生成最终回复
  ↓
返回结果
```

## 📝 文件依赖关系

```
config/settings.py (配置中心)
  ↓
  ├─→ core/models.py (使用配置)
  ├─→ core/tools/web_search.py (使用配置)
  └─→ api/http_server.py (使用配置)

core/models.py + core/prompts.py + core/tools/
  ↓
agents/base_agent.py (组装 Agent)
  ↓
  ├─→ api/routers/chat.py (API 接口)
  └─→ scripts/demo_cli.py (CLI 工具)

api/routers/chat.py
  ↓
api/http_server.py (注册路由)
```

## 🎯 关键设计决策

### 1. 为什么使用 Pydantic Settings？
- 类型安全
- 自动验证
- 环境变量支持
- 清晰的配置结构

### 2. 为什么使用 Loguru？
- 简单易用
- 彩色输出
- 自动轮转
- 异常追踪

### 3. 为什么分离 tools 模块？
- 模块化
- 易于扩展
- 独立测试
- 清晰的职责

### 4. 为什么使用 AgentExecutor？
- LangChain 推荐
- 管理执行循环
- 错误处理
- 迭代限制

### 5. 为什么提供 CLI 和 API？
- CLI - 快速测试和演示
- API - 生产环境集成
- 满足不同使用场景

## 🚀 扩展指南

### 添加新工具
1. 在 `core/tools/` 创建新文件
2. 使用 `@tool` 装饰器定义工具
3. 在 `core/tools/__init__.py` 导出
4. 添加到 `ALL_TOOLS` 或 `BASIC_TOOLS`

### 添加新 Agent 模式
1. 在 `core/prompts.py` 的 `SYSTEM_PROMPTS` 添加新模式
2. 使用 `create_base_agent(prompt_mode="新模式")` 创建

### 添加新 API 端点
1. 在 `api/routers/` 创建新路由文件
2. 定义端点和模型
3. 在 `api/http_server.py` 注册路由

### 添加新 Agent 类型
1. 在 `agents/` 创建新文件
2. 继承 `BaseAgent` 或独立实现
3. 在 `agents/__init__.py` 导出

## 📚 参考文档

- [README.md](README.md) - 完整项目文档
- [QUICKSTART.md](QUICKSTART.md) - 快速开始
- [STAGE1_COMPLETION.md](STAGE1_COMPLETION.md) - 完成报告
- API 文档: http://localhost:8000/docs

---

**最后更新：** 2025-11-05
**版本：** 0.1.0 (第 1 阶段)

