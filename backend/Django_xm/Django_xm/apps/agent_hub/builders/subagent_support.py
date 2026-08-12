"""子 agent 官方机制支持模块。

deepagents 0.7.5 升级后，原 ``subagent_patch.py`` 的 monkey-patch 机制不再需要：

- 子 agent 内 ``interrupt()`` 冒泡到父 graph 与 ``Command(resume=...)`` 恢复：
  deepagents 0.7.5 + langgraph 1.2 官方原生支持（无需注入 checkpointer）。
- 子 agent 工具事件：通过 langchain ``AgentMiddleware`` 官方扩展点捕获
  （本模块提供），无需 patch ``_build_task_tool``。

本模块提供两个 AgentMiddleware（均注入到子 agent 的 middleware 栈）：

1. ``SubAgentNestingMiddleware``：
   - 在 ``before_model`` 阶段从父 configurable 读取嵌套层级（depth / agent_path），
     计算当前子 agent 的递增值并写入 state（``subagent_depth`` 等）。
   - 子 agent 名称取自 ``get_config().metadata.lc_agent_name``（create_agent 官方设置）。
   - 供 ``ApprovalMiddleware`` 与 ``SubAgentToolEventMiddleware`` 从 state 读取。

2. ``SubAgentToolEventMiddleware``：
   - ``aafter_model``：提取本轮 AIMessage.tool_calls，转发 ``TOOL_CALL_PENDING`` 事件。
   - ``awrap_tool_call``：捕获工具执行结果，转发 ``TOOL_CALL_COMPLETED`` / ``TOOL_CALL_FAILED``。
   - 回调 ``_on_tool_event`` 由 adapter.py 注入到 ``config["configurable"]["_on_tool_event"]``，
     经父 config 自动传播（langgraph ensure_config 逐 key merge）到子 agent。

命名规范：
- 这些字段为后端 Python 内部标识符，使用 snake_case（PEP8）；
  仅当写入网络传输 payload 时才由前端 camelCase 转换层处理。
"""

from __future__ import annotations

import logging
from typing import Any, NotRequired, TypedDict

from langchain.agents.middleware import AgentMiddleware
from langgraph.config import get_config

logger = logging.getLogger(__name__)


class SubAgentNestingState(TypedDict, total=False):
    """子 agent 嵌套层级字段（写入 agent state）。

    字段说明：
        subagent_depth: 嵌套层级（1=一级子 agent，2=二级子 agent）
        subagent_path: 完整调用链路（如 ["main", "general-purpose"]）
        subagent_risk_ceiling: 子 agent 角色风险上限（RiskLevel 字符串值）
    """

    subagent_depth: NotRequired[int]
    subagent_path: NotRequired[list[str]]
    subagent_risk_ceiling: NotRequired[str]


def _read_configurable() -> dict[str, Any]:
    """读取当前执行的 RunnableConfig.configurable（含父 config 传播的字段）。"""
    try:
        config = get_config()
    except Exception:
        return {}
    if not isinstance(config, dict):
        return {}
    configurable = config.get("configurable")
    return configurable if isinstance(configurable, dict) else {}


def _read_agent_name() -> str:
    """读取当前执行上下文的 agent 名称（create_agent 的 metadata.lc_agent_name）。"""
    try:
        config = get_config()
        metadata = config.get("metadata") if isinstance(config, dict) else None
        name = metadata.get("lc_agent_name") if isinstance(metadata, dict) else None
        return name or ""
    except Exception:
        return ""


