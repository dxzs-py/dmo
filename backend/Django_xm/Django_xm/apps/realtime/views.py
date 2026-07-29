"""实时同步快照校对 API。

提供会话全量状态快照接口，供前端在 stream_finalized / tool_call_completed /
approval_approved 事件后调用，对本地状态进行校对（事件驱动 + 快照校对模式）。

GET /api/v1/realtime/snapshot/{session_id}/

历史接口：
- GET /api/v1/realtime/poll/  已移除（500ms 轮询补偿被快照校对替代，见 spec.md RC4）
"""

import logging

from django.apps import apps
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from Django_xm.apps.approvals.services.approval_helpers import (
    build_approval_index_item,
    enrich_tool_calls_with_approvals,
)
from Django_xm.common.error_codes import ErrorCode
from Django_xm.common.responses import error_response, success_response
from Django_xm.common.serializers import EmptySerializer

logger = logging.getLogger(__name__)


class SnapshotView(APIView):
    """会话全量状态快照接口。

    返回指定会话的完整状态（消息列表、工具调用、审批状态），
    供前端在 stream_finalized / tool_call_completed / approval_approved 事件后校对本地状态。

    GET /api/v1/realtime/snapshot/{session_id}/

    响应格式：
    {
        "code": 0,
        "data": {
            "session_id": "xxx",
            "session_title": "xxx",
            "messages": [
                {
                    "id": "1",
                    "session_id": "xxx",
                    "role": "user",
                    "content": "...",
                    "created_at": "2026-07-18T...",
                    "tool_calls": []
                }
            ],
            "tool_calls": [
                {
                    "id": "call_xxx",
                    "tool_call_id": "call_xxx",
                    "name": "shell_exec",
                    "status": "completed",
                    "message_id": "1",
                    "approval": {"state": "approved", "approval_id": "call_xxx"}
                }
            ],
            "approvals": [
                {
                    "id": "1",
                    "interrupt_id": "call_xxx",
                    "tool_call_id": "call_xxx",
                    "state": "approved",
                    "created_at": "2026-07-18T...",
                    "resolved_at": "2026-07-18T..."
                }
            ]
        }
    }
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: EmptySerializer})
    def get(self, request, session_id: str):
        ChatSession = apps.get_model("chat", "ChatSession")
        ChatMessage = apps.get_model("chat", "ChatMessage")
        Approval = apps.get_model("approvals", "Approval")

        # 1. 权限校验：用户会话归属
        # 注意：ChatSession 的公开标识为 session_id（UUID 字符串），非自增 id。
        # ChatSession.objects 默认使用 SoftDeleteManager，已排除 is_deleted=True。
        try:
            session = ChatSession.objects.get(session_id=session_id, user=request.user)
        except ChatSession.DoesNotExist:
            return error_response(
                code=ErrorCode.NOT_FOUND,
                message="会话不存在或无权访问",
                http_status=404,
            )

        # 2. 聚合消息列表（含 tool_calls JSON）
        # ChatMessage.objects 默认使用 SoftDeleteManager，已排除 is_deleted=True。
        messages = ChatMessage.objects.filter(session=session).order_by("created_at")
        messages_data = []
        all_tool_calls = []
        for msg in messages:
            msg_tool_calls = msg.tool_calls if isinstance(msg.tool_calls, list) else []
            msg_data = {
                "id": str(msg.id),
                "session_id": session.session_id,
                "role": msg.role,
                "content": msg.content or "",
                "created_at": msg.created_at.isoformat() if msg.created_at else None,
                "tool_calls": msg_tool_calls,
            }
            messages_data.append(msg_data)
            # 收集所有 tool_calls（含 approval 状态），并标注 message_id 便于前端回溯
            for tc in msg_tool_calls:
                if isinstance(tc, dict):
                    tc["message_id"] = str(msg.id)
                    all_tool_calls.append(tc)

        # 3. 聚合审批状态（保留 approvals 列表供前端独立消费 + 构建索引供 tool_calls 注入复用）
        # Approval 无 session_id 字段，使用 chat_session_id 关联会话；
        # Approval 无 tool_call_id 字段，该值存放在 extra JSON 中（回退到 interrupt_id）；
        # Approval 无 updated_at 字段，使用 resolved_at 表示审批终结时间。
        # approval_index 复用 build_approval_index_item 构建完整字段（与 WebSocket 事件
        # approval payload 对齐），确保快照 API 返回的 tool_calls.approval 包含
        # title/description/operation/danger_level 等 UI 展示字段。
        approvals_qs = Approval.objects.filter(chat_session_id=session_id)
        approvals_data = []
        approval_index = {}
        for apv in approvals_qs:
            extra = apv.extra if isinstance(apv.extra, dict) else {}
            apv_tc_id = extra.get("tool_call_id") or apv.interrupt_id
            approvals_data.append(
                {
                    "id": str(apv.id),
                    "interrupt_id": apv.interrupt_id,
                    "tool_call_id": apv_tc_id,
                    "state": apv.state,
                    "created_at": apv.created_at.isoformat() if apv.created_at else None,
                    "resolved_at": apv.resolved_at.isoformat() if apv.resolved_at else None,
                }
            )
            if apv_tc_id:
                approval_index[str(apv_tc_id)] = build_approval_index_item(apv)

        # 4. 合并 approval 状态到 tool_calls（复用公共函数 + 预查询索引避免重复 DB 查询）
        # approval_index 由本视图构建（与 approvals_data 同源），传入 enrich 跳过 DB 查询。
        # 合并策略与 approval_helpers.build_approval_index 保持一致，确保返回结构与历史完全一致。
        enrich_tool_calls_with_approvals(all_tool_calls, session_id, approval_index=approval_index)

        # 5. 返回标准化 JSON
        return success_response(
            {
                "session_id": session.session_id,
                "session_title": session.title or "",
                "messages": messages_data,
                "tool_calls": all_tool_calls,
                "approvals": approvals_data,
            }
        )
