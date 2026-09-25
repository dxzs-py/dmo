"""
AI Engine 服务层 - 提供模型管理、用量追踪等核心服务

包含：
- LLM 工厂（模型创建、预设、流式）
- LLM Fallback 机制（运行时降级、结构化输出 fallback）
- LLM 缓存与速率限制
- 用量追踪（Token 用量统计）
- Token 追踪（模型调用 Token 统计）
- 项目上下文检测
- 建议生成

Agent 创建请使用 Django_xm.apps.agent_hub.create()（旧的 BaseAgent / create_base_agent 已删除）。
"""
