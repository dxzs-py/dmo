"""LangGraph 适配器：对接独立 LangGraph graph 的子代理执行。

核心（对齐 Trae Solo / spec D3）：
- ``astream`` 迭代结束 ≠ 任务完成；流消费完毕必须读 ``state.next`` 判定。
- interrupt 时协程退出释放资源，状态置 ``interrupted_pending_user_input``。
- 子代理在独立线程 + 独立事件循环执行（创建 agent / astream / 释放连接同 loop）。
- 终态（completed/failed）触发生命周期回调（spec D4，唤醒父 Graph）。
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from Django_xm.apps.ai_engine.models import SubAgentStatus
from Django_xm.apps.ai_engine.subagent_runtime.adapters.base import BaseRuntimeAdapter
from Django_xm.apps.ai_engine.subagent_runtime.runtime import MAX_SUBAGENT_ROUNDS

logger = logging.getLogger(__name__)


class LangGraphAdapter(BaseRuntimeAdapter):
    """对接 LangGraph CompiledStateGraph（BASE agent 工具链 + DeepAgents 独立 graph）。"""

    def __init__(self, runtime: Any):
        self.runtime = runtime

    # ── 对外契约 ────────────────────────────────────────────────────────

    async def spawn(self, instance: Any, agent_config: Any, configurable: dict | None = None) -> None:
        """启动子代理执行（独立线程，非阻塞）。"""
        thread = threading.Thread(
            target=self._run_in_thread,
            args=(instance, agent_config, configurable, None),
            daemon=True,
        )
        thread.start()

    async def resume(self, instance: Any, resume_payload: Any) -> None:
        """恢复中断的子代理（独立线程，用 Command(resume) 断点续跑）。"""
        # 恢复需要重新创建 graph（graph 对象不能跨事件循环复用）
        agent_config = await self._rebuild_agent_config(instance)
        if agent_config is None:
            await self.runtime._update_status(instance.thread_id, SubAgentStatus.FAILED)
            return
        thread = threading.Thread(
            target=self._run_in_thread,
            args=(instance, agent_config, None, resume_payload),
            daemon=True,
        )
        thread.start()

    async def get_state(self, instance: Any) -> dict:
        """获取子代理 graph 的真实 state。"""
        return {"thread_id": instance.thread_id}

    async def terminate(self, instance: Any) -> None:
        """终止子代理（仅标记状态，不强制 kill 协程；由 runtime 统一更新状态）。"""
        return None

    # ── 后台执行 ────────────────────────────────────────────────────────

    def _run_in_thread(self, instance: Any, agent_config: Any, configurable: dict | None, resume_payload: Any) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._execute(instance, agent_config, configurable, resume_payload))
        except Exception:
            logger.exception(f"子代理执行异常: {instance.thread_id}")
            loop.run_until_complete(
                self.runtime._update_status(instance.thread_id, SubAgentStatus.FAILED)
            )
            loop.run_until_complete(self._notify_finished(instance, "failed"))
        finally:
            # 三种退出路径（正常完成 / interrupt / 异常）统一释放异步 checkpointer 连接
            try:
                from Django_xm.apps.ai_engine.services.checkpointer_factory import release_async_checkpointer

                loop.run_until_complete(release_async_checkpointer())
            except Exception:
                logger.exception("释放子代理 checkpointer 连接失败（非致命）")
            try:
                loop.close()
            except Exception:
                pass
            asyncio.set_event_loop(None)

    async def _execute(self, instance: Any, agent_config: Any, configurable: dict | None, resume_payload: Any) -> None:
        from langchain_core.messages import HumanMessage
        from langgraph.types import Command as LgCommand

        from Django_xm.apps.agent_hub import create as agent_hub_create

        thread_id = instance.thread_id
        run_config = {
            "configurable": self._build_configurable(instance, configurable),
            "recursion_limit": MAX_SUBAGENT_ROUNDS,
        }

        # 创建子代理 graph（独立 thread_id + 独立 checkpoint）
        agent = await agent_hub_create(agent_config)

        if resume_payload is None:
            # 首次 spawn：HumanMessage 输入（task 为任务描述，来自 metadata）
            task = (instance.metadata or {}).get("task", "")
            graph_input = {"messages": [HumanMessage(content=task)]}
        else:
            # resume：Command(resume={interrupt_id: decisions})
            interrupt_id = (instance.pending_interrupt_info or {}).get("interrupt_id", "")
            graph_input = LgCommand(resume={interrupt_id: resume_payload})

        # 消费 astream（事件转发由中间件钩子完成，无需手动消费 chunk）
        async for _chunk in agent.graph.astream(
            graph_input,
            config=run_config,
            stream_mode=["messages", "updates"],
        ):
            pass

        # 关键：astream 结束 ≠ 完成，读真实 state 判定
        state = await agent.graph.aget_state(run_config)
        if state is not None and getattr(state, "next", None):
            # interrupt 挂起：写 pending_interrupt_info，退出协程等外部 resume
            pending = self._extract_interrupt_info(state)
            await self.runtime._update_status(
                thread_id,
                SubAgentStatus.INTERRUPTED_PENDING_USER_INPUT,
                pending_interrupt_info=pending,
            )
            logger.info(f"子代理 interrupt 挂起: {thread_id}, interrupt_id={pending.get('interrupt_id')}")
        else:
            result_preview = self._extract_result(state)
            await self.runtime._update_status(
                thread_id,
                SubAgentStatus.COMPLETED,
                result_preview=result_preview,
            )
            logger.info(f"子代理执行完成: {thread_id}, result_len={len(result_preview)}")
            await self._notify_finished(instance, "completed")

    # ── 辅助 ────────────────────────────────────────────────────────────

    async def _notify_finished(self, instance: Any, status: str) -> None:
        """子代理终态（completed/failed）触发生命周期回调，唤醒父 Graph（spec D4）。"""
        try:
            from Django_xm.apps.ai_engine.subagent_runtime.lifecycle import get_lifecycle_manager

            await get_lifecycle_manager().on_subagent_finished(
                subagent_thread_id=instance.thread_id,
                parent_thread_id=instance.parent_thread_id,
                status=status,
            )
        except Exception:
            logger.exception(f"子代理生命周期回调失败（非致命）: {instance.thread_id}")

    def _build_configurable(self, instance: Any, configurable: dict | None) -> dict:
        """构造子代理 configurable。

        - ``thread_id``：子代理独立 id（独立 checkpoint）。
        - ``subagent_thread_id``：SSE 定向推送路由标识符（协议字段，snake_case）。
        - 事件转发回调（_on_tool_event / _on_subagent_content）从父 configurable 继承。
        - ``risk_ceiling``：子代理角色风险上限（审批中间件读取）。
        """
        meta = instance.metadata or {}
        depth = meta.get("depth", 0)
        agent_name = meta.get("agent_name", "general-purpose")
        cfg: dict = {
            "thread_id": instance.thread_id,
            "subagent_thread_id": instance.thread_id,
            "depth": depth,
            "agent_name": agent_name,
            "agent_path": ["main", agent_name],
            "risk_ceiling": meta.get("risk_ceiling"),
        }
        if configurable:
            for _key in ("_on_tool_event", "_on_subagent_content", "chat_session_id", "assistant_message_id"):
                if _key in configurable and configurable[_key] is not None:
                    cfg[_key] = configurable[_key]
        return cfg

    @staticmethod
    def _extract_interrupt_info(state: Any) -> dict:
        """从 state.tasks 提取 interrupt 信息（ApprovalMiddleware 批量审批 payload）。

        严格对齐 spec pending_interrupt_info 批量结构契约
        （interrupt_type / interrupt_id / requests:[{tool_call_id/tool_name/args/risk_level/reason}]），
        框架私有字段（_approval / graph_interrupt_id / session_id / danger_level 等）不透出上层。
        """
        for task in getattr(state, "tasks", []) or []:
            for intr in getattr(task, "interrupts", []) or []:
                value = getattr(intr, "value", None)
                if not isinstance(value, dict) or not value.get("_approval"):
                    continue
                raw_requests = value.get("requests", []) or []
                requests = []
                for req in raw_requests:
                    if not isinstance(req, dict):
                        continue
                    requests.append(
                        {
                            "tool_call_id": req.get("tool_call_id", ""),
                            "tool_name": req.get("tool_name", ""),
                            "args": req.get("args", {}) or {},
                            "risk_level": req.get("risk_level", ""),
                            "reason": req.get("description") or req.get("title") or "",
                        }
                    )
                return {
                    "interrupt_type": "approval",
                    "interrupt_id": getattr(intr, "id", ""),
                    "requests": requests,
                }
        return {"interrupt_type": "approval", "interrupt_id": "", "requests": []}

    @staticmethod
    def _extract_result(state: Any) -> str:
        """从 state.values 提取最终 AI 回复文本（结果预览）。"""
        try:
            values = getattr(state, "values", None) or {}
            messages = values.get("messages", [])
            for msg in reversed(messages):
                content = getattr(msg, "content", None)
                if content:
                    text = str(content)
                    return text[:5000]
        except Exception:
            logger.exception("提取子代理结果失败（非致命）")
        return ""

    async def _rebuild_agent_config(self, instance: Any) -> Any:
        """resume 时从 instance.metadata 重建 AgentConfig。

        graph 对象不能跨事件循环复用，resume 必须重新创建 agent。
        工具集按 metadata["tool_names"] 恢复（与 spawn 时一致），
        system_prompt 按 metadata["system_prompt"] 恢复（角色提示）。
        """
        from Django_xm.apps.agent_hub import AgentConfig, AgentType
        from Django_xm.apps.tools import get_all_basic_tools, get_tools_for_request_async

        meta = instance.metadata or {}
        tool_names = meta.get("tool_names") or []
        user_id = meta.get("user_id")

        if tool_names:
            try:
                tools = await get_tools_for_request_async(
                    use_tools=True,
                    selected_tools=list(tool_names),
                    user_id=user_id,
                )
            except Exception as e:
                logger.warning(f"resume 重建工具集失败，回退基础工具集: {e}")
                tools = get_all_basic_tools()
        else:
            tools = get_all_basic_tools()

        return AgentConfig(
            agent_type=AgentType.BASE,
            name=meta.get("agent_name", "general-purpose"),
            tools=tools,
            system_prompt=meta.get("system_prompt", ""),
            user_id=user_id,
            session_id=meta.get("session_id"),
            model_name=meta.get("model_name"),
        )
