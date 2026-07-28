"""
Monkey-patch ToolNode._afunc to handle multiple parallel GraphInterrupts.

When multiple tool calls run in parallel via asyncio.gather and more than one
raises GraphInterrupt, the default gather(return_exceptions=False) only
propagates the first exception, discarding the rest.  This patch uses
return_exceptions=True so that *all* interrupts are collected and merged into
a single GraphInterrupt that carries every Interrupt value.

HARD CONSTRAINT (project_memory):
    langgraph ToolNode._afunc must be monkey-patched to use asyncio.gather
    with return_exceptions=True and merge all GraphInterrupts to handle
    multiple parallel tool approvals.
"""

import asyncio
import logging

logger = logging.getLogger(__name__)

_original_afunc = None


def patch_tool_node():
    """Apply the monkey-patch to ToolNode._afunc (idempotent)."""
    from langgraph.prebuilt.chat_agent_executor import ToolNode

    global _original_afunc
    if _original_afunc is not None:
        return  # Already patched

    _original_afunc = ToolNode._afunc

    async def _patched_afunc(self, input, config, runtime):
        # ---- Replicate the original _afunc setup ----
        tool_calls, input_type = self._parse_input(input)
        from langgraph.prebuilt.tool_node import get_config_list
        config_list = get_config_list(config, len(tool_calls))

        # 获取 state.messages，用于检测已有 ToolMessage（跳过被拒绝的工具）
        if isinstance(input, dict):
            state_messages = input.get("messages", []) or []
        elif isinstance(input, list):
            state_messages = input
        else:
            state_messages = []
        from langchain_core.messages import ToolMessage as _ToolMessage
        existing_tool_call_ids = {
            getattr(m, "tool_call_id", None)
            for m in state_messages
            if isinstance(m, _ToolMessage) and getattr(m, "tool_call_id", None)
        }

        tool_runtimes = []
        skipped_call_ids = []
        for call, cfg in zip(tool_calls, config_list, strict=False):
            # 跳过已有 ToolMessage 的 tool_call（如被用户拒绝的工具）
            if call.get("id") in existing_tool_call_ids:
                skipped_call_ids.append(call.get("id"))
                continue
            state = self._extract_state(input, cfg)
            from langgraph.prebuilt.tool_node import ToolRuntime
            tool_runtime = ToolRuntime(
                state=state,
                tool_call_id=call["id"],
                config=cfg,
                context=runtime.context,
                store=runtime.store,
                stream_writer=runtime.stream_writer,
                tools=list(self.tools_by_name.values()),
                execution_info=runtime.execution_info,
                server_info=runtime.server_info,
            )
            tool_runtimes.append(tool_runtime)

        if skipped_call_ids:
            logger.info(
                f"[ToolNodePatch] 跳过 {len(skipped_call_ids)} 个已有 ToolMessage 的 tool_call: "
                f"{skipped_call_ids}"
            )

        coros = []
        for call, tool_runtime in zip(tool_calls, tool_runtimes, strict=False):
            # 跳过已被跳过的 call（zip 会用原 tool_calls，但 tool_runtimes 已过滤）
            if call.get("id") in skipped_call_ids:
                continue
            coros.append(self._arun_one(call, input_type, tool_runtime))

        # ---- Key change: gather with return_exceptions=True ----
        results = await asyncio.gather(*coros, return_exceptions=True)

        # ---- Post-process: collect interrupts, re-raise other exceptions ----
        from langgraph.errors import GraphBubbleUp, GraphInterrupt

        all_interrupts = []
        successful_outputs = []
        other_exception = None

        # Debug: log result types for diagnosing multi-interrupt issues
        interrupt_count = sum(1 for r in results if isinstance(r, GraphInterrupt))
        if len(results) > 1 or interrupt_count > 0:
            logger.info(
                f"[ToolNodePatch] _patched_afunc: {len(results)} results, "
                f"{interrupt_count} GraphInterrupt(s), "
                f"types={[type(r).__name__ for r in results]}"
            )

        for result in results:
            if isinstance(result, GraphInterrupt):
                # GraphInterrupt.args[0] is the Sequence[Interrupt] passed to __init__
                all_interrupts.extend(result.args[0] if result.args else ())
            elif isinstance(result, GraphBubbleUp):
                # GraphBubbleUp (non-interrupt) must always propagate
                if other_exception is None:
                    other_exception = result
            elif isinstance(result, BaseException):
                # Any other exception — store the first one to re-raise
                if other_exception is None:
                    other_exception = result
            else:
                successful_outputs.append(result)

        # If there are GraphInterrupts, merge them into one and raise
        if all_interrupts:
            raise GraphInterrupt(all_interrupts)

        # If there's a non-interrupt exception, re-raise it
        if other_exception is not None:
            raise other_exception

        # All succeeded — combine and return
        return self._combine_tool_outputs(successful_outputs, input_type)

    ToolNode._afunc = _patched_afunc
    logger.info("[ToolNodePatch] ToolNode._afunc 已 monkey-patch（支持多并行 GraphInterrupt）")
