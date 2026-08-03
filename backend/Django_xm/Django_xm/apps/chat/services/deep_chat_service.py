"""
深度模式聊天服务

从 chat_service.py 拆分出的深度思考/深度研究相关逻辑：
- 深度思考流式处理
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

from ..utils import _lcp_len, convert_chat_history
from .stream_helpers import (
    process_stream_chunk,
    update_usage_and_tokens,
)

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

    async def process_deep_thinking_stream(
        self,
        data: dict[str, Any],
        usage_tracker,
        token_detail_tracker: TokenDetailTracker | None = None,
        tools: list | None = None,
        model_instance=None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """深度思考流式处理（可作为 chat/agent 模式的叠加能力）"""
        if tools is None:
            tools = await self._chat_service._get_tools(data)
        if model_instance is None:
            model_instance = self._chat_service._resolve_model_instance(data)
        provider_id = data.get("provider_id")

        prompt_mode = data.get("mode", "agent")
        tool_config = self._chat_service._build_tool_config(data)
        agent, thread_config, use_checkpointer = await self._chat_service._create_agent_with_memory(
            data,
            prompt_mode=prompt_mode,
            model_instance=model_instance,
            tool_config=tool_config,
            tools=tools,
        )

        human_msg = await self._chat_service._acreate_human_message(data)

        if use_checkpointer:
            self._chat_service._apply_context_engineering_for_checkpointer(
                user_message=data.get("message", ""),
                model_name=data.get("model_name"),
                mode=data.get("mode", "agent"),
            )
            graph_input = {"messages": [human_msg]}
            config = thread_config
        else:
            chat_history = data.get("chat_history", [])
            langchain_chat_history = convert_chat_history(chat_history)
            messages = list(langchain_chat_history) if langchain_chat_history else []
            messages.append(human_msg)
            graph_input = {"messages": messages}
            config = {"recursion_limit": 500}

        current_message_content = ""
        all_messages = []
        tool_calls_map: dict[str, dict] = {}
        tool_call_count: dict[str, int] = {}
        accumulated_reasoning: dict[str, str] = {
            "content": "",
            "_stream_state": data.get("_stream_state"),  # 共享状态，供 generate() finally 兜底刷新
        }
        tool_args_accumulator: dict[str, str] = {}
        thinking_start_time = time.time()
        has_sent_reasoning = False
        has_model_reasoning = False

        yield {
            "type": "reasoning",
            "data": {
                "content": "正在深度思考中...",
                "duration": 0,
                "source": "deep_thinking",
            },
        }
        has_sent_reasoning = True

        with TokenUsageCallbackHandler() as cb:
            from Django_xm.apps.ai_engine.services.llm_fallback import FallbackDetectionCallback

            fb_callback = FallbackDetectionCallback(
                expected_provider=provider_id or "",
                expected_model=data.get("model_name", "") or "",
            )
            config["callbacks"] = [cb, fb_callback]
            # 使用多 stream mode：messages 获取消息流，updates 捕获 interrupt 事件
            interrupt_info = None
            try:
                async for chunk in agent.graph.astream(graph_input, config=config, stream_mode=["messages", "updates"]):
                    # 多 stream mode 下 chunk 是 (mode_name, data) 元组
                    if isinstance(chunk, tuple) and len(chunk) == 2:
                        mode_name, mode_data = chunk
                    else:
                        mode_name, mode_data = "messages", chunk

                    # 处理 updates stream mode（包含 interrupt 事件）
                    if mode_name == "updates":
                        if isinstance(mode_data, dict) and "__interrupt__" in mode_data:
                            interrupts = mode_data["__interrupt__"]
                            if interrupts:
                                from Django_xm.apps.chat.services.stream_helpers import (
                                    extract_interrupt_ids,
                                    parse_approval_interrupt,
                                )
                                from Django_xm.apps.tools.base import is_approval_interrupt

                                # 收集所有审批请求（兼容批量格式和旧格式单条）
                                batch_approval_data = []  # [(graph_intr_id, tool_call_id, approval_data)]
                                for intr in interrupts:
                                    interrupt_value, batch_id, resume_id = extract_interrupt_ids(intr)

                                    if not is_approval_interrupt(interrupt_value):
                                        continue

                                    parsed_list = parse_approval_interrupt(
                                        interrupt_value,
                                        graph_interrupt_id=batch_id,
                                        langgraph_resume_id=resume_id,
                                        tool_calls_map=tool_calls_map,
                                        tool_args_accumulator=tool_args_accumulator,
                                    )
                                    for approval_data in parsed_list:
                                        tool_call_id = approval_data.get("tool_call_id", "")
                                        batch_approval_data.append(
                                            (resume_id, tool_call_id, approval_data)
                                        )

                                session_id = data.get("session_id", "")
                                message_id = str(
                                    data.get("_assistant_message_id") or data.get("message_id", "")
                                )

                                for graph_intr_id, tool_call_id, approval_data in batch_approval_data:
                                    tool_name = approval_data.get("tool_name", "unknown")
                                    action = approval_data.get("action", "confirm")
                                    logger.info(
                                        f"深度思考 approval interrupt: tool={tool_name}, "
                                        f"action={action}, danger={approval_data.get('danger_level', 'medium')}"
                                    )

                                    # 注入 session_id / message_id
                                    approval_data["session_id"] = session_id
                                    approval_data["message_id"] = message_id

                                    # 持久化工具/模型配置到 approval.extra
                                    from Django_xm.apps.approvals.services.approval_service import (
                                        build_approval_extra,
                                        request_approval_async,
                                    )
                                    approval_data["extra"] = build_approval_extra(
                                        data,
                                        tool_call_id=tool_call_id,
                                        graph_interrupt_id=approval_data.get("graph_interrupt_id", ""),
                                        langgraph_resume_id=approval_data.get("langgraph_resume_id", ""),
                                        message_id=message_id,
                                        base_extra=approval_data.get("extra"),
                                    )

                                    try:
                                        await request_approval_async(
                                            source="chat",
                                            source_id=session_id or "",
                                            interrupt_id=tool_call_id,
                                            approval_data=approval_data,
                                        )
                                        logger.info(
                                            f"[Approval] DB记录已创建: interrupt_id={tool_call_id}, "
                                            f"session={session_id}"
                                        )
                                    except Exception as e:
                                        logger.error(
                                            f"[Approval] DB记录创建失败: interrupt_id={tool_call_id}, error={e}"
                                        )

                                    yield {
                                        "type": "approval",
                                        "data": approval_data,
                                    }
                        continue  # updates 模式的其他事件跳过

                    # 处理 messages stream mode
                    all_messages.append(mode_data if not isinstance(mode_data, tuple) else mode_data[0])

                    try:
                        for event in process_stream_chunk(
                            mode_data,
                            tool_calls_map,
                            current_message_content,
                            tool_call_count=tool_call_count,
                            lcp_func=_lcp_len,
                            accumulated_reasoning=accumulated_reasoning,
                            tool_args_accumulator=tool_args_accumulator,
                            mode=prompt_mode,
                            enable_deep_thinking=True,
                            session_id=data.get("session_id", ""),
                            message_id=str(data.get("_assistant_message_id") or data.get("message_id", "")),
                            module_id=data.get("session_id", ""),
                        ):
                            if event.get("type") == "chunk":
                                current_message_content += event.get("content", "")
                            elif event.get("type") == "reasoning":
                                if not has_sent_reasoning:
                                    has_sent_reasoning = True
                                has_model_reasoning = True
                            yield event
                    except Exception as chunk_err:
                        logger.warning(f"处理流式 chunk 失败: {chunk_err}")
                        continue

                    await asyncio.sleep(0.01)
            except Exception as stream_err:
                # 异常路径：先刷新可能残留的缓冲内容，避免前端什么都没看到
                pending = accumulated_reasoning.get("_pending_content", "") if accumulated_reasoning else ""
                if pending:
                    logger.debug(f"深度思考模式异常路径: 刷新缓冲内容 ({len(pending)} 字符)")
                    yield {"type": "chunk", "content": pending}
                    current_message_content += pending
                    accumulated_reasoning["_pending_content"] = ""
                    # 同步清除 stream_state，避免 generate() finally 重复刷新
                    from .stream_helpers import _sync_pending_to_stream_state

                    _sync_pending_to_stream_state(accumulated_reasoning)

                # GraphRecursionError: Agent 步数超限，但已收集了部分结果
                # 优雅降级：用已收集的内容生成回复，不丢弃上下文
                from langgraph.errors import GraphRecursionError

                if isinstance(stream_err, GraphRecursionError):
                    logger.warning(
                        f"深度思考模式达到递归上限，优雅降级: 已收集 {len(all_messages)} 条消息, "
                        f"内容长度={len(current_message_content)}"
                    )
                    # 如果已经收集到内容，直接返回已有结果
                    if current_message_content:
                        yield {"type": "chunk", "content": ""}
                    else:
                        # 没有内容时，从 all_messages 中提取最后的 AI 回复
                        for msg in reversed(all_messages):
                            if isinstance(msg, AIMessage) and msg.content:
                                current_message_content = msg.content if isinstance(msg.content, str) else str(msg.content)
                                yield {"type": "chunk", "content": msg.content}
                                break
                        if not current_message_content:
                            yield {
                                "type": "chunk",
                                "content": "任务执行步骤较多，已达到单次执行上限。以上是已收集的部分结果。",
                            }
                    # 跳过 fallback，继续后续的 finalize 和 reasoning 处理
                elif provider_id and model_instance and tools:
                    # 其他异常（如 API 连接错误）：回退到无工具纯对话模式
                    logger.warning(
                        f"深度思考模式模型 {provider_id} agent模式执行失败，回退到无工具纯对话模式: {type(stream_err).__name__}: {stream_err}"
                    )
                    try:
                        async for fallback_event in self._stream_without_tools(
                            model_instance, data, usage_tracker, token_detail_tracker
                        ):
                            yield fallback_event
                    except Exception as fallback_err:
                        logger.exception(f"无工具回退模式也失败: {type(fallback_err).__name__}")
                        yield {
                            "type": "error",
                            "content": f"模型服务暂时不可用，请稍后重试（{type(fallback_err).__name__}）",
                        }
                    return
                else:
                    logger.exception(
                        f"deep-thinking astream 执行异常: {type(stream_err).__name__}"
                    )
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

        # 审批中断场景：Agent 被 interrupt 暂停，等待用户确认
        # 跳过 finalize 逻辑（不需要补发 final_ai_message 等），直接结束流
        from .stream_helpers import finalize_tool_calls

        if interrupt_info is not None:
            logger.info(f"深度思考审批中断，跳过 finalize: tool={interrupt_info.get('tool_name')}")
            for tool_update_event in finalize_tool_calls(all_messages, tool_calls_map, tool_args_accumulator):
                yield tool_update_event
            return

        for tool_update_event in finalize_tool_calls(all_messages, tool_calls_map, tool_args_accumulator):
            yield tool_update_event

        # Agent 模式：刷新缓冲的 content（最终回答）
        pending = accumulated_reasoning.get("_pending_content", "") if accumulated_reasoning else ""
        if pending:
            logger.debug(f"深度思考模式: 刷新缓冲的最终回答内容 ({len(pending)} 字符)")
            yield {"type": "chunk", "content": pending}
            current_message_content += pending
            accumulated_reasoning["_pending_content"] = ""
            # 同步清除 stream_state，避免 generate() finally 重复刷新
            from .stream_helpers import _sync_pending_to_stream_state

            _sync_pending_to_stream_state(accumulated_reasoning)

        # 深度思考模式兜底：当模型（如 qwen3:8b + reasoning=True）将全部输出
        # 放入 thinking 字段而 content 为空时，将推理内容作为主内容发送
        # 但如果有 interrupt（工具审批等待），不应触发兜底
        if (
            not current_message_content.strip()
            and not interrupt_info
            and accumulated_reasoning
            and accumulated_reasoning.get("content", "").strip()
        ):
            reasoning_text = accumulated_reasoning["content"].strip()
            logger.info(f"深度思考兜底: content 为空，将推理内容 ({len(reasoning_text)} 字符) 作为主内容发送")
            yield {"type": "chunk", "content": reasoning_text}
            current_message_content = reasoning_text

        thinking_duration = round(time.time() - thinking_start_time, 1)

        final_reasoning = (accumulated_reasoning.get("content") or "").strip()
        if has_model_reasoning and final_reasoning:
            yield {
                "type": "reasoning",
                "data": {
                    "content": final_reasoning,
                    "duration": thinking_duration,
                    "source": "deep_thinking",
                },
            }
        elif has_sent_reasoning or not has_model_reasoning:
            yield {
                "type": "reasoning",
                "data": {
                    "content": f"深度思考完成，共思考了 {thinking_duration} 秒",
                    "duration": thinking_duration,
                    "source": "deep_thinking",
                },
            }

    async def create_deep_research_task(
        self,
        query: str,
        session_id: str | None = None,
        use_web_search: bool = True,
        retriever_tool=None,
        task_title: str | None = None,
    ) -> str:
        """创建深度研究任务并返回 task_id（不执行研究）"""
        from django.contrib.auth import get_user_model

        from Django_xm.apps.research.services.cross_app import get_research_task_manager

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