"""官方 Deep Agent 适配器模块。

职责：
- ``OfficialDeepAgentAdapter``：将 ``create_deep_agent`` 返回的 CompiledStateGraph
  适配为与 ``DeepResearchAgent`` 兼容的接口（research/aresearch/astream_research），
  使上层调用方无需关心底层实现差异。
  - 集成 interrupt 审批机制（astream_research_with_interrupts）
  - 集成韧性模块（retry/timeout/degrade/fallback）
  - 工具调用生命周期事件发布到实时频道
  - 降级时使用 original_tools / original_config 重建 graph

抽取自原 ``official_deep_agent.py``（Task 18.1）。

依赖关系：
- 依赖 ``patches.py``：``_DeepAgentExecutor`` / ``_extract_ai_response``。
- 被 ``builders.py`` 导入：``OfficialDeepAgentAdapter``（作为工厂函数返回类型）。
"""

import asyncio
import os
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

# subagent_patch：set_current_checkpointer 注入父 graph 的 checkpointer 到子 agent，
# 使子 agent 的 interrupt() 不再被 Pregel 抑制（is_nested=False 时 _suppress_interrupt）。
# _on_tool_event 回调由本模块在 config["configurable"] 中注入，转发子 agent 工具事件到父 SSE 流。
from Django_xm.apps.agent_hub.builders.subagent_patch import (
    reset_current_checkpointer,
    set_current_checkpointer,
)
from Django_xm.apps.core.config import get_logger
from Django_xm.apps.ai_engine.services.thinking import extract_thinking_content
from Django_xm.apps.research.services.patches import (
    _DeepAgentExecutor,
    _extract_ai_response,
)

logger = get_logger(__name__)


