"""SubAgentMiddleware 补丁

修复 deepagents SubAgentMiddleware 的两个问题：

Issue 1: 子智能体缺少 checkpointer
    - 子智能体通过 create_agent() 创建时未传入 checkpointer
    - interrupt 冒泡到父 graph 后，恢复时子智能体从零开始
    - LLM 可能生成不同 tool_calls，导致重复审批
    - 修复：patch _get_subagents，从 contextvar 获取 checkpointer 并注入

Issue 2: 子智能体工具事件未转发到父 SSE 流
    - atask 使用 subagent.ainvoke()，subagent_config 只继承 configurable
    - 父 graph 看不到子智能体内部的 tool call 和 tool result
    - 修复：patch _build_task_tool，atask 继承父 graph 的 callbacks

使用方式：
    from .subagent_patch import patch_subagent_middleware, set_current_checkpointer
    patch_subagent_middleware()  # 幂等，应用 patch
    set_current_checkpointer(config.checkpointer)  # 设置当前协程的 checkpointer
    graph = create_deep_agent(**agent_kwargs)
    set_current_checkpointer(None)  # 清理

注意：
    - checkpointer 通过 contextvar 传递，协程隔离，线程安全
    - LangGraph 的 checkpointer 按 (thread_id, checkpoint_ns) 存储，
      子 graph 有独立 namespace，不会与父 graph 冲突
"""

import contextvars
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# 关键依赖导入校验，避免运行时 NameError
assert json is not None, "json 模块未正确导入，请检查 import 语句"

# 协程隔离的 checkpointer 上下文变量
_CURRENT_CHECKPOINTER: contextvars.ContextVar[Any] = contextvars.ContextVar("subagent_checkpointer", default=None)

_patched = False


def set_current_checkpointer(checkpointer: Any) -> contextvars.Token:
    """设置当前协程的 checkpointer，供子智能体创建时使用

    Args:
        checkpointer: LangGraph Checkpointer 实例，或 None 清除

    Returns:
        Token 用于恢复原值
    """
    return _CURRENT_CHECKPOINTER.set(checkpointer)


def reset_current_checkpointer(token: contextvars.Token) -> None:
    """恢复 checkpointer contextvar 到原值

    Args:
        token: set_current_checkpointer 返回的 Token
    """
    _CURRENT_CHECKPOINTER.reset(token)


