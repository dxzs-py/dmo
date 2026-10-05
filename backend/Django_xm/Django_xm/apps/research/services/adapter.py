"""官方 Deep Agent 适配器模块。

职责：
- ``OfficialDeepAgentAdapter``：将 ``create_deep_agent`` 返回的 CompiledStateGraph
  适配为上层统一接口（research/aresearch），
  使上层调用方无需关心底层实现差异。
  - 集成 interrupt 审批机制（astream_research_with_interrupts）
  - 集成韧性模块（retry/timeout/degrade/fallback）
  - 工具调用生命周期事件发布到实时频道
  - 降级时使用 original_tools / original_config 重建 graph

抽取自原 ``official_deep_agent.py``（Task 18.1）。
废弃的 ``DeepResearchAgent``（deep_agent.py）已删除，
本适配器是深度研究的唯一官方实现。

E6 三边界拆分（行为等价重构，audit B 组 §2.3）：
- ``result_extraction``：结果提取 + 磁盘扫描 + 短回复磁盘兜底（3 处重复参数化归一）。
- ``event_publishing``：实时事件发布（``DeepResearchEventPublisher``：
  工具生命周期 / 子代理转发回调 / 推理与正文 chunk 广播）。
- ``resume_protocol``：审批中断解析 / resume_values 分组 / 业务等待挂起判定。
本类保留：图执行三入口编排、韧性执行器装配、D4 预算挂载编排、
会话级累计容器（accumulated_reasoning/_main_content/subagent_contents/
_tool_entries——落库权威来源）。

依赖关系：
- 依赖 ``patches.py``：``_DeepAgentExecutor`` / ``_extract_ai_response``。
- 依赖同包拆分模块：``result_extraction`` / ``event_publishing`` / ``resume_protocol``。
- 被 ``builders.py`` 导入：``OfficialDeepAgentAdapter``（作为工厂函数返回类型）。
"""

from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from Django_xm.apps.ai_engine.config import AGENT_RECURSION_LIMIT_RESEARCH

# 子 agent 机制（deepagents 0.7.5 官方，取代旧 subagent_patch monkey-patch）：
# - 中断冒泡 + Command(resume) 恢复：0.7.5 原生支持，无需 checkpointer contextvar 注入。
# - 嵌套层级字段（depth/agent_path）：本模块在主 config 注入基础值（depth=0/agent_path=["main"]），
#   仅服务子代理 state.subagent_depth/subagent_path 展示元数据（SubAgentNestingMiddleware
#   写入 state）；事件路由与内容累计唯一依据为 subagent_thread_id（spec MODIFIED）。
# - 子 agent 工具事件：SubAgentToolEventMiddleware 从 configurable 读取本模块注入的
#   _on_tool_event 回调并转发到父 SSE 流。
from Django_xm.apps.core.logging_utils import get_logger
from Django_xm.apps.research.services.event_publishing import DeepResearchEventPublisher
from Django_xm.apps.research.services.resume_protocol import handle_resume_interrupt
from Django_xm.apps.research.services.result_extraction import (
    apply_short_report_disk_fallback,
    extract_files_info,
    scan_disk_files,
)
from Django_xm.apps.research.services.patches import (
    _DeepAgentExecutor,
    _extract_ai_response,
)

logger = get_logger(__name__)


