# 快速修复指南

## ✅ 已修复的问题

1. **导入路径错误** - `langchain.tools.retriever` → `langchain_core.tools.retriever`
2. **API 变更** - 使用新的 `create_agent` API 替代 `create_tool_calling_agent`
3. **导出缺失** - 添加 `query_rag_agent` 到 `rag/__init__.py`

## 🚀 快速开始

### 1. 安装依赖

```bash
cd backend

# 如果使用虚拟环境
source venv/bin/activate  # Linux/Mac
# 或
venv\Scripts\activate  # Windows

# 安装所有依赖（重要！）
pip install -r requirements.txt

# 或者分步安装关键依赖
pip install rich click faiss-cpu langchain-text-splitters
```

**重要提示**：
- `faiss-cpu` 是必需的，用于向量存储
- `rich` 和 `click` 是 CLI 工具必需的
- `langchain-text-splitters` 是文本分块必需的

### 2. 配置环境变量

确保 `.env` 文件中有 OpenAI API Key：

```bash
OPENAI_API_KEY=your_api_key_here
```

### 3. 测试导入

```bash
python -c "from rag import create_rag_agent, query_rag_agent; print('✅ 导入成功')"
```

### 4. 创建索引

```bash
python scripts/rag_cli.py index create test_docs data/documents/test --description "测试文档"
```

### 5. 查询测试

```bash
python scripts/rag_cli.py query test_docs "什么是机器学习？" --show-sources
```

### 6. 交互模式

```bash
python scripts/rag_cli.py interactive test_docs
```

## 📝 修改的文件

1. ✅ `rag/retrievers.py` - 修复导入路径
2. ✅ `rag/rag_agent.py` - 使用新 API
3. ✅ `rag/__init__.py` - 添加导出
4. ✅ `api/routers/rag.py` - 更新调用方式
5. ✅ `scripts/rag_cli.py` - 更新调用方式

## 🔍 验证步骤

### 测试 1: 导入测试

```bash
python -c "from rag import (
    load_document,
    split_documents,
    get_embeddings,
    create_vector_store,
    create_retriever,
    create_rag_agent,
    query_rag_agent,
    IndexManager
); print('✅ 所有模块导入成功')"
```

### 测试 2: 创建索引

```bash
python scripts/rag_cli.py index create test_docs data/documents/test
```

预期输出：
```
📝 创建索引: test_docs
📂 加载文档...
✅ 加载了 3 个文档
✂️  分块文档...
✅ 生成了 XX 个文本块
🔢 创建 embeddings...
✅ Embeddings 准备完成
🗄️  创建向量索引...
✅ 索引创建完成
✅ 索引创建成功: test_docs
```

### 测试 3: 列出索引

```bash
python scripts/rag_cli.py index list
```

### 测试 4: 查询

```bash
python scripts/rag_cli.py query test_docs "什么是机器学习？"
```

### 测试 5: API 服务器

```bash
# 启动服务器
python api/http_server.py

# 在另一个终端测试
curl http://localhost:8000/rag/health
```

## ⚠️ 常见问题

### 问题 1: ModuleNotFoundError: No module named 'rich'

**解决方案**：
```bash
pip install rich click
```

### 问题 2: ModuleNotFoundError: No module named 'faiss'

**解决方案**：
```bash
pip install faiss-cpu
```

### 问题 3: 权限错误

**解决方案**：
```bash
# 确保有读取 .env 文件的权限
chmod 644 .env

# 确保脚本有执行权限
chmod +x start_rag_cli.sh
```

### 问题 4: OpenAI API Key 未配置

**解决方案**：
创建或编辑 `.env` 文件：
```bash
cp env.example .env
# 然后编辑 .env 文件，添加你的 API Key
```

## 📚 新 API 使用示例

### Python 代码

```python
from rag import (
    load_directory,
    split_documents,
    get_embeddings,
    IndexManager,
    create_retriever,
    create_rag_agent,
    query_rag_agent,
)

# 1. 加载文档
documents = load_directory("data/documents/test")

# 2. 分块
chunks = split_documents(documents)

# 3. 创建索引
manager = IndexManager()
embeddings = get_embeddings()
manager.create_index("my_docs", chunks, embeddings)

# 4. 加载索引并创建 Agent
vector_store = manager.load_index("my_docs", embeddings)
retriever = create_retriever(vector_store)
agent = create_rag_agent(retriever)

# 5. 查询
result = query_rag_agent(agent, "什么是机器学习？")
print(result["answer"])
```

### CLI 命令

```bash
# 索引管理
python scripts/rag_cli.py index create <name> <path>
python scripts/rag_cli.py index list
python scripts/rag_cli.py index info <name>
python scripts/rag_cli.py index delete <name>

# 查询
python scripts/rag_cli.py query <index> "<question>"
python scripts/rag_cli.py search <index> "<query>"
python scripts/rag_cli.py interactive <index>
```

### API 调用

```bash
# 创建索引
curl -X POST "http://localhost:8000/rag/index" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "my_docs",
    "directory_path": "data/documents/test",
    "description": "测试文档"
  }'

# 查询
curl -X POST "http://localhost:8000/rag/query" \
  -H "Content-Type: application/json" \
  -d '{
    "index_name": "my_docs",
    "query": "什么是机器学习？"
  }'
```

## ✅ 完成检查清单

- [x] 修复导入路径
- [x] 更新 Agent API
- [x] 添加导出函数
- [x] 更新 API 路由
- [x] 更新 CLI 工具
- [x] 创建修复文档
- [ ] 安装依赖
- [ ] 测试创建索引
- [ ] 测试查询功能

## 🎉 总结

所有代码已经修复完成！现在只需要：

1. 安装依赖：`pip install -r requirements.txt`
2. 配置 API Key
3. 开始使用！

如果遇到任何问题，请查看：
- `docs/stage_02/LANGCHAIN_1.0.3_FIXES.md` - 详细的修复说明
- `docs/stage_02/README.md` - 完整的使用指南
- `logs/app.log` - 运行日志

