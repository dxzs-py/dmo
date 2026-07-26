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

import asyncio
import contextvars
import json
import logging
from typing import Any, Sequence

logger = logging.getLogger(__name__)

# 关键依赖导入校验，避免运行时 NameError
assert json is not None, "json 模块未正确导入，请检查 import 语句"

# 协程隔离的 checkpointer 上下文变量
_CURRENT_CHECKPOINTER: contextvars.ContextVar[Any] = contextvars.ContextVar(
    'subagent_checkpointer', default=None
)

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
        from langchain.agents import create_agent
        from langchain.agents.middleware import HumanInTheLoopMiddleware
        from deepagents._models import resolve_model

        for spec in self._subagents:
            if "runnable" in spec:
                # CompiledSubAgent - use as-is（已预编译，不重新创建）
                from typing import cast
                from deepagents.middleware.subagents import CompiledSubAgent
                compiled = cast(CompiledSubAgent, spec)
                runnable = compiled["runnable"].with_config({
                    "metadata": {"lc_agent_name": compiled["name"]},
                    "run_name": compiled["name"],
                })
                specs.append({
                    "name": compiled["name"],
                    "description": compiled["description"],
                    "runnable": runnable,
                })
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

            specs.append({
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
            })

        logger.info(
            f"[SubAgentPatch] _get_subagents 已注入 checkpointer: "
            f"{len(specs)} 个子智能体"
        )
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
        from langchain.tools import ToolRuntime
        from langchain_core.messages import HumanMessage, ToolMessage
        from langchain_core.runnables import Runnable, RunnableConfig
        from langchain_core.tools import StructuredTool
        from langgraph.types import Command
        from deepagents.middleware.subagents import (
            TaskToolSchema,
            _EXCLUDED_STATE_KEYS,
        )

        # Build the graphs dict and descriptions from the unified spec list
        subagent_graphs: dict[str, Runnable] = {
            spec["name"]: spec["runnable"] for spec in subagents
        }
        subagent_description_str = "\n".join(
            f"- {s['name']}: {s['description']}" for s in subagents
        )

        # Use custom description if provided, otherwise use default template
        if task_description is None:
            from deepagents.middleware.subagents import TASK_TOOL_DESCRIPTION
            description = TASK_TOOL_DESCRIPTION.format(
                available_agents=subagent_description_str
            )
        elif "{available_agents}" in task_description:
            description = task_description.format(
                available_agents=subagent_description_str
            )
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
            subagent_state = {
                k: v for k, v in runtime.state.items()
                if k not in _EXCLUDED_STATE_KEYS
            }
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
            subagent, subagent_state = _validate_and_prepare_state(
                subagent_type, description, runtime
            )
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
            subagent, subagent_state = _validate_and_prepare_state(
                subagent_type, description, runtime
            )

            # 关键修复（Issue 2）：继承父 graph 的 callbacks + 转发工具事件
            # 原版只继承 configurable，导致子智能体内部的工具调用事件
            # （AIMessage.tool_calls、ToolMessage）无法被父 SSE 流消费。
            # 父 graph 的 astream(stream_mode=["messages"]) 只能看到 task 工具的
            # 输入 AIMessage 和输出 ToolMessage，看不到子智能体内部工具调用。
            # 解决：从 configurable 读取 _on_tool_event 回调，用 astream 转发事件。
            parent_callbacks = runtime.config.get("callbacks")
            parent_configurable = runtime.config.get("configurable", {}) or {}
            on_tool_event = parent_configurable.get("_on_tool_event")

            subagent_config: RunnableConfig = {
                "configurable": {
                    **parent_configurable,
                    "ls_agent_type": "subagent",
                }
            }
            # 移除 _on_tool_event，避免向更深层子智能体递归传递
            subagent_config["configurable"].pop("_on_tool_event", None)
            if parent_callbacks:
                subagent_config["callbacks"] = parent_callbacks
                logger.debug(
                    f"[SubAgentPatch] atask 继承父 callbacks: "
                    f"subagent={subagent_type}, callbacks_count={len(parent_callbacks) if isinstance(parent_callbacks, list) else 1}"
                )

            if on_tool_event is not None:
                # 有回调时用 astream 转发工具事件到父 SSE 流
                result = await _astream_with_tool_events(
                    subagent,
                    subagent_state,
                    subagent_config,
                    on_tool_event,
                    subagent_type=subagent_type,
                )
                logger.debug(
                    f"[SubAgentPatch] atask astream 转发完成: "
                    f"subagent={subagent_type}"
                )
            else:
                result = await subagent.ainvoke(subagent_state, subagent_config)
            return _return_command_with_state_update(result, runtime.tool_call_id)

        async def _astream_with_tool_events(
            subagent: Runnable,
            subagent_state: dict,
            subagent_config: RunnableConfig,
            on_tool_event,
            *,
            subagent_type: str,
        ) -> dict:
            """通过 astream 执行子智能体，转发工具事件到 on_tool_event 回调

            与父 graph 的 astream_research_with_interrupts 事件格式保持一致：
            - AIMessage/AIMessageChunk(含 tool_calls) → TOOL_CALL_INPUT_READY
            - ToolMessage → TOOL_CALL_COMPLETED / TOOL_CALL_FAILED

            事件提取逻辑统一复用 tool_event_extractor.extract_tool_events_from_message，
            支持 AIMessageChunk 参数聚合与 ToolMessage 阶段补发，确保前端工具参数
            不再显示为 {}。

            Returns:
                子智能体最终状态字典（与 ainvoke 返回值格式一致）
            """
            from Django_xm.apps.tools.tool_event_extractor import extract_tool_events_from_message

            final_state: dict = {}
            accumulated_messages: list = []
            # 已发射 tool 事件的 tool_call_id 集合，避免流式 chunk 重复发射
            # 根因：与 official_deep_agent.py 一致，deepagents astream(messages)
            # 只产出 AIMessageChunk，不产出完整 AIMessage，需去重。
            seen_tool_call_ids: set = set()

            async for chunk in subagent.astream(
                subagent_state,
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
                msg_obj = (
                    mode_data[0]
                    if isinstance(mode_data, tuple) and len(mode_data) == 2
                    else mode_data
                )

                # 工具事件检测：复用公共模块 extract_tool_events_from_message
                # 关键修复：deepagents astream(messages) 只产出 AIMessageChunk，
                # 原条件 `not isinstance(msg_obj, AIMessageChunk)` 排除了所有 chunk，
                # 导致子智能体 tool (input) 事件从未转发，前端参数显示为 {}。
                # 公共模块接受 AIMessage/AIMessageChunk，用 seen_tool_call_ids 去重，
                # 并在 ToolMessage 阶段从累积 chunk 聚合完整参数后补发。
                tool_events = extract_tool_events_from_message(
                    msg_obj, seen_tool_call_ids, accumulated_messages,
                )
                for evt in tool_events:
                    evt_kwargs = {'parameters': evt.get('parameters', {})}
                    if 'result' in evt:
                        evt_kwargs['result'] = evt['result']
                    if 'error' in evt:
                        evt_kwargs['error'] = evt['error']
                    try:
                        evt_result = on_tool_event(
                            evt['event_type'],
                            evt['tool_call_id'],
                            evt['tool_name'] or "unknown",
                            **evt_kwargs,
                        )
                        if asyncio.iscoroutine(evt_result):
                            await evt_result
                    except Exception as e:
                        logger.warning(
                            f"[SubAgentPatch] 子智能体 tool 事件转发失败 "
                            f"(subagent={subagent_type}, event={evt['event_type']}): {e}"
                        )

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