class SubAgentNestingMiddleware(AgentMiddleware):
    """子 agent 嵌套层级注入中间件。

    在子 agent 每次模型调用前（before_model），从父 configurable 继承
    ``depth`` / ``agent_path``（由主 agent 启动时注入），计算当前子 agent 的
    递增值（depth+1、path 追加子 agent 名称），写入 state。

    主 agent 本身不挂此中间件，因此 state 中无这些字段 → ApprovalMiddleware
    读到默认空值，视为主 agent（不做子 agent 风险加权）。
    """

    name = "subagent_nesting"
    state_schema = SubAgentNestingState

    def __init__(self, risk_ceiling: str = "HIGH"):
        """初始化。

        Args:
            risk_ceiling: 子 agent 角色风险上限（RiskLevel 字符串值）兜底值。
                before_model 时优先从 deep_builder._SUBAGENT_RISK_CEILINGS
                按子 agent 名称动态查询（web-researcher/doc-analyst=CONTROLLED，
                general-purpose=HIGH），查询失败回退到此值。
                共享同一实例也可为不同子 agent 提供正确 ceiling。
        """
        super().__init__()
        self.risk_ceiling = risk_ceiling

    def _resolve_risk_ceiling(self, agent_name: str) -> str:
        """按子 agent 名称解析角色风险上限。"""
        if agent_name:
            try:
                from Django_xm.apps.agent_hub.builders.deep_builder import _SUBAGENT_RISK_CEILINGS

                ceiling = _SUBAGENT_RISK_CEILINGS.get(agent_name)
                if ceiling is not None:
                    return ceiling.value if hasattr(ceiling, "value") else str(ceiling)
            except Exception as exc:
                logger.debug(f"[SubAgentNesting] 风险上限查询失败(非致命): {exc}")
        return self.risk_ceiling

    def before_model(self, state: dict[str, Any], runtime) -> dict[str, Any] | None:
        """计算并写入当前子 agent 的嵌套层级字段。"""
        configurable = _read_configurable()

        parent_depth = configurable.get("depth", 0)
        if not isinstance(parent_depth, int) or parent_depth < 0:
            parent_depth = 0
        parent_path = configurable.get("agent_path")
        if not isinstance(parent_path, list):
            parent_path = ["main"]

        agent_name = _read_agent_name()
        child_path = [*parent_path, agent_name] if agent_name else [*parent_path]
        risk_ceiling = self._resolve_risk_ceiling(agent_name)

        logger.debug(
            f"[SubAgentNesting] depth={parent_depth + 1}, "
            f"agent_path={child_path}, risk_ceiling={risk_ceiling}, "
            f"agent_name={agent_name or '(unnamed)'}"
        )
        return {
            "subagent_depth": parent_depth + 1,
            "subagent_path": child_path,
            "subagent_risk_ceiling": risk_ceiling,
        }


def _extract_nesting_from_state(state: dict[str, Any] | None) -> dict[str, Any]:
    """从 agent state 提取嵌套层级字段（由 SubAgentNestingMiddleware 写入）。"""
    state = state or {}
    depth = state.get("subagent_depth", 0)
    if not isinstance(depth, int) or depth < 0:
        depth = 0
    agent_path = state.get("subagent_path")
    if not isinstance(agent_path, list):
        agent_path = []
    risk_ceiling = state.get("subagent_risk_ceiling") or ""
    return {
        "depth": depth,
        "agent_path": agent_path,
        "risk_ceiling": risk_ceiling,
    }


