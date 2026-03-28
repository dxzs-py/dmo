# 🔧 修复 Tavily 搜索工具错误

## 问题描述

在运行时遇到以下错误：

```
LangChainDeprecationWarning: The class `TavilySearchResults` was deprecated in LangChain 0.3.25 and will be removed in 1.0.
2 validation errors for TavilySearchResults
include_domains
  Input should be a valid list [type=list_type, input_value=None, input_type=NoneType]
exclude_domains
  Input should be a valid list [type=list_type, input_value=None, input_type=NoneType]
```

## 原因

1. **`TavilySearchResults` 已弃用**：在 LangChain 0.3.25 中被标记为弃用，将在 1.0 中移除
2. **参数验证问题**：旧版本的 `include_domains` 和 `exclude_domains` 不接受 `None` 值
3. **推荐使用新包**：LangChain V1.0.0 推荐使用独立的 `langchain-tavily` 包

## 解决方案

### 1. 安装新的 langchain-tavily 包

```bash
# 激活虚拟环境
cd backend
source venv/bin/activate  # Mac/Linux
# 或
venv\Scripts\activate  # Windows

# 安装 langchain-tavily
pip install langchain-tavily==0.2.1
```

### 2. 更新依赖文件

已更新 `requirements.txt`：

```txt
# LangChain 集成包
langchain-tavily==0.2.1  # Tavily 搜索工具（V1.0.0 推荐）
```

### 3. 代码已自动适配

`core/tools/web_search.py` 已更新为：

- ✅ 优先使用新的 `langchain-tavily` 包
- ✅ 如果未安装，回退到旧的 `langchain-community` 包
- ✅ 自动处理参数验证问题（`None` vs 空列表）
- ✅ 提供清晰的错误提示

## 验证安装

### 方法 1：Python 命令行

```python
# 测试导入
from langchain_tavily import TavilySearchResults
print("✅ langchain-tavily 安装成功")
```

### 方法 2：运行测试

```bash
python scripts/test_basic.py
```

### 方法 3：运行 CLI

```bash
python scripts/demo_cli.py
```

然后尝试使用搜索功能：
```
👤 你: 搜索 LangChain 1.0.3 新特性
```

## 新旧包对比

### 旧包（已弃用）❌
```python
from langchain_community.tools.tavily_search import TavilySearchResults

tool = TavilySearchResults(
    max_results=5,
    search_depth="advanced",
    include_domains=None,  # ❌ 会报错
    exclude_domains=None,  # ❌ 会报错
    api_key="...",
)
```

### 新包（推荐）✅
```python
from langchain_tavily import TavilySearchResults

tool = TavilySearchResults(
    max_results=5,
    search_depth="advanced",
    api_key="...",
    # include_domains 和 exclude_domains 是可选的
)
```

## 配置 Tavily API Key

确保在 `.env` 文件中设置了 Tavily API Key：

```env
TAVILY_API_KEY=tvly-your-key-here
```

如果没有 API Key，可以：

1. 访问 https://tavily.com/ 注册账号
2. 获取免费的 API Key
3. 添加到 `.env` 文件

## 如果不使用 Tavily

如果不需要网络搜索功能，可以：

### 选项 1：只使用基础工具

```python
from core.tools import BASIC_TOOLS  # 不包含 web_search

agent = create_base_agent(tools=BASIC_TOOLS)
```

### 选项 2：移除搜索工具

```python
from core.tools import get_current_time, calculator

agent = create_base_agent(tools=[get_current_time, calculator])
```

## 故障排除

### 问题 1：仍然看到弃用警告

**原因：** 可能还在使用旧包

**解决：**
```bash
pip uninstall langchain-community
pip install langchain-tavily==0.2.1
```

### 问题 2：导入错误

**错误：** `ModuleNotFoundError: No module named 'langchain_tavily'`

**解决：**
```bash
pip install langchain-tavily==0.2.1
```

### 问题 3：API Key 错误

**错误：** `Tavily API Key 未设置`

**解决：**
1. 检查 `.env` 文件是否存在
2. 确认 `TAVILY_API_KEY` 已设置
3. 重启应用

## 参考文档

- [Tavily Search 官方文档](https://python.langchain.com/docs/integrations/tools/tavily_search/)
- [langchain-tavily PyPI](https://pypi.org/project/langchain-tavily/)
- [Tavily API 文档](https://docs.tavily.com/)

## 总结

✅ **已修复的问题：**
1. 更新到推荐的 `langchain-tavily` 包
2. 修复参数验证错误
3. 添加向后兼容性
4. 提供清晰的错误提示

✅ **需要做的：**
1. 安装 `langchain-tavily` 包
2. 确保 `.env` 中有 `TAVILY_API_KEY`
3. 重新运行应用

---

**最后更新：** 2025-11-05
**状态：** ✅ 已修复

