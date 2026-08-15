"""SubAgentRuntime HTTP 接口。

提供子代理恢复（resume_subagent）端点：
    POST /api/v1/ai-engine/subagents/resume/

参数（JSON，snake_case）：
    subagent_thread_id: str   子代理独立 thread_id（SubAgentInstance.thread_id）
    resume_payload: object    审批决策 payload（batch 决策 dict：{tool_call_id: bool}）

与主会话 resume 完全隔离：仅操作指定子 Agent thread 的 checkpoint 断点续跑，
父 Agent 逻辑不参与恢复流程。
"""

from __future__ import annotations

import logging

from asgiref.sync import async_to_sync
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from Django_xm.apps.ai_engine.models import SubAgentInstance, SubAgentStatus
from Django_xm.apps.ai_engine.subagent_runtime import get_subagent_runtime
from Django_xm.apps.ai_engine.subagent_runtime.exceptions import (
    SubAgentInvalidStateError,
    SubAgentNotFoundError,
)
from Django_xm.common.error_codes import ErrorCode
from Django_xm.common.responses import error_response, success_response

logger = logging.getLogger(__name__)


class SubAgentResumeView(APIView):
    """恢复中断的子代理（从 checkpoint 断点续跑）。"""

    permission_classes = [IsAuthenticated]

    @extend_schema(exclude=True)
    def post(self, request):
        subagent_thread_id = request.data.get("subagent_thread_id")
        resume_payload = request.data.get("resume_payload")

        if not subagent_thread_id or not isinstance(subagent_thread_id, str):
            return error_response(
                code=ErrorCode.INVALID_PARAMS,
                message="subagent_thread_id 必填",
                http_status=status.HTTP_400_BAD_REQUEST,
            )
        if not isinstance(resume_payload, dict):
            return error_response(
                code=ErrorCode.INVALID_PARAMS,
                message="resume_payload 必须为对象（审批决策 dict）",
                http_status=status.HTTP_400_BAD_REQUEST,
            )

        instance = self._get_instance(subagent_thread_id)
        if instance is None:
            return error_response(
                code=ErrorCode.NOT_FOUND,
                message="子代理不存在",
                http_status=status.HTTP_404_NOT_FOUND,
            )

        if not self._assert_ownership(instance, request.user):
            return error_response(
                code=ErrorCode.FORBIDDEN,
                message="无权操作该子代理",
                http_status=status.HTTP_403_FORBIDDEN,
            )

        try:
            async_to_sync(get_subagent_runtime().resume)(subagent_thread_id, resume_payload)
        except SubAgentNotFoundError:
            return error_response(
                code=ErrorCode.NOT_FOUND,
                message="子代理不存在",
                http_status=status.HTTP_404_NOT_FOUND,
            )
        except SubAgentInvalidStateError as e:
            return error_response(
                code=ErrorCode.VALIDATION_FAILED,
                message=str(e),
                http_status=status.HTTP_409_CONFLICT,
            )
        except Exception:
            logger.exception(f"resume_subagent 失败: {subagent_thread_id}")
            return error_response(
                code=ErrorCode.INTERNAL_ERROR,
                message="子代理恢复失败",
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return success_response(
            data={
                "subagent_thread_id": subagent_thread_id,
                "status": SubAgentStatus.RUNNING.value,
            }
        )

    @staticmethod
    def _get_instance(subagent_thread_id: str) -> SubAgentInstance | None:
        return SubAgentInstance.objects.filter(thread_id=subagent_thread_id).first()

    @staticmethod
    def _assert_ownership(instance: SubAgentInstance, user) -> bool:
        """校验当前用户拥有该子代理（依据 metadata.user_id）。

        历史实例 metadata 缺 user_id 时回退为允许（与主链路 user_id 一致时的宽松策略），
        实际权限边界由父会话权限进一步约束。
        """
        meta = instance.metadata or {}
        owner_id = meta.get("user_id")
        if owner_id is None:
            return True
        try:
            return int(owner_id) == int(getattr(user, "id", None) or 0)
        except (TypeError, ValueError):
            return False


class SubAgentListView(APIView):
    """查询指定父线程下的子代理列表（前端 SubAgentCard 数据源）。

    参数（query，snake_case）：
        parent_thread_id: str   父线程 thread_id（主 agent = session_id 或 task_id）

    返回（data.subagents，snake_case）：
        thread_id / agent_name / status / pending_interrupt_info /
        result_preview / created_at / assistant_message_id
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(exclude=True)
    def get(self, request):
        parent_thread_id = request.query_params.get("parent_thread_id")
        if not parent_thread_id or not isinstance(parent_thread_id, str):
            return error_response(
                code=ErrorCode.INVALID_PARAMS,
                message="parent_thread_id 必填",
                http_status=status.HTTP_400_BAD_REQUEST,
            )

        instances = SubAgentInstance.objects.filter(
            parent_thread_id=parent_thread_id
        ).order_by("created_at")

        if not self._assert_ownership(instances, request.user):
            return error_response(
                code=ErrorCode.FORBIDDEN,
                message="无权查看该父线程下的子代理",
                http_status=status.HTTP_403_FORBIDDEN,
            )

        subagents = [self._serialize(inst) for inst in instances]
        return success_response(data={"subagents": subagents})

    @staticmethod
    def _serialize(inst: SubAgentInstance) -> dict:
        """序列化子代理实例（网络传输键 snake_case）。"""
        meta = inst.metadata or {}
        return {
            "thread_id": inst.thread_id,
            "parent_thread_id": inst.parent_thread_id,
            "agent_name": meta.get("agent_name", ""),
            "status": inst.status,
            "pending_interrupt_info": inst.pending_interrupt_info,
            "result_preview": inst.result_preview or "",
            "created_at": inst.created_at.isoformat() if inst.created_at else None,
            # 关联消息：前端据此将子代理卡片挂到对应 AI 消息下方（spec D10）
            "assistant_message_id": meta.get("assistant_message_id", ""),
        }

    @staticmethod
    def _assert_ownership(instances, user) -> bool:
        """校验当前用户拥有这批子代理（依据 metadata.user_id）。

        列表为空时返回 True（无数据可泄露）；任一实例 owner_id 明确且不匹配则拒绝；
        历史实例缺 user_id 时回退允许（与 resume 的宽松策略一致）。
        """
        try:
            user_id = int(getattr(user, "id", None) or 0)
        except (TypeError, ValueError):
            user_id = 0
        for inst in instances:
            owner_id = (inst.metadata or {}).get("user_id")
            if owner_id is None:
                continue
            try:
                if int(owner_id) != user_id:
                    return False
            except (TypeError, ValueError):
                return False
        return True
