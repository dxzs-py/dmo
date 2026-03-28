# Stage 5: Guardrails / 安全与结构化输出 - 使用指南

## 📖 概述

Stage 5 为 LC-StudyLab 系统添加了完整的安全防护层和结构化输出能力，确保系统可以安全地部署到生产环境。

### 核心功能

1. **输入 Guardrails**：防止恶意输入、敏感信息泄露
2. **输出 Guardrails**：确保输出内容安全、格式正确
3. **结构化输出**：使用 Pydantic 定义和验证输出格式
4. **安全集成**：为 RAG Agent、Workflow、DeepAgent 添加安全检查

## 🏗️ 架构

```
core/guardrails/
├── __init__.py              # 导出所有公共接口
├── content_filters.py       # 内容安全过滤器
├── input_validators.py      # 输入验证器
├── output_validators.py     # 输出验证器
├── schemas.py               # Pydantic 结构化输出模型
└── middleware.py            # Guardrails 中间件

rag/
└── safe_rag_agent.py        # 安全 RAG Agent

workflows/
├── safe_nodes.py            # 安全节点包装器
└── safe_study_flow.py       # 安全学习工作流

deep_research/
└── safe_deep_agent.py       # 安全深度研究智能体
```

## 🚀 快速开始

### 1. 基础使用：内容过滤器

```python
from core.guardrails import ContentFilter

# 创建过滤器
filter = ContentFilter(
    enable_pii_detection=True,      # 检测个人信息
    enable_content_safety=True,     # 检测不安全内容
    enable_injection_detection=True, # 检测 Prompt Injection
    mask_pii=True,                  # 自动脱敏
)

# 过滤输入
result = filter.filter_input("我的手机号是 13812345678")
print(result.is_safe)           # True/False
print(result.filtered_content)  # 脱敏后的内容
print(result.issues)            # 检测到的问题列表
```

### 2. 输入验证器

```python
from core.guardrails import InputValidator

# 创建验证器
validator = InputValidator(
    min_length=1,
    max_length=50000,
    strict_mode=False,  # 严格模式：警告也视为错误
)

# 验证输入
result = validator.validate("用户输入的问题")

if result.is_valid:
    print(f"验证通过: {result.filtered_input}")
else:
    print(f"验证失败: {result.errors}")

# 或者直接抛出异常
filtered_input = validator.validate_or_raise("用户输入")
```

### 3. 输出验证器

```python
from core.guardrails import OutputValidator

# 创建验证器（RAG 场景）
validator = OutputValidator(
    require_sources=True,  # 要求必须有引用来源
    strict_mode=False,
)

# 验证输出
result = validator.validate(
    output="这是回答内容",
    sources=["doc1.pdf", "doc2.md"],
)

if result.is_valid:
    print(f"输出有效: {result.filtered_output}")
else:
    print(f"输出无效: {result.errors}")
```

### 4. 结构化输出

```python
from core.guardrails import RAGResponse, StudyPlan, Quiz

# RAG 回答
response = RAGResponse(
    answer="LangChain 是一个用于开发大语言模型应用的框架",
    sources=["langchain_docs.md", "tutorial.pdf"],
    confidence=0.95,
)

# 学习计划
from core.guardrails import StudyPlanStep, DifficultyLevel

plan = StudyPlan(
    topic="LangChain 全栈开发",
    difficulty=DifficultyLevel.INTERMEDIATE,
    total_hours=40.0,
    steps=[
        StudyPlanStep(
            step_number=1,
            title="基础概念",
            description="学习核心概念",
            estimated_hours=8.0,
            resources=["官方文档"],
            key_concepts=["Agents", "Chains"],
        ),
    ],
    prerequisites=["Python 基础"],
    learning_objectives=["掌握 LangChain"],
)

# 测验
from core.guardrails import QuizQuestion, QuestionType

quiz = Quiz(
    title="LangChain 基础测验",
    topic="LangChain 核心概念",
    questions=[
        QuizQuestion(
            question_number=1,
            question_type=QuestionType.SINGLE_CHOICE,
            question="什么是 LangChain?",
            options=["A. 框架", "B. 库", "C. 工具"],
            correct_answer="A",
            points=1,
        ),
    ],
    total_points=1,
    passing_score=1,
)
```

