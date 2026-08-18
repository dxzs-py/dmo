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

主/子代理判定（fix-deep-research-subagent-activation spec D1）：
- 唯一判定依据为 ``configurable.subagent_thread_id`` 非空（SubAgentRuntime 适配器
  ``_build_configurable`` 在 spawn 时写入）；主 agent 无此字段。
- 主 agent：``subagent_depth`` 恒写 0，不注入 ``subagent_path`` 等子代理语义 state。
- 真子代理：``subagent_depth = configurable.depth``（适配器写入的绝对深度，
  ``runtime.spawn`` 已算好 parent_depth + 1，中间件不再 +1）。
- 废弃 depth 推断式判定（主 agent 显式注入 depth=0 的场景下 depth+1 必然误判），
  depth 降级为展示用元数据。

最大嵌套深度（Task 1）：
- deepagents inline subagent 链路与 ``spawn_sub_agent`` 链路共用同一
  深度上限（apps/tools/langchain/agent_context.py 的 get_max_agent_depth()，
  配置源 settings.AGENT_MAX_DEPTH，默认 3；语义：主=0/子=1/孙=2/曾孙=3）。
- ``SubAgentNestingMiddleware.before_model`` 读取适配器写入的绝对深度，
  超过深度上限（``get_max_agent_depth()``）时写入 ``subagent_depth_blocked=True`` 标记（不抛异常）；
  随后 ``wrap_model_call`` / ``awrap_model_call`` 检查该标记，**拦截子 agent 的模型调用**
  并返回含明确错误（当前深度与最大深度）的 AIMessage。子 agent 因此不执行任何任务，
  正常结束；deepagents task 工具（_return_command_with_state_update）将该 AIMessage
  文本转为 ToolMessage 回传主 agent——与超限拒绝派生的语义一致：主 agent 收到明确
  错误后调整策略继续，不产生无限嵌套，也不破坏主 agent
  执行流（不抛异常，避免经 ToolNode 冒泡导致主 graph 失败）。

命名规范：
- 这些字段为后端 Python 内部标识符，使用 snake_case（PEP8）；
  仅当写入网络传输 payload 时才由前端 camelCase 转换层处理。