class OfficialDeepAgentAdapter:
    """
    官方 Deep Agent 适配器

    将 create_deep_agent 返回的 CompiledStateGraph 适配为
    与 DeepResearchAgent 兼容的接口（research/aresearch），
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
        # 子代理图层正文/思考累计（spec MODIFIED：按 subagent_thread_id 键累计）：
        #   key = subagent_thread_id（SubAgentRuntime 适配器写入的路由标识符）
        #   value = {"content": str, "reasoning_content": str}
        # 由 _on_subagent_content 回调（SubAgentContentMiddleware 转发）累积；
        # 任务完成时由 research_runner 携带到 writeback，持久化到
        # ChatMessage.subagent_contents，供前端刷新后恢复子代理图层正文
        # （前端 subagentContents[subagentThreadId] 对齐）。
        self.subagent_contents: dict[str, dict[str, str]] = {}
        # 主 agent 图层累计正文（position 注入依据 + 过程信息展示权威源）：
        # astream messages 循环中 AIMessageChunk content 追加累计（与 chat 模块
        # content_state 同构）；工具调用发起点读 len(_main_content) 作为主 agent
        # 图层 position。跨审批 resume / 业务等待挂起持续累计（同一 adapter 复用），
        # 挂起/完成时随结果落库，供前端刷新后恢复主 agent 过程正文。
        self._main_content = ""
        # D4/P2-10：会话 Token 预算熔断标记（超限时由 _check_session_budget 置位，
        # 随 result.budget_exceeded 携带到上层）
        self._budget_exceeded: bool = False
        # 主/子代理统一工具条目会话级聚合（tool_call_id → entry）：落库权威来源。
        # 条目同时含主代理（depth=0）与子代理（含 subagent_thread_id/agent_name/depth
        # 图层字段）——ResearchTask.tool_calls 无写入点、ChatMessage.tool_calls 仅依赖
        # 审批重建路径落库会丢失图层字段且无审批的 SAFE 工具完全丢失
        # → 刷新后工具卡归集失败（乱序根源）。由 _publish_tool_event
        # （主/子 agent 工具事件统一入口）聚合，任务完成/挂起时经 research_runner
        # 携带到 writeback 合并进 ChatMessage.tool_calls。闭包跨审批 resume 持续
        # （同一 adapter 实例复用），增量合并（按 id）不丢历史。
        self._tool_entries: dict[str, dict] = {}
        # 工具事件唯一提取器（ToolEventExtractor，有状态）：跨 chunk / 跨审批 resume
        # 复用同一实例，内部持有 accumulated_messages / seen_tool_call_ids / 无 id
        # 暂存缓冲，保证同一 tool_call_id 的 PENDING 只发射一次（含 ToolMessage 阶段
        # 参数聚合后的补发语义）。恢复模式（服务重启/业务等待续跑）时向 seen 集合
        # 注入 checkpoint 已注册的 tool_call_id，避免 resume 后重复发射 PENDING。
        from Django_xm.apps.tools.tool_event_extractor import ToolEventExtractor

        self._tool_event_extractor = ToolEventExtractor()
        # E6 三边界拆分：实时事件发布器（工具生命周期/子代理转发/推理与正文广播）。
        # 持有本实例引用读写会话级累计容器；本类保留 _publish_tool_event
        # 实例级委托入口（测试注入缝，见该方法的 docstring）。
        self._event_publisher = DeepResearchEventPublisher(self)

    def research(
        self, query: str, config: dict[str, Any] | None = None, callbacks: list | None = None
    ) -> dict[str, Any]:
        logger.info(f"[OfficialDeepAgent] 同步研究: {query[:50]}...")

        if config is None:
            config = {}
        if "configurable" not in config:
            config["configurable"] = {}
        config["configurable"]["thread_id"] = self.thread_id
        # 深度研究多轮规划 + 子代理编排，链路更深（常量取值依据见 ai_engine/config.py）
        config.setdefault("recursion_limit", AGENT_RECURSION_LIMIT_RESEARCH)
        if callbacks:
            config["callbacks"] = callbacks

        try:
            result = self.graph.invoke(
                {"messages": [HumanMessage(content=query)]},
                config=config,
            )

            final_report = _extract_ai_response(result)
            files_info = extract_files_info(result)
            disk_files = scan_disk_files(self.work_dir)

            # 当 AI 回复过短时（通常是确认语），从磁盘文件提取完整报告
            # （E6 拆分：3 处重复兜底块参数化归一，见 result_extraction）
            final_report = apply_short_report_disk_fallback(final_report, disk_files)

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
        # 深度研究多轮规划 + 子代理编排，链路更深（常量取值依据见 ai_engine/config.py）
        config.setdefault("recursion_limit", AGENT_RECURSION_LIMIT_RESEARCH)
        if callbacks:
            config["callbacks"] = callbacks

        try:
            result = await self.graph.ainvoke(
                {"messages": [HumanMessage(content=query)]},
                config=config,
            )

            final_report = _extract_ai_response(result)
            files_info = extract_files_info(result)
            disk_files = scan_disk_files(self.work_dir)

            # 当 AI 回复过短时（通常是确认语），从磁盘文件提取完整报告
            # （E6 拆分：3 处重复兜底块参数化归一，见 result_extraction）
            final_report = apply_short_report_disk_fallback(final_report, disk_files)

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

        from langgraph.types import Command

        from Django_xm.apps.ai_engine.services.agent_resilience import (
            DuplicateToolCallDetector,
            ExecutionTimeoutManager,
            get_resilience_config,
        )

        # 工具事件提取与发布（与子 agent 事件转发路径一致）
        # - 提取：复用实例级 self._tool_event_extractor（有状态，跨 chunk/resume）
        # - 发布：经实例级 _publish_tool_event 入口 → event_publishing 发布边界
        #   （publish_tool_lifecycle_event 编排：状态机 transition + 条目聚合统一入口）
        from Django_xm.common.event_schema import EventType
        from Django_xm.common.execution_loop import run_astream_loop

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
        # 深度研究多轮规划 + 子代理编排，链路更深（常量取值依据见 ai_engine/config.py）
        config.setdefault("recursion_limit", AGENT_RECURSION_LIMIT_RESEARCH)
        if callbacks:
            config["callbacks"] = callbacks

        # 注入子代理/内容回调到 config["configurable"]（E6 拆分：回调构造移至
        # event_publishing 发布边界，闭包体原样迁移、签名与注入契约不变）：
        # - _on_tool_event：SubAgentToolEventMiddleware（子 agent middleware）从
        #   configurable 读取此回调，将子 agent 工具调用事件转发到父 SSE 流，
        #   父 config 经 langgraph ensure_config 自动传播到子 agent——回调构造
        #   evt dict 后经 self._publish_tool_event（实例级入口 → 发布边界）统一发布，
        #   与父 graph 的工具事件发布路径完全一致（service.transition_async）；
        #   回调签名 on_tool_event(event_type, tool_call_id, tool_name, **kwargs)，
        #   kwargs 可能包含 parameters / result / error / depth / risk_ceiling
        #   （agent_path 不再透传——spec REMOVED：路由唯一依据 subagent_thread_id）。
        event_publisher = self._event_publisher
        config["configurable"]["_on_tool_event"] = event_publisher.make_tool_event_callback()

        # - _on_subagent_content：SubAgentContentMiddleware（子 agent middleware）从
        #   configurable 读取此回调，在子代理每次模型调用后转发其正文与中间思考到
        #   父 SSE 流（Agent 图层嵌套），父 config 经 langgraph ensure_config 自动
        #   传播到子 agent——按 subagent_thread_id 累计（spec MODIFIED：废弃
        #   agent_path 拼接键，仅保留为展示元数据参数）+ msg_id 幂等去重（审批
        #   interrupt 恢复时 LangGraph 重放节点防重复/防错位）+
        #   STREAM_SUBAGENT_CONTENT 发布（data 不携带 agent_path，仅保留
        #   subagent_thread_id 定向路由；事件携带 message_id 定位归属消息）。
        config["configurable"]["_on_subagent_content"] = event_publisher.make_subagent_content_callback()
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

        # ── D4/P2-10：会话级 Token 硬预算守卫（与 chat 链 COST-01 同源对齐）──
        # 判定实现复用共享层 SessionTokenBudgetGuard（ai_engine/services/usage_tracker.py），
        # chat/research 不各自重建预算逻辑；挂载编排因两链事件/正文通道不同而各自实现
        # （chat 走 SSE chunk 事件，research 走 writeback 广播 + main_content 落库）。
        # session_id 归属：
        # - chat 关联场景（chat_session_id 非空）：归属父 chat 会话——基线取
        #   ChatMessage.token_count DB 聚合（get_session_total_tokens），深研自身
        #   消耗经 callbacks 上的 TokenUsageCallbackHandler 作为当前请求增量计入，
        #   即深研与父会话共享同一预算池（与 chat 主链子代理语义一致：子代理
        #   LLM 调用经 langgraph 继承 callbacks 同样汇入该 cb，无独立子预算）；
        # - 独立深度研究：chat_session_id 为空 → 回退 thread_id（research task_id，
        #   不存在于 ChatSession → DB 聚合基线为 0），预算约束本次执行的全部
        #   LLM 增量（主链 + 子代理）。
        # 预算未配置（session_token_budget<=0）时守卫自动不启用（enabled=False）。
        from Django_xm.apps.ai_engine.services.token_counter import TokenUsageCallbackHandler
        from Django_xm.apps.ai_engine.services.usage_tracker import (
            create_session_token_budget_guard,
        )

        budget_guard = create_session_token_budget_guard(self.chat_session_id or self.thread_id)
        # 当前请求 token 增量来源：research_runner.execute_research_async 传入的
        # TokenUsageCallbackHandler（挂在 config["callbacks"]，每轮 LLM 调用结束累加，
        # 子代理经 langgraph config 继承汇入同一 cb）。
        usage_cb = next(
            (c for c in (config.get("callbacks") or []) if isinstance(c, TokenUsageCallbackHandler)),
            None,
        )

        # 超预算收尾提示：与 chat 链 _BUDGET_EXCEEDED_NOTICE 同文案（loop.py:265），
        # research 侧追加到 self._main_content（经 result.main_content → writeback
        # 落库 ChatMessage.content / ResearchTask.content）。
        _BUDGET_EXCEEDED_NOTICE = "\n\n[系统提示] 本会话 Token 预算已耗尽，已自动停止本次生成。"

        async def _check_session_budget() -> dict[str, Any] | None:
            """检查会话 Token 硬预算，超限时发布事件并追加正文提示（对齐 chat COST-01）。

            Returns:
                超限时的预算 info dict；未超限/未启用/检查异常返回 None（不阻塞主流程）。
            """
            if not budget_guard.enabled:
                return None
            try:
                info = budget_guard.check(usage_cb.total_tokens if usage_cb is not None else 0)
            except Exception as e:
                logger.warning(f"[OfficialDeepAgent] 会话预算检查失败（忽略）: {e}")
                return None
            if not info.get("exceeded"):
                return None

            logger.warning(
                f"[OfficialDeepAgent] 会话 Token 预算耗尽，终止本轮研究: "
                f"task={self.thread_id}, total={info.get('total')}/{info.get('limit')}"
            )
            # 发布 budget_exceeded 事件（session/task 双频道路由与正文广播一致；
            # 前端未监听不影响熔断；发布失败不阻断终止）
            try:
                from Django_xm.common.event_schema import EventSource
                from Django_xm.common.realtime_events import publish_event

                await publish_event(
                    EventType.BUDGET_EXCEEDED,
                    {
                        "source": EventSource.DEEP_RESEARCH,
                        "source_id": self.thread_id,
                        "session_id": self.chat_session_id or "",
                        "data": {
                            "session_id": self.chat_session_id or self.thread_id,
                            "used_tokens": info.get("total"),
                            "budget_limit": info.get("limit"),
                        },
                    },
                    session_id=self.chat_session_id or None,
                    task_id=self.thread_id,
                )
            except Exception:
                logger.exception("[OfficialDeepAgent] budget_exceeded 事件发布失败（熔断继续）")
            # 正文追加系统提示并广播（随 result.main_content 落库，对齐 chat
            # "正文追加系统提示 → finalizer 持久化"语义）
            self._main_content += _BUDGET_EXCEEDED_NOTICE
            self._budget_exceeded = True
            try:
                from Django_xm.apps.research.services.writeback import broadcast_stream_content

                _cfg_budget = config.get("configurable") or {}
                broadcast_stream_content(
                    session_id=self.chat_session_id,
                    task_id=self.thread_id,
                    message_id=_cfg_budget.get("assistant_message_id")
                    or getattr(self, "_assistant_message_id", ""),
                    content=self._main_content,
                )
            except Exception as _budget_broadcast_err:
                logger.warning(
                    f"[OfficialDeepAgent] 预算提示正文广播失败（熔断继续）: {_budget_broadcast_err}"
                )
            return info

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
            # 工具事件提取状态由实例级 self._tool_event_extractor 持有
            # （跨 chunk / 跨审批 resume 复用）；此处不再维护局部 seen/accumulated 容器。
            # 恢复模式预热已见集合（B7）：新建 adapter（服务重启/业务等待续跑）内存态从空
            # 开始，从 checkpoint state 提取已注册的 tool_call_id 注入 extractor.seen，
            # 避免 ToolMessage 阶段触发 PENDING 补发导致非法状态转换 WARNING
            # （同一 adapter 续跑时幂等，无副作用）。
            if resume_command is not None:
                try:
                    state = await self.graph.aget_state(config)
                    if state and hasattr(state, "values") and state.values:
                        from langchain_core.messages import AIMessage

                        _seen = self._tool_event_extractor.seen_tool_call_ids
                        _preheat_new = 0
                        for msg in state.values.get("messages", []) or []:
                            if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
                                for tc in msg.tool_calls:
                                    tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
                                    if tc_id and tc_id not in _seen:
                                        _seen.add(tc_id)
                                        _preheat_new += 1
                        logger.info(
                            f"[OfficialDeepAgent] 恢复模式预热 seen_tool_call_ids: "
                            f"count={len(_seen)}, new={_preheat_new}"
                        )
                except Exception as e:
                    logger.warning(f"[OfficialDeepAgent] 预热 seen_tool_call_ids 失败(非致命): {e}")
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
                # 基于公共执行循环骨架 run_astream_loop（spec D8）：
                # - messages 模式 → _process_chunk（工具事件提取 + 韧性检测）
                # - updates 模式 → _handle_updates（interrupt 审批 / 业务等待挂起）
                # - 重复调用警告 / 子代理重试指令注入由公共循环统一承载
                async def loop_fn(
                    _agent,
                    graph_input_arg,
                    config_arg,
                    _ctx,
                    _strategy,
                    _data,
                ):
                    nonlocal subagent_wait_suspend

                    # messages 模式 chunk 处理器（公共循环注入）
                    async def _process_chunk(mode_data, control, resume_values):
                        # astream 在 messages 模式下产出 (message, metadata) 元组，
                        # deepagents astream 只产出 AIMessageChunk（非完整 AIMessage），
                        # 需要复用 tool_event_extractor 公共模块聚合 tool_call_chunks，
                        # 在 ToolMessage 阶段补发完整 parameters，避免前端显示为 {}。
                        msg_obj = (
                            mode_data[0] if isinstance(mode_data, tuple) and len(mode_data) == 2 else mode_data
                        )
                        try:
                            # 实例级有状态提取器：跨 chunk 聚合 args、跨审批 resume 共享
                            # seen 集合，行为与原局部容器提取完全一致（含无 id 暂存）。
                            tool_events = self._tool_event_extractor.feed(msg_obj)
                        except Exception as e:
                            logger.warning(f"[OfficialDeepAgent] 工具事件提取失败: {e}")
                            tool_events = []

                        for evt in tool_events:
                            # 重复工具调用检测：仅对 PENDING 事件记录，
                            # 避免对同一 tool_call_id 的 COMPLETED/FAILED 重复计数
                            if evt.get("event_type") == EventType.TOOL_CALL_PENDING:
                                # D4/P2-10：每轮工具调用前检查会话 Token 硬预算
                                # （与 chat loop.py 同语义——工具循环自我膨胀的
                                # 服务端刹车；超限置 should_break 后不再发布该
                                # 工具事件，公共循环立即中断 astream）
                                if await _check_session_budget() is not None:
                                    control.should_break = True
                                    return
                                warning = duplicate_detector.record(
                                    evt.get("tool_name", "unknown"),
                                    evt.get("parameters") or {},
                                )
                                if warning is not None:
                                    control.warnings.append(SystemMessage(content=warning.to_prompt()))
                            await self._publish_tool_event(evt)

                        # 捕获 LLM thinking/reasoning 与主 agent 正文累计广播
                        # （E6 拆分：两段广播体原样迁移至 event_publishing 发布边界，
                        # 先推理后正文的段序保持；累计容器仍在实例上）
                        event_publisher.handle_reasoning_chunk(msg_obj, config_arg)
                        event_publisher.handle_content_chunk(msg_obj, config_arg)

                        # 检测到重复调用：中断当前流以注入警告（公共循环据
                        # control.warnings 中断 astream 并注入后重入）
                        if control.warnings:
                            return

                        # D4/P2-10：本轮 LLM 调用结束（流式末尾 chunk 携带
                        # usage_metadata）后检查会话预算（与 chat loop.py 同语义）
                        if getattr(msg_obj, "usage_metadata", None) is not None:
                            if await _check_session_budget() is not None:
                                control.should_break = True
                                return

                        # 公共循环契约：process_chunk 必须是 async generator
                        # （execution_loop 以 async for 迭代，chat 模块同构）。
                        # research 工具/推理事件直接发布到实时频道，此处不产出
                        # 事件，仅以 yield 标记为 async generator 防止协程被误迭代
                        # （否则 TypeError: got coroutine）。
                        yield

                    # updates 模式处理器（公共循环注入）
                    async def _handle_updates(mode_data, resume_values, suspend_box, control):
                        # 审批中断解析/决策收集与业务等待挂起判定（E6 拆分）：
                        # 协议体原样迁移至 resume_protocol.handle_resume_interrupt，
                        # 本协程仅遍历 interrupt 条目并回填 suspend_box（公共循环
                        # 契约不变：async generator + 写入 resume_values/suspend_box）。
                        if isinstance(mode_data, dict) and "__interrupt__" in mode_data:
                            interrupts = mode_data["__interrupt__"]
                            if interrupts:
                                for intr in interrupts:
                                    suspend_info = await handle_resume_interrupt(
                                        intr,
                                        on_interrupt=on_interrupt,
                                        timeout_mgr=timeout_mgr,
                                        resume_values=resume_values,
                                    )
                                    if suspend_info is not None:
                                        suspend_box["suspend"] = suspend_info
                                        return

                        # 公共循环契约：handle_updates 必须是 async generator
                        # （execution_loop 以 async for 迭代）。审批/业务等待处理
                        # 直接写入 resume_values/suspend_box，此处不产出事件，
                        # 仅以 yield 标记为 async generator（防 got coroutine）。
                        yield

                    # 注入韧性消息到 graph state（重复调用警告 / 子代理重试指令）
                    async def _inject_state_messages(messages):
                        await self.graph.aupdate_state(config_arg, {"messages": messages})

                    suspend_box: dict = {}
                    async for _event in run_astream_loop(
                        graph=self.graph,
                        graph_input=graph_input_arg,
                        config=config_arg,
                        process_chunk=_process_chunk,
                        handle_updates=_handle_updates,
                        resume_values=all_resume_values,
                        suspend_box=suspend_box,
                        timeout_mgr=timeout_mgr,
                        check_soft_timeout=True,
                        inject_state_messages=_inject_state_messages,
                    ):
                        # 公共循环不产出事件（research 工具/推理事件直接发布到实时频道）
                        pass

                    if suspend_box.get("suspend"):
                        subagent_wait_suspend = suspend_box["suspend"]

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
                        "subagent_thread_ids": subagent_wait_suspend["subagent_thread_ids"],
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
            files_info = extract_files_info(accumulated_result)
            disk_files = scan_disk_files(self.work_dir)

            # 当 AI 回复过短时，从磁盘文件提取完整报告
            # （E6 拆分：3 处重复兜底块参数化归一，见 result_extraction）
            final_report = apply_short_report_disk_fallback(final_report, disk_files)

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
            # 主 agent 累计正文携带到结果：由 research_runner 透传到 writeback
            # 落库（ChatMessage.content / ResearchTask.content），供前端刷新后
            # 恢复主 agent 过程正文（position 内联对齐的权威正文）。
            result["main_content"] = self._main_content
            # D4/P2-10：预算熔断标记（对齐 chat data["_budget_exceeded"]），
            # 供上层（research_runner/session_executor）感知非正常终止原因。
            if self._budget_exceeded:
                result["budget_exceeded"] = True
            # Agent 图层嵌套规范 Task 1.5：子代理图层正文/思考携带到结果，
            # 由 research_runner 透传到 writeback_to_chat_message 落库
            if self.subagent_contents:
                result["subagent_contents"] = self.subagent_contents
            # 主/子代理统一工具条目聚合携带到结果：由 research_runner 透传到
            # writeback 合并进 ChatMessage.tool_calls（图层字段贯通，乱序根源修复）
            if self._tool_entries:
                result["tool_entries"] = self._tool_entries
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

    async def _publish_tool_event(self, evt: dict[str, Any]) -> None:
        """发布单个工具调用生命周期事件到实时频道（E6 拆分：实现委托发布边界）。

        保留实例级入口的原因：
        - test_session_budget 等测试以实例属性替换本方法作为工具事件注入缝；
        - 子代理回调（event_publishing.make_tool_event_callback）与 _process_chunk
          均经该入口发布，实例属性查找保证替换全局生效。

        实现原样迁移至 ``DeepResearchEventPublisher.publish_tool_event``
        （event_publishing.py）：收敛历史内联四步（register → bind_position →
        transition_async → aggregate_tool_entry）为单次 ``publish_tool_lifecycle_event``
        调用，与 chat 主链路 / chat 子代理共享同一编排实现（跨模块路由、
        position 绑定、条目聚合、参数兜底、PENDING 补发防护）——payload 形状
        与发布顺序不变。
        """
        await self._event_publisher.publish_tool_event(evt)

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

    def get_status(self) -> dict[str, Any]:
        return {
            "thread_id": self.thread_id,
            "engine": "deepagents_official",
        }
