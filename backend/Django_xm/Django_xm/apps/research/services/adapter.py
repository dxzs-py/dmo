"""官方 Deep Agent 适配器模块。

职责：
- ``OfficialDeepAgentAdapter``：将 ``create_deep_agent`` 返回的 CompiledStateGraph
  适配为上层统一接口（research/aresearch/astream_research），
  使上层调用方无需关心底层实现差异。
  - 集成 interrupt 审批机制（astream_research_with_interrupts）
  - 集成韧性模块（retry/timeout/degrade/fallback）
  - 工具调用生命周期事件发布到实时频道
  - 降级时使用 original_tools / original_config 重建 graph

抽取自原 ``official_deep_agent.py``（Task 18.1）。
废弃的 ``DeepResearchAgent``（deep_agent.py）已删除，
本适配器是深度研究的唯一官方实现。

依赖关系：
- 依赖 ``patches.py``：``_DeepAgentExecutor`` / ``_extract_ai_response``。
- 被 ``builders.py`` 导入：``OfficialDeepAgentAdapter``（作为工厂函数返回类型）。
"""

import asyncio
import os
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from Django_xm.apps.ai_engine.services.thinking import extract_thinking_content

# 子 agent 机制（deepagents 0.7.5 官方，取代旧 subagent_patch monkey-patch）：
# - 中断冒泡 + Command(resume) 恢复：0.7.5 原生支持，无需 checkpointer contextvar 注入。
# - 嵌套层级字段（depth/agent_path）：本模块在主 config 注入基础值（depth=0/agent_path=["main"]），
#   仅服务子代理 state.subagent_depth/subagent_path 展示元数据（SubAgentNestingMiddleware
#   写入 state）；事件路由与内容累计唯一依据为 subagent_thread_id（spec MODIFIED）。
# - 子 agent 工具事件：SubAgentToolEventMiddleware 从 configurable 读取本模块注入的
#   _on_tool_event 回调并转发到父 SSE 流。
from Django_xm.apps.core.config import get_logger
from Django_xm.apps.research.services.patches import (
    _DeepAgentExecutor,
    _extract_ai_response,
)

logger = get_logger(__name__)


