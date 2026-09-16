"""SubAgentRuntime HTTP 接口。

提供子代理列表查询端点：
    GET /api/v1/ai-engine/subagents/?parent_thread_id=xxx[&recursive=true]
    recursive=true：服务端 BFS 一次请求返回全树（含嵌套后代），消除前端
    逐层递归拉取造成的请求放大（嵌套场景 1+N+M 请求 → 打满限流 429）。

子代理审批恢复已并入统一审批链路（POST /api/v1/approvals/{interrupt_id}/resume/）：
前端审批 → gateway 信令（extra.subagent_thread_id 路由）→ SessionManager
子代理分支 → finalize + runtime.resume。不再提供独立的 subagents/resume 端点，
保证主/子代理审批链路完全一致（仅展示位置不同）。
"""

from __future__ import annotations

import logging

from drf_spectacular.utils import extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from Django_xm.apps.ai_engine.models import SubAgentInstance
from Django_xm.common.error_codes import ErrorCode
from Django_xm.common.exceptions import BaseAppError
from Django_xm.common.responses import success_response

logger = logging.getLogger(__name__)


class SubAgentListView(APIView):
    """查询指定父线程下的子代理列表（前端 SubAgentCard 数据源）。

    参数（query，snake_case）：
        parent_thread_id: str   父线程 thread_id（主 agent = session_id 或 task_id）
        recursive: str           可选；值为 "true" 时以 parent_thread_id 为根做
                                 服务端 BFS，一次请求返回全树（含任意深度嵌套
                                 后代），替代前端逐层递归拉取（请求放大根因，
                                 嵌套场景单次刷新 1+N+M 个请求 → 打满限流 429）

    返回（data.subagents，snake_case）：
        thread_id / parent_thread_id / depth / agent_name / task / status /
        pending_interrupt_info / result_preview / created_at /
        assistant_message_id / spawn_tool_call_id

    recursive=true 时结果为以 parent_thread_id 为根的整棵子代理树（按层向下
    遍历直到无新节点，created_at 升序排列）；权限校验覆盖树中全部实例。
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(exclude=True)
    def get(self, request):
        parent_thread_id = request.query_params.get("parent_thread_id")
        if not parent_thread_id or not isinstance(parent_thread_id, str):
            raise BaseAppError("parent_thread_id 必填", business_code=ErrorCode.INVALID_PARAMS)

        recursive = request.query_params.get("recursive") == "true"
        if recursive:
            instances = self._collect_subtree(parent_thread_id)
        else:
            instances = list(
                SubAgentInstance.objects.filter(parent_thread_id=parent_thread_id).order_by("created_at")
            )

        if not self._assert_ownership(instances, request.user):
            raise BaseAppError("无权查看该父线程下的子代理", business_code=ErrorCode.FORBIDDEN)

        instances.sort(key=lambda inst: inst.created_at)
        subagents = [self._serialize(inst) for inst in instances]
        return success_response(data={"subagents": subagents})

    @staticmethod
    def _collect_subtree(root_parent_thread_id: str) -> list[SubAgentInstance]:
        """以 root_parent_thread_id 为根做 BFS，收集整棵子代理树。

        按层查询 parent_thread_id__in=<当前层父 id 集合>，逐层向下直到无新
        节点；以 thread_id 集合去重防环（异常数据出现父子环时不会死循环）。
        """
        collected: list[SubAgentInstance] = []
        seen_thread_ids: set[str] = set()
        current_parent_ids = {root_parent_thread_id}
        while current_parent_ids:
            layer = list(
                SubAgentInstance.objects.filter(parent_thread_id__in=current_parent_ids).order_by("created_at")
            )
            current_parent_ids = set()
            for inst in layer:
                if inst.thread_id in seen_thread_ids:
                    continue
                seen_thread_ids.add(inst.thread_id)
                collected.append(inst)
                current_parent_ids.add(inst.thread_id)
        return collected

    @staticmethod
    def _serialize(inst: SubAgentInstance) -> dict:
        """序列化子代理实例（网络传输键 snake_case）。"""
        meta = inst.metadata or {}
        return {
            "thread_id": inst.thread_id,
            "parent_thread_id": inst.parent_thread_id,
            # 嵌套深度（主 agent=0，子=1，孙=2，曾孙=3；历史实例无此字段时兜底 1）
            "depth": meta.get("depth", 1),
            "agent_name": meta.get("agent_name", ""),
            # 任务描述（历史实例 metadata 无此字段时返回空串）
            "task": meta.get("task", ""),
            "status": inst.status,
            "pending_interrupt_info": inst.pending_interrupt_info,
            "result_preview": inst.result_preview or "",
            "created_at": inst.created_at.isoformat() if inst.created_at else None,
            # 关联消息：前端据此将子代理卡片挂到对应 AI 消息下方（spec D10）
            "assistant_message_id": meta.get("assistant_message_id", ""),
            # 关联工具调用：触发派生的 spawn 工具调用 ID（历史实例无此字段时返回空串）
            "spawn_tool_call_id": meta.get("spawn_tool_call_id", ""),
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
