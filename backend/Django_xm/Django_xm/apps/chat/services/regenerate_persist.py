"""重新生成 — 持久化与审批请求辅助（Task 15.5 拆分）。

从 regenerate_service.py 抽离的 DB 写入辅助函数：
- _request_approval: 委托 approval_service 发起审批请求，保存 regenerate 完整配置
- _save_regenerated_message: 将流式生成的 content/tool_calls 保存到 message 顶层和 versions
- _save_partial_regen_content: 审批中断时保存部分已生成内容
- _cleanup_streaming_flag: 清理 is_streaming 标记
- _rollback_regenerated_message: 重新生成失败时回滚版本

依赖方向：独立底层模块，被 stream/resume 引用。
"""

import logging
from typing import Any

from asgiref.sync import sync_to_async
from django.utils import timezone

from Django_xm.apps.approvals.models import Approval
from Django_xm.apps.approvals.services import approval_service
from Django_xm.apps.chat.models import ChatMessage
from Django_xm.common.event_schema import EventType, PayloadValidationError
from Django_xm.common.realtime_events import publish_event

logger = logging.getLogger(__name__)


async def _request_approval(
    interrupt_id: str,
    session_id: str,
    message_id: int,
    approval_data: dict[str, Any],
    data: dict[str, Any],
    regen_thread_id: str,
) -> None:
    """委托 approval_service 发起审批请求，保存 regenerate 完整配置到 extra。"""
    sync_payload = {
        "interrupt_id": interrupt_id,
        "source": Approval.SOURCE_CHAT,
        "source_id": session_id,
        "session_id": session_id,
        "state": "pending",
        "tool_name": approval_data.get("tool_name"),
        "title": approval_data.get("title", ""),
        "description": approval_data.get("description", ""),
        "action": approval_data.get("action", "confirm"),
        "operation": approval_data.get("operation", ""),
        "danger_level": approval_data.get("danger_level", "medium"),
        "parameters": approval_data.get("parameters", {}),
        "tool_call_id": approval_data.get("tool_call_id", ""),
        "extra": {
            **(approval_data.get("extra") or {}),
            "tool_config": {
                "use_tools": data.get("use_tools", True),
                "use_web_search": data.get("use_web_search", False),
                "use_mcp": data.get("use_mcp", False),
                "selected_mcp_servers": data.get("selected_mcp_servers"),
                "selected_tools": data.get("selected_tools"),
                "use_knowledge_base": data.get("use_knowledge_base", False),
                "selected_knowledge_bases": data.get("selected_knowledge_bases", []),
                "tool_tier": data.get("tool_tier", "standard"),
            },
            "model_config": {
                "provider_id": data.get("provider_id"),
                "model_name": data.get("model_name"),
                "use_deep_thinking": data.get("use_deep_thinking", False),
                "special_params": data.get("special_params"),
                "temperature": data.get("temperature"),
                "max_tokens": data.get("max_tokens"),
            },
            "regenerate": {
                "thread_id": regen_thread_id,
                "message_id": message_id,
            },
        },
        "message_id": message_id,
    }
    try:
        await approval_service.request_approval_async(
            source=Approval.SOURCE_CHAT,
            source_id=session_id,
            interrupt_id=interrupt_id,
            approval_data=sync_payload,
        )
    except Exception as approval_err:
        logger.warning(f"[Regenerate] request_approval_async 首次失败，尝试截断重试: {approval_err}")
        try:
            fallback_payload = dict(sync_payload)
            for fld in ("title", "tool_name", "tool_call_id"):
                val = fallback_payload.get(fld)
                if isinstance(val, str) and len(val) > 200:
                    fallback_payload[fld] = val[:200]
            await approval_service.request_approval_async(
                source=Approval.SOURCE_CHAT,
                source_id=session_id,
                interrupt_id=interrupt_id,
                approval_data=fallback_payload,
            )
        except Exception:
            logger.exception(f"[Regenerate] 截断重试仍失败: interrupt_id={interrupt_id}")