def _assert_non_empty_decision(result) -> None:
    """审批决策不得为空：空 dict 表示审批创建失败，直接抛错终止任务。

    会话级单执行流语义：on_interrupt 必须返回非空决策（审批批次已创建），
    空返回意味着审批链路异常，任务无法等待恢复，只能终止。
    （抽象为模块级函数，避免 astream 循环 try 块内直接 raise 的 TRY301。）
    """
    if isinstance(result, dict) and not result:
        raise RuntimeError(
            "审批创建失败：on_interrupt 返回空决策，"
            "无审批批次可等待（任务终止）"
        )


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
        # 累积的 LLM 推理内容（深度思考功能）：
        # - 每次流式 chunk 追加，广播时发送累积后的完整内容（与代理模式 deep_chat_service 一致），
        #   避免前端覆盖语义下仅显示最后一个 chunk。
        # - 任务完成时由 research_runner 携带到 writeback，持久化到 ChatMessage.reasoning。
        self.accumulated_reasoning: str = ""
        # 子代理重试指令队列（SessionExecutor 注入，astream 循环按 chunk 消费）：
        # 队列元素为 {agent_path, tool_call_id, agent_name, original_args, ...}，
        # 消费后构造 SystemMessage 经 aupdate_state 注入 graph state 后重入 astream，
        # 主 agent 收到指令后以原始入参重新调用目标子代理（Task 3 单独重启失败子代理）。
        self._retry_instruction_queue: asyncio.Queue | None = None
        # 子代理图层正文/思考累计（spec MODIFIED：按 subagent_thread_id 键累计）：
        #   key = subagent_thread_id（SubAgentRuntime 适配器写入的路由标识符）
        #   value = {"content": str, "reasoning_content": str}
        # 由 _on_subagent_content 回调（SubAgentContentMiddleware 转发）累积；
        # 任务完成时由 research_runner 携带到 writeback，持久化到
        # ChatMessage.subagent_contents，供前端刷新后恢复子代理图层正文
        # （前端 subagentContents[subagentThreadId] 对齐）。
        self.subagent_contents: dict[str, dict[str, str]] = {}
        # 主 agent 图层已输出 content 累计长度（position 注入依据，Task 2.1）：
        # astream messages 循环中 AIMessageChunk content 追加；工具调用发起点
        # 读 len(_main_content_len) 作为主 agent 图层 position。
        self._main_content_len = 0

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

        会话级单执行流（执行器模式）：
            - 初始执行（resume_command=None）：graph_input = {"messages": [HumanMessage(content=query)]}
              on_interrupt 返回非空 decisions dict → Command(resume=...) 自动重入恢复
            - 恢复执行（resume_command=Command(resume=...)）：
              graph_input = resume_command，从 checkpoint 恢复 agent 执行

        集成韧性模块：重试（指数退避）、降级（减少工具）、回退（无工具直接 LLM 回答）。

        子代理重试指令（Task 3 单独重启失败子代理）：
        SessionExecutor 通过 ``_retry_instruction_queue`` 注入重试指令，
        chunk 循环在每轮 messages chunk 后非阻塞消费队列，经
        ``graph.aupdate_state`` 将 SystemMessage 追加到 graph state 后
        重入 astream（与重复工具调用警告注入同机制），主 agent 下一轮
        LLM 调用可见指令并以原始入参重新调用目标子代理。

        Args:
            query: 研究查询（resume_command 模式时可为 None）
            config: LangGraph 运行配置
            callbacks: LangChain 回调列表
            on_interrupt: 审批中断回调，签名 on_interrupt(interrupt_data: dict) -> resume_value
                          interrupt_data 包含 tool_name, interrupt_id, title, description 等
                          返回非空 dict {langgraph_resume_id: {tool_call_id: bool}} → Command(resume=...)
                          返回空 dict {} → 审批创建失败，抛出 RuntimeError 终止执行
            resume_command: 恢复模式时传入 Command(resume=...)，None 表示初始执行

        Returns:
            与 research() 方法相同格式的结果字典
        """
        from types import SimpleNamespace

        from langgraph.types import Command, Interrupt

        from Django_xm.apps.agent_hub.services.agent_resilience import (
            DuplicateToolCallDetector,
            ExecutionTimeoutManager,
            get_resilience_config,
        )
        from Django_xm.apps.tools.base import is_approval_interrupt, is_subagent_wait_interrupt

        # 工具事件提取与发布（与子 agent 事件转发路径一致）
        # 注意：工具事件的实际发布由 _publish_tool_event 内部通过
        # service.transition_async 完成（状态机统一入口），此处仅需 EventType
        from Django_xm.apps.tools.tool_event_extractor import extract_tool_events_from_message
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
        # SubAgentToolEventMiddleware（子 agent middleware）会从 configurable 读取此回调，
        # 将子 agent 工具调用事件转发到父 SSE 流。父 config 经 langgraph ensure_config
        # 自动传播到子 agent。
        #
        # 回调签名：
        #   on_tool_event(event_type, tool_call_id, tool_name, **kwargs) -> coroutine
        # kwargs 可能包含 parameters / result / error / depth / risk_ceiling
        # （agent_path 不再透传——spec REMOVED：agent_path 从事件 payload 中删除，
        # 路由唯一依据 subagent_thread_id）
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
            SubAgentToolEventMiddleware 通过 kwargs 传递
            depth / risk_ceiling（agent_name 由回调自行读取），
            本回调透传到 evt dict，由 _publish_tool_event 注册到 ToolCallContext，
            最终经 transition_async 透传到事件 payload，前端 ToolCallCard 可展示
            嵌套层级（与父 agent 直接调用的工具行为一致）；agent_path 不透传
            （spec REMOVED：事件 payload 不再携带 agent_path）。
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
            # 注意：不能使用 `val not in {"", 0}` 判断——list 参与 set 成员测试
            # 会抛 `unhashable type: 'list'`。改用 tuple 成员测试（in 逐元素 ==
            # 比较，list == "" / list == 0 均安全返回 False）。
            for sub_field in (
                "parent_tool_call_id",
                "depth",
                "agent_name",
                "risk_ceiling",
                # description：子 agent 角色描述（任务目标，Task 2.4），
                # 由 SubAgentToolEventMiddleware 从 nesting 透传，仅子 agent 事件携带
                "description",
                # subagent_thread_id：子代理 SSE 定向推送路由标识符（spec D10），
                # 由 SubAgentToolEventMiddleware 从 configurable 透传，仅子 agent 事件携带
                "subagent_thread_id",
            ):
                val = kwargs.get(sub_field)
                if val is None or val in ("", 0):
                    # 空字符串 / 0（depth=0 主 agent）不写入
                    continue
                evt[sub_field] = val
            await self._publish_tool_event(evt)

        config["configurable"]["_on_tool_event"] = _on_tool_event

        # 注入子代理正文/思考回调到 config["configurable"]：
        # SubAgentContentMiddleware（子 agent middleware）从 configurable 读取此回调，
        # 在子代理每次模型调用后转发其正文与中间思考到父 SSE 流（Agent 图层嵌套）。
        # 父 config 经 langgraph ensure_config 自动传播到子 agent。
        #
        # 回调签名：
        #   on_subagent_content(agent_path, content, reasoning_content, agent_name, depth,
        #                       subagent_thread_id, msg_id) -> coroutine
        # （agent_path 仅保留为展示元数据参数；路由与累计唯一依据 subagent_thread_id）
        #
        # 处理逻辑（spec MODIFIED：路由收敛）：
        # 1. 按 subagent_thread_id 累计到 self.subagent_contents（废弃 agent_path
        #    拼接键），content/reasoning_content 追加式累积（多轮模型调用拼接）；
        #    按 msg_id 幂等去重——审批 interrupt 恢复时 LangGraph 重放节点，
        #    同一条 AIMessage 会再次触发回调，跳过已转发消息（防重复 + 防错位）；
        # 2. 发布 STREAM_SUBAGENT_CONTENT WebSocket 事件到 session/task 频道，
        #    前端按 subagent_thread_id 路由到对应子代理卡片实时展示
        #    （data 不携带 agent_path，仅保留 subagent_thread_id 定向路由）；
        # 3. 事件携带 message_id（chat 关联场景定位归属消息）。
        if not hasattr(self, "_sent_subagent_msg_keys"):
            self._sent_subagent_msg_keys: dict[str, set] = {}

        async def _on_subagent_content(
            agent_path,
            content,
            reasoning_content,
            agent_name,
            depth,
            subagent_thread_id="",
            msg_id="",
        ):
            """子代理正文/中间思考转发回调（spec MODIFIED：按 subagent_thread_id 路由累计）。"""
            if not subagent_thread_id:
                return
            seen = self._sent_subagent_msg_keys.setdefault(subagent_thread_id, set())
            if msg_id and msg_id in seen:
                logger.debug(
                    f"[OfficialDeepAgent] 跳过重放子代理正文: "
                    f"subagent_thread_id={subagent_thread_id}, msg_id={msg_id}"
                )
                return
            if msg_id:
                seen.add(msg_id)
            entry = self.subagent_contents.setdefault(subagent_thread_id, {"content": "", "reasoning_content": ""})
            if content:
                entry["content"] = (entry.get("content") or "") + content
            if reasoning_content:
                entry["reasoning_content"] = (entry.get("reasoning_content") or "") + reasoning_content
            try:
                from Django_xm.common.event_schema import EventSource, EventType
                from Django_xm.common.realtime_events import publish_event

                _configurable = getattr(self, "_last_config", None)
                _cfg = _configurable if isinstance(_configurable, dict) else {}
                _session_id = (_cfg.get("configurable") or {}).get("chat_session_id") or self.chat_session_id
                _assistant_message_id = (_cfg.get("configurable") or {}).get("assistant_message_id") or ""
                if not _assistant_message_id:
                    _assistant_message_id = getattr(self, "_assistant_message_id", "")

                # 深度研究执行流：session/task 双频道（与工具事件路由一致）
                await publish_event(
                    EventType.STREAM_SUBAGENT_CONTENT,
                    {
                        "source": EventSource.DEEP_RESEARCH,
                        "source_id": self.thread_id,
                        "message_id": _assistant_message_id or None,
                        "data": {
                            # data 不携带 agent_path（spec MODIFIED：data.agent_path 废弃，
                            # 路由唯一依据顶层 subagent_thread_id，协议标识符保持
                            # snake_case，见 sessionTransformers 转换边界）
                            "content": content,
                            "reasoning_content": reasoning_content,
                            "agent_name": agent_name or "",
                            "depth": depth,
                        },
                    },
                    session_id=_session_id or None,
                    task_id=self.thread_id,
                    subagent_thread_id=subagent_thread_id or None,
                )
            except Exception as e:
                logger.warning(
                    f"[OfficialDeepAgent] 广播子代理正文失败: "
                    f"subagent_thread_id={subagent_thread_id}, err={e}"
                )

        config["configurable"]["_on_subagent_content"] = _on_subagent_content
        # 供 _on_subagent_content / _publish_tool_event 读取运行时 config
        # （chat_session_id / assistant_message_id 等执行期注入字段）
        self._last_config = config

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

        # 主 config 注入嵌套层级基础值（deepagents 0.7.5 官方机制）：
        # 子 agent 经 langgraph ensure_config 自动继承主 configurable；
        # SubAgentNestingMiddleware 在子 agent 内基于这些值计算递增值写入 state。
        # 该注入仅服务 state 展示元数据（subagent_depth/subagent_path），
        # 不参与事件路由（spec MODIFIED：路由唯一依据 subagent_thread_id）。
        if "depth" not in config["configurable"]:
            config["configurable"]["depth"] = 0
        if "agent_path" not in config["configurable"]:
            config["configurable"]["agent_path"] = ["main"]

        try:
            # 双模式初始化：
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
            # 业务等待挂起（spec D4）：loop_fn 检测到 _subagent_wait interrupt 时写入，
            # 外层循环据此返回 suspended 结果（不 finalize、不 Command(resume)）。
            subagent_wait_suspend: dict | None = None

            # 外层 interrupt 循环：处理 __interrupt__ 事件 + Command(resume=...) 恢复
            while True:
                all_resume_values.clear()  # 清空上一轮的审批结果，复用同一 dict 对象
                subagent_wait_suspend = None  # 每轮重置业务等待挂起标记
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
                    nonlocal subagent_wait_suspend
                    current_input = graph_input_arg
                    while True:  # 重复工具调用警告注入重入
                        local_pending_warnings: list = []
                        retry_injected = False
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

                                            # 业务等待挂起（spec D4）：wait_for_subagent 触发，
                                            # 非审批 interrupt，不产出审批 UI。记录挂起信息后
                                            # 退出流，由外层返回 suspended 结果（父协程退出）。
                                            if is_subagent_wait_interrupt(interrupt_value):
                                                subagent_wait_suspend = {
                                                    "subagent_thread_id": interrupt_value.get("subagent_thread_id", ""),
                                                    "interrupt_id": interrupt_id,
                                                }
                                                logger.info(
                                                    f"[OfficialDeepAgent] 业务等待挂起: "
                                                    f"subagent={subagent_wait_suspend['subagent_thread_id']}, "
                                                    f"interrupt_id={interrupt_id}"
                                                )
                                                break

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
                                                    async def _call_interrupt_handler(interrupts):
                                                        """调用审批中断回调（暂停超时计时）。

                                                        会话级单执行流：on_interrupt 必须返回非空决策；
                                                        空 dict 表示审批创建失败，直接抛错终止（不再走
                                                        Celery 时代"退出等待外部恢复"的 Path D 语义）。
                                                        """
                                                        timeout_mgr.pause()
                                                        try:
                                                            result = on_interrupt(interrupts)
                                                            if asyncio.iscoroutine(result):
                                                                result = await result
                                                        finally:
                                                            timeout_mgr.resume()
                                                        _assert_non_empty_decision(result)
                                                        return result

                                                    batch_resume = await _call_interrupt_handler(interrupt_list)
                                                    # 按 langgraph_resume_id 分组构造 resume dict
                                                    # Command(resume=...) 的 key 必须是 LangGraph Interrupt.id
                                                    langgraph_id = interrupt_list[0].get("langgraph_resume_id", "")
                                                    if isinstance(batch_resume, dict):
                                                        if langgraph_id:
                                                            all_resume_values.setdefault(
                                                                langgraph_id, {}
                                                            ).update(batch_resume)
                                                        else:
                                                            all_resume_values.update(batch_resume)
                                                    elif langgraph_id:
                                                        for bi in interrupt_list:
                                                            all_resume_values.setdefault(
                                                                langgraph_id, {}
                                                            )[bi["interrupt_id"]] = batch_resume
                                                    else:
                                                        for bi in interrupt_list:
                                                            all_resume_values[
                                                                bi["interrupt_id"]
                                                            ] = batch_resume
                                                else:
                                                    langgraph_id = interrupt_list[0].get("langgraph_resume_id", "")
                                                    if langgraph_id:
                                                        for bi in interrupt_list:
                                                            all_resume_values.setdefault(
                                                                langgraph_id, {}
                                                            )[bi["interrupt_id"]] = False
                                                    else:
                                                        for bi in interrupt_list:
                                                            all_resume_values[bi["interrupt_id"]] = False
                                        if subagent_wait_suspend is not None:
                                            # 业务等待挂起：退出 async for，外层返回 suspended 结果
                                            break
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
                                # 累积后广播完整内容：每次 chunk 追加到 accumulated_reasoning，
                                # 广播 content 为累积后的完整推理文本（前端 AiReasoning 覆盖语义下内容完整）。
                                thinking_text = extract_thinking_content(msg_obj)
                                if thinking_text:
                                    self.accumulated_reasoning = (self.accumulated_reasoning or "") + thinking_text
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
                                                content=self.accumulated_reasoning,
                                            )
                                        except Exception as e:
                                            logger.warning(
                                                f"[OfficialDeepAgent] 广播推理内容失败: "
                                                f"session={_session_id}, msg={_message_id}, err={e}"
                                            )

                                # 主 agent 图层 content 累计（position 注入依据，Task 2.1）：
                                # messages 模式 chunk 为主 agent 的模型输出（deepagents 子代理
                                # 内部输出不进入主 stream），content 追加到 _main_content_len，
                                # 供主 agent 图层工具调用 position 采集（触发瞬间已输出长度）。
                                _chunk_content = getattr(msg_obj, "content", None) or ""
                                if _chunk_content:
                                    self._main_content_len += len(_chunk_content)

                                # 检测到重复调用：中断当前流以注入警告
                                # break 退出 async for，由下方 aupdate_state 注入后重入
                                if local_pending_warnings:
                                    break

                                # 子代理重试指令注入：每轮 messages chunk 后非阻塞消费
                                # 重试队列，有指令时经 aupdate_state 追加 SystemMessage 到
                                # graph state 后重入 astream（与重复工具调用警告注入同机制）。
                                # 注意：审批中断（all_resume_values 非空）时流即将结束，
                                # 恢复由外层 Command(resume=...) 驱动，若此刻注入会丢失
                                # 恢复决策 → 跳过本轮，指令在恢复流中继续被消费。
                                pending_retries = self._drain_retry_instructions()
                                if pending_retries:
                                    if all_resume_values:
                                        logger.info(
                                            "[OfficialDeepAgent] 存在待恢复审批决策，"
                                            "重试指令延后到恢复流消费"
                                        )
                                    else:
                                        logger.info(
                                            f"[OfficialDeepAgent] 注入 {len(pending_retries)} "
                                            "条子代理重试指令到 agent 状态"
                                        )
                                        try:
                                            await self.graph.aupdate_state(
                                                config_arg,
                                                {"messages": pending_retries},
                                            )
                                        except Exception as e:
                                            logger.warning(
                                                f"[OfficialDeepAgent] 注入子代理重试指令失败: {e}"
                                            )
                                        retry_injected = True
                                        break

                        # 业务等待挂起：跳过重复警告/重试注入，直接退出 while True
                        if subagent_wait_suspend is not None:
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
                        # 子代理重试指令注入：break 退出 async for 后重入 astream
                        if retry_injected:
                            current_input = None  # 从当前 checkpoint 续流
                            continue
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

                # 业务等待挂起（spec D4）：父 Graph 暂停，返回 suspended 结果，
                # 由执行器注册 awaiter 并退出父协程（不 finalize、不 Command(resume)）。
                if subagent_wait_suspend is not None:
                    return {
                        "success": False,
                        "suspended": True,
                        "subagent_thread_id": subagent_wait_suspend["subagent_thread_id"],
                        "interrupt_id": subagent_wait_suspend["interrupt_id"],
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
            # Agent 图层嵌套规范 Task 1.5：子代理图层正文/思考携带到结果，
            # 由 research_runner 透传到 writeback_to_chat_message 落库
            if self.subagent_contents:
                result["subagent_contents"] = self.subagent_contents
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

    def _drain_retry_instructions(self) -> list[SystemMessage]:
        """非阻塞消费子代理重试指令队列，构造注入用的 SystemMessage 列表。

        由 astream chunk 循环在每轮 chunk 后调用；队列为空时返回空列表。
        指令内容携带目标子代理名称/调用链路/原始委派入参，主 agent 收到后
        以原始入参重新调用目标子代理（Task 3 单独重启失败子代理）。

        Returns:
            list[SystemMessage]：待注入 graph state 的重试指令消息
        """
        if self._retry_instruction_queue is None:
            return []
        messages: list[SystemMessage] = []
        while True:
            try:
                instruction = self._retry_instruction_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            messages.append(self._build_retry_system_message(instruction))
        return messages

    @staticmethod
    def _build_retry_system_message(instruction: dict) -> SystemMessage:
        """构造子代理重试指令 SystemMessage。

        Args:
            instruction: 重试指令 dict，字段：
                - agent_name: 目标子代理名称（agent_path 末位）
                - agent_path: 完整调用链路（如 ["main", "web-researcher"]）
                - tool_call_id: 失败子代理工具调用 ID
                - original_args: 子代理原始委派入参（task 工具参数）

        Returns:
            SystemMessage：注入 graph state 的重试指令（主 agent 下一轮 LLM 可见）
        """
        import json as _json

        agent_name = instruction.get("agent_name") or ""
        agent_path = instruction.get("agent_path") or []
        tool_call_id = instruction.get("tool_call_id") or ""
        original_args = instruction.get("original_args") or {}
        path_display = " → ".join(str(p) for p in agent_path) if agent_path else agent_name
        args_display = ""
        if isinstance(original_args, dict) and original_args:
            try:
                args_display = _json.dumps(original_args, ensure_ascii=False)
            except (TypeError, ValueError):
                args_display = str(original_args)
        content = (
            "【用户指令：重新执行失败的子代理】\n"
            f"目标子代理: {agent_name or 'unknown'}\n"
            f"调用链路: {path_display}\n"
            f"失败工具调用 ID: {tool_call_id or 'unknown'}\n"
            f"原始委派参数: {args_display or '（无，请根据当前研究进展重新委派）'}\n"
            "要求：立即通过 task 工具重新调用该子代理完成同一研究任务，"
            "执行过程中如遇工具失败请调整策略重试，完成后将结果汇总到最终报告。"
        )
        return SystemMessage(content=content)

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
        evt 中的 parent_tool_call_id / depth / agent_name / risk_ceiling
        （由 _on_tool_event 从 SubAgentToolEventMiddleware 透传）注册到 ToolCallContext，
        transition_async 从 context 透传到事件 payload，前端 ToolCallCard 可展示
        嵌套层级。主 agent 直接调用的工具不携带这些字段（evt 中无对应 key）。
        agent_path 不再注册/透传（spec REMOVED：agent_path 从事件 payload 与
        转发链中删除，路由唯一依据 subagent_thread_id）。

        Args:
            evt: extract_tool_events_from_message 返回的事件 dict，字段：
                - event_type: EventType (TOOL_CALL_PENDING / TOOL_CALL_COMPLETED / TOOL_CALL_FAILED)
                - tool_call_id: str
                - tool_name: str
                - parameters: dict
                - result: str (仅 COMPLETED)
                - error: str (仅 FAILED)
                - parent_tool_call_id/depth/agent_name/risk_ceiling/description:
                  子 agent 嵌套字段（可选，description 为任务目标描述）
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
            except Exception as e:
                logger.warning(
                    f"[OfficialDeepAgent] 读取 tool_call 参数兜底失败: "
                    f"tc_id={tool_call_id}, err={e}"
                )

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
        #    - message_id：chat 关联场景从当前 config 读取 assistant_message_id，
        #      使工具事件 payload 携带归属消息 ID。前端 toolCallsMap → message.toolCalls
        #      的归属依赖 messageBackendId，缺失时聊天深度研究模式的工具卡片会消失（根因修复）
        #    - 子 agent 嵌套层级字段：从 evt 提取（仅子 agent 工具事件携带）
        cross_module_id = derive_cross_module_id_from_source("deep_research", self.chat_session_id)

        # 从当前 langgraph 运行上下文读取 assistant_message_id（research_runner
        # 在 config.configurable 中注入；聊天关联场景非空，独立深度研究为空）
        _assistant_message_id = ""
        if self.chat_session_id:
            try:
                from langgraph.config import get_config as _get_config

                _cfg = _get_config()
                if isinstance(_cfg, dict):
                    _assistant_message_id = (
                        (_cfg.get("configurable") or {}).get("assistant_message_id") or ""
                    )
            except Exception:
                _assistant_message_id = ""
        if not _assistant_message_id:
            _assistant_message_id = getattr(self, "_assistant_message_id", "")

        # 提取子 agent 嵌套层级字段（Phase E3）
        # evt 中无对应 key 时使用默认空值（主 agent 场景）
        # agent_path 不再提取/注册（spec REMOVED：路由与累计唯一依据 subagent_thread_id）
        sub_parent_tool_call_id = evt.get("parent_tool_call_id", "") or ""
        sub_depth = evt.get("depth", 0)
        if not isinstance(sub_depth, int) or sub_depth < 0:
            sub_depth = 0
        sub_agent_name = evt.get("agent_name", "") or ""
        # risk_ceiling 统一转字符串（可能是 RiskLevel 枚举）
        sub_risk_ceiling_raw = evt.get("risk_ceiling")
        if sub_risk_ceiling_raw is not None and not isinstance(sub_risk_ceiling_raw, str):
            sub_risk_ceiling = (
                sub_risk_ceiling_raw.value if hasattr(sub_risk_ceiling_raw, "value") else str(sub_risk_ceiling_raw)
            )
        else:
            sub_risk_ceiling = sub_risk_ceiling_raw or ""
        # description：子 agent 角色描述（任务目标，Task 2.4），仅子 agent 事件携带
        sub_description = evt.get("description", "") or ""
        # subagent_thread_id：子代理 SSE 定向推送路由标识符（spec D10），
        # 由 SubAgentToolEventMiddleware 从 configurable 透传，仅子代理事件携带
        sub_subagent_thread_id = evt.get("subagent_thread_id", "") or ""

        try:
            service.register(
                ToolCallContext(
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    module=EventSource.DEEP_RESEARCH,
                    module_id=self.thread_id,
                    message_id=_assistant_message_id,
                    parameters=parameters,
                    cross_module_id=cross_module_id,
                    # 子 agent 嵌套层级字段（Phase E3）
                    parent_tool_call_id=sub_parent_tool_call_id,
                    depth=sub_depth,
                    agent_name=sub_agent_name,
                    risk_ceiling=sub_risk_ceiling,
                    description=sub_description,
                    # 子代理 SSE 定向推送路由标识符（spec D10）
                    subagent_thread_id=sub_subagent_thread_id,
                )
            )
        except Exception as e:
            logger.warning(f"[OfficialDeepAgent] 注册工具调用上下文失败 (tool={tool_name}, tc_id={tool_call_id}): {e}")

        # position 采集（Task 2.1，按图层局部化，单一权威来源）：
        # - 子代理（subagent_thread_id 非空，spec D1）：position = len(该子代理图层
        #   已转发正文 content 长度；subagent_contents 按 subagent_thread_id 键累计)
        # - 主 agent：position = len(主 agent 已输出 content 累计长度)
        # 经 service.bind_position 写入 context（仅补全空字段），后续状态事件不覆盖
        # （keep_existing + bind_position 双保险，D3 硬约束）。
        _position: int | None = None
        if sub_subagent_thread_id:
            _sub_entry = self.subagent_contents.get(sub_subagent_thread_id) or {}
            _position = len(_sub_entry.get("content") or "")
        else:
            _position = self._main_content_len
        if _position is not None:
            try:
                service.bind_position(tool_call_id, _position)
            except Exception as e:
                logger.warning(
                    f"[OfficialDeepAgent] 绑定 position 失败: tc_id={tool_call_id}, "
                    f"position={_position}, err={e}"
                )

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