## 🛡️ 安全 RAG Agent

### 创建和使用

```python
from rag import get_embeddings, load_vector_store, create_retriever
from rag.safe_rag_agent import create_safe_rag_agent

# 加载向量库
embeddings = get_embeddings()
vector_store = load_vector_store("data/indexes/my_docs", embeddings)
retriever = create_retriever(vector_store)

# 创建安全 RAG Agent
agent = create_safe_rag_agent(
    retriever=retriever,
    enable_input_validation=True,   # 启用输入验证
    enable_output_validation=True,  # 启用输出验证
    strict_mode=False,              # 非严格模式
)

# 查询（返回结构化输出）
result = agent.query("什么是 LangChain？", return_structured=True)
print(result.answer)      # 回答内容
print(result.sources)     # 引用来源
print(result.confidence)  # 置信度

# 异步查询
result = await agent.aquery("什么是 LangChain？")

# 流式查询
for chunk in agent.stream("什么是 LangChain？"):
    print(chunk, end="", flush=True)
```

### 安全特性

1. **输入验证**：自动检测和阻止恶意输入
2. **输出验证**：确保回答包含引用来源
3. **结构化输出**：返回 `RAGResponse` 对象
4. **敏感信息脱敏**：自动处理个人信息

## 🔄 安全 Workflow

### 创建安全工作流

```python
from workflows.safe_study_flow import create_safe_study_flow_graph

# 创建安全学习工作流
graph = create_safe_study_flow_graph(
    enable_human_review=True,  # 启用人工审核
    strict_mode=False,         # 非严格模式
)

# 运行工作流
config = {"configurable": {"thread_id": "user_123"}}
result = graph.invoke({
    "question": "如何学习 LangChain？",
    "messages": []
}, config)

print(result["plan"])      # 学习计划
print(result["quiz"])      # 测验题
print(result["feedback"])  # 反馈
```

### 为现有节点添加 Guardrails

```python
from workflows.safe_nodes import with_guardrails, create_safe_node

# 方式 1: 使用装饰器
@with_guardrails(
    input_field="question",
    output_field="answer",
    require_sources=True,
)
def my_rag_node(state):
    # 节点逻辑
    return state

# 方式 2: 使用函数式 API
from workflows.nodes import planner_node

safe_planner = create_safe_node(
    planner_node,
    validate_input=True,
    validate_output=True,
    input_field="question",
    output_field="plan",
)
```

## 🔬 安全 DeepAgent

### 创建和使用

```python
from deep_research.safe_deep_agent import create_safe_deep_research_agent

# 创建安全深度研究智能体
agent = create_safe_deep_research_agent(
    thread_id="research_123",
    enable_web_search=True,
    enable_human_review=True,  # 启用人工审核
    strict_mode=True,          # 严格模式
)

# 执行研究（返回结构化报告）
report = agent.research("分析 LangChain 1.0 的新特性")

print(report.title)        # 报告标题
print(report.summary)      # 执行摘要
for section in report.sections:
    print(f"{section.title}: {section.content}")
print(report.conclusions)  # 研究结论
print(report.references)   # 参考文献
```

### 安全特性

1. **输入验证**：检查研究问题的安全性
2. **工具调用审核**：记录所有工具调用
3. **输出验证**：确保研究报告的质量和安全性
4. **人工审核**：关键步骤可暂停等待确认

## 🧪 测试

### 运行 Guardrails 基础测试

```bash
cd backend
python scripts/test_guardrails.py
```

测试内容：
- 内容过滤器
- 输入验证器
- 输出验证器
- 结构化输出
- 集成测试

### 运行安全 RAG 测试

```bash
cd backend
python scripts/test_safe_rag.py
```

测试内容：
- 基本功能
- 输入验证
- 输出验证
- 异步查询
- 流式查询

## 📊 最佳实践

### 1. 选择合适的验证模式