class OfficialDeepAgentAdapter:
    """
    官方 Deep Agent 适配器

    将 create_deep_agent 返回的 CompiledStateGraph 适配为
    与 DeepResearchAgent 兼容的接口（research/aresearch/astream_research），
    使上层调用方无需关心底层实现差异。
    """

    def __init__(
        self,
        graph,
        thread_id: str,
        work_dir: str | None = None,
        model=None,
        original_tools: list[Any] | None = None,
        original_config: dict[str, Any] | None = None,
        **kwargs,
    ):
        self.graph = graph
        self.thread_id = thread_id
        self.work_dir = work_dir
        self.model = model
        # 原始完整工具列表与 graph 构建配置，用于降级时用降级工具集重建 graph
        # - original_tools: create_deep_agent 首次构建时使用的 tools 列表
        # - original_config: create_deep_agent 首次构建时的 kwargs（model/system_prompt/middleware/subagents 等）
        # 二者均为可选（向后兼容旧调用方），缺失时 _rebuild_with_degraded_tools 将返回 None 触发回退
        self.original_tools = original_tools
        self.original_config = original_config
        # chat_session_id 用于工具事件发布时的跨模块同步：
        # - 非空（chat 关联场景）：发布到 session:{chat_session_id} + task:{thread_id} 双频道
        # - None（独立深度研究场景）：仅发布到 task:{thread_id} 频道
        # 实例化时未传入则保持 None，astream_research_with_interrupts 内部按需从 ResearchTask 反查
        self.chat_session_id: str | None = kwargs.get("chat_session_id")
        self._kwargs = kwargs

    def research(
        self, query: str, config: dict[str, Any] | None = None, callbacks: list | None = None
    ) -> dict[str, Any]:
        logger.info(f"[OfficialDeepAgent] 同步研究: {query[:50]}...")

        if config is None:
            config = {}
        if "configurable" not in config:
            config["configurable"] = {}
        config["configurable"]["thread_id"] = self.thread_id
        config.setdefault("recursion_limit", 1000)
        if callbacks:
            config["callbacks"] = callbacks

        try:
            result = self.graph.invoke(
                {"messages": [HumanMessage(content=query)]},
                config=config,
            )

            final_report = _extract_ai_response(result)
            files_info = self._extract_files_info(result)
            disk_files = self._scan_disk_files()

            # 当 AI 回复过短时（通常是确认语），从磁盘文件提取完整报告
            if len(final_report.strip()) < 200 and disk_files:
                for report_key in ("reports/final_report.md", "final_report.md"):
                    if report_key in disk_files:
                        report_content = disk_files[report_key].get("content", "")
                        if len(report_content.strip()) > len(final_report.strip()):
                            final_report = report_content
                            logger.info(f"[OfficialDeepAgent] 使用磁盘报告文件替代短回复 ({len(final_report)} 字符)")
                        break

            return {
                "success": True,
                "query": query,
                "final_report": final_report,
                "plan": None,
                "current_step": "completed",
                "error": None,
                "files": files_info if files_info else disk_files,
                "state_files": disk_files,
            }

        except Exception as e:
            logger.exception("[OfficialDeepAgent] 同步研究失败")
            return {
                "success": False,
                "query": query,
                "final_report": None,
                "error": str(e),
            }

    async def aresearch(
        self, query: str, config: dict[str, Any] | None = None, callbacks: list | None = None
    ) -> dict[str, Any]:
        logger.info(f"[OfficialDeepAgent] 异步研究: {query[:50]}...")

        if config is None:
            config = {}
        if "configurable" not in config:
            config["configurable"] = {}
        config["configurable"]["thread_id"] = self.thread_id
        config.setdefault("recursion_limit", 1000)
        if callbacks:
            config["callbacks"] = callbacks

        try:
            result = await self.graph.ainvoke(
                {"messages": [HumanMessage(content=query)]},
                config=config,
            )

            final_report = _extract_ai_response(result)
            files_info = self._extract_files_info(result)
            disk_files = self._scan_disk_files()

            # 当 AI 回复过短时（通常是确认语），从磁盘文件提取完整报告
            if len(final_report.strip()) < 200 and disk_files:
                for report_key in ("reports/final_report.md", "final_report.md"):
                    if report_key in disk_files:
                        report_content = disk_files[report_key].get("content", "")
                        if len(report_content.strip()) > len(final_report.strip()):
                            final_report = report_content
                            logger.info(f"[OfficialDeepAgent] 使用磁盘报告文件替代短回复 ({len(final_report)} 字符)")
                        break

            return {
                "success": True,
                "query": query,
                "final_report": final_report,
                "plan": None,
                "current_step": "completed",
                "error": None,
                "files": files_info if files_info else disk_files,
                "state_files": disk_files,
            }

        except Exception as e:
            logger.exception("[OfficialDeepAgent] 异步研究失败")
            return {
                "success": False,
                "query": query,
                "final_report": None,
                "error": str(e),
            }

    async def astream_research_with_interrupts(
        self,
        query: str | None,
        config: dict[str, Any] | None = None,
        callbacks: list | None = None,
        on_interrupt=None,
        *,
        resume_command=None,
    ) -> dict[str, Any]:
        """流式研究 + interrupt 审批机制

        使用 graph.astream() + stream_mode=["messages", "updates"] 执行，
        捕获 __interrupt__ 事件，通过 on_interrupt 回调通知外部。

        Path D 双模式：
            - 初始执行（resume_command=None）：graph_input = {"messages": [HumanMessage(content=query)]}
              on_interrupt 返回非空 dict → Command(resume=...) 恢复（同步审批流）
              on_interrupt 返回空 dict → 退出信号（Path D：DB 持久化 + Celery 恢复）
            - 恢复执行（resume_command=Command(resume=...)）：
              graph_input = resume_command，从 checkpoint 恢复 agent 执行

        集成韧性模块：重试（指数退避）、降级（减少工具）、回退（无工具直接 LLM 回答）。

        Args:
            query: 研究查询（resume_command 模式时可为 None）
            config: LangGraph 运行配置
            callbacks: LangChain 回调列表
            on_interrupt: 审批中断回调，签名 on_interrupt(interrupt_data: dict) -> resume_value
                          interrupt_data 包含 tool_name, interrupt_id, title, description 等
                          返回值约定：
                          - 非空 dict {interrupt_id: bool} → 同步恢复（Command(resume=...)）
                          - 空 dict {} → Path D 退出信号（已创建 Approval DB 记录，worker 退出）
            resume_command: 恢复模式时传入 Command(resume=...)，None 表示初始执行

        Returns:
            与 research() 方法相同格式的结果字典；
            Path D 退出时返回 {"success": False, "error": "interrupted", ...}
        """
        from types import SimpleNamespace

        from langgraph.types import Command, Interrupt

        from Django_xm.apps.agent_hub.services.agent_resilience import (
            DuplicateToolCallDetector,
            ExecutionTimeoutManager,
            get_resilience_config,
        )
        from Django_xm.apps.tools.base import is_approval_interrupt

        # 工具事件提取与发布（与 subagent_patch.py 保持一致）
        # 注意：工具事件的实际发布由 _publish_tool_event 内部通过
        # service.transition_async 完成（状态机统一入口），此处仅需 EventType
        from Django_xm.apps.tools.tool_event_extractor import extract_tool_events_from_message
        from Django_xm.common.approval_utils import derive_cross_module_id_from_source
        from Django_xm.common.event_schema import EventType

        # resume_command 模式下 query 可能为 None，使用占位符避免 [:50] 切片失败
        query_display = (query or "(resume)")[:50]
        logger.info(f"[OfficialDeepAgent] 流式研究(interrupt): {query_display}...")

        # 解析 chat_session_id（用于工具事件的跨模块同步路由）
        # - 实例化时已传入：直接使用
        # - 未传入：尝试从 ResearchTask.session_id 反查（chat 模块触发深度研究时写入）
        # - 反查失败：视为独立深度研究模式，仅向 task 频道发布
        if self.chat_session_id is None:
            try:
                from asgiref.sync import sync_to_async

                from Django_xm.apps.research.models import ResearchTask

                @sync_to_async(thread_sensitive=True)
                def _load_chat_session_id() -> str | None:
                    task = (
                        ResearchTask.objects.filter(
                            task_id=self.thread_id,
                            is_deleted=False,
                        )
                        .only("session_id")
                        .first()
                    )
                    return task.session_id if task else None

                self.chat_session_id = await _load_chat_session_id()
                if self.chat_session_id:
                    logger.info(f"[OfficialDeepAgent] 从 ResearchTask 反查 chat_session_id={self.chat_session_id}")
            except Exception as e:
                logger.warning(
                    f"[OfficialDeepAgent] 反查 ResearchTask.session_id 失败，工具事件仅发布到 task 频道: {e}"
                )

        if config is None:
            config = {}
        if "configurable" not in config:
            config["configurable"] = {}
        config["configurable"]["thread_id"] = self.thread_id
        config.setdefault("recursion_limit", 1000)
        if callbacks:
            config["callbacks"] = callbacks

        # 注入 _on_tool_event 回调到 config["configurable"]：
        # subagent_patch.py 的 _patched_build_task_tool.atask 会从 configurable 读取此回调，
        # 通过 astream 转发子 agent 工具调用事件到父 SSE 流。
        #
        # 回调签名（与 subagent_patch.py _astream_with_tool_events 一致）：
        #   on_tool_event(event_type, tool_call_id, tool_name, **kwargs) -> coroutine
        # kwargs 可能包含 parameters / result / error
        #
        # 回调内部构造 evt dict 并调用 _publish_tool_event 发布到统一 tool_call_lifecycle.service，
        # 与父 graph 的工具事件发布路径完全一致（service.transition_async）。
        async def _on_tool_event(
            event_type,
            tool_call_id,
            tool_name,
            **kwargs,
        ):
            """子 agent 工具事件转发回调。

            将子 agent 内部的工具调用事件转发到父 SSE 流，
            通过统一 ``service.transition_async`` 发布到实时频道。

            子 agent 嵌套层级字段（Phase E3）：
            subagent_patch._astream_with_tool_events 通过 kwargs 传递
            parent_tool_call_id / depth / agent_name / agent_path / risk_ceiling，
            本回调透传到 evt dict，由 _publish_tool_event 注册到 ToolCallContext，
            最终经 transition_async 透传到事件 payload，前端 ToolCallCard 可展示
            完整调用链路（与父 agent 直接调用的工具行为一致）。
            """
            logger.info(
                f"[OfficialDeepAgent] _on_tool_event 入口: event_type={event_type}, "
                f"tool={tool_name}, tc_id={tool_call_id}, "
                f"has_params={bool(kwargs.get('parameters'))}, has_result={'result' in kwargs}"
            )
            evt: dict[str, Any] = {
                "event_type": event_type,
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
            }
            # 透传 parameters / result / error
            # must-use is-not-None check: 空 dict {} 是 falsy 但仍是有效值，
            # "if kwargs.get('parameters'):" 会丢弃空 dict → evt 缺 key → _publish_tool_event 取到 {}
            if kwargs.get("parameters") is not None:
                evt["parameters"] = kwargs["parameters"]
            if "result" in kwargs and kwargs["result"] is not None:
                evt["result"] = kwargs["result"]
            if "error" in kwargs and kwargs["error"] is not None:
                evt["error"] = kwargs["error"]
            # 透传子 agent 嵌套层级字段（Phase E3）
            for sub_field in (
                "parent_tool_call_id",
                "depth",
                "agent_name",
                "agent_path",
                "risk_ceiling",
            ):
                val = kwargs.get(sub_field)
                if val is not None and val not in {"", 0}:
                    evt[sub_field] = val
                elif val == 0 and sub_field == "depth":
                    # depth=0 是主 agent，不写入（仅子 agent depth>0 才写入）
                    pass
            await self._publish_tool_event(evt)

        config["configurable"]["_on_tool_event"] = _on_tool_event

        resilience_config = get_resilience_config()
        # soft_timeout 仅作警告，hard_timeout 默认 None（不限制）
        # 修复 or 操作符 bug：原 `or 900` 让 None 默认值变成 900，违反"None 表示不限制"设计意图
        timeout_mgr = ExecutionTimeoutManager(
            soft_timeout=resilience_config.soft_timeout if resilience_config.soft_timeout is not None else None,
            hard_timeout=resilience_config.hard_timeout if resilience_config.hard_timeout is not None else None,
        )
        # 重复工具调用检测器：短期内相同 tool_name + 相同 parameters 超过阈值时
        # 注入提示，引导 agent 调整策略而非继续重试
        duplicate_detector = DuplicateToolCallDetector(
            window_seconds=resilience_config.duplicate_tool_call_window,
            threshold=resilience_config.duplicate_tool_call_threshold,
        )

        # StreamContext-like 对象，供 AgentExecutor 使用
        # - retry_count: 重试计数（每个外层 interrupt 循环重置）
        # - all_messages / current_message_content: GraphRecursionError 降级时使用（deep agent 不维护，留空）
        # - fallback_triggered: _run_fallback 时标记
        ctx = SimpleNamespace(
            retry_count=0,
            all_messages=[],
            current_message_content="",
            fallback_triggered=False,
        )

        # 创建 _DeepAgentExecutor（公共执行器的 deep agent 专用子类）
        # 替代原内联的 retry/timeout/degrade 循环：
        # - retry/timeout: 由 AgentExecutor._iter_with_timeout 提供
        # - degrade: 由 _DeepAgentExecutor._run_degrade 提供（支持 async rebuild + 多级降级）
        # - fallback: 由 _DeepAgentExecutor._run_fallback 提供（调用 _fallback_direct_answer）
        executor = _DeepAgentExecutor(
            fallback_service=None,  # 不使用（_run_fallback 已重写）
            rebuild_agent_fn=lambda _tools: (None, None),  # 不使用（_run_degrade 已重写）
            tools=self.original_tools or [],
            model_instance=self.model,
            data={"query": query},
            usage_tracker=None,
            token_detail_tracker=None,
            resilience_config=resilience_config,
            timeout_manager=timeout_mgr,
            duplicate_detector=duplicate_detector,
            rebuild_coro_fn=self._rebuild_with_degraded_tools,
            fallback_direct_answer_fn=self._fallback_direct_answer,
            deep_agent=self,
            query=query,
            graph_config=config,
        )

        # 设置当前协程的 checkpointer contextvar：
        # subagent_patch.py 的 _patched_get_subagents 会从 contextvar 读取 checkpointer
        # 并注入到子 agent 的 create_agent()，使子 agent 的 interrupt() 不再被 Pregel 抑制。
        # try/finally 确保 contextvar 在流结束（正常或异常）后恢复原值，避免泄漏。
        graph_checkpointer = getattr(self.graph, "checkpointer", None)
        _checkpointer_token = set_current_checkpointer(graph_checkpointer)
        try:
            # Path D 双模式初始化：
            # - resume_command 非空：恢复模式，从 checkpoint 续流（query 可为 None）
            # - resume_command 为 None：初始执行，使用 HumanMessage 包装 query
            if resume_command is not None:
                graph_input = resume_command
                logger.info("[OfficialDeepAgent] 恢复模式: 使用 resume_command 从 checkpoint 续流")
            else:
                graph_input = {"messages": [HumanMessage(content=query or "")]}
            accumulated_result = None
            # 工具事件追踪状态：整个流期间持续累积（含 interrupt 恢复后的续流）
            # - seen_tool_call_ids: 已发射 INPUT_READY 的 tool_call_id 集合，避免流式 chunk 重复发射
            # - accumulated_messages: 累积的 AIMessage/AIMessageChunk/ToolMessage 列表，
            #   供 ToolMessage 阶段从 tool_call_chunks 聚合完整参数
            seen_tool_call_ids: set = set()
            # B7: 恢复模式预热 seen_tool_call_ids —— 从 checkpoint state 中提取已注册的
            # tool_call_id 集合，避免 ToolMessage 阶段触发 PENDING 补发导致非法状态转换 WARNING。
            if resume_command is not None:
                try:
                    state = await self.graph.aget_state(config)
                    if state and hasattr(state, "values") and state.values:
                        from langchain_core.messages import AIMessage

                        for msg in state.values.get("messages", []) or []:
                            if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
                                for tc in msg.tool_calls:
                                    tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
                                    if tc_id:
                                        seen_tool_call_ids.add(tc_id)
                        logger.info(
                            f"[OfficialDeepAgent] 恢复模式预热 seen_tool_call_ids: "
                            f"count={len(seen_tool_call_ids)}, ids={list(seen_tool_call_ids)[:5]}..."
                        )
                except Exception as e:
                    logger.warning(f"[OfficialDeepAgent] 预热 seen_tool_call_ids 失败(非致命): {e}")
            accumulated_messages: list = []
            # 在循环外定义，避免 loop_fn 闭包捕获每次迭代的重新赋值（B023）
            all_resume_values: dict = {}
            # Path D 退出信号：on_interrupt 返回空 dict 时置为 True，
            # 表示已创建 Approval DB 记录，worker 应退出等待 Celery 恢复
            exit_signal: dict = {"exited": False}

            # 外层 interrupt 循环：处理 __interrupt__ 事件 + Command(resume=...) 恢复
            while True:
                all_resume_values.clear()  # 清空上一轮的审批结果，复用同一 dict 对象
                exit_signal["exited"] = False  # 重置退出信号
                ctx.retry_count = 0  # 重置重试计数

                # 定义 loop_fn：核心流式循环（闭包捕获 all_resume_values 等）
                # 处理 chunks：updates 模式（interrupt 事件）+ messages 模式（工具事件提取）
                # 重复工具调用警告在 loop_fn 内部注入并重入 astream（对 AgentExecutor 透明）
                async def loop_fn(
                    _agent,
                    graph_input_arg,
                    config_arg,
                    _ctx,
                    _strategy,
                    _data,
                ):
                    current_input = graph_input_arg
                    while True:  # 重复工具调用警告注入重入
                        local_pending_warnings: list = []
                        async for chunk in self.graph.astream(
                            current_input,
                            config=config_arg,
                            stream_mode=["messages", "updates"],
                        ):
                            # 检查 soft timeout（仅警告一次）
                            if timeout_mgr.check_soft_timeout():
                                logger.warning(f"[Resilience] 深度研究执行超时 (soft): {timeout_mgr.elapsed:.1f}s")

                            # 多 stream mode 下 chunk 是 (mode_name, data) 元组
                            if isinstance(chunk, tuple) and len(chunk) == 2:
                                mode_name, mode_data = chunk
                            else:
                                mode_name, mode_data = "messages", chunk

                            # 处理 updates stream mode（包含 interrupt 事件）
                            if mode_name == "updates":
                                if isinstance(mode_data, dict) and "__interrupt__" in mode_data:
                                    interrupts = mode_data["__interrupt__"]
                                    if interrupts:
                                        for intr in interrupts:
                                            if isinstance(intr, Interrupt):
                                                interrupt_value = intr.value
                                                interrupt_id = intr.id
                                            elif isinstance(intr, dict):
                                                interrupt_value = intr.get("value", intr)
                                                interrupt_id = intr.get("id", "")
                                            else:
                                                interrupt_value = intr
                                                interrupt_id = ""

                                            if is_approval_interrupt(interrupt_value):
                                                # 使用公共审批中断解析器，统一批量/单工具两种格式
                                                from Django_xm.common.approval_parser import (
                                                    parse_approval_interrupt as _parse_interrupt,
                                                )

                                                interrupt_list = _parse_interrupt(
                                                    interrupt_value,
                                                    graph_interrupt_id=interrupt_id,
                                                    langgraph_resume_id=interrupt_id,
                                                )
                                                if not interrupt_list:
                                                    continue

                                                tool_name = interrupt_list[0]["tool_name"]
                                                is_batch = len(interrupt_list) > 1
                                                logger.info(
                                                    f"[OfficialDeepAgent] 审批中断: "
                                                    f"{'批量' if is_batch else '单工具'}, "
                                                    f"{len(interrupt_list)} 个工具, "
                                                    f"tools={[b['tool_name'] for b in interrupt_list]}"
                                                )
                                                if on_interrupt is not None:
                                                    logger.info(
                                                        f"[OfficialDeepAgent] 实时通知审批回调"
                                                        f"{'(批量)' if is_batch else ''}: "
                                                        f"{len(interrupt_list)} 个工具"
                                                    )
                                                    timeout_mgr.pause()
                                                    try:
                                                        batch_resume = on_interrupt(interrupt_list)
                                                        if asyncio.iscoroutine(batch_resume):
                                                            batch_resume = await batch_resume
                                                    finally:
                                                        timeout_mgr.resume()
                                                    # Path D 退出信号检测
                                                    if isinstance(batch_resume, dict) and not batch_resume:
                                                        logger.info(
                                                            "[OfficialDeepAgent] Path D 退出信号: "
                                                            "已创建审批 DB 记录，worker 退出"
                                                        )
                                                        exit_signal["exited"] = True
                                                        break
                                                    # 按 langgraph_resume_id 分组构造 resume dict
                                                    # Command(resume=...) 的 key 必须是 LangGraph Interrupt.id
                                                    langgraph_id = interrupt_list[0].get("langgraph_resume_id", "")
                                                    if isinstance(batch_resume, dict):
                                                        if langgraph_id:
                                                            all_resume_values.setdefault(langgraph_id, {}).update(batch_resume)
                                                        else:
                                                            all_resume_values.update(batch_resume)
                                                    else:
                                                        if langgraph_id:
                                                            for bi in interrupt_list:
                                                                all_resume_values.setdefault(langgraph_id, {})[bi["interrupt_id"]] = batch_resume
                                                        else:
                                                            for bi in interrupt_list:
                                                                all_resume_values[bi["interrupt_id"]] = batch_resume
                                                else:
                                                    langgraph_id = interrupt_list[0].get("langgraph_resume_id", "")
                                                    if langgraph_id:
                                                        for bi in interrupt_list:
                                                            all_resume_values.setdefault(langgraph_id, {})[bi["interrupt_id"]] = False
                                                    else:
                                                        for bi in interrupt_list:
                                                            all_resume_values[bi["interrupt_id"]] = False
                                continue

                            # messages 模式：提取工具调用生命周期事件并发布到实时频道
                            # astream 在 messages 模式下产出 (message, metadata) 元组，
                            # deepagents astream 只产出 AIMessageChunk（非完整 AIMessage），
                            # 需要复用 tool_event_extractor 公共模块聚合 tool_call_chunks，
                            # 在 ToolMessage 阶段补发完整 parameters，避免前端显示为 {}。
                            if mode_name == "messages":
                                msg_obj = (
                                    mode_data[0] if isinstance(mode_data, tuple) and len(mode_data) == 2 else mode_data
                                )
                                try:
                                    tool_events = extract_tool_events_from_message(
                                        msg_obj,
                                        seen_tool_call_ids,
                                        accumulated_messages,
                                    )
                                except Exception as e:
                                    logger.warning(f"[OfficialDeepAgent] 工具事件提取失败: {e}")
                                    tool_events = []

                                for evt in tool_events:
                                    # 重复工具调用检测：仅对 PENDING 事件记录，
                                    # 避免对同一 tool_call_id 的 COMPLETED/FAILED 重复计数
                                    if evt.get("event_type") == EventType.TOOL_CALL_PENDING:
                                        warning = duplicate_detector.record(
                                            evt.get("tool_name", "unknown"),
                                            evt.get("parameters") or {},
                                        )
                                        if warning is not None:
                                            local_pending_warnings.append(SystemMessage(content=warning.to_prompt()))
                                    await self._publish_tool_event(evt)

                                # 捕获 LLM thinking/reasoning 内容（AIMessageChunk.additional_kwargs.reasoning_content）
                                thinking_text = extract_thinking_content(msg_obj)
                                if thinking_text:
                                    _configurable = config_arg.get("configurable", {})
                                    _session_id = _configurable.get("chat_session_id") or self.chat_session_id
                                    _message_id = _configurable.get("assistant_message_id")
                                    if _session_id and _message_id:
                                        try:
                                            from Django_xm.apps.research.services.writeback import (
                                                broadcast_stream_reasoning,
                                            )

                                            broadcast_stream_reasoning(
                                                session_id=_session_id,
                                                task_id=self.thread_id,
                                                message_id=_message_id,
                                                content=thinking_text,
                                            )
                                        except Exception:
                                            pass

                                # 检测到重复调用：中断当前流以注入警告
                                # break 退出 async for，由下方 aupdate_state 注入后重入
                                if local_pending_warnings:
                                    break

                        # 重复工具调用警告注入：通过 aupdate_state 追加 SystemMessage
                        # 到 graph state，agent 下一轮 LLM 调用会看到提示并调整策略。
                        # 注入后以 current_input=None 重入 astream，从当前 checkpoint 续流。
                        if local_pending_warnings:
                            logger.info(
                                f"[Resilience] 注入 {len(local_pending_warnings)} 条重复工具调用警告到 agent 状态"
                            )
                            try:
                                await self.graph.aupdate_state(
                                    config_arg,
                                    {"messages": local_pending_warnings},
                                )
                            except Exception as e:
                                logger.warning(f"[Resilience] 注入重复调用警告失败: {e}")
                            current_input = None  # 从当前 checkpoint 续流
                            continue  # 继续重入 astream（不计入 retry_count）
                        break  # astream 正常结束，退出 loop_fn 的 while True
                    # loop_fn 必须是 async generator（AgentExecutor._iter_with_timeout 要求）
                    # yield 一个结束标记，使 loop_fn 成为合法的 async generator
                    yield {"type": "loop_done"}

                # 执行 _DeepAgentExecutor（带韧性的流式执行）
                # 替代原内联的 retry/timeout/degrade 循环
                fallback_result = None
                async for event in executor.run(
                    loop_fn,
                    None,
                    graph_input,
                    config,
                    ctx,
                    None,
                    {"query": query or ""},
                ):
                    # 捕获 fallback 结果事件
                    if event.get("type") == "deep_agent_fallback_result":
                        fallback_result = event.get("data")

                # 如果触发回退，返回 fallback 结果
                if fallback_result is not None:
                    return fallback_result

                # Path D 退出信号优先检查：
                # on_interrupt 返回空 dict 时 exit_signal["exited"] = True，
                # 表示已创建 Approval DB 记录，worker 应退出。
                # research_runner.execute_research_async 检测到 "interrupted" 后
                # 返回 ResearchResult(success=False)，Celery 任务结束。
                # 用户审批后由 research_resume_task 从 checkpoint 恢复。
                if exit_signal["exited"]:
                    logger.info("[OfficialDeepAgent] Path D 退出: worker 结束，等待 research_resume_task 恢复")
                    return {
                        "success": False,
                        "query": query,
                        "final_report": None,
                        "error": "interrupted",
                        "current_step": "interrupted",
                        "files": None,
                        "state_files": None,
                    }

                # 流结束后检查是否有实时回调收集的审批结果
                if all_resume_values:
                    # 使用 Command(resume=...) 一次性恢复所有 interrupt
                    graph_input = Command(resume=all_resume_values)
                    logger.info(
                        f"[OfficialDeepAgent] 恢复 agent: {len(all_resume_values)} 个 interrupt, "
                        f"resume_dict={all_resume_values}"
                    )
                    # 继续循环，重新流式执行
                    continue
                else:
                    # 无中断，流已完成，提取最终结果
                    break

            # 从 checkpointer 获取最终状态
            final_state = await self.graph.aget_state(config)
            if final_state and hasattr(final_state, "values") and final_state.values:
                accumulated_result = final_state.values
            else:
                accumulated_result = {}

            final_report = _extract_ai_response(accumulated_result)
            files_info = self._extract_files_info(accumulated_result)
            disk_files = self._scan_disk_files()

            # 当 AI 回复过短时，从磁盘文件提取完整报告
            if len(final_report.strip()) < 200 and disk_files:
                for report_key in ("reports/final_report.md", "final_report.md"):
                    if report_key in disk_files:
                        report_content = disk_files[report_key].get("content", "")
                        if len(report_content.strip()) > len(final_report.strip()):
                            final_report = report_content
                            logger.info(f"[OfficialDeepAgent] 使用磁盘报告文件替代短回复 ({len(final_report)} 字符)")
                        break

            result = {
                "success": True,
                "query": query,
                "final_report": final_report,
                "plan": None,
                "current_step": "completed",
                "error": None,
                "files": files_info if files_info else disk_files,
                "state_files": disk_files,
            }
            if executor._current_degradation is not None:
                result["degraded"] = True
                result["degradation_level"] = executor._current_degradation.value
            return result

        except Exception as e:
            error_msg = str(e) or repr(e) or type(e).__name__
            logger.exception(f"[OfficialDeepAgent] 流式研究(interrupt)失败: {error_msg}")
            return {
                "success": False,
                "query": query,
                "final_report": None,
                "error": error_msg,
            }
        finally:
            # 恢复 checkpointer contextvar 到原值，避免泄漏到后续协程
            # （set_current_checkpointer 返回 Token，reset_current_checkpointer 用 Token 恢复）
            reset_current_checkpointer(_checkpointer_token)

    async def _publish_tool_event(self, evt: dict[str, Any]) -> None:
        """发布单个工具调用生命周期事件到实时频道。

        通过状态机统一入口 ``service.transition_async`` 发布事件：
        - 先 ``service.register`` 注册上下文（幂等，已存在则仅补全空字段）
        - 再 ``await service.transition_async`` 完成状态转换并发布事件

        路由策略（与 writeback.broadcast_stream_completed 一致）：
        - chat 关联场景（self.chat_session_id 非空）：session + task 双频道
          由 register 时设置的 cross_module_id 触发，transition_async 从
          context 透传 cross_module_id 给底层 publish_tool_call
        - 独立深度研究场景：仅 task 频道

        子 agent 嵌套层级字段（Phase E3）：
        evt 中的 parent_tool_call_id / depth / agent_name / agent_path / risk_ceiling
        （由 _on_tool_event 从 subagent_patch 透传）注册到 ToolCallContext，
        transition_async 从 context 透传到事件 payload，前端 ToolCallCard 可展示
        完整调用链路。主 agent 直接调用的工具不携带这些字段（evt 中无对应 key）。

        Args:
            evt: extract_tool_events_from_message 返回的事件 dict，字段：
                - event_type: EventType (TOOL_CALL_PENDING / TOOL_CALL_COMPLETED / TOOL_CALL_FAILED)
                - tool_call_id: str
                - tool_name: str
                - parameters: dict
                - result: str (仅 COMPLETED)
                - error: str (仅 FAILED)
                - parent_tool_call_id/depth/agent_name/agent_path/risk_ceiling: 子 agent 嵌套字段（可选）
        """
        from Django_xm.common.approval_utils import derive_cross_module_id_from_source
        from Django_xm.common.event_schema import EventSource, EventType
        from Django_xm.common.tool_call_lifecycle import ToolCallContext, service

        event_type = evt.get("event_type")
        tool_call_id = evt.get("tool_call_id", "") or ""
        tool_name = evt.get("tool_name") or "unknown"
        parameters = evt.get("parameters") or {}
        # 3.4 参数权威源兜底：extractor 参数聚合失败（parameters 为空）时，
        # 从 ctx（middleware SAFE/审批/无策略注册路径已写入完整 AIMessage 参数）兜底，
        # 避免前端显示参数为空 []（write_todos/web_search 等工具实测复现）。
        if not parameters:
            try:
                _ctx = service.get_context(tool_call_id)
                _ctx_params = _ctx.get("parameters") if _ctx else None
                if isinstance(_ctx_params, dict) and _ctx_params:
                    parameters = _ctx_params
            except Exception:
                pass

        logger.info(
            f"[OfficialDeepAgent] _publish_tool_event 入口: event_type={event_type}, "
            f"tool={tool_name}, tc_id={tool_call_id}, "
            f"params_keys={list(parameters.keys()) if isinstance(parameters, dict) else type(parameters).__name__}"
        )

        # event_type 必须为 EventType 枚举（extract_tool_events_from_message 保证）
        # 防御性校验：跳过非法事件类型，避免 transition_async 内部抛 KeyError
        if not isinstance(event_type, EventType):
            logger.warning(
                f"[OfficialDeepAgent] 跳过非 EventType 事件: event_type={event_type!r}, tool_call_id={tool_call_id}"
            )
            return

        # PENDING 补发防护（P13）：ToolMessage 阶段 extractor 会对 AIMessage 阶段
        # args 不完整（未发射）的工具补发 PENDING。若 lifecycle context 已推进到
        # 非 PENDING 状态（SAFE 自动通过场景：ApprovalMiddleware 直接发布 RUNNING；
        # 审批场景：WAITING/RUNNING），补发 PENDING 会导致状态机
        # `running → pending` 非法转换 WARNING。此时 RUNNING/WAITING 事件已携带
        # 完整 parameters，前端无需重复 PENDING，直接跳过（debug 记录）。
        # 子 agent 事件经 _on_tool_event 统一走本函数，主/子 agent 均覆盖。
        if event_type == EventType.TOOL_CALL_PENDING:
            _existing_ctx = service.get_context(tool_call_id)
            _last_event = _existing_ctx.get("last_event_type") if _existing_ctx else None
            if _last_event and _last_event != EventType.TOOL_CALL_PENDING.value:
                logger.debug(
                    f"[OfficialDeepAgent] 跳过 PENDING 补发（状态已推进）: "
                    f"tool_call_id={tool_call_id}, last_event_type={_last_event}"
                )
                return

        # 1. 注册工具调用上下文（幂等）
        #    - module_id = task_id（深度研究任务 ID）
        #    - cross_module_id = chat_session_id（关联 chat 场景触发双频道广播）
        #    - message_id 留空：deep_research 模块无关联 chat message，
        #      若 chat 模块已注册过同 tool_call_id 则由 register 合并补全
        #    - 子 agent 嵌套层级字段：从 evt 提取（仅子 agent 工具事件携带）
        cross_module_id = derive_cross_module_id_from_source("deep_research", self.chat_session_id)

        # 提取子 agent 嵌套层级字段（Phase E3）
        # evt 中无对应 key 时使用默认空值（主 agent 场景）
        sub_parent_tool_call_id = evt.get("parent_tool_call_id", "") or ""
        sub_depth = evt.get("depth", 0)
        if not isinstance(sub_depth, int) or sub_depth < 0:
            sub_depth = 0
        sub_agent_name = evt.get("agent_name", "") or ""
        sub_agent_path = evt.get("agent_path")
        if not isinstance(sub_agent_path, list):
            sub_agent_path = []
        # risk_ceiling 统一转字符串（可能是 RiskLevel 枚举）
        sub_risk_ceiling_raw = evt.get("risk_ceiling")
        if sub_risk_ceiling_raw is not None and not isinstance(sub_risk_ceiling_raw, str):
            sub_risk_ceiling = (
                sub_risk_ceiling_raw.value if hasattr(sub_risk_ceiling_raw, "value") else str(sub_risk_ceiling_raw)
            )
        else:
            sub_risk_ceiling = sub_risk_ceiling_raw or ""

        try:
            service.register(
                ToolCallContext(
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    module=EventSource.DEEP_RESEARCH,
                    module_id=self.thread_id,
                    message_id="",
                    parameters=parameters,
                    cross_module_id=cross_module_id,
                    # 子 agent 嵌套层级字段（Phase E3）
                    parent_tool_call_id=sub_parent_tool_call_id,
                    depth=sub_depth,
                    agent_name=sub_agent_name,
                    agent_path=sub_agent_path,
                    risk_ceiling=sub_risk_ceiling,
                )
            )
        except Exception as e:
            logger.warning(f"[OfficialDeepAgent] 注册工具调用上下文失败 (tool={tool_name}, tc_id={tool_call_id}): {e}")

        # 2. 状态机转换：transition_async 内部 await publish_tool_call，
        #    parameters / message_id / cross_module_id / graph_interrupt_id
        #    / 子 agent 嵌套层级字段 从 context 透传，底层传输逻辑不变
        # must-use is-not-None: empty dict {} 是有效占位 → 保留为 {} 而非 None，
        # 后续 middleware SAFE 工具路径通过 is_non_empty_params 保护不会覆盖已有非空参数
        transition_kwargs: dict[str, Any] = {
            "parameters": parameters
            if (isinstance(parameters, dict) and parameters)
            else ({} if isinstance(parameters, dict) else None),
        }
        if "result" in evt:
            transition_kwargs["result"] = evt["result"]
        if "error" in evt:
            transition_kwargs["error"] = evt["error"]

        try:
            await service.transition_async(
                tool_call_id,
                event_type,
                **transition_kwargs,
            )
        except Exception as e:
            logger.warning(
                f"[OfficialDeepAgent] 发布工具事件失败 "
                f"(event={event_type.value}, "
                f"tool={tool_name}, tc_id={tool_call_id}): {e}"
            )

    async def _rebuild_with_degraded_tools(self, degraded_tools: list[Any]) -> Any | None:
        """使用降级工具集重建 graph

        基于 self.original_config 中保存的 create_deep_agent 构建参数，
        替换 tools 为降级后的工具列表，重新构建 CompiledStateGraph。

        Args:
            degraded_tools: 降级后的工具列表（非空）

        Returns:
            新的 CompiledStateGraph；重建失败或缺少必要配置时返回 None，
            由调用方触发 _fallback_direct_answer 回退。
        """
        if not degraded_tools:
            return None
        if not self.original_config:
            logger.warning("[Resilience] 无法重建 graph: 缺少 original_config")
            return None

        try:
            from deepagents import create_deep_agent
        except Exception:
            logger.exception("[Resilience] 导入 create_deep_agent 失败")
            return None

        try:
            # 复制原始构建配置，替换 tools 为降级工具集
            base_config: dict[str, Any] = dict(self.original_config) if isinstance(self.original_config, dict) else {}
            base_config["tools"] = degraded_tools

            # 兜底：若 original_config 未带 model，使用实例的 self.model
            if "model" not in base_config and self.model is not None:
                base_config["model"] = self.model

            new_graph = create_deep_agent(**base_config)
            logger.info(f"[Resilience] graph 重建成功 (tools={len(degraded_tools)})")
            return new_graph
        except Exception:
            logger.exception("[Resilience] graph 重建失败")
            return None

    async def _fallback_direct_answer(self, query: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
        """深度研究最终回退：无工具直接 LLM 回答"""
        logger.warning("[Resilience] 深度研究回退到无工具直接回答")
        try:
            messages = [HumanMessage(content=query)]
            if hasattr(self, "model") and self.model:
                # model 可能是 ChatModel 实例或 model string
                if hasattr(self.model, "ainvoke"):
                    response = await self.model.ainvoke(messages)
                    content = response.content if hasattr(response, "content") else str(response)
                else:
                    # model 是字符串，需要创建 ChatModel 实例
                    from Django_xm.apps.ai_engine.services.llm_factory import get_chat_model

                    chat_model = get_chat_model()
                    response = await chat_model.ainvoke(messages)
                    content = response.content if hasattr(response, "content") else str(response)
            else:
                from Django_xm.apps.ai_engine.services.llm_factory import get_chat_model

                chat_model = get_chat_model()
                response = await chat_model.ainvoke(messages)
                content = response.content if hasattr(response, "content") else str(response)

            return {
                "success": True,
                "query": query,
                "final_report": content,
                "plan": None,
                "current_step": "completed",
                "error": None,
                "files": [],
                "state_files": {},
                "degraded": True,
                "degradation_level": "no_tools",
            }
        except Exception as e:
            logger.exception("[Resilience] 深度研究回退回答也失败")
            return {
                "success": False,
                "query": query,
                "final_report": None,
                "error": f"深度研究执行失败: {e!s}",
                "error_code": "FALLBACK_FAILED",
            }

    async def astream_research(
        self,
        query: str,
        config: dict[str, Any] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        logger.info(f"[OfficialDeepAgent] 流式研究: {query[:50]}...")

        if config is None:
            config = {}
        if "configurable" not in config:
            config["configurable"] = {}
        config["configurable"]["thread_id"] = self.thread_id
        config.setdefault("recursion_limit", 1000)

        try:
            yield {
                "event_type": "research_start",
                "node_name": "root",
                "data": {"query": query},
            }

            async for event in self.graph.astream_events(
                {"messages": [HumanMessage(content=query)]},
                config=config,
                version="v2",
            ):
                kind = event.get("event", "")
                name = event.get("name", "")
                data = event.get("data", {})

                if kind == "on_chat_model_stream":
                    chunk = data.get("chunk")
                    if chunk and hasattr(chunk, "content") and chunk.content:
                        yield {
                            "event_type": "token_stream",
                            "node_name": name,
                            "data": {"content": chunk.content},
                        }
                elif kind == "on_tool_start":
                    yield {
                        "event_type": "tool_start",
                        "node_name": name,
                        "data": {"input": str(data.get("input", ""))[:200]},
                    }
                elif kind == "on_tool_end":
                    yield {
                        "event_type": "tool_end",
                        "node_name": name,
                        "data": {"output": str(data.get("output", ""))[:200]},
                    }
                elif kind == "on_chain_end" and name == "LangGraph":
                    output = data.get("output", {})
                    final_report = _extract_ai_response(output)
                    yield {
                        "event_type": "research_end",
                        "node_name": "root",
                        "data": {
                            "final_report": final_report,
                            "success": True,
                        },
                    }
                elif kind == "on_chain_error":
                    yield {
                        "event_type": "error",
                        "node_name": name,
                        "data": {"error": str(data.get("error", "未知错误"))},
                    }

        except Exception as e:
            logger.exception("[OfficialDeepAgent] 流式研究失败")
            yield {
                "event_type": "error",
                "node_name": "root",
                "data": {"error": str(e)},
            }

    def get_status(self) -> dict[str, Any]:
        return {
            "thread_id": self.thread_id,
            "engine": "deepagents_official",
        }

    def _get_files_from_state(self, config: dict[str, Any]) -> dict[str, Any]:
        try:
            state = self.graph.get_state(config)
            if state and hasattr(state, "values") and state.values:
                files = state.values.get("files", {})
                if files:
                    logger.info(f"[OfficialDeepAgent] 从 state 获取到 {len(files)} 个文件")
                    return files
            logger.info("[OfficialDeepAgent] state 中无 files 数据")
        except Exception as e:
            logger.warning(f"[OfficialDeepAgent] 从 state 获取文件失败: {e}")
        return {}

    def _scan_disk_files(self) -> dict[str, Any]:
        if not self.work_dir or not os.path.isdir(self.work_dir):
            return {}
        files = {}
        for subdir in ("notes", "plans", "reports"):
            sub_path = os.path.join(self.work_dir, subdir)
            if not os.path.isdir(sub_path):
                continue
            for root, _dirs, fnames in os.walk(sub_path):
                for fname in fnames:
                    full_path = os.path.join(root, fname)
                    rel_path = os.path.relpath(full_path, self.work_dir).replace("\\", "/")
                    try:
                        with open(full_path, encoding="utf-8") as f:
                            content = f.read()
                        files[rel_path] = {
                            "content": content,
                            "encoding": "utf-8",
                        }
                    except (UnicodeDecodeError, OSError):
                        pass
        if files:
            logger.info(f"[OfficialDeepAgent] 从磁盘扫描到 {len(files)} 个文件: {list(files.keys())}")
        return files

    @staticmethod
    def _extract_files_info(result: dict[str, Any]) -> list[dict[str, Any]]:
        files = []
        messages = result.get("messages", [])
        for msg in messages:
            if hasattr(msg, "tool_calls"):
                for tc in msg.tool_calls:
                    if tc.get("name") == "write_file":
                        args = tc.get("args", {})
                        path = args.get("path", "")
                        if path:
                            files.append(
                                {
                                    "path": path,
                                    "name": os.path.basename(path),
                                    "type": "file",
                                    "size": len(args.get("content", "")),
                                }
                            )
            if hasattr(msg, "name") and msg.name == "write_file":
                pass
        return files
