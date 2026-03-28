# DeepAgent 快速开始指南

## 🚀 5 分钟快速上手

本指南将帮助你在 5 分钟内开始使用 DeepAgent 深度研究功能。

## 📋 前置要求

1. **Python 环境**：Python 3.10+
2. **API Keys**：
   - OpenAI API Key（必需）
   - Tavily API Key（可选，用于网络搜索）

## 🔧 配置

### 1. 设置环境变量

编辑 `backend/.env` 文件：

```bash
# 必需
OPENAI_API_KEY=your_openai_api_key_here

# 可选（用于网络搜索）
TAVILY_API_KEY=your_tavily_api_key_here
```

### 2. 验证安装

```bash
cd backend
python -c "from deep_research import create_deep_research_agent; print('✅ DeepAgent 已安装')"
```

## 💡 使用示例

### 示例 1：基础研究（Python 代码）

```python
from deep_research import create_deep_research_agent

# 1. 创建 DeepAgent
agent = create_deep_research_agent(
    thread_id="my_first_research",
    enable_web_search=True,
    enable_doc_analysis=False,
)

# 2. 执行研究
result = agent.research("LangChain 1.0 有哪些主要新特性？")

# 3. 查看结果
print("研究状态:", result["status"])
print("\n最终报告:")
print(result["final_report"])

# 4. 查看生成的文件
from core.tools.filesystem import get_filesystem
fs = get_filesystem("my_first_research")
files = fs.list_files()
print("\n生成的文件:")
for f in files:
    print(f"  - {f}")
```

### 示例 2：使用 API

#### 步骤 1：启动服务器

```bash
cd backend
bash start_server.sh
```

#### 步骤 2：启动研究任务

```bash
curl -X POST "http://localhost:8000/deep-research/start" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "分析 AI 领域的最新趋势",
    "enable_web_search": true,
    "enable_doc_analysis": false
  }'
```

**响应示例：**
```json
{
  "status": "success",
  "thread_id": "research_abc123",
  "message": "研究任务已启动，正在后台执行",
  "estimated_time": "5-10分钟"
}
```

#### 步骤 3：查询状态

```bash
# 使用上一步返回的 thread_id
curl "http://localhost:8000/deep-research/status/research_abc123"
```

#### 步骤 4：获取结果

```bash
# 等待任务完成后
curl "http://localhost:8000/deep-research/result/research_abc123"
```

#### 步骤 5：查看文件

```bash
# 列出所有文件
curl "http://localhost:8000/deep-research/files/research_abc123"

# 读取最终报告
curl "http://localhost:8000/deep-research/file/research_abc123/reports/final_report.md"
```

### 示例 3：完整研究（含文档分析）

```python
from deep_research import create_deep_research_agent
from rag import get_embeddings, load_vector_store, create_retriever_tool

# 1. 加载文档索引
embeddings = get_embeddings()
vector_store = load_vector_store("data/indexes/test_index", embeddings)
retriever = vector_store.as_retriever()
retriever_tool = create_retriever_tool(retriever)

# 2. 创建 DeepAgent（启用文档分析）
agent = create_deep_research_agent(
    thread_id="full_research_001",
    enable_web_search=True,
    enable_doc_analysis=True,
    retriever_tool=retriever_tool,
)

# 3. 执行研究
result = agent.research("什么是 RAG？它有哪些应用场景？")

# 4. 查看结果
print(result["final_report"])
```

## 🧪 运行测试

### 快速测试

```bash
cd backend
python scripts/test_deep_research.py
```

### 测试输出示例

```
═══════════════════════════════════════════════════
     DeepAgent 深度研究功能测试套件
═══════════════════════════════════════════════════

当前配置:
  OpenAI API: ✅ 已配置
  Tavily API: ✅ 已配置
  模型: gpt-4o
  数据目录: data

╭─────────────────────────────────────────╮
│  测试 1: 文件系统功能                    │
╰─────────────────────────────────────────╯

✅ 文件系统测试通过！

╭─────────────────────────────────────────╮
│  测试 2: 基础研究（网络搜索）             │
╰─────────────────────────────────────────╯

✅ 基础研究测试通过！

════════════════════════════════════════════
测试总结
════════════════════════════════════════════

通过: 2 | 失败: 0 | 跳过: 2

✅ 所有测试通过！🎉
```