class SubAgentToolEventMiddleware(AgentMiddleware):
    """子 agent 工具事件转发中间件。

    将子 agent 内部的工具调用事件转发到父 SSE 流：
    - ``aafter_model``：本轮 AIMessage 产生 tool_calls 时转发 ``TOOL_CALL_PENDING``
      （参数完整，直接取自 tool_call.args）。
    - ``awrap_tool_call``：工具执行完成/失败时转发 ``TOOL_CALL_COMPLETED`` /
      ``TOOL_CALL_FAILED``。

    回调签名（adapter.py 的 ``_on_tool_event``，async）：
        on_tool_event(event_type, tool_call_id, tool_name, **kwargs)
    kwargs 可含 parameters / result / error / depth / agent_path / risk_ceiling。

    嵌套层级字段（depth/agent_path/risk_ceiling）随事件透传，
    供前端 ToolCallCard 展示完整调用链路；agent_name 取自 metadata。
    """

    name = "subagent_tool_events"
    state_schema = SubAgentNestingState

    @staticmethod
    async def _forward_event(
        on_tool_event,
        event_type,
        tool_call_id: str,
        tool_name: str,
        *,
        parameters: dict[str, Any] | None = None,
        result: Any = None,
        error: Any = None,
        nesting: dict[str, Any],
    ) -> None:
        """构造事件 kwargs 并调用回调（含嵌套层级字段透传）。"""
        kwargs: dict[str, Any] = {}
        if parameters is not None:
            kwargs["parameters"] = parameters
        if result is not None:
            kwargs["result"] = result
        if error is not None:
            kwargs["error"] = error
        depth = nesting.get("depth", 0)
        if depth > 0:
            kwargs["depth"] = depth
        if nesting.get("agent_path"):
            kwargs["agent_path"] = nesting["agent_path"]
        if nesting.get("agent_name"):
            kwargs["agent_name"] = nesting["agent_name"]
        if nesting.get("risk_ceiling"):
            kwargs["risk_ceiling"] = nesting["risk_ceiling"]
        try:
            await on_tool_event(event_type, tool_call_id, tool_name, **kwargs)
        except Exception as exc:
            logger.warning(
                f"[SubAgentToolEvent] 事件转发失败: event={event_type}, "
                f"tool={tool_name}, tc_id={tool_call_id}: {exc}"
            )

    async def aafter_model(self, state: dict[str, Any], runtime) -> dict[str, Any] | None:
        """转发本轮模型产生的工具调用 PENDING 事件。"""
        from langchain_core.messages import AIMessage

        from Django_xm.common.event_schema import EventType

        on_tool_event = _read_configurable().get("_on_tool_event")
        if on_tool_event is None:
            return None

        messages = state.get("messages", []) or []
        last_ai_msg = None
        for msg in reversed(messages):
            if isinstance(msg, AIMessage):
                last_ai_msg = msg
                break
        if not last_ai_msg or not getattr(last_ai_msg, "tool_calls", None):
            return None

        nesting = _extract_nesting_from_state(state)
        agent_name = _read_agent_name()
        if agent_name:
            nesting["agent_name"] = agent_name

        from Django_xm.apps.tools.param_extractor import extract_tool_params

        for tc in last_ai_msg.tool_calls:
            tool_name = tc.get("name", "")
            tc_id = tc.get("id", "")
            args = extract_tool_params(tc)
            logger.info(
                f"[SubAgentToolEvent] PENDING: tool={tool_name}, tc_id={tc_id}, "
                f"subagent={agent_name or '(main)'}"
            )
            await self._forward_event(
                on_tool_event,
                EventType.TOOL_CALL_PENDING,
                tc_id,
                tool_name,
                parameters=args if isinstance(args, dict) else {},
                nesting=nesting,
            )
        return None

    async def awrap_tool_call(self, request, execute):
        """捕获工具执行结果，转发 COMPLETED / FAILED 事件。"""
        from Django_xm.common.event_schema import EventType

        tc = request.tool_call
        tool_name = tc.get("name", "")
        tc_id = tc.get("id", "")
        args = tc.get("args") or {}

        on_tool_event = _read_configurable().get("_on_tool_event")
        nesting = _extract_nesting_from_state(request.state)
        agent_name = _read_agent_name()
        if agent_name:
            nesting["agent_name"] = agent_name

        try:
            result = await execute(request)
        except Exception as exc:
            logger.info(
                f"[SubAgentToolEvent] FAILED: tool={tool_name}, tc_id={tc_id}, "
                f"subagent={agent_name or '(main)'}: {exc}"
            )
            if on_tool_event is not None:
                await self._forward_event(
                    on_tool_event,
                    EventType.TOOL_CALL_FAILED,
                    tc_id,
                    tool_name,
                    parameters=args if isinstance(args, dict) else {},
                    error=str(exc),
                    nesting=nesting,
                )
            raise

        content = getattr(result, "content", None)
        logger.info(
            f"[SubAgentToolEvent] COMPLETED: tool={tool_name}, tc_id={tc_id}, "
            f"subagent={agent_name or '(main)'}"
        )
        if on_tool_event is not None:
            await self._forward_event(
                on_tool_event,
                EventType.TOOL_CALL_COMPLETED,
                tc_id,
                tool_name,
                parameters=args if isinstance(args, dict) else {},
                result=content,
                nesting=nesting,
            )
        return result
