# Stage 5 功能详解

## 📋 目录

1. [内容过滤器](#内容过滤器)
2. [输入验证器](#输入验证器)
3. [输出验证器](#输出验证器)
4. [结构化输出 Schema](#结构化输出-schema)
5. [Guardrails 中间件](#guardrails-中间件)
6. [安全 RAG Agent](#安全-rag-agent)
7. [安全 Workflow](#安全-workflow)
8. [安全 DeepAgent](#安全-deepagent)

---

## 内容过滤器

### 功能概述

`ContentFilter` 是核心的内容安全检查组件，提供以下功能：

1. **Prompt Injection 检测**：识别恶意提示词注入攻击
2. **个人信息检测**：识别手机号、邮箱、身份证等敏感信息
3. **内容安全检查**：过滤暴力、色情、违法等不当内容
4. **自动脱敏**：对检测到的敏感信息进行脱敏处理

### 检测模式

#### 1. Prompt Injection 检测

检测以下模式：
- `ignore previous instructions`
- `disregard previous`
- `you are now`
- `system:`
- `[SYSTEM]`
- 等等...

#### 2. 个人信息检测

支持检测：
- **手机号**：`1[3-9]\d{9}`
- **邮箱**：`xxx@xxx.xxx`
- **身份证**：18 位身份证号
- **信用卡**：16 位信用卡号
- **IP 地址**：IPv4 地址

#### 3. 内容安全检测

基于关键词匹配（可扩展）：
- 暴力、色情、赌博、毒品
- 恐怖、诈骗
- hack、crack、exploit
- 等等...

### 使用示例

```python
from core.guardrails import ContentFilter, ContentSafetyLevel

# 创建过滤器
filter = ContentFilter(
    enable_pii_detection=True,
    enable_content_safety=True,
    enable_injection_detection=True,
    mask_pii=True,
)

# 过滤输入
result = filter.filter_input("我的手机号是 13812345678")

# 检查结果
print(result.is_safe)              # True/False
print(result.safety_level)         # SAFE/WARNING/UNSAFE
print(result.issues)               # ["检测到个人敏感信息: phone"]
print(result.filtered_content)     # "我的手机号是 138****5678"
print(result.details)              # {"pii_types": ["phone"]}
```

### 自定义扩展

```python
# 添加自定义不安全关键词
filter.UNSAFE_KEYWORDS.extend([
    "自定义敏感词1",
    "自定义敏感词2",
])

# 添加自定义检测模式
filter.INJECTION_PATTERNS.append(r"custom_pattern")
```

---

## 输入验证器

### 功能概述

`InputValidator` 对用户输入进行全面验证：

1. **长度检查**：最小/最大长度限制
2. **空值检查**：是否允许空输入
3. **内容安全**：集成 ContentFilter
4. **验证模式**：普通模式 vs 严格模式

### 验证流程

```
用户输入
  ↓
检查空值
  ↓
检查长度
  ↓
内容安全检查（ContentFilter）
  ↓
返回验证结果
```

### 使用示例

```python
from core.guardrails import InputValidator

# 创建验证器
validator = InputValidator(
    min_length=1,
    max_length=50000,
    allow_empty=False,
    strict_mode=False,  # 警告不阻止
)

# 验证输入
result = validator.validate("用户输入的问题")

if result.is_valid:
    print(f"✅ 验证通过")
    print(f"过滤后: {result.filtered_input}")
    if result.warnings:
        print(f"⚠️ 警告: {result.warnings}")
else:
    print(f"❌ 验证失败")
    print(f"错误: {result.errors}")

# 元数据
print(result.metadata)  # {"input_length": 10, "safety_level": "safe"}
```

### 严格模式 vs 普通模式

**普通模式**（`strict_mode=False`）：
- 警告不阻止执行
- 适合开发环境
- 更灵活

**严格模式**（`strict_mode=True`）：
- 警告也视为错误
- 适合生产环境
- 更安全

```python
# 普通模式
validator = InputValidator(strict_mode=False)
result = validator.validate("包含手机号 13812345678")
# result.is_valid = True, result.warnings = ["检测到敏感信息"]

# 严格模式
strict_validator = InputValidator(strict_mode=True)
result = strict_validator.validate("包含手机号 13812345678")
# result.is_valid = False, result.errors = ["检测到敏感信息"]
```

---

## 输出验证器

### 功能概述

`OutputValidator` 验证模型输出的安全性和质量：

1. **长度检查**：输出长度限制
2. **内容安全**：过滤不安全输出
3. **来源验证**：RAG 场景要求引用来源
4. **格式校验**：确保输出符合预期格式

### RAG 专用验证

```python
from core.guardrails import OutputValidator

# RAG 验证器（要求来源）
rag_validator = OutputValidator(
    require_sources=True,
    min_length=10,
    max_length=100000,
)

# 验证（必须提供 sources）
result = rag_validator.validate(
    output="这是基于文档的回答",
    sources=["doc1.pdf", "doc2.md"],
)

if result.is_valid:
    print("✅ 输出有效")
else:
    print(f"❌ 输出无效: {result.errors}")
```

### 来源使用检查

验证器会检查输出是否真的使用了提供的来源：

```python
# 简单实现：检查词汇重叠度
# 如果输出与来源的词汇重叠超过 30%，认为使用了该来源
```

---

## 结构化输出 Schema

### 概述

使用 Pydantic 定义各种场景的输出格式，提供：

1. **类型安全**：自动类型检查
2. **数据验证**：字段级别的验证规则
3. **序列化**：JSON 序列化/反序列化
4. **文档生成**：自动生成 JSON Schema

### RAGResponse

```python
from core.guardrails import RAGResponse

response = RAGResponse(
    answer="回答内容",
    sources=["doc1.pdf", "doc2.md"],
    confidence=0.95,
    metadata={"retrieved_chunks": 3},
)

# 访问字段
print(response.answer)
print(response.sources)

# 序列化
json_str = response.model_dump_json()

# 反序列化
response2 = RAGResponse.model_validate_json(json_str)
```

**字段说明**：
- `answer`: 回答内容（必填，最少 10 字符）
- `sources`: 引用来源列表（必填，至少 1 个）
- `confidence`: 置信度（可选，0-1 之间）
- `metadata`: 额外元数据（可选）

### StudyPlan

```python
from core.guardrails import StudyPlan, StudyPlanStep, DifficultyLevel

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
        StudyPlanStep(
            step_number=2,
            title="实践项目",
            description="实际项目练习",
            estimated_hours=32.0,
            resources=["教程"],
            key_concepts=["RAG", "Workflows"],
        ),
    ],
    prerequisites=["Python 基础"],
    learning_objectives=["掌握 LangChain"],
)
```

**验证规则**：
- 步骤编号必须从 1 开始连续递增
- 总时长必须大于 0
- 至少包含 1 个步骤

### ResearchReport

```python
from core.guardrails import ResearchReport, ResearchSection

report = ResearchReport(
    title="LangChain 研究报告",
    topic="LangChain 企业应用",
    summary="本报告深入研究了...",
    sections=[
        ResearchSection(
            section_number=1,
            title="引言",
            content="LangChain 是...",
            sources=["doc1.pdf"],
            key_findings=["发现1", "发现2"],
        ),
    ],
    conclusions=["结论1", "结论2"],
    references=["参考文献1", "参考文献2"],
)
```

### Quiz

```python
from core.guardrails import Quiz, QuizQuestion, QuestionType

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
            explanation="LangChain 是一个框架",
            points=1,
        ),
    ],
    total_points=1,
    passing_score=1,
    time_limit_minutes=30,
)
```

**验证规则**：
- 题目编号必须连续
- 选择题必须有至少 2 个选项
- 总分必须等于所有题目分值之和
- 及格分数不能超过总分

---

## Guardrails 中间件

### GuardrailsMiddleware

为 LangChain Runnable 添加 Guardrails 的中间件类。

```python
from core.guardrails import GuardrailsMiddleware, InputValidator, OutputValidator

# 创建中间件
middleware = GuardrailsMiddleware(
    input_validator=InputValidator(),
    output_validator=OutputValidator(),
    raise_on_error=True,
)

# 验证输入
filtered_input = middleware.validate_input("用户输入")

# 验证输出
filtered_output = middleware.validate_output("模型输出")
```

### create_guardrails_runnable

为现有 Runnable 添加 Guardrails：

```python
from langchain_core.runnables import RunnableLambda
from core.guardrails import create_guardrails_runnable

# 原始 Runnable
def my_function(input_data):
    return f"处理: {input_data}"

runnable = RunnableLambda(my_function)

# 添加 Guardrails
safe_runnable = create_guardrails_runnable(
    runnable,
    validate_input=True,
    validate_output=True,
    raise_on_error=True,
)

# 使用
result = safe_runnable.invoke("用户输入")
```

---

## 安全 RAG Agent

### SafeRAGAgent

集成 Guardrails 的 RAG Agent，提供：

1. **输入验证**：自动检查用户问题
2. **输出验证**：确保回答包含来源
3. **结构化输出**：返回 RAGResponse 对象
4. **异步支持**：支持异步和流式查询

### 核心方法

#### query()

```python
result = agent.query(
    query="什么是 LangChain？",
    return_structured=True,  # 返回 RAGResponse
)

# 结构化输出
print(result.answer)
print(result.sources)
print(result.confidence)
```

#### aquery()

```python
result = await agent.aquery(
    query="什么是 LangChain？",
    return_structured=True,
)
```

#### stream()

```python
for chunk in agent.stream("什么是 LangChain？"):
    print(chunk, end="", flush=True)
```

### 安全流程

```
用户输入
  ↓
输入验证（InputValidator）
  ↓
执行 RAG Agent
  ↓
提取来源
  ↓
输出验证（OutputValidator + 来源检查）
  ↓
返回结构化输出（RAGResponse）
```

---

## 安全 Workflow

### 安全节点包装器

为 LangGraph 节点添加 Guardrails：

#### with_input_guardrails

```python
from workflows.safe_nodes import with_input_guardrails

@with_input_guardrails(input_field="question")
def my_node(state):
    # 节点逻辑
    return state
```

#### with_output_guardrails

```python
from workflows.safe_nodes import with_output_guardrails

@with_output_guardrails(
    output_field="answer",
    require_sources=True,
)
def my_rag_node(state):
    # 节点逻辑
    return state
```

#### with_guardrails

同时添加输入和输出验证：

```python
from workflows.safe_nodes import with_guardrails

@with_guardrails(
    input_field="question",
    output_field="answer",
    require_sources=True,
)
def my_node(state):
    # 节点逻辑
    return state
```

### 安全学习工作流

```python
from workflows.safe_study_flow import create_safe_study_flow_graph

graph = create_safe_study_flow_graph(
    enable_human_review=True,  # 人工审核
    strict_mode=False,
)

# 运行
config = {"configurable": {"thread_id": "user_123"}}
result = graph.invoke({
    "question": "如何学习 LangChain？",
    "messages": []
}, config)
```

### 人工审核节点

```python
from workflows.safe_nodes import create_human_review_node

human_review = create_human_review_node(
    review_field="plan",
    approval_required=True,
)

workflow.add_node("human_review", human_review)
```

---

## 安全 DeepAgent

### SafeDeepResearchAgent

为 DeepAgent 添加安全检查：

1. **输入验证**：检查研究问题
2. **工具调用审核**：记录所有工具调用
3. **输出验证**：确保报告质量
4. **人工审核**：关键步骤可暂停

### 使用示例

```python
from deep_research.safe_deep_agent import create_safe_deep_research_agent

agent = create_safe_deep_research_agent(
    thread_id="research_123",
    enable_web_search=True,
    enable_human_review=True,
    strict_mode=True,
)

# 执行研究
report = agent.research("分析 LangChain 1.0 的新特性")

# 结构化报告
print(report.title)
print(report.summary)
for section in report.sections:
    print(f"{section.title}: {section.content}")
print(report.conclusions)
print(report.references)

# 查看工具调用日志
log = agent.get_tool_calls_log()
```

### 安全流程

```
研究问题
  ↓
输入验证
  ↓
人工审核（可选）
  ↓
执行研究（DeepAgent）
  ↓
提取来源
  ↓
输出验证
  ↓
返回结构化报告（ResearchReport）
```

---

## 总结

Stage 5 提供了完整的安全防护体系：

1. **多层防护**：输入验证 + 输出验证 + 内容过滤
2. **灵活配置**：普通模式 vs 严格模式
3. **结构化输出**：Pydantic Schema 确保数据质量
4. **全面集成**：RAG Agent + Workflow + DeepAgent
5. **可扩展性**：易于自定义和扩展

系统现在具备企业级的安全能力！