async def _save_regenerated_message(
    message_id: int,
    content: str,
    tool_calls_map: dict[str, dict],
    accumulated_reasoning: dict[str, str],
    usage_tracker,
) -> None:
    """将流式生成的 content/tool_calls 保存到 message 顶层和 versions[current_version]。"""
    clean_tool_calls = []
    for tc_info in tool_calls_map.values():
        clean = {k: v for k, v in tc_info.items() if not k.startswith("_")}
        clean_tool_calls.append(clean)

    reasoning_content = ""
    if accumulated_reasoning and accumulated_reasoning.get("content"):
        reasoning_content = accumulated_reasoning["content"]

    @sync_to_async
    def _do_save():
        msg = ChatMessage.objects.select_related("session").filter(id=message_id).first()
        if not msg:
            return None

        # 增量合并 tool_calls：resume 阶段的 tool_calls_map 只包含恢复后新产生的工具调用，
        # 需要与中断前已持久化的 tool_calls 合并，避免覆盖丢失
        existing_tcs = list(msg.tool_calls or [])
        existing_ids = {tc.get("id") for tc in existing_tcs if isinstance(tc, dict)}
        for tc in clean_tool_calls:
            tc_id = tc.get("id")
            if tc_id and tc_id not in existing_ids:
                existing_tcs.append(tc)
            elif tc_id:
                # 已存在的 tool_call：用新数据更新（如 status、output 等字段）
                for i, existing in enumerate(existing_tcs):
                    if isinstance(existing, dict) and existing.get("id") == tc_id:
                        existing_tcs[i] = {**existing, **tc}
                        break
        msg.tool_calls = existing_tcs

        # content 追加：resume 阶段的 content 是新产生的文本，需与中断前的内容拼接
        if content:
            msg.content = (msg.content or "") + content

        if usage_tracker is not None:
            msg.model = usage_tracker.model_id or msg.model or ""
        if msg.versions and 0 <= msg.current_version < len(msg.versions):
            msg.versions[msg.current_version] = {
                "content": msg.content,
                "tool_calls": list(msg.tool_calls or []),
                "sources": msg.sources or [],
                "reasoning": reasoning_content,
                "created_at": timezone.now().isoformat(),
                "model": msg.model or "",
            }
        msg.is_streaming = False
        msg.save()
        return msg

    saved_msg = await _do_save()
    if saved_msg:
        try:
            from Django_xm.apps.chat.serializers import ChatMessageSerializer

            real_session_id = saved_msg.session.session_id
            await publish_event(
                EventType.MESSAGE_UPDATED,
                {
                    "session_id": real_session_id,
                    "message_id": str(saved_msg.id),
                    "message": ChatMessageSerializer(saved_msg).data,
                },
                session_id=real_session_id,
            )
        except PayloadValidationError:
            logger.exception(
                f"[Regenerate] MESSAGE_UPDATED payload 校验失败，跳过广播: "
                f"session_id={real_session_id}, message_id={saved_msg.id}",
            )
        except Exception as pub_err:
            logger.warning(f"[Regenerate] 广播 message_updated 失败: {pub_err}")


