"""
深度模式聊天服务

从 chat_service.py 拆分出的深度研究相关逻辑：
- 深度研究任务执行（Celery + Redis 订阅）
- 无工具回退模式
"""

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from asgiref.sync import sync_to_async
from langchain_core.messages import AIMessage, ToolMessage

from Django_xm.apps.ai_engine.services.cost_tracker import TokenDetailTracker
from Django_xm.apps.ai_engine.services.token_counter import TokenUsageCallbackHandler
from Django_xm.apps.research.services.cross_app import REDIS_CHANNEL_PREFIX

from ..utils import convert_chat_history
from .stream_helpers import update_usage_and_tokens

logger = logging.getLogger(__name__)

_RESEARCH_TIMEOUT = 1800

_redis_pool = None


def _get_redis_pool(broker_url):
    global _redis_pool
    if _redis_pool is None:
        import redis as redis_lib

        _redis_pool = redis_lib.ConnectionPool.from_url(broker_url, max_connections=10)
    return _redis_pool


# =============================================================================
# sync_to_async 预包装函数
# 将原本在 async def 方法内部每次调用时重新创建的 @sync_to_async
# 局部闭包提取为模块级函数，装饰器在模块加载时仅执行一次。
# =============================================================================


@sync_to_async(thread_sensitive=True)
def _get_user_sync(user_id):
    """同步获取 User 对象"""
    from django.contrib.auth import get_user_model

    User = get_user_model()
    return User.objects.get(id=user_id)


@sync_to_async(thread_sensitive=True)
def _create_task_sync(task_manager, thread_id, title, enable_web_search, enable_doc_analysis, created_by, session_id):
    """同步创建研究任务"""
    task_manager.create_task(
        thread_id,
        title,
        enable_web_search=enable_web_search,
        enable_doc_analysis=enable_doc_analysis,
        created_by=created_by,
        session_id=session_id,
    )


@sync_to_async(thread_sensitive=True)
def _update_kb_sync(thread_id, knowledge_base_ids):
    """同步更新研究任务的知识库字段"""
    from Django_xm.apps.research.services.cross_app import update_research_task_fields

    update_research_task_fields(thread_id, knowledge_base_ids=knowledge_base_ids)


@sync_to_async(thread_sensitive=True)
def update_research_task_fields_async(thread_id: str, **fields):
    """异步更新研究任务字段（供 create_deep_research_task 存储工具选择等额外字段）"""
    from Django_xm.apps.research.services.cross_app import update_research_task_fields

    update_research_task_fields(thread_id, **fields)


@sync_to_async(thread_sensitive=True)
def update_chat_message_research_task_id_async(
    assistant_message_id: int,
    research_task_id: str,
) -> None:
    """异步更新 ChatMessage.research_task_id 字段"""
    from django.apps import apps

    ChatMessage = apps.get_model("chat", "ChatMessage")
    ChatMessage.objects.filter(id=assistant_message_id).update(
        research_task_id=research_task_id,
    )


@sync_to_async(thread_sensitive=True)
def _update_celery_task_id_sync(thread_id, celery_task_id):
    """同步更新研究任务的 Celery task ID"""
    from Django_xm.apps.research.services.cross_app import update_research_task_fields

    update_research_task_fields(thread_id, celery_task_id=celery_task_id)


@sync_to_async(thread_sensitive=True)
def _mark_timeout_sync(task_manager, thread_id):
    """同步标记研究任务超时失败"""
    task_manager.update_task_status(
        thread_id,
        {"status": "failed", "error_message": f"研究任务超时（{_RESEARCH_TIMEOUT}秒）"},
    )


@sync_to_async(thread_sensitive=True)
def _update_status_sync(task_manager, thread_id, final_report):
    """同步更新研究任务为已完成状态"""
    task_manager.update_task_status(
        thread_id,
        {"status": "completed", "final_report": final_report},
    )


