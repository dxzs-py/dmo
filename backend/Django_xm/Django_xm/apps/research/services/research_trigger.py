"""
深度研究任务触发服务

从 deep_chat_service.py 拆分出的深度研究任务触发逻辑：
- 创建深度研究任务记录
- 启动 Celery 异步任务
"""
import logging
import uuid

logger = logging.getLogger(__name__)


class ResearchTriggerService:
    """深度研究任务触发服务"""

    def __init__(self, chat_service=None):
        """
        Args:
            chat_service: 可选的 ChatService 实例，用于访问聊天服务方法
        """
        self._chat_service = chat_service

    async def create_task(
        self, query: str, session_id: str | None = None,
        use_web_search: bool = True,
        retriever_tool=None,
        task_title: str | None = None,
        chat_message_id: str | None = None,
    ) -> str:
        """创建深度研究任务并返回 task_id（不执行研究）"""
        from asgiref.sync import sync_to_async
        from django.contrib.auth import get_user_model

        from Django_xm.apps.research.services.task_manager import get_task_manager
        User = get_user_model()

        thread_id = f"research_{uuid.uuid4().hex[:12]}"
        task_manager = get_task_manager()

        created_by = None
        if self._chat_service.user_id:
            try:
                @sync_to_async(thread_sensitive=True)
                def _get_user():
                    return User.objects.get(id=self._chat_service.user_id)

                created_by = await _get_user()
            except User.DoesNotExist:
                pass

        @sync_to_async(thread_sensitive=True)
        def _create_task():
            task_manager.create_task(
                thread_id,
                task_title or query,
                enable_web_search=use_web_search,
                enable_doc_analysis=retriever_tool is not None,
                created_by=created_by,
                session_id=session_id,
                chat_message_id=chat_message_id,
            )

        await _create_task()
        return thread_id

    async def start_celery(
        self, query: str, session_id: str | None = None,
        use_web_search: bool = True,
        retriever_tool=None,
        extra_tools: list | None = None,
        enable_deep_thinking: bool = False,
        provider_id: str | None = None,
        model_name: str | None = None,
        task_id: str | None = None,
        knowledge_base_ids: list | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        special_params: dict | None = None,
        continue_task_id: str | None = None,
        task_title: str | None = None,
    ) -> str:
        """启动深度研究 Celery 任务并返回 thread_id（不订阅 Redis 频道）

        架构说明：
            chat SSE 在深度研究模式下应"立即返回"——发送 deep_research 事件后立即结束流，
            研究过程由 Celery worker 异步执行，研究结果通过两条独立链路回到前端：
              1. writeback_to_chat_message：回写 final_report 到 ChatMessage（持久化）
              2. broadcast_stream_completed：广播 stream_completed WebSocket 事件（实时通知）

        本方法仅负责：
          - 更新 knowledge_base_ids（如提供）
          - 提取 MCP/tool 配置
          - 启动 Celery 任务 run_research_task.delay()
          - 更新 ResearchTask.celery_task_id

        v5：已删除 stream_deep_research_task / _wait_for_research_result_streaming
            （自研 Redis Pub/Sub 审批通道），chat SSE 立即返回，研究结果通过
            writeback_to_chat_message + WebSocket stream_completed 事件回写。

        Args:
            query: 用户查询
            session_id: 关联的聊天会话 ID
            use_web_search: 是否启用网络搜索
            retriever_tool: 知识库 retriever 工具（仅用于判断 enable_doc_analysis）
            extra_tools: 额外工具列表（用于提取 MCP/tool 配置）
            enable_deep_thinking: 是否启用深度思考
            provider_id: LLM provider ID
            model_name: LLM 模型名
            task_id: 已创建的任务 ID（如为 None 则内部创建新任务）
            knowledge_base_ids: 知识库 ID 列表
            temperature: LLM 温度
            max_tokens: LLM 最大 token
            special_params: LLM 特殊参数
            continue_task_id: 续写任务 ID
            task_title: 任务标题

        Returns:
            thread_id: 深度研究任务 ID
        """
        from asgiref.sync import sync_to_async

        from Django_xm.tasks.deep_research import run_research_task

        thread_id = task_id

        if knowledge_base_ids:
            @sync_to_async(thread_sensitive=True)
            def _update_kb():
                from Django_xm.apps.research.models import ResearchTask
                ResearchTask.objects.filter(task_id=thread_id).update(
                    knowledge_base_ids=knowledge_base_ids,
                )
            await _update_kb()

        use_mcp = any(
            (getattr(t, 'metadata', {}) or {}).get('is_mcp_tool', False)
            for t in (extra_tools or [])
        )
        selected_mcp_servers = []
        selected_tool_names = []
        for t in (extra_tools or []):
            meta = getattr(t, 'metadata', {}) or {}
            t_name = getattr(t, 'name', '')
            # 跳过 retriever_tool，它们由 knowledge_base_ids 在 worker 端重建
            if t_name and t_name not in selected_tool_names and not t_name.startswith('knowledge_base_'):
                selected_tool_names.append(t_name)
            server_name = meta.get('mcp_server_name', '')
            if server_name and server_name not in selected_mcp_servers:
                selected_mcp_servers.append(server_name)

        celery_result = run_research_task.delay(
            thread_id=thread_id,
            query=query,
            enable_web_search=use_web_search,
            enable_doc_analysis=retriever_tool is not None,
            knowledge_base_ids=knowledge_base_ids,
            user_id=self._chat_service.user_id,
            use_mcp=use_mcp or bool(selected_mcp_servers),
            selected_mcp_servers=selected_mcp_servers or None,
            selected_tools=selected_tool_names or None,
            provider_id=provider_id,
            model_name=model_name,
            enable_deep_thinking=enable_deep_thinking,
            temperature=temperature,
            max_tokens=max_tokens,
            special_params=special_params,
            continue_task_id=continue_task_id,
        )

        from Django_xm.apps.research.models import ResearchTask

        @sync_to_async(thread_sensitive=True)
        def _update_celery_task_id():
            ResearchTask.objects.filter(task_id=thread_id).update(
                celery_task_id=celery_result.id,
            )

        await _update_celery_task_id()

        logger.info(
            f"[DeepChat] Celery 深度研究任务已启动: thread_id={thread_id}, "
            f"celery_task_id={celery_result.id}, session_id={session_id}"
        )
        return thread_id
