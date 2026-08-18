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

from Django_xm.apps.ai_engine.models import SubAgentInstance, SubAgentStatus
from Django_xm.apps.ai_engine.subagent_runtime.exceptions import (
    SubAgentInvalidStateError,
    SubAgentNestingLimitError,
    SubAgentNotFoundError,
)

logger = logging.getLogger(__name__)

# 子 Agent 最大嵌套深度（0=主 agent，1=子 agent，2=孙 agent，3=曾孙 agent）。
# 与 tools.langchain.agent_context.MAX_AGENT_DEPTH 语义一致（同一配置源
# settings.AGENT_MAX_DEPTH），但此处独立维护读取逻辑，
# 避免 ai_engine → tools 的跨 app 反向依赖。
MAX_SUBAGENT_DEPTH: int = 3

# 子 Agent 单实例最大轮次（LangGraph recursion_limit，超限抛 GraphRecursionError）。
MAX_SUBAGENT_ROUNDS = 200


def _get_max_subagent_depth() -> int:
    """读取子 Agent 最大嵌套深度（settings.AGENT_MAX_DEPTH，异常回退默认值）。

    lazy 读取：避免 import 期强依赖 Django settings（如独立 unittest 上下文）。
    """
    try:
        from django.conf import settings

        return int(getattr(settings, "AGENT_MAX_DEPTH", MAX_SUBAGENT_DEPTH))
    except Exception:
        return MAX_SUBAGENT_DEPTH


class SubAgentRuntime:
    """子 Agent 运行时外壳（唯一入口）。

    默认使用 LangGraphAdapter；底层框架可切换，上层无感知。
    """

    def __init__(self, adapter: Any | None = None):
        from Django_xm.apps.ai_engine.subagent_runtime.adapters.langgraph_adapter import LangGraphAdapter

        self.adapter = adapter or LangGraphAdapter(self)

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
        except Exception:
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
            "user_id": getattr(agent_config, "user_id", None),
            "session_id": getattr(agent_config, "session_id", None),
            "tool_names": [getattr(t, "name", "") for t in (getattr(agent_config, "tools", None) or [])],
            "risk_ceiling": await self._resolve_risk_ceiling(agent_name),
            # 关联消息：spawn 工具的父 configurable 携带 assistant_message_id，
            # 前端据此将子代理卡片挂到对应 AI 消息下方（spec D10 前端路由）。
            "assistant_message_id": (configurable or {}).get("assistant_message_id") or "",
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
        """恢复中断的子 Agent（从 checkpoint 断点续跑）。"""
        instance = await self._get_instance(subagent_thread_id)
        if instance is None:
            raise SubAgentNotFoundError(f"子代理不存在: {subagent_thread_id}")
        if instance.status != SubAgentStatus.INTERRUPTED_PENDING_USER_INPUT:
            raise SubAgentInvalidStateError(
                f"子代理状态不允许 resume: {subagent_thread_id} status={instance.status}"
            )

        await self._update_status(subagent_thread_id, SubAgentStatus.RUNNING)
        try:
            await self.adapter.resume(instance, resume_payload)
        except Exception:
            logger.exception(f"子代理恢复失败: {subagent_thread_id}")
            await self._update_status(subagent_thread_id, SubAgentStatus.FAILED)

        return instance

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
