# Stage 4: DeepAgents 深度研究模式

## 📋 概述

Stage 4 实现了基于 LangChain v1.0.3 的 DeepAgents 深度研究模式，这是一个能够自动规划、执行复杂研究任务的高级智能体系统。

## 🎯 核心功能

### 1. DeepAgent 深度研究智能体

**主要特性：**
- 自动生成研究计划
- 协调多个子智能体
- 管理研究流程
- 生成结构化报告

**工作流程：**
```
用户提问 → Planner（规划） → WebResearcher（网络搜索） 
         → DocAnalyst（文档分析，可选） → ReportWriter（报告撰写）
```

### 2. SubAgents 子智能体系统

#### WebResearcher（网络研究员）
- **职责**：网络搜索和信息整理
- **工具**：Tavily 搜索、文件系统
- **输出**：结构化的研究笔记

#### DocAnalyst（文档分析师）
- **职责**：文档分析和知识提取
- **工具**：RAG 检索、文件系统
- **输出**：文档分析报告

#### ReportWriter（报告撰写者）
- **职责**：报告撰写和内容组织
- **工具**：文件系统
- **输出**：最终研究报告

### 3. 文件系统工具

**ResearchFileSystem 类：**
- 独立的工作空间（基于 thread_id）
- 自动创建目录结构（plans/notes/reports/temp）
- 文件 CRUD 操作
- 文件搜索功能

**LangChain 工具：**
- `write_research_file`: 写入研究文件
- `read_research_file`: 读取研究文件
- `list_research_files`: 列出文件
- `search_research_files`: 搜索文件内容

## 🏗️ 技术架构

### 目录结构

```
backend/
├── deep_research/
│   ├── __init__.py
│   ├── deep_agent.py          # DeepAgent 核心实现
│   └── subagents.py           # 子智能体定义
├── core/
│   └── tools/
│       └── filesystem.py       # 文件系统工具
├── api/
│   └── routers/
│       └── deep_research.py    # 深度研究 API
└── scripts/
    └── test_deep_research.py   # 测试脚本
```

### 核心类

#### DeepResearchAgent

```python
class DeepResearchAgent:
    """深度研究智能体"""
    
    def __init__(
        self,
        thread_id: str,
        enable_web_search: bool = True,
        enable_doc_analysis: bool = False,
        retriever_tool: Optional[BaseTool] = None,
    ):
        ...
    
    def research(self, query: str) -> Dict[str, Any]:
        """执行研究任务"""
        ...
```

#### ResearchFileSystem

```python
class ResearchFileSystem:
    """研究文件系统"""
    
    def __init__(self, thread_id: str):
        ...
    
    def write_file(self, filename: str, content: str, subdirectory: str):
        ...
    
    def read_file(self, filename: str, subdirectory: str) -> str:
        ...
```

## 🚀 快速开始

### 1. 基础使用

```python
from deep_research import create_deep_research_agent

# 创建 DeepAgent
agent = create_deep_research_agent(
    thread_id="research_001",
    enable_web_search=True,
    enable_doc_analysis=False,
)

# 执行研究
result = agent.research("分析 LangChain 1.0 的新特性")

# 查看结果
print(result["final_report"])
```

### 2. 完整研究（含文档分析）

```python
from deep_research import create_deep_research_agent
from rag import get_embeddings, load_vector_store, create_retriever_tool

# 加载文档索引
embeddings = get_embeddings()
vector_store = load_vector_store("data/indexes/my_docs", embeddings)
retriever = vector_store.as_retriever()
retriever_tool = create_retriever_tool(retriever)

# 创建 DeepAgent（含文档分析）
agent = create_deep_research_agent(
    thread_id="research_002",
    enable_web_search=True,
    enable_doc_analysis=True,
    retriever_tool=retriever_tool,
)

# 执行研究
result = agent.research("什么是 RAG？它有哪些应用场景？")
```

### 3. 使用文件系统

```python
from core.tools.filesystem import get_filesystem

# 获取文件系统
fs = get_filesystem("research_001")

# 列出文件
files = fs.list_files()
print(f"找到 {len(files)} 个文件")

# 读取报告
report = fs.read_file("final_report.md", subdirectory="reports")
print(report)

# 搜索文件
results = fs.search_files("LangChain")
for result in results:
    print(f"文件: {result['filename']}, 匹配: {result['match_count']}")
```

## 📡 API 接口

### 1. 启动研究任务

**POST /deep-research/start**

```bash
curl -X POST "http://localhost:8000/deep-research/start" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "分析 LangChain 1.0 的新特性",
    "research_depth": "standard",
    "enable_web_search": true,
    "enable_doc_analysis": false
  }'
```

**响应：**
```json
{
  "status": "success",
  "thread_id": "research_abc123",
  "message": "研究任务已启动，正在后台执行",
  "estimated_time": "5-10分钟"
}
```

### 2. 查询研究状态

**GET /deep-research/status/{thread_id}**

```bash
curl "http://localhost:8000/deep-research/status/research_abc123"
```

**响应：**
```json
{
  "status": "running",
  "thread_id": "research_abc123",
  "current_step": "researching",
  "progress": 50,
  "message": "正在执行研究任务..."
}
```

### 3. 获取研究结果

**GET /deep-research/result/{thread_id}**

```bash
curl "http://localhost:8000/deep-research/result/research_abc123"
```

**响应：**
```json
{
  "status": "completed",
  "thread_id": "research_abc123",
  "query": "分析 LangChain 1.0 的新特性",
  "final_report": "# 研究报告\n\n## 执行摘要\n...",
  "plan": {...},
  "steps_completed": {
    "web_research": true,
    "doc_analysis": false,
    "report": true
  },
  "metadata": {...}
}
```

