"""SubAgentRuntime —— 子 Agent 唯一对外入口。

职责（严格遵守边界，禁止业务逻辑侵入）：
- 元数据持久化（SubAgentInstance ORM）
- 参数校验 + 嵌套深度 / 轮次上限防护
- 委派适配器执行底层 graph（spawn / resume / get_state / terminate）
- 不编写 Agent 业务逻辑、不构造 graph、不写 prompt。

上层业务（spawn 工具 / 深度研究编排）只能：
    from Django_xm.apps.ai_engine.subagent_runtime import SubAgentRuntime
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from asgiref.sync import sync_to_async

from Django_xm.apps.ai_engine.config import AGENT_RECURSION_LIMIT_SUBAGENT
from Django_xm.apps.ai_engine.models import SubAgentInstance, SubAgentStatus
from Django_xm.apps.ai_engine.subagent_runtime.exceptions import (
    SubAgentInvalidStateError,
    SubAgentNestingLimitError,
    SubAgentNotFoundError,
)
from Django_xm.common.agent_limits import DEFAULT_MAX_AGENT_DEPTH, get_max_agent_depth

logger = logging.getLogger(__name__)

# 子 Agent 最大嵌套深度：C14 收敛后常量与判定函数唯一实现在
# Django_xm/common/agent_limits.py（settings.AGENT_MAX_DEPTH，默认 3）。
# ai_engine 禁止 import tools（反向依赖），故单源放 common 包；
# 本名仅为 subagent_runtime/__init__.py 导出面保留的别名。
MAX_SUBAGENT_DEPTH: int = DEFAULT_MAX_AGENT_DEPTH

# 子 Agent 单实例最大轮次（LangGraph recursion_limit，超限抛 GraphRecursionError）。
# 取值依据（3A-P2-1）：子代理任务面窄而浅（见 ai_engine/config.py 常量注释）；
# 本名保留为对外导出别名（subagent_runtime/__init__.py 导出面不变）。
MAX_SUBAGENT_ROUNDS = AGENT_RECURSION_LIMIT_SUBAGENT

# 判定函数别名：兼容既有内部调用点（原 _get_max_subagent_depth）。
_get_max_subagent_depth = get_max_agent_depth


class SubAgentRuntime:
    """子 Agent 运行时外壳（唯一入口）。

    默认使用 LangGraphAdapter；底层框架可切换，上层无感知。
    """

    def __init__(self, adapter: Any | None = None, graph_factory=None, config_factory=None):
        from Django_xm.apps.ai_engine.subagent_runtime.adapters.langgraph_adapter import LangGraphAdapter

        self.adapter = adapter or LangGraphAdapter(
            self, graph_factory=graph_factory, config_factory=config_factory
        )

    # ── 元数据持久化（sync_to_async 包装 ORM） ──────────────────────────

    @staticmethod
    async def _get_instance(thread_id: str) -> SubAgentInstance | None:
        try:
            return await sync_to_async(
                lambda: SubAgentInstance.objects.filter(thread_id=thread_id).first(),
                thread_sensitive=False,
            )()
        except Exception:
            logger.exception(f"读取子代理实例失败: {thread_id}")
            return None

    @staticmethod
    async def _create_instance(
        thread_id: str,
        parent_thread_id: str,
        metadata: dict,
    ) -> SubAgentInstance:
        return await sync_to_async(
            lambda: SubAgentInstance.objects.create(
                thread_id=thread_id,
                parent_thread_id=parent_thread_id,
                status=SubAgentStatus.RUNNING,
                metadata=metadata,
            ),
            thread_sensitive=False,
        )()

    @staticmethod
    async def _update_status(
        thread_id: str,
        status: str,
        *,
        pending_interrupt_info: dict | None = None,
        result_preview: str | None = None,
    ) -> None:
        def _update():
            try:
                inst = SubAgentInstance.objects.filter(thread_id=thread_id).first()
                if inst is None:
                    return
                inst.status = status
                if pending_interrupt_info is not None:
                    inst.pending_interrupt_info = pending_interrupt_info
                if result_preview is not None:
                    inst.result_preview = result_preview
                inst.save(update_fields=["status", "pending_interrupt_info", "result_preview", "updated_at"])
            except Exception:
                logger.exception(f"更新子代理状态失败: {thread_id} -> {status}")

        await sync_to_async(_update, thread_sensitive=False)()

    # ── 嵌套深度防护 ────────────────────────────────────────────────────

    async def _resolve_depth(self, parent_thread_id: str) -> int:
        """计算新子代理嵌套深度（主 agent=0，子=1，孙=2，曾孙=3）。

        parent 无 SubAgentInstance 记录时视为顶层（主 agent depth=0），
        故新子代理 depth = parent.depth + 1 = 1。
        """
        if not parent_thread_id:
            return 1
        parent = await self._get_instance(parent_thread_id)
        parent_depth = 0
        if parent is not None:
            parent_meta = parent.metadata or {}
            pd = parent_meta.get("depth", 0)
            if isinstance(pd, int) and pd >= 0:
                parent_depth = pd
        return parent_depth + 1

    async def _resolve_risk_ceiling(self, agent_name: str) -> str | None:
        """按注册表解析子代理风险上限（RiskLevel.value）。

        未命中注册表时返回 None（由 ApprovalMiddleware 使用默认策略）。
        """
        try:
            from Django_xm.apps.ai_engine.subagent_runtime.registry import get_subagent_spec

            spec = get_subagent_spec(agent_name)
            return spec.risk_ceiling if spec else None
        except Exception as e:
            # 失败时 ApprovalMiddleware 会走默认策略（风险上限不生效）。
            # 该降级是有意设计，但必须留痕，否则"上限突然不生效"无从追查。
            logger.debug(f"[SubAgentRuntime] 读取子代理 {agent_name} 风险上限失败，走默认策略: {e}")
            return None

    # ── 对外 API ────────────────────────────────────────────────────────

    async def spawn(
        self,
        parent_thread_id: str,
        agent_config: Any,
        task: str,
        configurable: dict | None = None,
        spawn_tool_call_id: str = "",
    ) -> SubAgentInstance:
        """创建并启动子 Agent（异步非阻塞，立即返回实例）。

        Args:
            parent_thread_id: 父线程 thread_id（主 agent = session_id）。
            agent_config: 子代理 AgentConfig（工具集/LLM/角色提示已由上层构造）。
            task: 子代理任务描述（作为 HumanMessage 输入）。
            configurable: 子代理 graph 的 configurable（含事件转发回调 / 嵌套层级）。
            spawn_tool_call_id: 触发派生的 spawn 工具自身 tool_call_id
                （LangChain BaseTool 执行时注入；前端据此将卡片挂到对应工具卡后）。

        Returns:
            SubAgentInstance（thread_id + status=running）。
        """
        depth = await self._resolve_depth(parent_thread_id)
        if depth > _get_max_subagent_depth():
            raise SubAgentNestingLimitError(
                f"子代理嵌套深度超限（max={_get_max_subagent_depth()}，当前将达={depth}）"
            )

        # C14 协作式取消配套：已被终止（failed 终态）的父线程不得再派生子代理，
        # 防止 terminate 后父图在下一个取消检查点前继续 spawn 孙代理
        parent = await self._get_instance(parent_thread_id) if parent_thread_id else None
        if parent is not None and parent.status == SubAgentStatus.FAILED:
            raise SubAgentInvalidStateError(
                f"父线程已终止，拒绝派生子代理: parent={parent_thread_id}"
            )

        thread_id = f"subagent_{uuid.uuid4().hex[:16]}"
        agent_name = getattr(agent_config, "name", "") or "general-purpose"
        system_prompt = getattr(agent_config, "system_prompt", "") or ""

        # resume 重建 AgentConfig 所需字段：tool_names 用于按名称恢复完整工具集
        # （工具对象/store 不可 JSON 序列化，故只存名称；store 由 agent_hub.create 注入）。
        metadata = {
            "depth": depth,
            "agent_name": agent_name,
            "task": task or "",
            "system_prompt": system_prompt,
            "model_name": getattr(agent_config, "model_name", None),
            "provider_id": getattr(agent_config, "provider_id", None),
            "user_id": getattr(agent_config, "user_id", None),
            "session_id": getattr(agent_config, "session_id", None),
            "tool_names": [getattr(t, "name", "") for t in (getattr(agent_config, "tools", None) or [])],
            "risk_ceiling": await self._resolve_risk_ceiling(agent_name),
            # 关联消息：spawn 工具的父 configurable 携带 assistant_message_id，
            # 前端据此将子代理卡片挂到对应 AI 消息下方（spec D10 前端路由）。
            "assistant_message_id": (configurable or {}).get("assistant_message_id") or "",
            # 子代理审批/事件路由必需字段（configurable 内存 registry 重启即失，
            # metadata 为重启恢复后的唯一持久化来源）。
            "chat_session_id": (configurable or {}).get("chat_session_id") or "",
            # 关联工具调用：spawn 工具自身的 tool_call_id（框架执行时注入），
            # 前端据此将子代理卡片精确关联到触发派生的 tool_call。
            "spawn_tool_call_id": spawn_tool_call_id or "",
        }

        instance = await self._create_instance(
            thread_id=thread_id,
            parent_thread_id=parent_thread_id,
            metadata=metadata,
        )

        # 启动后台执行（adapter 内部非阻塞，立即返回）
        try:
            await self.adapter.spawn(instance, agent_config, configurable)
        except Exception:
            logger.exception(f"子代理启动失败: {thread_id}")
            await self._update_status(thread_id, SubAgentStatus.FAILED)
            # 启动失败仍返回实例（status 由 adapter 或此处更新），不抛异常中断父 graph
            # 由 spawn 工具决定是否将失败透传为 ToolMessage。

        return instance

    async def resume(self, subagent_thread_id: str, resume_payload: Any) -> SubAgentInstance:
        """恢复中断的子 Agent（从 checkpoint 断点续跑）。

        两种可恢复挂起：
        - 审批中断（status=interrupted_pending_user_input）：resume_payload 为审批决策；
        - 业务等待挂起（status=running + pending_interrupt_info._subagent_wait=True）：
          resume_payload 为 {"subagent_results": [...]}，由孙代理终态唤醒恢复
          （langgraph_adapter 注册的父 awaiter 触发，非用户审批）。
        """
        instance = await self._get_instance(subagent_thread_id)
        if instance is None:
            raise SubAgentNotFoundError(f"子代理不存在: {subagent_thread_id}")
        pending = instance.pending_interrupt_info or {}
        is_approval_pending = instance.status == SubAgentStatus.INTERRUPTED_PENDING_USER_INPUT
        is_business_wait = instance.status == SubAgentStatus.RUNNING and bool(pending.get("_subagent_wait"))
        if not (is_approval_pending or is_business_wait):
            raise SubAgentInvalidStateError(
                f"子代理状态不允许 resume: {subagent_thread_id} status={instance.status}"
            )

        await self._update_status(subagent_thread_id, SubAgentStatus.RUNNING)
        try:
            await self.adapter.resume(instance, resume_payload)
        except Exception as e:
            # 恢复失败（adapter 未消化异常，如执行线程启动失败）：
            # 置终态并触发生命周期回调，父会话 fan-in 等待者立即收到失败
            # （否则挂到 watchdog 超时）。终态通知失败原因仅落日志
            # （ParentAwaiter 契约只携带 status），DB result/status 为权威记录。
            logger.exception(f"子代理恢复失败: {subagent_thread_id}")
            await self._update_status(subagent_thread_id, SubAgentStatus.FAILED)
            await self._notify_failed(instance, reason=repr(e))

        return instance

    async def _notify_failed(self, instance: Any, reason: str = "") -> None:
        """resume 失败终态回调（对齐 adapter._notify_finished / spawn 异常路径）。

        唤醒父会话等待者（lifecycle.on_subagent_finished）；通知本身失败不传播
        （状态已落 DB，非致命）。
        """
        try:
            from Django_xm.apps.ai_engine.subagent_runtime.lifecycle import get_lifecycle_manager

            logger.error(
                f"子代理 resume 终态为 failed: sub={instance.thread_id}, "
                f"parent={instance.parent_thread_id}, reason={reason}"
            )
            await get_lifecycle_manager().on_subagent_finished(
                subagent_thread_id=instance.thread_id,
                parent_thread_id=instance.parent_thread_id,
                status=SubAgentStatus.FAILED,
            )
        except Exception:
            logger.exception(f"子代理 resume 失败回调异常（非致命）: {instance.thread_id}")

    async def get_instance(self, subagent_thread_id: str) -> SubAgentInstance | None:
        """查询子代理实例（供父 Agent 轮询 / 前端恢复）。"""
        return await self._get_instance(subagent_thread_id)

    async def terminate(self, subagent_thread_id: str) -> None:
        """终止子代理（仅标记 failed，不强制 kill 正在运行的协程）。"""
        instance = await self._get_instance(subagent_thread_id)
        if instance is None:
            raise SubAgentNotFoundError(f"子代理不存在: {subagent_thread_id}")
        await self._update_status(subagent_thread_id, SubAgentStatus.FAILED)
        try:
            await self.adapter.terminate(instance)
        except Exception:
            logger.exception(f"子代理 terminate 失败（非致命）: {subagent_thread_id}")

    async def list_instances(self, parent_thread_id: str) -> list[SubAgentInstance]:
        """列出指定父线程下的所有子代理（会话删除时上层遍历回收）。"""
        try:
            return await sync_to_async(
                lambda: list(
                    SubAgentInstance.objects.filter(parent_thread_id=parent_thread_id).order_by("created_at")
                ),
                thread_sensitive=False,
            )()
        except Exception:
            logger.exception(f"列出子代理失败: {parent_thread_id}")
            return []


# 进程级单例（懒加载，避免重复构造 adapter）
_runtime: SubAgentRuntime | None = None


def get_subagent_runtime() -> SubAgentRuntime:
    """获取进程级 SubAgentRuntime 单例。"""
    global _runtime  # noqa: PLW0603 - 模块级单例惰性初始化
    if _runtime is None:
        _runtime = SubAgentRuntime()
    return _runtime