## 📁 文件结构

研究完成后，会在 `data/research/{thread_id}/` 下生成以下文件：

```
data/research/my_first_research/
├── plans/
│   └── research_plan.md      # 研究计划
├── notes/
│   ├── web_research.md       # 网络搜索笔记
│   └── doc_analysis.md       # 文档分析报告（如果启用）
├── reports/
│   └── final_report.md       # 最终研究报告
└── temp/
    └── ...                   # 临时文件
```

## 🎯 研究问题示例

### 技术调研
```
"分析 LangChain 1.0 相比 0.x 版本的主要改进"
"对比 FastAPI 和 Flask 的性能和特性"
"总结 Python 异步编程的最佳实践"
```

### 趋势分析
```
"分析 2024 年 AI 领域的主要技术趋势"
"总结大语言模型的最新进展"
"研究边缘计算的应用场景和挑战"
```

### 对比研究
```
"对比 OpenAI GPT-4 和 Anthropic Claude 的能力差异"
"分析 PostgreSQL 和 MongoDB 的适用场景"
"对比 React 和 Vue 的开发体验"
```

## 💡 使用技巧

### 1. 设计好的研究问题

**好的问题特征：**
- 具体明确
- 有研究价值
- 范围适中
- 可以量化

**示例：**
- ✅ "分析 LangChain 1.0 的三个主要新特性"
- ❌ "LangChain 是什么？"

### 2. 选择合适的模式

| 模式 | 适用场景 | 预计时间 |
|------|---------|---------|
| 仅网络搜索 | 快速了解、最新信息 | 3-5 分钟 |
| 网络 + 文档 | 深入分析、结合内部知识 | 5-10 分钟 |

### 3. 管理研究任务

```python
# 使用有意义的 thread_id
thread_id = f"research_{topic}_{date}"

# 定期清理
from core.tools.filesystem import get_filesystem
fs = get_filesystem(thread_id)
fs.cleanup()  # 清理临时文件
```

### 4. 读取研究结果

```python
# 读取最终报告
fs = get_filesystem("my_research")
report = fs.read_file("final_report.md", subdirectory="reports")

# 搜索特定内容
results = fs.search_files("关键词")
for result in results:
    print(f"{result['filename']}: {result['match_count']} 个匹配")
```

## 🐛 常见问题

### Q1: 研究任务失败怎么办？

**A:** 检查以下几点：
1. API Key 是否正确配置
2. 网络连接是否正常
3. 查看日志文件：`logs/app.log`

```bash
# 查看最新日志
tail -f logs/app.log
```

### Q2: 如何提高报告质量？

**A:** 
1. 使用更强大的模型（如 GPT-4）
2. 设计更具体的研究问题
3. 启用文档分析，结合内部知识
4. 调整搜索关键词

### Q3: 研究速度太慢怎么办？

**A:**
1. 使用更快的模型（如 GPT-4o-mini）
2. 减少搜索次数
3. 禁用文档分析（如果不需要）
4. 使用基础研究模式

### Q4: 如何查看中间结果？

**A:**
```python
from core.tools.filesystem import get_filesystem

fs = get_filesystem("your_thread_id")

# 列出所有文件
files = fs.list_files()
print(files)

# 读取研究笔记
notes = fs.read_file("web_research.md", subdirectory="notes")
print(notes)
```

## 📚 下一步

- 📖 阅读 [完整文档](./README.md)
- 🔧 查看 [API 文档](./API.md)
- 💻 查看 [实施计划](./STAGE4_PLAN.md)
- 🎓 学习 [最佳实践](./README.md#最佳实践)

## 🎉 完成！

恭喜！你已经学会了 DeepAgent 的基本使用。

现在你可以：
- ✅ 创建深度研究任务
- ✅ 使用 API 接口
- ✅ 管理研究文件
- ✅ 读取研究结果

开始你的第一个研究吧！🚀

---

**需要帮助？**
- 查看 [README.md](./README.md)
- 查看日志：`logs/app.log`
- 运行测试：`python scripts/test_deep_research.py`