```python
# 开发环境：非严格模式（警告不阻止执行）
agent = create_safe_rag_agent(
    retriever=retriever,
    strict_mode=False,
)

# 生产环境：严格模式（警告也视为错误）
agent = create_safe_rag_agent(
    retriever=retriever,
    strict_mode=True,
)
```

### 2. 自定义内容过滤器

```python
from core.guardrails import ContentFilter

# 自定义过滤规则
custom_filter = ContentFilter(
    enable_pii_detection=True,
    enable_content_safety=True,
    enable_injection_detection=True,
    mask_pii=True,
)

# 添加自定义关键词
custom_filter.UNSAFE_KEYWORDS.extend([
    "自定义敏感词1",
    "自定义敏感词2",
])

# 使用自定义过滤器
from core.guardrails import InputValidator

validator = InputValidator(content_filter=custom_filter)
```

### 3. 处理验证错误

```python
from core.guardrails import InputValidator

validator = InputValidator(strict_mode=True)

try:
    filtered_input = validator.validate_or_raise(user_input)
    # 继续处理
except ValueError as e:
    # 记录错误
    logger.error(f"输入验证失败: {e}")
    # 返回友好的错误消息给用户
    return {"error": "您的输入包含不安全内容，请修改后重试"}
```

### 4. 结构化输出的验证

```python
from pydantic import ValidationError
from core.guardrails import RAGResponse

try:
    response = RAGResponse(
        answer="回答内容",
        sources=["doc1.pdf"],
        confidence=0.95,
    )
except ValidationError as e:
    print(f"结构化输出验证失败: {e}")
    # 处理验证错误
```

## 🔧 配置选项

### ContentFilter 配置

```python
ContentFilter(
    enable_pii_detection=True,      # 检测个人信息
    enable_content_safety=True,     # 检测不安全内容
    enable_injection_detection=True, # 检测 Prompt Injection
    mask_pii=True,                  # 自动脱敏
)
```

### InputValidator 配置

```python
InputValidator(
    content_filter=custom_filter,   # 自定义过滤器
    min_length=1,                   # 最小长度
    max_length=50000,               # 最大长度
    allow_empty=False,              # 是否允许空输入
    strict_mode=False,              # 严格模式
)
```

### OutputValidator 配置

```python
OutputValidator(
    content_filter=custom_filter,   # 自定义过滤器
    require_sources=False,          # 是否要求来源
    min_length=1,                   # 最小长度
    max_length=100000,              # 最大长度
    check_factuality=False,         # 检查事实性（未实现）
    strict_mode=False,              # 严格模式
)
```

## 📝 注意事项

1. **性能影响**：Guardrails 会增加一定的处理时间，建议在生产环境中根据需求平衡安全性和性能

2. **误报处理**：简单的关键词匹配可能产生误报，建议根据实际情况调整过滤规则

3. **人工审核**：当前的人工审核是演示性质的，实际应用中需要集成真实的审核流程

4. **扩展性**：可以根据需求集成第三方 Guardrails 服务（如 GuardrailsAI、Pangea 等）

## 🔗 相关文档

- [STAGE5_PLAN.md](./STAGE5_PLAN.md) - 开发计划
- [FEATURES.md](./FEATURES.md) - 功能详解
- [BEST_PRACTICES.md](./BEST_PRACTICES.md) - 最佳实践指南

## 📚 参考资料

- [LangChain Guardrails](https://docs.langchain.com/oss/python/langchain/guardrails)
- [Structured Output](https://docs.langchain.com/oss/python/langchain/structured-output)
- [Pydantic Documentation](https://docs.pydantic.dev/latest/)
- [Human-in-the-loop](https://docs.langchain.com/oss/python/langchain/human-in-the-loop)

## ✅ 完成标志

Stage 5 已完成以下目标：

- ✅ 实现完整的 Guardrails 模块
- ✅ 定义结构化输出 Schema
- ✅ 为 RAG Agent 添加安全检查
- ✅ 为 Workflow 添加安全节点
- ✅ 为 DeepAgent 添加安全包装
- ✅ 编写完整的测试套件
- ✅ 提供详细的使用文档

系统现在具备企业级的安全防护能力，可以安全地部署到生产环境！

