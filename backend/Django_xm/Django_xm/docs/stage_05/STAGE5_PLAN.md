# Stage 5: Guardrails / 安全与结构化输出 - 开发计划

## 📋 目标

为整个 LC-StudyLab 系统添加安全防护层和结构化输出能力，确保：
1. 用户输入安全（防止 prompt injection、敏感信息泄露等）
2. 模型输出安全（内容过滤、格式校验）
3. RAG 场景的引用来源约束
4. 关键操作的人工审核机制

## 🎯 核心功能

### 1. 输入 Guardrails
- **Prompt Injection 检测**：识别并阻止恶意提示词注入
- **敏感信息过滤**：检测并脱敏个人隐私信息（手机号、邮箱、身份证等）
- **内容安全检查**：过滤暴力、违法、仇恨言论等不当内容
- **长度限制**：防止超长输入导致的资源消耗

### 2. 输出 Guardrails
- **内容安全过滤**：确保输出内容符合安全规范
- **事实性检查**：对关键信息进行验证
- **格式校验**：确保输出符合预期格式
- **引用来源验证**（RAG 专用）：确保回答必须基于检索到的文档

### 3. 结构化输出
- **Pydantic Schema 定义**：为不同场景定义输出模型
  - RAG 回答格式（answer + sources）
  - 学习计划格式
  - 研究报告格式
  - 测验题格式
- **自动校验**：使用 Pydantic 进行类型和格式校验
- **错误处理**：输出不符合格式时的重试机制

### 4. 中间件集成
- **Agent 级别**：在 base_agent 中集成 guardrails
- **Workflow 级别**：在 LangGraph 节点前后添加检查
- **DeepAgent 级别**：在关键工具调用前添加人工审核

## 🏗️ 实现架构

```
core/guardrails/
├── __init__.py
├── content_filters.py      # 内容安全过滤器
├── input_validators.py     # 输入验证器
├── output_validators.py    # 输出验证器
├── schemas.py              # Pydantic 结构化输出模型
└── middleware.py           # Guardrails 中间件封装
```

## 📝 开发步骤

### Step 1: 实现核心 Guardrails 模块
- [x] 创建 guardrails 目录结构
- [ ] 实现输入验证器（prompt injection、敏感信息、内容安全）
- [ ] 实现输出验证器（内容安全、格式校验）
- [ ] 实现内容过滤器（通用过滤逻辑）

### Step 2: 定义结构化输出 Schema
- [ ] RAG 回答 Schema（RAGResponse）
- [ ] 学习计划 Schema（StudyPlan）
- [ ] 研究报告 Schema（ResearchReport）
- [ ] 测验题 Schema（Quiz）

### Step 3: 集成到现有系统
- [ ] 在 base_agent 中添加 guardrails 支持
- [ ] 在 RAG Agent 中启用结构化输出
- [ ] 在 LangGraph workflow 节点中添加 guardrails
- [ ] 在 DeepAgent 中添加工具调用审核

### Step 4: 测试与文档
- [ ] 编写单元测试
- [ ] 编写集成测试
- [ ] 编写使用文档
- [ ] 编写最佳实践指南

## 🔧 技术选型

### 基础技术
- **Pydantic v2**：结构化输出和数据验证
- **LangChain Guardrails**：官方 guardrails 支持
- **正则表达式**：敏感信息检测
- **关键词匹配**：简单的内容安全检查

### 可选集成（未来扩展）
- **GuardrailsAI**：更强大的 guardrails 框架
- **Pangea**：云端安全服务
- **NeMo Guardrails**：NVIDIA 的 guardrails 方案

## 📊 验收标准

1. ✅ 所有 Agent 都支持可配置的 guardrails
2. ✅ RAG 回答必须包含引用来源
3. ✅ 能够检测并阻止常见的 prompt injection 攻击
4. ✅ 敏感信息能够被自动脱敏
5. ✅ 关键操作支持人工审核机制
6. ✅ 结构化输出自动校验并提供友好错误提示
7. ✅ 完整的测试覆盖和文档

## 🚀 快速开始（完成后）

```bash
# 测试 guardrails 功能
cd backend
python scripts/test_guardrails.py

# 测试结构化输出
python scripts/test_structured_output.py

# 测试集成效果
python scripts/test_safe_rag.py
```

## 📚 参考文档

- LangChain Guardrails: https://docs.langchain.com/oss/python/langchain/guardrails
- Structured Output: https://docs.langchain.com/oss/python/langchain/structured-output
- Pydantic: https://docs.pydantic.dev/latest/
- Human-in-the-loop: https://docs.langchain.com/oss/python/langchain/human-in-the-loop

## 🎯 预期成果

完成本阶段后，LC-StudyLab 将具备：
1. 企业级的安全防护能力
2. 可靠的结构化输出
3. 灵活的 guardrails 配置
4. 完整的安全最佳实践示例

这将使系统可以安全地部署到生产环境中。