### 4. 列出研究文件

**GET /deep-research/files/{thread_id}**

```bash
curl "http://localhost:8000/deep-research/files/research_abc123"
```

**响应：**
```json
{
  "thread_id": "research_abc123",
  "files": [
    "plans/research_plan.md",
    "notes/web_research.md",
    "reports/final_report.md"
  ],
  "total": 3
}
```

### 5. 获取文件内容

**GET /deep-research/file/{thread_id}/{filename}**

```bash
curl "http://localhost:8000/deep-research/file/research_abc123/reports/final_report.md"
```

## 🧪 测试

### 运行测试脚本

```bash
# 进入 backend 目录
cd backend

# 运行测试
python scripts/test_deep_research.py
```

### 测试场景

1. **文件系统测试**：测试文件 CRUD 操作
2. **基础研究测试**：测试网络搜索功能
3. **完整研究测试**：测试网络搜索 + 文档分析
4. **API 集成测试**：测试 HTTP 接口

### 手动测试

```bash
# 1. 启动服务器
bash start_server.sh

# 2. 在另一个终端启动研究任务
curl -X POST "http://localhost:8000/deep-research/start" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "分析 AI 领域的最新趋势",
    "enable_web_search": true
  }'

# 3. 记录返回的 thread_id，然后查询状态
curl "http://localhost:8000/deep-research/status/{thread_id}"

# 4. 等待完成后获取结果
curl "http://localhost:8000/deep-research/result/{thread_id}"
```

## 📊 性能指标

### 预期性能

- **基础研究**（仅网络搜索）：3-5 分钟
- **标准研究**（网络 + 少量文档）：5-10 分钟
- **深度研究**（网络 + 大量文档）：10-15 分钟

### 资源使用

- **内存**：约 500MB - 1GB
- **磁盘**：每个研究任务约 1-5MB
- **API 调用**：
  - LLM：10-30 次
  - 搜索：5-10 次
  - Embedding：根据文档数量

## 🔧 配置

### 环境变量

```bash
# 必需
OPENAI_API_KEY=your_openai_key

# 可选（网络搜索）
TAVILY_API_KEY=your_tavily_key

# 文件系统路径
DATA_DIR=data
```

### Settings 配置

在 `config/settings.py` 中可以配置：

```python
# DeepAgent 配置（未来可添加）
deep_agent_max_iterations: int = 20
deep_agent_filesystem_path: str = "data/research"
deep_agent_planning_model: str = "gpt-4o"
```

## 📝 最佳实践

### 1. 研究问题设计

**好的研究问题：**
- ✅ "分析 LangChain 1.0 相比 0.x 版本的主要改进"
- ✅ "对比 OpenAI GPT-4 和 Anthropic Claude 的能力差异"
- ✅ "总结 RAG 系统的最佳实践和优化方法"

**不好的研究问题：**
- ❌ "LangChain 是什么？"（太简单）
- ❌ "告诉我所有关于 AI 的信息"（太宽泛）
- ❌ "1+1=?"（不需要研究）

### 2. 选择合适的模式

- **基础研究**：快速了解某个主题
- **标准研究**：深入分析，需要多个来源
- **深度研究**：全面调研，包含文档分析

### 3. 文件管理

- 定期清理临时文件
- 重要研究结果及时备份
- 使用有意义的 thread_id

### 4. 错误处理

- 检查 API Key 配置
- 确保网络连接正常
- 监控磁盘空间
- 查看日志文件

## 🐛 故障排除

### 问题 1：研究任务失败

**可能原因：**
- API Key 未配置或无效
- 网络连接问题
- 磁盘空间不足

**解决方案：**
1. 检查 `.env` 文件中的 API Key
2. 测试网络连接
3. 检查磁盘空间
4. 查看日志：`logs/app.log`

### 问题 2：文档分析失败

**可能原因：**
- 未提供 retriever_tool
- 索引文件不存在或损坏

**解决方案：**
1. 确保已创建文档索引
2. 检查索引路径是否正确
3. 重新构建索引

### 问题 3：报告质量不佳

**可能原因：**
- 研究问题设计不当
- 搜索结果质量低
- 模型温度设置不合适

**解决方案：**
1. 重新设计研究问题
2. 调整搜索关键词
3. 使用更强大的模型（如 GPT-4）

## 📚 参考资料

### LangChain 官方文档
- [DeepAgents Quickstart](https://docs.langchain.com/oss/python/deepagents/quickstart)
- [Subagents](https://docs.langchain.com/oss/python/deepagents/subagents)
- [LangGraph](https://docs.langchain.com/oss/python/langgraph/quickstart)

### 项目文档
- [STAGE4_PLAN.md](./STAGE4_PLAN.md) - 实施计划
- [QUICKSTART.md](./QUICKSTART.md) - 快速开始指南
- [API.md](./API.md) - API 详细文档

## 🎉 总结

Stage 4 成功实现了 DeepAgents 深度研究模式，主要成果：

✅ **核心功能**
- DeepAgent 深度研究智能体
- 三个专门的子智能体
- 完整的文件系统工具
- RESTful API 接口

✅ **技术特性**
- 基于 LangChain v1.0.3
- 使用 LangGraph 构建工作流
- 支持网络搜索和文档分析
- 异步后台任务执行

✅ **文档和测试**
- 详细的中文注释
- 完整的测试脚本
- 使用指南和 API 文档

下一步可以考虑：
- 添加更多子智能体（数据分析师、可视化专家等）
- 实现 Human-in-the-loop 交互
- 添加长期记忆功能
- 优化性能和成本

---

**创建时间**: 2024-11-10
**版本**: 1.0
**状态**: 已完成