async def _save_partial_regen_content(
    message_id: int,
    content: str,
    tool_calls_map: dict[str, dict],
    accumulated_reasoning: dict[str, str],
) -> None:
    """审批中断时保存部分已生成内容，保持 is_streaming=False 但不清空版本。"""
    clean_tool_calls = []
    for tc_info in tool_calls_map.values():
        clean = {k: v for k, v in tc_info.items() if not k.startswith("_")}
        clean_tool_calls.append(clean)

    reasoning_content = accumulated_reasoning.get("content", "") if accumulated_reasoning else ""

    @sync_to_async
    def _do_save():
        msg = ChatMessage.objects.select_related("session").filter(id=message_id).first()
        if not msg:
            return None
        existing = msg.content or ""
        if content and content not in existing:
            msg.content = existing + content
        if clean_tool_calls:
            existing_tcs = list(msg.tool_calls or [])
            existing_ids = {tc.get("id") for tc in existing_tcs if isinstance(tc, dict)}
            for tc in clean_tool_calls:
                tc_id = tc.get("id")
                if tc_id and tc_id not in existing_ids:
                    existing_tcs.append(tc)
            msg.tool_calls = existing_tcs
        if reasoning_content:
            cur_reasoning = msg.reasoning if isinstance(msg.reasoning, dict) else {}
            if not cur_reasoning.get("content"):
                cur_reasoning["content"] = reasoning_content
            msg.reasoning = cur_reasoning
        if msg.versions and 0 <= msg.current_version < len(msg.versions):
            ver = msg.versions[msg.current_version]
            ver["content"] = msg.content
            ver["tool_calls"] = list(msg.tool_calls or [])
            ver["reasoning"] = reasoning_content
        msg.save()
        return msg

    saved_msg = await _do_save()
    if saved_msg:
        try:
            from Django_xm.apps.chat.serializers import ChatMessageSerializer

            real_session_id = saved_msg.session.session_id
            await publish_event(
                EventType.MESSAGE_UPDATED,
                {
                    "session_id": real_session_id,
                    "message_id": str(saved_msg.id),
                    "message": ChatMessageSerializer(saved_msg).data,
                },
                session_id=real_session_id,
            )
        except PayloadValidationError:
            logger.exception(
                f"[Regenerate] 审批中断 MESSAGE_UPDATED payload 校验失败，跳过广播: "
                f"session_id={real_session_id}, message_id={saved_msg.id}",
            )
        except Exception as pub_err:
            logger.warning(f"[Regenerate] 审批中断广播 message_updated 失败: {pub_err}")


async def _cleanup_streaming_flag(message_id: int) -> None:
    """清理 message 的 is_streaming 标记。"""

    @sync_to_async
    def _do_cleanup():
        ChatMessage.objects.filter(id=message_id).update(is_streaming=False)

    await _do_cleanup()


async def _rollback_regenerated_message(message_id: int) -> None:
    """重新生成失败时回滚版本：删除空版本，恢复上一个版本。"""

    @sync_to_async
    def _do_rollback():
        msg = ChatMessage.objects.select_related("session").filter(id=message_id).first()
        if not msg:
            return None
        versions = list(msg.versions or [])
        if len(versions) < 2:
            msg.content = ""
            msg.tool_calls = []
            msg.sources = []
            msg.reasoning = {}
            msg.is_streaming = False
            msg.save()
            return msg

        versions.pop()
        prev_idx = len(versions) - 1
        msg.versions = versions
        msg.current_version = prev_idx

        prev_version = versions[prev_idx]
        msg.content = prev_version.get("content", "") or ""
        msg.tool_calls = list(prev_version.get("tool_calls", []) or [])
        msg.sources = list(prev_version.get("sources", []) or [])
        reasoning = prev_version.get("reasoning", {})
        msg.reasoning = reasoning if reasoning else {}
        msg.is_streaming = False
        msg.save()
        return msg

    rolled_msg = await _do_rollback()
    if rolled_msg:
        try:
            from Django_xm.apps.chat.serializers import ChatMessageSerializer

            real_session_id = rolled_msg.session.session_id
            await publish_event(
                EventType.MESSAGE_REGENERATE_REVERTED,
                {
                    "session_id": real_session_id,
                    "message_id": str(rolled_msg.id),
                    "message": ChatMessageSerializer(rolled_msg).data,
                },
                session_id=real_session_id,
            )
        except PayloadValidationError:
            logger.exception(
                f"[Regenerate] MESSAGE_REGENERATE_REVERTED payload 校验失败，跳过广播: "
                f"session_id={real_session_id}, message_id={rolled_msg.id}",
            )
        except Exception as pub_err:
            logger.warning(f"[Regenerate] 广播 message_regenerate_reverted 失败: {pub_err}")