"""

from __future__ import annotations

import logging
from typing import Any, NotRequired, TypedDict

from langchain.agents.middleware import AgentMiddleware
from langgraph.config import get_config

from Django_xm.apps.tools.langchain.agent_context import MAX_AGENT_DEPTH, get_max_agent_depth

# MAX_AGENT_DEPTH 仅为兼容既有 import（tests.test_subagent_nesting）保留 re-export，
# 运行时深度判断统一调用 get_max_agent_depth()（支持 settings.AGENT_MAX_DEPTH 配置）。
_ = MAX_AGENT_DEPTH

logger = logging.getLogger(__name__)


def _build_depth_blocked_message(current_depth: int) -> str:
    """构造子代理嵌套深度超限错误消息（与 agent_context.is_max_depth_reached 语义一致）。"""
    max_depth = get_max_agent_depth()
    return (
        f"已达到最大子代理嵌套深度({max_depth})，当前深度={current_depth}。"
        f"请直接在当前代理中完成任务，不要创建更多子代理。"
    )


class SubAgentNestingState(TypedDict, total=False):
    """子 agent 嵌套层级字段（写入 agent state）。

    字段说明：
        subagent_depth: 嵌套层级（1=一级子 agent，2=二级子 agent）
        subagent_path: 完整调用链路（如 ["main", "general-purpose"]）
        subagent_risk_ceiling: 子 agent 角色风险上限（RiskLevel 字符串值）
        subagent_description: 子 agent 角色描述（任务目标，取自 subagent_runtime.registry，
            供前端组头展示）
        subagent_depth_blocked: 深度超限标记（True=超过 MAX_AGENT_DEPTH，
            由 wrap_model_call / awrap_model_call 拦截模型调用）
    """

    subagent_depth: NotRequired[int]
    subagent_path: NotRequired[list[str]]
    subagent_risk_ceiling: NotRequired[str]
    subagent_description: NotRequired[str]
    subagent_depth_blocked: NotRequired[bool]


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


def _read_subagent_thread_id() -> str:
    """读取子代理路由标识符（spec D10 SSE 定向推送）。

    ``subagent_thread_id`` 由 SubAgentRuntime 适配器写入 configurable
    （LangGraphAdapter._build_configurable），主 agent 无此字段（返回空串）。
    """
    return _read_configurable().get("subagent_thread_id") or ""


class SubAgentNestingMiddleware(AgentMiddleware):
    """子 agent 嵌套层级注入中间件（主/子判定 spec D1）。

    以 ``configurable.subagent_thread_id`` 非空作为子代理唯一判定：
    - 主 agent（thread_id 为空）：``subagent_depth`` 恒写 0，不写入
      ``subagent_path`` / ``subagent_risk_ceiling`` / ``subagent_description``
      （不注入子代理语义 state）。
    - 真子代理（thread_id 非空）：``subagent_depth = configurable.depth``
      （SubAgentRuntime 适配器 spawn 时已算好的绝对深度，不再 +1），
      ``subagent_path`` 追加子代理名称，风险上限/描述按注册表解析写入。
    """

    name = "subagent_nesting"
    state_schema = SubAgentNestingState

    def __init__(self, risk_ceiling: str = "high"):
        """初始化。

        Args:
            risk_ceiling: 子 agent 角色风险上限（RiskLevel 字符串值）兜底值。
                before_model 时优先从 subagent_runtime.registry 按子 agent 名称
                动态查询（web-researcher/doc-analyst=controlled，
                general-purpose=high），查询失败回退到此值。
                共享同一实例也可为不同子 agent 提供正确 ceiling。
        """
        super().__init__()
        self.risk_ceiling = risk_ceiling

    def _resolve_risk_ceiling(self, agent_name: str) -> str:
        """按子 agent 名称解析角色风险上限（单一来源：subagent_runtime.registry）。"""
        if agent_name:
            try:
                from Django_xm.apps.ai_engine.subagent_runtime.registry import get_subagent_spec

                spec = get_subagent_spec(agent_name)
                if spec is not None:
                    return spec.risk_ceiling
            except Exception as exc:
                logger.debug(f"[SubAgentNesting] 风险上限查询失败(非致命): {exc}")
        return self.risk_ceiling

    def _resolve_description(self, agent_name: str) -> str:
        """按子 agent 名称解析角色描述（单一来源：subagent_runtime.registry）。"""
        if agent_name:
            try:
                from Django_xm.apps.ai_engine.subagent_runtime.registry import get_subagent_spec

                spec = get_subagent_spec(agent_name)
                return spec.description if spec else ""
            except Exception as exc:
                logger.debug(f"[SubAgentNesting] 子 agent 描述查询失败(非致命): {exc}")
        return ""

    def before_model(self, state: dict[str, Any], runtime) -> dict[str, Any] | None:
        """计算并写入当前 agent 的嵌套层级字段（主/子判定 spec D1）。

        以 ``configurable.subagent_thread_id`` 非空作为子代理唯一判定：
        - 主 agent（thread_id 为空）：``subagent_depth`` 恒写 0、不写
          ``subagent_path`` 等子代理语义字段（防止下游中间件误判为子代理）。
        - 真子代理（thread_id 非空）：``subagent_depth = configurable.depth``
          （适配器 spawn 时已算好的绝对深度，中间件不再 +1）。

        深度限制（Task 1，与 ``spawn_sub_agent`` 派生共用同一深度上限）：
        绝对深度超过 ``get_max_agent_depth()``（settings.AGENT_MAX_DEPTH，
        默认 3；主=0/子=1/孙=2/曾孙=3）时写入
        ``subagent_depth_blocked=True`` 标记（不抛异常，避免经 ToolNode 冒泡
        破坏主 agent 执行流）。随后 wrap_model_call / awrap_model_call 检查该
        标记拦截模型调用并返回明确错误，与 agent_context.is_max_depth_reached
        分支语义一致（主 agent 收到含当前/最大深度的错误后调整策略继续）。
        """
        configurable = _read_configurable()
        subagent_thread_id = configurable.get("subagent_thread_id") or ""

        # 主 agent（spec D1）：恒写 depth=0，不注入子代理语义 state
        if not subagent_thread_id:
            logger.debug("[SubAgentNesting] 主 agent: subagent_depth=0（subagent_thread_id 为空）")
            return {"subagent_depth": 0}

        # 真子代理：绝对深度由适配器写入 configurable.depth（runtime.spawn
        # 已计算 parent_depth + 1），异常值兜底为 1（子代理深度至少为 1）
        subagent_depth = configurable.get("depth", 1)
        if not isinstance(subagent_depth, int) or subagent_depth < 1:
            subagent_depth = 1
        parent_path = configurable.get("agent_path")
        if not isinstance(parent_path, list):
            parent_path = ["main"]

        # agent_name 优先从 configurable 读（spawn_sub_agent 派生注入），
        # 回退 create_agent metadata（deepagents 子代理）。
        agent_name = configurable.get("agent_name") or _read_agent_name()
        child_path = [*parent_path, agent_name] if agent_name else [*parent_path]
        risk_ceiling = self._resolve_risk_ceiling(agent_name)
        subagent_description = self._resolve_description(agent_name)

        update: dict[str, Any] = {
            "subagent_depth": subagent_depth,
            "subagent_path": child_path,
            "subagent_risk_ceiling": risk_ceiling,
            "subagent_description": subagent_description,
        }
        max_depth = get_max_agent_depth()
        if subagent_depth > max_depth:
            logger.warning(
                f"[SubAgentNesting] 子代理嵌套深度超限被阻止: "
                f"subagent={agent_name or '(unnamed)'}, 当前深度={subagent_depth}, "
                f"最大深度={max_depth}"
            )
            update["subagent_depth_blocked"] = True

        logger.debug(
            f"[SubAgentNesting] depth={subagent_depth}, "
            f"agent_path={child_path}, risk_ceiling={risk_ceiling}, "
            f"agent_name={agent_name or '(unnamed)'}"
        )
        return update

    def wrap_model_call(self, request, call):
        """同步模型调用拦截：深度超限时返回错误 AIMessage，不调用真实模型。

        子 agent 因超限被标记 blocked 后，本钩子截断其执行：模型调用被替换为
        含明确错误（当前深度/最大深度）的 AIMessage（无 tool_calls），子 agent
        因此不执行任何任务并正常结束；deepagents task 工具将该 AIMessage 文本
        转为 ToolMessage 回传主 agent（与超限拒绝派生返回错误语义一致）。
        """
        if request.state.get("subagent_depth_blocked"):
            current_depth = request.state.get("subagent_depth", 0)
            logger.info(f"[SubAgentNesting] 拦截超限子 agent 的模型调用 (depth={current_depth})")
            from langchain_core.messages import AIMessage

            return AIMessage(content=_build_depth_blocked_message(current_depth))
        return call(request)

    async def awrap_model_call(self, request, call):
        """异步模型调用拦截（深度研究链路 astream 使用，逻辑同 wrap_model_call）。"""
        if request.state.get("subagent_depth_blocked"):
            current_depth = request.state.get("subagent_depth", 0)
            logger.info(f"[SubAgentNesting] 拦截超限子 agent 的模型调用 (depth={current_depth})")
            from langchain_core.messages import AIMessage

            return AIMessage(content=_build_depth_blocked_message(current_depth))
        return await call(request)


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
    description = state.get("subagent_description") or ""
    return {
        "depth": depth,
        "agent_path": agent_path,
        "risk_ceiling": risk_ceiling,
        "description": description,
    }


class SubAgentToolEventMiddleware(AgentMiddleware):
    """子 agent 工具事件转发中间件（主/子判定 spec D1/D2）。

    将子 agent 内部的工具调用事件转发到父 SSE 流：
    - ``aafter_model``：本轮 AIMessage 产生 tool_calls 时转发 ``TOOL_CALL_PENDING``
      （参数完整，直接取自 tool_call.args）。
    - ``awrap_tool_call``：工具执行完成/失败时转发 ``TOOL_CALL_COMPLETED`` /
      ``TOOL_CALL_FAILED``。

    仅对子代理（``configurable.subagent_thread_id`` 非空）转发；主 agent 的
    工具事件由主执行链路（run_stream_loop / adapter 主循环）唯一发布与持久化，
    本中间件转发主 agent 事件会造成双路径发布与持久化归属错位（spec D2 禁止）。

    回调签名（adapter.py 的 ``_on_tool_event``，async）：
        on_tool_event(event_type, tool_call_id, tool_name, **kwargs)
    kwargs 可含 parameters / result / error / depth / risk_ceiling / description
    （agent_path 不再随工具事件透传——路由唯一依据为 subagent_thread_id，
    spec MODIFIED/REMOVED：agent_path 从事件 payload 中删除）。

    嵌套层级字段（depth/risk_ceiling/description）随事件透传作展示元数据，
    供前端 ToolCallCard 展示层级与任务目标描述；agent_name 取自 metadata。
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
        """构造事件 kwargs 并调用回调（含嵌套层级字段透传）。

        本方法仅被子代理转发路径调用，depth/risk_ceiling/description 等作为
        展示元数据直接透传（不再以 depth 推断是否子代理）；agent_path 不透传
        （spec REMOVED：agent_path 从事件 payload 中删除，路由唯一依据
        subagent_thread_id）。
        """
        kwargs: dict[str, Any] = {}
        if parameters is not None:
            kwargs["parameters"] = parameters
        if result is not None:
            kwargs["result"] = result
        if error is not None:
            kwargs["error"] = error
        kwargs["depth"] = nesting.get("depth", 0)
        if nesting.get("agent_name"):
            kwargs["agent_name"] = nesting["agent_name"]
        if nesting.get("risk_ceiling"):
            kwargs["risk_ceiling"] = nesting["risk_ceiling"]
        if nesting.get("description"):
            kwargs["description"] = nesting["description"]
        # subagent_thread_id（spec D10）：子代理 SSE 定向推送路由标识符，
        # 从 configurable 读取（SubAgentRuntime 适配器注入），仅子代理非空。
        subagent_thread_id = _read_subagent_thread_id()
        if subagent_thread_id:
            kwargs["subagent_thread_id"] = subagent_thread_id
        try:
            await on_tool_event(event_type, tool_call_id, tool_name, **kwargs)
        except Exception as exc:
            logger.warning(
                f"[SubAgentToolEvent] 事件转发失败: event={event_type}, "
                f"tool={tool_name}, tc_id={tool_call_id}: {exc}"
            )

    async def aafter_model(self, state: dict[str, Any], runtime) -> dict[str, Any] | None:
        """转发本轮模型产生的工具调用 PENDING 事件。

        仅对子代理（``subagent_thread_id`` 非空，spec D1）转发：主 agent 的
        工具事件由主执行链路（run_stream_loop._publish_input_ready_events /
        adapter 主循环）统一发布，本中间件若转发主 agent 事件会导致重复发布。
        """
        from langchain_core.messages import AIMessage

        from Django_xm.common.event_schema import EventType

        on_tool_event = _read_configurable().get("_on_tool_event")
        if on_tool_event is None:
            return None

        # 仅子代理（spec D1）转发；主 agent 工具事件由主链路发布
        if not _read_subagent_thread_id():
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
                f"subagent={agent_name or '(unnamed)'}"
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
        """捕获工具执行结果，转发 COMPLETED / FAILED 事件。

        仅对子代理（``subagent_thread_id`` 非空，spec D1）转发；
        主 agent 工具事件由主链路发布。
        """
        from Django_xm.common.event_schema import EventType

        tc = request.tool_call
        tool_name = tc.get("name", "")
        tc_id = tc.get("id", "")
        args = tc.get("args") or {}

        on_tool_event = _read_configurable().get("_on_tool_event")
        # 仅子代理（spec D1）转发；主 agent 工具事件由主链路发布
        if not _read_subagent_thread_id():
            return await execute(request)
        nesting = _extract_nesting_from_state(request.state)
        agent_name = _read_agent_name()
        if agent_name:
            nesting["agent_name"] = agent_name

        try:
            result = await execute(request)
        except Exception as exc:
            logger.info(
                f"[SubAgentToolEvent] FAILED: tool={tool_name}, tc_id={tc_id}, "
                f"subagent={agent_name or '(unnamed)'}: {exc}"
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
            f"subagent={agent_name or '(unnamed)'}"
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


class SubAgentContentMiddleware(AgentMiddleware):
    """子代理正文/中间思考流采集中间件（主/子判定 spec D1/D2）。

    在子代理每次模型调用后（``aafter_model``）捕获本轮 AIMessage 的正文
    （``content``）与中间思考（``reasoning_content``），经 configurable 中
    父执行层注入的 ``_on_subagent_content`` 回调转发到父 SSE/WS 流。

    回调签名（由 adapter.py / chat 执行层注入，async）：
        on_subagent_content(agent_path, content, reasoning_content, agent_name, depth,
                            subagent_thread_id, msg_id)

    - msg_id：本轮 AIMessage 的唯一 id（无 id 时回退 content 确定性哈希），
      供回调侧按消息幂等去重——审批 interrupt 恢复时 LangGraph 重放节点，
      aafter_model 会对同一条 AIMessage 再次触发，回调必须跳过已转发的消息，
      否则子代理卡片内容重复且 tool_call position 错位（时序错乱根因）。

    - 仅子代理（``configurable.subagent_thread_id`` 非空，spec D1）捕获转发；
      主 agent 的正文/思考由主执行链路 ``stream_content_update`` /
      ``stream_reasoning`` 唯一发布（spec D2 单路径，本中间件捕获主 agent
      内容会导致思考流被误路由丢弃）。
    - 嵌套层级字段（depth/agent_path/agent_name）取自 state（SubAgentNestingMiddleware
      在 before_model 写入），仅作展示元数据透传（不参与主/子判定）。
    - 前端按 subagent_thread_id 路由到对应子代理卡片，与工具事件同一归属语义；
      reasoning 与 content 独立字段透传（前端分别累计展示）。
    - 与 SubAgentToolEventMiddleware 同机制：父 config 经 langgraph ensure_config
      自动传播到子 agent，本中间件从 configurable 读取回调。
    """

    name = "subagent_content"
    state_schema = SubAgentNestingState

    @staticmethod
    def _extract_reasoning(msg) -> str:
        """从 AIMessage 提取中间思考内容（兼容 DeepSeek/Ollama/Anthropic）。"""
        if msg is None:
            return ""
        try:
            from Django_xm.apps.chat.services.stream_chunk_processors import extract_thinking_content

            return extract_thinking_content(msg) or ""
        except Exception:
            logger.debug("[SubAgentContent] extract_thinking_content 提取失败（回退兜底路径）", exc_info=True)
        # 兜底：additional_kwargs.reasoning_content 直接读取
        try:
            additional_kwargs = getattr(msg, "additional_kwargs", {}) or {}
            reasoning = additional_kwargs.get("reasoning_content")
            if isinstance(reasoning, str):
                return reasoning
        except Exception:
            logger.debug("[SubAgentContent] additional_kwargs 读取失败（返回空思考）", exc_info=True)
        return ""

    async def aafter_model(self, state: dict[str, Any], runtime) -> dict[str, Any] | None:
        """捕获本轮子代理模型输出的正文与中间思考，转发到父流。

        仅对子代理（``subagent_thread_id`` 非空，spec D1）捕获：主 agent 的
        正文/思考由主执行链路 ``stream_content_update`` / ``stream_reasoning``
        唯一发布（spec D2），本中间件捕获主 agent 内容会导致思考流被误路由
        到子代理频道而丢弃（实测 agent 模式 reasoning 持久化为空的根因）。
        """
        from langchain_core.messages import AIMessage

        on_subagent_content = _read_configurable().get("_on_subagent_content")
        if on_subagent_content is None:
            return None

        # 仅子代理（spec D1）捕获；主 agent 正文/思考由主链路发布
        if not _read_subagent_thread_id():
            return None

        nesting = _extract_nesting_from_state(state)
        depth = nesting.get("depth", 0)
        agent_path = nesting.get("agent_path") or []

        messages = state.get("messages", []) or []
        last_ai_msg = None
        for msg in reversed(messages):
            if isinstance(msg, AIMessage):
                last_ai_msg = msg
                break
        if last_ai_msg is None:
            return None

        content = getattr(last_ai_msg, "content", None) or ""
        # 深度思考未启用时丢弃模型仍输出的 reasoning（部分模型无法被
        # thinking=disabled 参数关闭思考，但用户未开启就不应展示中间推理）。
        # 开关值由 spawn_sub_agent 写入子代理 configurable（enable_deep_thinking）。
        deep_thinking_enabled = bool((_read_configurable() or {}).get("enable_deep_thinking", False))
        reasoning = self._extract_reasoning(last_ai_msg) if deep_thinking_enabled else ""
        if not content and not reasoning:
            return None

        agent_name = _read_agent_name()
        subagent_thread_id = _read_subagent_thread_id()
        # 消息幂等键：优先 AIMessage.id；缺失时用 content 确定性哈希
        # （重放消息 content 不变 → 键相同；不同轮次 content 必不同 → 不误伤）
        ai_id = getattr(last_ai_msg, "id", None)
        msg_key = ai_id or f"auto:{hash(content)}"
        logger.info(
            f"[SubAgentContent] 转发子代理正文: agent={agent_name or agent_path[-1]}, "
            f"subagent={subagent_thread_id}, msg_id={msg_key!r}, ai_id={ai_id!r}, "
            f"content_len={len(content)}, content_prefix={content[:24]!r}"
        )
        try:
            await on_subagent_content(
                agent_path=agent_path,
                content=content,
                reasoning_content=reasoning,
                agent_name=agent_name,
                depth=depth,
                subagent_thread_id=subagent_thread_id,
                msg_id=msg_key,
            )
            logger.debug(
                f"[SubAgentContent] 转发子代理正文: agent={agent_name or agent_path[-1]}, "
                f"content_len={len(content)}, reasoning_len={len(reasoning)}"
            )
        except Exception as exc:
            logger.warning(
                f"[SubAgentContent] 子代理正文转发失败: "
                f"agent={agent_name or agent_path[-1]}, err={exc}"
            )
        return None
