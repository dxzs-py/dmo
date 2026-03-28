# 🚀 快速开始指南

## 5 分钟快速体验 LC-StudyLab 第 1 阶段

### 步骤 1: 安装依赖

```bash
cd backend

# 创建虚拟环境
python -m venv venv

# 激活虚拟环境
source venv/bin/activate  # Mac/Linux
# 或
venv\Scripts\activate  # Windows

# 安装依赖
pip install -r requirements.txt
```

### 步骤 2: 配置环境变量

```bash
# 复制配置文件
cp env.example .env

# 编辑 .env 文件，至少需要设置：
# OPENAI_API_KEY=your-api-key-here
```

### 步骤 3: 运行测试

```bash
# 运行基础功能测试
python scripts/test_basic.py
```

如果看到 "🎉 所有测试通过！"，说明配置正确！

### 步骤 4: 启动 CLI 演示

```bash
# 方式 1: 使用启动脚本
./start_cli.sh

# 方式 2: 直接运行
python scripts/demo_cli.py
```

尝试这些命令：
```
👤 你: 你好，请介绍一下自己
👤 你: 现在几点？
👤 你: 计算 (123 + 456) * 2
👤 你: /help
```

### 步骤 5: 启动 API 服务器

```bash
# 方式 1: 使用启动脚本
./start_server.sh

# 方式 2: 直接运行
python api/http_server.py
```

访问 API 文档：http://localhost:8000/docs

### 步骤 6: 测试 API

```bash
# 测试非流式聊天
curl -X POST "http://localhost:8000/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "你好",
    "mode": "default",
    "use_tools": true
  }'

# 测试流式聊天
curl -X POST "http://localhost:8000/chat/stream" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "讲个笑话",
    "mode": "default"
  }'
```

## 🎯 核心功能演示

### 1. 时间工具

```python
from agents import create_base_agent

agent = create_base_agent()
print(agent.invoke("现在几点？"))
```

### 2. 计算器工具

```python
agent = create_base_agent()
print(agent.invoke("帮我计算 (10 + 20) * 3"))
```

### 3. 流式输出

```python
agent = create_base_agent(streaming=True)
for chunk in agent.stream("讲一个笑话"):
    print(chunk, end="", flush=True)
```

### 4. 网络搜索（需要 Tavily API Key）

```python
from core.tools import ALL_TOOLS

agent = create_base_agent(tools=ALL_TOOLS)
print(agent.invoke("搜索 LangChain 1.0.3 的新特性"))
```

### 5. 不同的 Agent 模式

```python
# 编程助手模式
agent = create_base_agent(prompt_mode="coding")
print(agent.invoke("什么是递归？"))

# 研究助手模式
agent = create_base_agent(prompt_mode="research")
print(agent.invoke("解释一下量子计算"))

# 简洁模式
agent = create_base_agent(prompt_mode="concise")
print(agent.invoke("Python 是什么？"))
```

## 🐛 常见问题

### Q1: 提示 "OPENAI_API_KEY 未设置"

**A:** 确保在 `.env` 文件中设置了 `OPENAI_API_KEY`：
```env
OPENAI_API_KEY=sk-your-key-here
```

### Q2: 网络搜索不可用

**A:** 网络搜索需要 Tavily API Key，在 `.env` 中设置：
```env
TAVILY_API_KEY=tvly-your-key-here
```

或者使用不带网络搜索的基础工具：
```python
from core.tools import BASIC_TOOLS
agent = create_base_agent(tools=BASIC_TOOLS)
```

### Q3: 模块导入错误

**A:** 确保激活了虚拟环境并安装了依赖：
```bash
source venv/bin/activate
pip install -r requirements.txt
```

### Q4: 端口 8000 被占用

**A:** 在 `.env` 中修改端口：
```env
SERVER_PORT=8001
```

## 📚 下一步

- 查看完整文档：[README.md](README.md)
- 探索 API 文档：http://localhost:8000/docs
- 查看代码注释了解实现细节
- 准备第 2 阶段：RAG 知识库模块

## 🎉 恭喜！

你已经成功运行了 LC-StudyLab 第 1 阶段！

这个阶段实现了：
- ✅ 基于 LangChain 1.0.3 的 Agent
- ✅ 流式输出
- ✅ 工具调用（时间、计算、搜索）
- ✅ FastAPI 接口
- ✅ CLI 交互工具

继续探索更多功能吧！ 🚀