@sync_to_async
def _load_approvals_sync(thread_id):
    """同步从 DB 加载审批记录"""
    from Django_xm.apps.approvals.models import Approval

    return list(
        Approval.objects.filter(
            source=Approval.SOURCE_DEEP_RESEARCH,
            source_id=thread_id,
        ).order_by("created_at")
    )


class DeepChatService:
    """深度模式聊天服务"""

    def __init__(self, chat_service):
        self._chat_service = chat_service

    @staticmethod
    def _clean_tool_call_messages(messages: list) -> list:
        """清理消息历史中未完成的 tool_calls

        当 agent 执行失败回退到无工具模式时，对话历史中可能包含
        assistant message（含 tool_calls）但没有对应的 ToolMessage 响应。
        OpenAI API 要求每个 tool_call_id 都必须有对应的 ToolMessage，
        否则会报 400 错误。此方法移除这些未完成的 tool_calls。
        """
        if not messages:
            return messages

        responded_ids = set()
        for msg in messages:
            if isinstance(msg, ToolMessage):
                responded_ids.add(msg.tool_call_id)

        cleaned: list[BaseMessage] = []
        for msg in messages:
            if isinstance(msg, AIMessage) and hasattr(msg, "tool_calls") and msg.tool_calls:
                unresponded = [tc for tc in msg.tool_calls if tc.get("id") not in responded_ids]
                if unresponded:
                    if msg.content:
                        cleaned.append(AIMessage(content=msg.content))
                    continue
            if isinstance(msg, ToolMessage):
                cleaned.append(msg)
                continue
            cleaned.append(msg)

        return cleaned

    async def create_deep_research_task(
        self,
        query: str,
        session_id: str | None = None,
        use_web_search: bool = True,
        retriever_tool=None,
        task_title: str | None = None,
        selected_tools: list | None = None,
        use_mcp: bool = False,
        selected_mcp_servers: list | None = None,
    ) -> str:
        """创建深度研究任务并返回 task_id（不执行研究）

        Args:
            selected_tools: 用户选择的工具名称列表（不含 knowledge_base_ 前缀的检索工具）
            use_mcp: 是否启用 MCP 工具
            selected_mcp_servers: 选中的 MCP 服务器名称列表
        """
        from django.contrib.auth import get_user_model

        from Django_xm.apps.research.services.cross_app import get_research_task_manager, update_research_task_fields

        User = get_user_model()

        thread_id = f"research_{uuid.uuid4().hex[:12]}"
        task_manager = get_research_task_manager()

        created_by = None
        if self._chat_service.user_id:
            try:
                created_by = await _get_user_sync(self._chat_service.user_id)
            except User.DoesNotExist:
                pass

        await _create_task_sync(
            task_manager,
            thread_id,
            task_title or query,
            use_web_search,
            retriever_tool is not None,
            created_by,
            session_id,
        )

        # B1: 将工具选择写入 ResearchTask DB，确保恢复路径（research_resume_task）
        # 能通过 _build_research_agent_from_task 读取 selected_tools/use_mcp/selected_mcp_servers
        if selected_tools or use_mcp or selected_mcp_servers:
            update_fields = {}
            if selected_tools is not None:
                update_fields["selected_tools"] = selected_tools
            if use_mcp:
                update_fields["use_mcp"] = use_mcp
            if selected_mcp_servers is not None:
                update_fields["selected_mcp_servers"] = selected_mcp_servers
            await update_research_task_fields_async(thread_id, **update_fields)

        return thread_id

    async def run_deep_research_task(
        self,
        query: str,
        session_id: str | None = None,
        usage_tracker=None,
        token_detail_tracker=None,
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
    ) -> dict[str, Any]:
        """
        通过 Celery 执行深度研究，使用 Redis Pub/Sub 等待结果

        流程：
        1. 创建/复用任务记录
        2. 触发 Celery 任务（publish_to_redis=True）
        3. 订阅 Redis channel 等待结果
        4. 返回标准化结果
        """
        from Django_xm.apps.research.services.cross_app import get_research_task_manager
        from Django_xm.tasks.deep_research import run_research_task

        thread_id = task_id or f"research_{uuid.uuid4().hex[:12]}"
        task_manager = get_research_task_manager()

        if not task_id:
            from django.contrib.auth import get_user_model

            User = get_user_model()

            created_by = None
            if self._chat_service.user_id:
                try:
                    created_by = await _get_user_sync(self._chat_service.user_id)
                except User.DoesNotExist:
                    pass

            await _create_task_sync(
                task_manager,
                thread_id,
                task_title or query,
                use_web_search,
                retriever_tool is not None,
                created_by,
                session_id,
            )

        if knowledge_base_ids:
            await _update_kb_sync(thread_id, knowledge_base_ids)

        use_mcp = any((getattr(t, "metadata", {}) or {}).get("is_mcp_tool", False) for t in (extra_tools or []))
        selected_mcp_servers = []
        selected_tool_names = []
        for t in extra_tools or []:
            meta = getattr(t, "metadata", {}) or {}
            t_name = getattr(t, "name", "")
            # 跳过 retriever_tool，它们由 knowledge_base_ids 在 worker 端重建
            if t_name and t_name not in selected_tool_names and not t_name.startswith("knowledge_base_"):
                selected_tool_names.append(t_name)
            server_name = meta.get("mcp_server_name", "")
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
            publish_to_redis=True,
            session_id=session_id,
        )

        await _update_celery_task_id_sync(thread_id, celery_result.id)

        result_data = await self._wait_for_research_result(thread_id)

        if result_data is None:
            await _mark_timeout_sync(task_manager, thread_id)
            return {
                "success": False,
                "final_report": f"深度研究超时，请到深度研究模块查看任务 {thread_id}",
                "files": None,
                "task_id": thread_id,
                "session_id": session_id,
                "research_summary": "",
            }

        if usage_tracker and token_detail_tracker and result_data.get("usage_data"):
            usage_data = result_data["usage_data"]
            usage_tracker.add_input_tokens(usage_data.get("prompt_tokens", 0))
            usage_tracker.add_output_tokens(usage_data.get("completion_tokens", 0))
            token_detail_tracker.update_from_metadata(
                {
                    "usage_metadata": {
                        "input_tokens": usage_data.get("prompt_tokens", 0),
                        "output_tokens": usage_data.get("completion_tokens", 0),
                    }
                }
            )
            token_detail_tracker.finish_record()

        await _update_status_sync(task_manager, thread_id, result_data.get("final_report", ""))

        final_report = result_data.get("final_report", "")
        research_summary = final_report[:2000] if final_report else ""
        return {
            "success": result_data.get("success", True),
            "final_report": final_report,
            "files": result_data.get("files"),
            "task_id": thread_id,
            "session_id": session_id,
            "research_summary": research_summary,
        }

    async def stream_deep_research_task(
        self,
        query: str,
        session_id: str | None = None,
        usage_tracker=None,
        token_detail_tracker=None,
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
    ) -> AsyncGenerator[dict[str, Any], None]:
        """
        通过 Celery 执行深度研究，支持流式审批事件

        与 run_deep_research_task 逻辑一致，但使用 _wait_for_research_result_streaming
        同时监听研究结果和审批频道。审批事件会以 {"type": "approval", "data": ...}
        形式 yield，最终研究结果以 {"_is_result": True, ...} 形式 yield。
        """
        from Django_xm.apps.research.services.cross_app import get_research_task_manager
        from Django_xm.tasks.deep_research import run_research_task

        thread_id = task_id or f"research_{uuid.uuid4().hex[:12]}"
        task_manager = get_research_task_manager()

        if not task_id:
            from django.contrib.auth import get_user_model

            User = get_user_model()

            created_by = None
            if self._chat_service.user_id:
                try:
                    created_by = await _get_user_sync(self._chat_service.user_id)
                except User.DoesNotExist:
                    pass

            await _create_task_sync(
                task_manager,
                thread_id,
                task_title or query,
                use_web_search,
                retriever_tool is not None,
                created_by,
                session_id,
            )

        if knowledge_base_ids:
            await _update_kb_sync(thread_id, knowledge_base_ids)

        use_mcp = any((getattr(t, "metadata", {}) or {}).get("is_mcp_tool", False) for t in (extra_tools or []))
        selected_mcp_servers = []
        selected_tool_names = []
        for t in extra_tools or []:
            meta = getattr(t, "metadata", {}) or {}
            t_name = getattr(t, "name", "")
            # 跳过 retriever_tool，它们由 knowledge_base_ids 在 worker 端重建
            if t_name and t_name not in selected_tool_names and not t_name.startswith("knowledge_base_"):
                selected_tool_names.append(t_name)
            server_name = meta.get("mcp_server_name", "")
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
            publish_to_redis=True,
            session_id=session_id,
        )

        await _update_celery_task_id_sync(thread_id, celery_result.id)

        # 使用流式等待替代阻塞等待，审批事件直接 yield 给上层
        result_data = None
        async for event in self._wait_for_research_result_streaming(thread_id):
            if event.get("_is_result"):
                result_data = event
                break
            else:
                # 审批事件，yield 给上层
                yield event

        if result_data is None:
            await _mark_timeout_sync(task_manager, thread_id)
            yield {
                "success": False,
                "final_report": f"深度研究超时，请到深度研究模块查看任务 {thread_id}",
                "files": None,
                "task_id": thread_id,
                "session_id": session_id,
                "research_summary": "",
                "_is_result": True,
            }
            return

        if usage_tracker and token_detail_tracker and result_data.get("usage_data"):
            usage_data = result_data["usage_data"]
            usage_tracker.add_input_tokens(usage_data.get("prompt_tokens", 0))
            usage_tracker.add_output_tokens(usage_data.get("completion_tokens", 0))
            token_detail_tracker.update_from_metadata(
                {
                    "usage_metadata": {
                        "input_tokens": usage_data.get("prompt_tokens", 0),
                        "output_tokens": usage_data.get("completion_tokens", 0),
                    }
                }
            )
            token_detail_tracker.finish_record()

        await _update_status_sync(task_manager, thread_id, result_data.get("final_report", ""))

        final_report = result_data.get("final_report", "")
        research_summary = final_report[:2000] if final_report else ""
        yield {
            "success": result_data.get("success", True),
            "final_report": final_report,
            "files": result_data.get("files"),
            "task_id": thread_id,
            "session_id": session_id,
            "research_summary": research_summary,
            "_is_result": True,
        }

    async def _wait_for_research_result(self, thread_id: str) -> dict[str, Any] | None:
        """订阅 Redis channel 等待 Celery 任务发布研究结果"""
        import redis as redis_lib
        from django.conf import settings as django_settings

        channel = f"{REDIS_CHANNEL_PREFIX}{thread_id}"
        broker_url = getattr(django_settings, "CELERY_BROKER_URL", "")

        if not broker_url:
            logger.error("CELERY_BROKER_URL 未配置，无法订阅研究结果")
            return None

        pubsub = None
        try:
            r = redis_lib.Redis(connection_pool=_get_redis_pool(broker_url))
            pubsub = r.pubsub()
            pubsub.subscribe(channel)

            logger.info(f"订阅研究结果: {channel}, 超时={_RESEARCH_TIMEOUT}s")

            result = await asyncio.wait_for(
                self._listen_pubsub(pubsub, channel),
                timeout=_RESEARCH_TIMEOUT,
            )
            return result
        except TimeoutError:
            logger.warning(f"等待研究结果超时: {channel}")
            return None
        except Exception:
            logger.exception("订阅研究结果异常")
            return None
        finally:
            if pubsub:
                try:
                    pubsub.unsubscribe(channel)
                    pubsub.close()
                except Exception:  # noqa: S110  # cleanup, pubsub 资源释放失败可忽略
                    pass

    @staticmethod
    async def _listen_pubsub(pubsub, channel: str) -> dict[str, Any]:
        """异步监听 Redis Pub/Sub 消息"""
        loop = asyncio.get_running_loop()

        def _get_message():
            while True:
                msg = pubsub.get_message(timeout=1.0)
                if msg and msg["type"] == "message":
                    return msg

        while True:
            msg = await loop.run_in_executor(None, _get_message)
            if msg and msg["type"] == "message":
                data = msg["data"]
                if isinstance(data, bytes):
                    data = data.decode("utf-8")
                return json.loads(data)

    async def _wait_for_research_result_streaming(self, thread_id: str) -> AsyncGenerator[dict[str, Any], None]:
        """监听研究结果的 Redis 频道 + 从 DB 读取审批历史（Path D）。

        Path D 变更：
        - 审批历史从 DB（Approval 模型）读取，不再从 Redis List
        - 实时审批事件通过 WebSocket 推送，此处仅监听研究结果
        - 研究结果仍通过 Redis Pub/Sub 发布
        """
        import redis as redis_lib
        from django.conf import settings as django_settings

        result_channel = f"{REDIS_CHANNEL_PREFIX}{thread_id}"
        broker_url = getattr(django_settings, "CELERY_BROKER_URL", "")

        if not broker_url:
            logger.error("CELERY_BROKER_URL 未配置，无法订阅研究结果")
            return

        # Path D：从 DB 读取历史审批并 yield（刷新/重连恢复场景）
        # 实时审批事件统一通过 WebSocket 推送（与 tool 事件一致）
        try:
            approvals = await _load_approvals_sync(thread_id)
            for approval in approvals:
                extra = approval.extra if isinstance(approval.extra, dict) else {}
                approval_data = {
                    "interrupt_id": approval.interrupt_id,
                    "tool_name": approval.tool_name or "",
                    "title": approval.title or "",
                    "description": approval.description or "",
                    "operation": approval.operation or "",
                    "danger_level": approval.danger_level or "medium",
                    "action": approval.action or "confirm",
                    "parameters": approval.parameters or {},
                    "state": approval.state,
                    "tool_call_id": extra.get("tool_call_id", ""),
                    "graph_interrupt_id": extra.get("graph_interrupt_id", ""),
                    "langgraph_resume_id": extra.get("langgraph_resume_id", ""),
                    "risk_level": extra.get("risk_level", ""),
                }
                if approval.user_input:
                    approval_data["user_input"] = approval.user_input
                yield {"type": "approval_history", "data": approval_data}
        except Exception as e:
            logger.warning(f"读取历史审批失败: {e}")

        pubsub = None
        try:
            r = redis_lib.Redis(connection_pool=_get_redis_pool(broker_url))

            pubsub = await asyncio.to_thread(r.pubsub)
            await asyncio.to_thread(pubsub.subscribe, result_channel)

            logger.info(f"订阅研究结果: {result_channel}, 超时={_RESEARCH_TIMEOUT}s")

            start_time = time.time()

            while True:
                elapsed = time.time() - start_time
                if elapsed > _RESEARCH_TIMEOUT:
                    logger.warning(f"等待研究结果超时: {thread_id}")
                    return

                # pubsub.get_message 是同步阻塞调用（timeout=1.0 最多阻塞 1s），
                # 必须通过 asyncio.to_thread 卸载到线程池，避免冻结事件循环
                msg = await asyncio.to_thread(pubsub.get_message, timeout=1.0)

                if msg and msg["type"] == "message":
                    channel = msg.get("channel", b"")
                    if isinstance(channel, bytes):
                        channel = channel.decode("utf-8")
                    data = msg["data"]
                    if isinstance(data, bytes):
                        data = data.decode("utf-8")
                    parsed = json.loads(data)

                    if channel == result_channel:
                        # 研究结果，标记后 yield
                        parsed["_is_result"] = True
                        yield parsed
                        return

                await asyncio.sleep(0.1)
        except Exception:
            logger.exception("订阅研究结果异常")
        finally:
            if pubsub:
                try:
                    await asyncio.to_thread(pubsub.unsubscribe)
                    await asyncio.to_thread(pubsub.close)
                except Exception:  # noqa: S110  # cleanup, pubsub 资源释放失败可忽略
                    pass

    async def _stream_without_tools(
        self,
        model_instance,
        data: dict[str, Any],
        usage_tracker,
        token_detail_tracker: TokenDetailTracker | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        chat_history = data.get("chat_history", [])
        chat_history, _ce_metadata = self._chat_service._apply_context_engineering(
            chat_history,
            data.get("message", ""),
            mode=data.get("mode", "agent"),
            model_name=data.get("model_name"),
        )
        langchain_chat_history = convert_chat_history(chat_history)

        messages = []
        if langchain_chat_history:
            messages.extend(langchain_chat_history)

        messages = self._clean_tool_call_messages(messages)

        human_msg = await self._chat_service._acreate_human_message(data)
        messages.append(human_msg)

        current_message_content = ""
        accumulated_reasoning: dict[str, str] = {"content": ""}
        thinking_start_time = time.time()

        with TokenUsageCallbackHandler() as cb:
            from Django_xm.apps.ai_engine.services.llm_fallback import FallbackDetectionCallback

            bound_model = getattr(model_instance, "bound", model_instance)
            fb_callback = FallbackDetectionCallback(
                expected_provider=data.get("provider_id", "") or getattr(bound_model, "_provider_id", "") or "",
                expected_model=data.get("model_name", "")
                or getattr(bound_model, "model_name", "")
                or getattr(bound_model, "model", "")
                or "",
            )
            try:
                async for chunk in model_instance.astream(messages, config={"callbacks": [cb, fb_callback]}):
                    content = getattr(chunk, "content", "")
                    if content:
                        current_message_content += content
                        yield {"type": "chunk", "content": content}

                    # 统一思考内容提取（兼容 DeepSeek/Ollama/Anthropic）
                    from Django_xm.apps.chat.services.stream_helpers import extract_thinking_content

                    _provider_id = data.get("provider_id", "") or getattr(model_instance, "_provider_id", "")
                    thinking_text = extract_thinking_content(chunk, _provider_id)
                    if thinking_text:
                        prev = accumulated_reasoning.get("content", "") or ""
                        accumulated_reasoning["content"] = prev + thinking_text
                        yield {
                            "type": "reasoning",
                            "data": {
                                "content": accumulated_reasoning["content"],
                                "duration": 0,
                            },
                        }

                    await asyncio.sleep(0.01)
            except Exception:
                logger.exception("无工具模式流式调用失败")
                raise

        update_usage_and_tokens(cb, usage_tracker, token_detail_tracker)

        # 检测运行时 LLM fallback
        if fb_callback.fallback_detected:
            fallback_info = fb_callback.get_fallback_info()
            if fallback_info:
                yield {
                    "type": "model_fallback",
                    "data": fallback_info,
                }
                try:
                    from Django_xm.apps.ai_engine.models import SystemConfig

                    SystemConfig.set_value(
                        "default_chat_model",
                        {
                            "provider_id": fallback_info["actual_provider"],
                            "model_name": fallback_info["actual_model"],
                        },
                    )
                except Exception:
                    # 持久化 fallback 配置失败不影响当前会话，运行时已切换
                    logger.debug("持久化模型 fallback 配置到 SystemConfig 失败")

        thinking_duration = round(time.time() - thinking_start_time, 1)
        final_reasoning = (accumulated_reasoning.get("content") or "").strip()
        if final_reasoning:
            yield {
                "type": "reasoning",
                "data": {
                    "content": final_reasoning,
                    "duration": thinking_duration,
                },
            }
        else:
            yield {
                "type": "reasoning",
                "data": {
                    "content": f"深度思考完成，共思考了 {thinking_duration} 秒",
                    "duration": thinking_duration,
                },
            }