def patch_subagent_middleware() -> None:
    """应用 SubAgentMiddleware 补丁（幂等）

    补丁内容：
    1. _get_subagents: 注入 checkpointer（Issue 1）
    2. _build_task_tool: atask 继承 callbacks（Issue 2）
    """
    global _patched
    if _patched:
        return

    from deepagents.middleware import subagents as _subagents_module

    # ---- Patch 1: _get_subagents 注入 checkpointer ----
    _original_get_subagents = _subagents_module.SubAgentMiddleware._get_subagents

    def _patched_get_subagents(self) -> list:
        """patched _get_subagents：注入 checkpointer 到子智能体

        从 contextvar 获取 checkpointer，传给 create_agent。
        若 contextvar 为 None（未设置），退回原行为。
        """
        checkpointer = _CURRENT_CHECKPOINTER.get()
        if checkpointer is None:
            # 未设置 checkpointer，退回原行为
            return _original_get_subagents(self)

        specs: list = []
        from deepagents._models import resolve_model
        from langchain.agents import create_agent
        from langchain.agents.middleware import HumanInTheLoopMiddleware

        for spec in self._subagents:
            if "runnable" in spec:
                # CompiledSubAgent - use as-is（已预编译，不重新创建）
                from typing import cast

                from deepagents.middleware.subagents import CompiledSubAgent

                compiled = cast(CompiledSubAgent, spec)
                runnable = compiled["runnable"].with_config(
                    {
                        "metadata": {"lc_agent_name": compiled["name"]},
                        "run_name": compiled["name"],
                    }
                )
                specs.append(
                    {
                        "name": compiled["name"],
                        "description": compiled["description"],
                        "runnable": runnable,
                    }
                )
                continue

            # SubAgent - validate required fields
            if "model" not in spec:
                msg = f"SubAgent '{spec['name']}' must specify 'model'"
                raise ValueError(msg)
            if "tools" not in spec:
                msg = f"SubAgent '{spec['name']}' must specify 'tools'"
                raise ValueError(msg)

            # Resolve model if string
            raw_model = spec["model"]
            model = resolve_model(raw_model)

            # Use middleware as provided (caller is responsible for building full stack)
            from langchain.agents.middleware.types import AgentMiddleware

            middleware: list[AgentMiddleware] = list(spec.get("middleware", []))

            interrupt_on = spec.get("interrupt_on")
            if interrupt_on:
                middleware.append(HumanInTheLoopMiddleware(interrupt_on=interrupt_on))

            specs.append(
                {
                    "name": spec["name"],
                    "description": spec["description"],
                    "runnable": create_agent(
                        model,
                        system_prompt=spec["system_prompt"],
                        tools=spec["tools"],
                        middleware=middleware,
                        name=spec["name"],
                        response_format=spec.get("response_format"),
                        checkpointer=checkpointer,  # 关键：注入 checkpointer
                    ),
                }
            )

        logger.info(f"[SubAgentPatch] _get_subagents 已注入 checkpointer: {len(specs)} 个子智能体")
        return specs

    _subagents_module.SubAgentMiddleware._get_subagents = _patched_get_subagents

    # ---- Patch 2: _build_task_tool atask 继承 callbacks ----
    _original_build_task_tool = _subagents_module._build_task_tool

    def _patched_build_task_tool(
        subagents: list,
        task_description: Any = None,
    ):
        """patched _build_task_tool：atask 继承父 graph 的 callbacks

        关键修复：subagent_config 添加 callbacks 字段，让子智能体的工具调用事件
        通过 callback 机制传递到父 graph 的 astream 流。

        其他逻辑与原版完全一致，仅 atask 的 subagent_config 不同。
        """
        from deepagents.middleware.subagents import (
            _EXCLUDED_STATE_KEYS,
            TaskToolSchema,
        )
        from langchain.tools import ToolRuntime
        from langchain_core.messages import HumanMessage, ToolMessage
        from langchain_core.runnables import Runnable, RunnableConfig
        from langchain_core.tools import StructuredTool
        from langgraph.types import Command

        # Build the graphs dict and descriptions from the unified spec list
        subagent_graphs: dict[str, Runnable] = {spec["name"]: spec["runnable"] for spec in subagents}
        subagent_description_str = "\n".join(f"- {s['name']}: {s['description']}" for s in subagents)

        # Use custom description if provided, otherwise use default template
        if task_description is None:
            from deepagents.middleware.subagents import TASK_TOOL_DESCRIPTION

            description = TASK_TOOL_DESCRIPTION.format(available_agents=subagent_description_str)
        elif "{available_agents}" in task_description:
            description = task_description.format(available_agents=subagent_description_str)
        else:
            description = task_description

        def _return_command_with_state_update(result: dict, tool_call_id: str) -> Command:
            if "messages" not in result:
                error_msg = (
                    "CompiledSubAgent must return a state containing a 'messages' key. "
                    "Custom StateGraphs used with CompiledSubAgent should include 'messages' "
                    "in their state schema to communicate results back to the main agent."
                )
                raise ValueError(error_msg)

            import dataclasses

            state_update = {k: v for k, v in result.items() if k not in _EXCLUDED_STATE_KEYS}

            structured = result.get("structured_response")
            if structured is not None:
                if hasattr(structured, "model_dump_json"):
                    content: str = structured.model_dump_json()
                elif dataclasses.is_dataclass(structured) and not isinstance(structured, type):
                    import json

                    content = json.dumps(dataclasses.asdict(structured))
                else:
                    import json

                    content = json.dumps(structured)
            else:
                content = result["messages"][-1].text.rstrip() if result["messages"][-1].text else ""

            return Command(
                update={
                    **state_update,
                    "messages": [ToolMessage(content, tool_call_id=tool_call_id)],
                }
            )

        def _validate_and_prepare_state(
            subagent_type: str,
            description: str,
            runtime: ToolRuntime,
        ) -> tuple[Runnable, dict]:
            subagent = subagent_graphs[subagent_type]
            subagent_state = {k: v for k, v in runtime.state.items() if k not in _EXCLUDED_STATE_KEYS}
            subagent_state["messages"] = [HumanMessage(content=description)]
            return subagent, subagent_state

        def task(
            description: str,
            subagent_type: str,
            runtime: ToolRuntime,
        ) -> str | Command:
            if subagent_type not in subagent_graphs:
                allowed_types = ", ".join([f"`{k}`" for k in subagent_graphs])
                return (
                    f"We cannot invoke subagent {subagent_type} because it does not exist, "
                    f"the only allowed types are {allowed_types}"
                )
            if not runtime.tool_call_id:
                value_error_msg = "Tool call ID is required for subagent invocation"
                raise ValueError(value_error_msg)
            subagent, subagent_state = _validate_and_prepare_state(subagent_type, description, runtime)
            subagent_config: RunnableConfig = {
                "configurable": {
                    **runtime.config.get("configurable", {}),
                    "ls_agent_type": "subagent",
                }
            }
            result = subagent.invoke(subagent_state, subagent_config)
            return _return_command_with_state_update(result, runtime.tool_call_id)

        async def atask(
            description: str,
            subagent_type: str,
            runtime: ToolRuntime,
        ) -> str | Command:
            if subagent_type not in subagent_graphs:
                allowed_types = ", ".join([f"`{k}`" for k in subagent_graphs])
                return (
                    f"We cannot invoke subagent {subagent_type} because it does not exist, "
                    f"the only allowed types are {allowed_types}"
                )
            if not runtime.tool_call_id:
                value_error_msg = "Tool call ID is required for subagent invocation"
                raise ValueError(value_error_msg)
            subagent, subagent_state = _validate_and_prepare_state(subagent_type, description, runtime)

            # 关键修复（Issue 2）：继承父 graph 的 callbacks + 转发工具事件
            # 原版只继承 configurable，导致子智能体内部的工具调用事件
            # （AIMessage.tool_calls、ToolMessage）无法被父 SSE 流消费。
            # 父 graph 的 astream(stream_mode=["messages"]) 只能看到 task 工具的
            # 输入 AIMessage 和输出 ToolMessage，看不到子智能体内部工具调用。
            # 解决：从 configurable 读取 _on_tool_event 回调，用 astream 转发事件。
            parent_callbacks = runtime.config.get("callbacks")
            parent_configurable = runtime.config.get("configurable", {}) or {}
            on_tool_event = parent_configurable.get("_on_tool_event")

            # 恢复模式检测（子 agent 审批恢复决策投递）：
            # 父 graph 恢复本 task 工具调用时，Command(resume=...) 的值以 NULL_TASK_ID
            # RESUME write 进入 ToolNode 任务 scratchpad（__pregel_scratchpad）。
            # 非空 → 恢复模式：不再注入全新 subagent_state（避免污染子 agent 消息状态、
            # LLM 重执行产出新 tool_call_id 导致决策失配），改为 Command(resume=...) 驱动
            # 子 agent 从自身 checkpoint 续流，把审批决策直接投递给子 agent 的 interrupt()。
            resume_value = None
            try:
                from langgraph._internal._constants import CONFIG_KEY_SCRATCHPAD

                _scratchpad = parent_configurable.get(CONFIG_KEY_SCRATCHPAD)
                if _scratchpad is not None and hasattr(_scratchpad, "get_null_resume"):
                    resume_value = _scratchpad.get_null_resume(consume=False)
            except Exception as exc:
                logger.debug(f"[SubAgentPatch] 恢复值检测失败(非致命): {exc}")
            if resume_value:
                logger.info(
                    f"[SubAgentPatch] atask 恢复模式: subagent={subagent_type}, "
                    f"resume_keys={list(resume_value.keys()) if isinstance(resume_value, dict) else type(resume_value).__name__}"
                )

            # 嵌套层级字段注入（Phase E1 + E3）：
            # 参考 Claude Code Task 工具设计，子 agent 需携带嵌套层级信息，
            # 供 ApprovalMiddleware 评估子 agent 风险加权 + 透传到 Approval.extra。
            #
            # 字段说明：
            # - parent_tool_call_id: 主 agent 调用 task 工具的 tool_call_id
            #   （用于审批卡片展示调用链路）
            # - depth: 嵌套层级（0=主 agent，1=一级子 agent，2=二级子 agent）
            #   （用于风险加权和深度限制）
            # - agent_name: 子 agent 名称（如 "web-researcher"）
            #   （供 ApprovalMiddleware._extract_subagent_context 读取）
            # - agent_path: 完整调用链路（如 ["main", "general-purpose", "web-researcher"]）
            #   （用于审计日志和前端展示完整调用路径）
            # - risk_ceiling: 子 agent 角色风险上限（RiskLevel 枚举值）
            #   （供 policies.assess_risk 的 subagent_context 参数使用）
            parent_depth = parent_configurable.get("depth", 0)
            if not isinstance(parent_depth, int) or parent_depth < 0:
                parent_depth = 0
            child_depth = parent_depth + 1

            # 深度上限保护：防止无限嵌套（默认 3 层）
            _MAX_DEPTH = 3
            if child_depth > _MAX_DEPTH:
                raise ValueError(
                    f"子 agent 嵌套深度超过上限 ({_MAX_DEPTH}): "
                    f"current_depth={child_depth}, agent_path="
                    f"{parent_configurable.get('agent_path', ['main'])}"
                )

            # 构造 agent_path：从父 configurable 继承并追加当前子 agent
            parent_agent_path = parent_configurable.get("agent_path")
            if isinstance(parent_agent_path, list) and parent_agent_path:
                child_agent_path = [*list(parent_agent_path), subagent_type]
            else:
                child_agent_path = ["main", subagent_type]

            # 查找子 agent 角色风险上限
            # 从 deep_builder._SUBAGENT_RISK_CEILINGS 读取，未注册的子 agent 默认 HIGH（不限制）
            from Django_xm.apps.agent_hub.builders.deep_builder import _SUBAGENT_RISK_CEILINGS
            from Django_xm.common.risk_levels import RiskLevel

            risk_ceiling = _SUBAGENT_RISK_CEILINGS.get(subagent_type, RiskLevel.HIGH)

            parent_tool_call_id = runtime.tool_call_id or ""

            subagent_config: RunnableConfig = {
                "configurable": {
                    **parent_configurable,
                    "ls_agent_type": "subagent",
                    # 嵌套层级字段（Phase E1 + E3）
                    "parent_tool_call_id": parent_tool_call_id,
                    "depth": child_depth,
                    "agent_name": subagent_type,
                    "agent_path": child_agent_path,
                    "risk_ceiling": risk_ceiling,
                }
            }
            # 移除 _on_tool_event，避免向更深层子智能体递归传递
            subagent_config["configurable"].pop("_on_tool_event", None)
            if parent_callbacks:
                subagent_config["callbacks"] = parent_callbacks
                logger.debug(
                    f"[SubAgentPatch] atask 继承父 callbacks: "
                    f"subagent={subagent_type}, depth={child_depth}, "
                    f"callbacks_count={len(parent_callbacks) if isinstance(parent_callbacks, list) else 1}"
                )

            if resume_value:
                # 恢复模式：不注入全新 subagent_state，从子 agent checkpoint 续流
                subagent_input = Command(resume=resume_value)
                subagent_state_for_log = "(resume)"
            else:
                # 初始模式：保持原行为（注入 task 描述）
                subagent_input = subagent_state
                subagent_state_for_log = f"(initial, {len(subagent_state.get('messages', []))} msgs)"

            if on_tool_event is not None:
                # 有回调时用 astream 转发工具事件到父 SSE 流
                result = await _astream_with_tool_events(
                    subagent,
                    subagent_input,
                    subagent_config,
                    on_tool_event,
                    subagent_type=subagent_type,
                    resume_value=resume_value or None,
                )
                logger.debug(
                    f"[SubAgentPatch] atask astream 转发完成: subagent={subagent_type}, "
                    f"mode={subagent_state_for_log}"
                )
            else:
                result = await subagent.ainvoke(subagent_input, subagent_config)
            return _return_command_with_state_update(result, runtime.tool_call_id)

        async def _astream_with_tool_events(
            subagent: Runnable,
            subagent_state: dict,
            subagent_config: RunnableConfig,
            on_tool_event,
            *,
            subagent_type: str,
            resume_value=None,
        ) -> dict:
            """通过 astream 执行子智能体，转发工具事件到 on_tool_event 回调

            与父 graph 的 astream_research_with_interrupts 事件格式保持一致：
            - AIMessage/AIMessageChunk(含 tool_calls) → TOOL_CALL_PENDING
            - ToolMessage → TOOL_CALL_COMPLETED / TOOL_CALL_FAILED

            事件提取逻辑统一复用 tool_event_extractor.extract_tool_events_from_message,
            支持 AIMessageChunk 参数聚合与 ToolMessage 阶段补发，确保前端工具参数
            不再显示为 {}.

            子 agent 嵌套层级字段透传（Phase E3）：
            atask 在构造 subagent_config 时已注入 parent_tool_call_id / depth /
            agent_name / agent_path / risk_ceiling 到 configurable。本函数从
            subagent_config["configurable"] 读取这些字段，随每个 tool 事件一起
            传递给 on_tool_event 回调，最终到达前端 ToolCallCard。

            回调签名（固定为 async）:
                async def on_tool_event(
                    event_type: EventType,
                    tool_call_id: str,
                    tool_name: str,
                    **kwargs,  # parameters / result / error /
                               # parent_tool_call_id / depth / agent_name / agent_path / risk_ceiling
                ) -> None

            实现由 adapter.py 的 ``_on_tool_event`` 提供，通过
            ``config["configurable"]["_on_tool_event"]`` 注入。

            Returns:
                子智能体最终状态字典（与 ainvoke 返回值格式一致）
            """
            from langchain_core.messages import SystemMessage

            from Django_xm.apps.agent_hub.services.agent_resilience import DuplicateToolCallDetector
            from Django_xm.apps.tools.tool_event_extractor import extract_tool_events_from_message
            from Django_xm.common.event_schema import EventType

            # 提取子 agent 嵌套层级字段（Phase E3）：
            # atask 已将这些字段注入到 subagent_config["configurable"]，
            # 此处一次性提取，随每个 tool 事件传递给 on_tool_event 回调。
            # 主 agent 直接调用的工具不经过本函数（无 subagent_config），
            # 故这些字段仅子 agent 工具事件携带。
            sub_configurable = (
                subagent_config.get("configurable", {}) or {} if isinstance(subagent_config, dict) else {}
            )
            sub_parent_tool_call_id = sub_configurable.get("parent_tool_call_id") or ""
            sub_depth = sub_configurable.get("depth", 0)
            if not isinstance(sub_depth, int) or sub_depth < 0:
                sub_depth = 0
            sub_agent_name = sub_configurable.get("agent_name") or ""
            sub_agent_path = sub_configurable.get("agent_path")
            if not isinstance(sub_agent_path, list):
                sub_agent_path = None
            sub_risk_ceiling = sub_configurable.get("risk_ceiling")
            # risk_ceiling 可能是 RiskLevel 枚举，回调端会统一处理

            final_state: dict = {}
            accumulated_messages: list = []
            # 已发射 tool 事件的 tool_call_id 集合，避免流式 chunk 重复发射
            # 根因：与 official_deep_agent.py 一致，deepagents astream(messages)
            # 只产出 AIMessageChunk，不产出完整 AIMessage，需去重。
            seen_tool_call_ids: set = set()
            # 恢复模式预热 seen_tool_call_ids：从子 agent checkpoint 提取已注册的
            # tool_call_id，避免 ToolMessage 阶段补发 PENDING 导致状态机非法转换告警
            # （与 adapter.py B7 预热逻辑一致）。
            if resume_value:
                try:
                    _cp_state = await subagent.aget_state(subagent_config)
                    if _cp_state is not None and hasattr(_cp_state, "values") and _cp_state.values:
                        from langchain_core.messages import AIMessage as _AIMessage

                        for _msg in _cp_state.values.get("messages", []) or []:
                            if isinstance(_msg, _AIMessage) and getattr(_msg, "tool_calls", None):
                                for _tc in _msg.tool_calls:
                                    _tc_id = _tc.get("id") if isinstance(_tc, dict) else getattr(_tc, "id", None)
                                    if _tc_id:
                                        seen_tool_call_ids.add(_tc_id)
                        logger.info(
                            f"[SubAgentPatch] 恢复模式预热 seen_tool_call_ids: "
                            f"subagent={subagent_type}, count={len(seen_tool_call_ids)}"
                        )
                except Exception as _prewarm_err:
                    logger.warning(
                        f"[SubAgentPatch] 恢复模式预热 seen_tool_call_ids 失败(非致命): {_prewarm_err}"
                    )
            # 重复工具调用检测器：子智能体内部的工具调用不冒泡到父 graph，
            # 需在子智能体层面独立检测，防止子智能体陷入重试循环。
            duplicate_detector = DuplicateToolCallDetector()
            pending_duplicate_warnings: list = []

            # while True 用于检测到重复调用时中断流、注入警告后重入
            current_input = subagent_state
            while True:
                async for chunk in subagent.astream(
                    current_input,
                    config=subagent_config,
                    stream_mode=["messages", "values"],
                ):
                    if not isinstance(chunk, tuple) or len(chunk) != 2:
                        continue
                    mode_name, mode_data = chunk

                    if mode_name == "values":
                        if isinstance(mode_data, dict):
                            final_state = mode_data
                        continue

                    if mode_name != "messages":
                        continue

                    # messages 模式：(message, metadata) 元组
                    msg_obj = mode_data[0] if isinstance(mode_data, tuple) and len(mode_data) == 2 else mode_data

                    # 工具事件检测：复用公共模块 extract_tool_events_from_message
                    # 关键修复：deepagents astream(messages) 只产出 AIMessageChunk，
                    # 原条件 `not isinstance(msg_obj, AIMessageChunk)` 排除了所有 chunk，
                    # 导致子智能体 tool (input) 事件从未转发，前端参数显示为 {}。
                    # 公共模块接受 AIMessage/AIMessageChunk，用 seen_tool_call_ids 去重，
                    # 并在 ToolMessage 阶段从累积 chunk 聚合完整参数后补发。
                    tool_events = extract_tool_events_from_message(
                        msg_obj,
                        seen_tool_call_ids,
                        accumulated_messages,
                    )
                    for evt in tool_events:
                        logger.info(
                            f"[SubAgentPatch] 工具事件提取: tool={evt.get('tool_name')}, "
                            f"tc_id={evt.get('tool_call_id')}, event_type={evt.get('event_type')}, "
                            f"subagent={subagent_type}"
                        )
                        # 重复工具调用检测：仅对 PENDING 记录
                        if evt.get("event_type") == EventType.TOOL_CALL_PENDING:
                            warning = duplicate_detector.record(
                                evt.get("tool_name") or "unknown",
                                evt.get("parameters") or {},
                            )
                            if warning is not None:
                                pending_duplicate_warnings.append(SystemMessage(content=warning.to_prompt()))
                        evt_kwargs = {"parameters": evt.get("parameters", {})}
                        if "result" in evt:
                            evt_kwargs["result"] = evt["result"]
                        if "error" in evt:
                            evt_kwargs["error"] = evt["error"]
                        # 子 agent 嵌套层级字段（Phase E3）：随事件一起传递
                        # depth>0 才传递（主 agent depth=0 不传，避免污染 payload）
                        if sub_depth > 0:
                            evt_kwargs["depth"] = sub_depth
                        if sub_parent_tool_call_id:
                            evt_kwargs["parent_tool_call_id"] = sub_parent_tool_call_id
                        if sub_agent_name:
                            evt_kwargs["agent_name"] = sub_agent_name
                        if sub_agent_path:
                            evt_kwargs["agent_path"] = sub_agent_path
                        if sub_risk_ceiling is not None:
                            evt_kwargs["risk_ceiling"] = sub_risk_ceiling
                        try:
                            # on_tool_event 签名固定为 async（adapter.py 的 _on_tool_event）
                            # 统一 await 调用，移除 iscoroutine 双模式判断
                            await on_tool_event(
                                evt["event_type"],
                                evt["tool_call_id"],
                                evt["tool_name"] or "unknown",
                                **evt_kwargs,
                            )
                        except Exception as e:
                            logger.warning(
                                f"[SubAgentPatch] 子智能体 tool 事件转发失败 "
                                f"(subagent={subagent_type}, event={evt['event_type']}): {e}"
                            )
                    # 检测到重复调用：中断流以注入警告
                    if pending_duplicate_warnings:
                        break

                # 注入重复调用警告到子智能体状态，重入 astream 续流
                if pending_duplicate_warnings:
                    logger.info(
                        f"[SubAgentPatch] 注入 {len(pending_duplicate_warnings)} 条"
                        f"重复调用警告到子智能体 {subagent_type} 状态"
                    )
                    try:
                        await subagent.aupdate_state(  # type: ignore[attr-defined]  # langgraph CompiledGraph extension on Runnable
                            subagent_config,
                            {"messages": pending_duplicate_warnings},
                        )
                    except Exception as e:
                        logger.warning(f"[SubAgentPatch] 注入重复调用警告失败 (subagent={subagent_type}): {e}")
                    pending_duplicate_warnings.clear()
                    current_input = None  # 从当前 checkpoint 续流
                    continue
                break  # 流正常结束

            # values 模式未产出时，从累积消息构建
            if not final_state:
                final_state = {"messages": accumulated_messages}
            return final_state

        return StructuredTool.from_function(
            name="task",
            func=task,
            coroutine=atask,
            description=description,
            infer_schema=False,
            args_schema=TaskToolSchema,
        )

    _subagents_module._build_task_tool = _patched_build_task_tool

    _patched = True
    logger.info("[SubAgentPatch] SubAgentMiddleware 已打补丁（checkpointer 注入 + callbacks 继承）")
