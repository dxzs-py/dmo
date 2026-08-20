"""
Agent 管理服务

从 chat_service.py 拆分出的 Agent 创建、配置和生命周期管理逻辑：
- Agent 创建（含 Checkpointer / Store 注入）
- 线程配置构建
"""

import logging
import uuid
from typing import Any

from .models_context import ChatContext

logger = logging.getLogger(__name__)


class AgentService:
    CHECKPOINTER_ENABLED = True

    def __init__(self, user_id: int | None = None):
        self.user_id = user_id
        self._checkpointer = None
        self._store = None

        if self.CHECKPOINTER_ENABLED:
            try:
                from Django_xm.apps.ai_engine.services.checkpointer_factory import get_store

                # 只初始化 Store（同步，全局缓存），不再创建同步 Checkpointer。
                # 流式聊天使用异步 Checkpointer，同步实例浪费连接且从不使用。
                self._store = get_store()
                logger.info("AgentService: Store 已初始化")
            except Exception as e:
                logger.warning(f"AgentService: Store 初始化失败: {e}")

    def build_thread_config(self, session_id: str | None = None, **kwargs) -> dict[str, Any]:
        thread_id = session_id or str(uuid.uuid4())
        config: dict[str, Any] = {
            "configurable": {"thread_id": thread_id, "checkpoint_ns": ""},
            "recursion_limit": kwargs.pop("recursion_limit", 500),
        }
        config.update(kwargs)
        return config

    async def create_agent_with_memory(
        self,
        data: dict[str, Any],
        prompt_mode: str = "agent",
        tool_config: dict[str, Any] | None = None,
        tools: list | None = None,
    ) -> tuple:
        from Django_xm.apps.agent_hub import AgentConfig, AgentType
        from Django_xm.apps.agent_hub import create as agent_hub_create
        from Django_xm.apps.ai_engine.services.checkpointer_factory import get_async_checkpointer

        session_id = data.get("session_id")
        use_checkpointer = self.CHECKPOINTER_ENABLED and session_id

        checkpointer = None
        store = None
        context_schema = None

        if use_checkpointer:
            try:
                async_cp = await get_async_checkpointer()
                if async_cp is not None:
                    checkpointer = async_cp
                    logger.info("流式聊天使用异步 Checkpointer")
                else:
                    use_checkpointer = False
                    logger.warning("异步 Checkpointer 不可用，禁用 Checkpointer 以避免 astream 异常")
            except Exception as e:
                logger.warning(f"获取异步 Checkpointer 失败，禁用 Checkpointer: {e}")
                use_checkpointer = False

            if use_checkpointer and checkpointer is not None:
                if self._store is not None:
                    store = self._store
                    context_schema = ChatContext
                    logger.info(f"Agent 已注入 Store + context_schema (user_id={self.user_id})")

        config = AgentConfig(
            agent_type=AgentType.BASE,
            provider_id=data.get("provider_id"),
            model_name=data.get("model_name"),
            temperature=data.get("temperature"),
            max_tokens=data.get("max_tokens"),
            special_params=data.get("special_params"),
            tools=tools,
            tool_config=tool_config,
            system_prompt=data.get("_research_system_prompt"),
            prompt_mode=prompt_mode,
            checkpointer=checkpointer,
            store=store,
            context_schema=context_schema,
            user_id=self.user_id,
            session_id=session_id,
            enable_guardrails=data.get("enable_guardrails", False),
            guardrails_strict_mode=data.get("guardrails_strict_mode", False),
            enable_pii=data.get("enable_pii", False),
            enable_human_in_loop=data.get("enable_human_in_loop", False),
        )

        agent = await agent_hub_create(config)
        thread_config = self.build_thread_config(session_id) if use_checkpointer else None
        # 返回 config：调用方从 config.model 读取 AgentFactory 统一创建的模型包装实例
        return agent, thread_config, use_checkpointer, config
