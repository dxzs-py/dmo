"""
深度模式聊天服务

从 chat_service.py 拆分出的深度研究相关逻辑：
- 深度研究任务创建与 Celery 启动
- 无工具回退模式
"""

import logging
import time
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from asgiref.sync import sync_to_async
from langchain_core.messages import AIMessage, ToolMessage

from Django_xm.apps.ai_engine.services.cost_tracker import TokenDetailTracker
from Django_xm.apps.ai_engine.services.token_counter import TokenUsageCallbackHandler

from ..utils import convert_chat_history
from .stream_helpers import update_usage_and_tokens

logger = logging.getLogger(__name__)


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
    """异步更新 ChatMessage.research_task_id 字段

    P12 修复：写库成功后广播 message_updated（携带权威 research_task_status），
    让所有浏览器及时获得研究任务权威状态，消除对快照校对延迟的依赖。
    否则触发浏览器 chat SSE 立即结束后 researchTaskStatus 仍为 null，
    ChatMessage._isResearchRunning 回退判定会误显"研究已完成"闪现。
    广播失败仅记录日志，不影响研究任务主流程（前端快照校对兜底）。
    """
    from django.apps import apps

    ChatMessage = apps.get_model("chat", "ChatMessage")
    updated = ChatMessage.objects.filter(id=assistant_message_id).update(
        research_task_id=research_task_id,
    )
    if not updated:
        return
    try:
        from Django_xm.apps.chat.serializers import ChatMessageSerializer
        from Django_xm.common.event_schema import EventType
        from Django_xm.common.realtime_events import publish_event_sync

        msg = ChatMessage.objects.get(id=assistant_message_id)
        publish_event_sync(
            EventType.MESSAGE_UPDATED,
            {
                "session_id": msg.session.session_id,
                "message_id": str(msg.id),
                "message": ChatMessageSerializer(msg).data,
            },
            session_id=msg.session.session_id,
        )
    except Exception:
        logger.warning(
            f"广播 message_updated 失败（research_task_id 关联后）: "
            f"message_id={assistant_message_id}, research_task_id={research_task_id}",
            exc_info=True,
        )


@sync_to_async(thread_sensitive=True)
def _update_celery_task_id_sync(thread_id, celery_task_id):
    """同步更新研究任务的 Celery task ID"""
    from Django_xm.apps.research.services.cross_app import update_research_task_fields

    update_research_task_fields(thread_id, celery_task_id=celery_task_id)


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

    async def _publish_task_created(self, thread_id: str) -> None:
        """发布 TASK_CREATED 实时事件到 user 频道，通知深度研究模块自动刷新列表。

        与独立深度研究模块 research/views.py 的 start 视图行为对齐（P8 根因修复）。
        """
        user_id = self._chat_service.user_id
        if not user_id:
            return
        try:
            from Django_xm.common.event_schema import EventType
            from Django_xm.common.realtime_events import publish_event

            await publish_event(
                EventType.TASK_CREATED,
                {"task_id": thread_id},
                user_id=str(user_id),
            )
            logger.info(f"已发布 task_created 事件: task_id={thread_id}")
        except Exception as e:
            logger.warning(f"发布 task_created 事件失败: task_id={thread_id}, error={e}")

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

        # 发布 task_created 实时事件，通知深度研究模块自动刷新任务列表
        await self._publish_task_created(thread_id)

        return thread_id

    async def start_celery(
        self,
        query: str,
        session_id: str | None = None,
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
    ) -> str:
        """启动深度研究 Celery 任务（不等待结果）

        Chat SSE 在深度研究模式下应"立即返回"——发送 deep_research 事件后立即结束流。
        研究过程由 Celery worker 异步执行，通过以下两条链路回写：
          1. writeback_to_chat_message：回写 final_report 到 ChatMessage（持久化）
          2. broadcast_stream_completed：广播 stream_completed WebSocket 事件（实时通知）

        审批 / 工具事件通过 WebSocket 统一推送，所有浏览器通过 realtime_events 同步。

        Returns:
            thread_id: 深度研究任务 ID
        """
        from Django_xm.tasks.deep_research import run_research_task

        thread_id = task_id

        if knowledge_base_ids:
            await _update_kb_sync(thread_id, knowledge_base_ids)

        use_mcp = any(
            (getattr(t, "metadata", {}) or {}).get("is_mcp_tool", False)
            for t in (extra_tools or [])
        )
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

        logger.info(
            f"[DeepChat] Celery 深度研究任务已启动: thread_id={thread_id}, "
            f"celery_task_id={celery_result.id}, session_id={session_id}"
        )
        return thread_id

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