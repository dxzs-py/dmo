"""chat agent 流式执行编排域（阶段 5 自 chat_service 拆分，行为等价纯移动）。

职责（单一变化方向）：agent 模式流式聊天的阶段编排——工具/研究上下文准备、
Agent 与 graph_input 构造、创建级模型降级检测、StreamContext 初始化、
降级 agent 预创建、AgentExecutor 装配与执行、finalize 收尾。

拆分形式（patch 面约束定形，见 .trae/memory/2026-10-06.md 阶段 5 节）：
ChatService 经 mixin 继承承载本域方法——类级 patch
``mock.patch.object(ChatService, "_process_normal_stream_chat", ...)`` 在
ChatService 类上设置属性即可 shadow mixin 同名方法（MRO 先命中类自身），
既有测试锚定零破坏。

**留在 chat_service.py 的方法体**（模块命名空间 patch 约束）：
- ``_build_stream_agent_and_input``：方法体引用 ``convert_chat_history``
  （test_context_engineering_off_loop:196 patch 打在 chat_service 模块
  globals，名字解析在定义模块）。
跨域调用一律经 ``self.``（运行期属性查找），本模块不 import chat_service（零循环）。
"""

import asyncio
import logging
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any

from asgiref.sync import sync_to_async

from Django_xm.apps.agent_hub.services.agent_executor import AgentExecutor
from Django_xm.apps.ai_engine.config import AGENT_RECURSION_LIMIT_CHAT
from Django_xm.apps.ai_engine.services.agent_resilience import (
    DegradationLevel,
    get_degraded_tools,
)
from Django_xm.apps.ai_engine.services.cost_tracker import TokenDetailTracker
from Django_xm.apps.chat.services.stream import (
    DeepThinkingStreamStrategy,
    FallbackStreamService,
    NormalStreamStrategy,
    StreamContext,
    finalize_stream,
    run_stream_loop,
)

from .stream_helpers import sync_usage_tracker_model

logger = logging.getLogger(__name__)


@dataclass
class _StreamExecutionPlan:
    """流式执行计划（阶段间传递的执行要素打包，避免长参数列表）。"""

    agent: Any
    graph_input: Any
    config: Any
    ctx: StreamContext
    strategy: Any
    tools: list
    model_instance: Any
    provider_id: str | None
    model_name: str | None


class AgentStreamMixin:
    """ChatService agent 流式执行编排域 mixin（方法体自 chat_service.py 原样迁入）。"""

    async def _process_normal_stream_chat(
        self,
        data: dict[str, Any],
        usage_tracker,
        token_detail_tracker: TokenDetailTracker | None = None,
        interrupt_handler=None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """普通/agent 模式流式聊天：阶段编排（实现拆分至下方私有方法）。"""
        tools = await self._get_tools(data)
        tool_config, research_context = await self._prepare_stream_tool_and_research(data)
        agent, graph_input, config, agent_config = await self._build_stream_agent_and_input(
            data, tool_config, tools, research_context
        )
        # 4.4/4.8：上下文工程降级事件冲刷——_build_stream_agent_and_input 非
        # checkpointer 分支新追加的 _stream_degradation_events（模式层的冲刷
        # 发生在本方法之前，此处补冲刷），在流式输出前以 degradation 事件广播
        for degradation in data.pop("_stream_degradation_events", []):
            yield {"type": "degradation", "data": degradation}
        # 模型已由 AgentFactory 经 get_chat_model(enable_fallback=True) 解析（写入 config.model）；
        # 此处检测创建级降级（用户所选模型不可用已被提升为 fallback 候选）并向前端发送提示
        model_instance, provider_id, model_name, fallback_events = (
            await self._detect_model_creation_fallback(data, agent_config.model, usage_tracker)
        )
        for _fallback_event in fallback_events:
            yield _fallback_event

        ctx = self._init_stream_context(data)
        strategy = DeepThinkingStreamStrategy() if data.get("_enable_deep_thinking") else NormalStreamStrategy()
        plan = _StreamExecutionPlan(
            agent=agent, graph_input=graph_input, config=config, ctx=ctx, strategy=strategy,
            tools=tools, model_instance=model_instance, provider_id=provider_id, model_name=model_name,
        )
        degraded_agent_holder = await self._precreate_degraded_agent(data, tool_config, tools)
        rebuild_agent_fn = self._make_rebuild_agent_fn(degraded_agent_holder)
        executor = self._build_stream_executor(
            data, tools, model_instance, rebuild_agent_fn, usage_tracker, token_detail_tracker
        )
        cb_holder: dict[str, Any] = {}
        async for event in self._run_stream_execution(plan, data, executor, interrupt_handler, cb_holder):
            yield event
        async for event in self._finalize_stream_or_skip(
            plan, data, cb_holder, usage_tracker, token_detail_tracker
        ):
            yield event

    async def _detect_model_creation_fallback(
        self, data: dict[str, Any], model_instance, usage_tracker=None
    ) -> tuple[Any, str | None, str | None, list[dict[str, Any]]]:
        """检测创建级模型降级：对比用户请求模型与 AgentFactory 实际创建的主模型。

        模型实例由 AgentFactory 统一经 get_chat_model(enable_fallback=True) 创建
        （用户所选模型不可用时已自动提升 fallback 候选为主模型），本方法仅检测
        该创建级降级并生成 model_fallback 事件提示前端，不创建任何模型。

        检测到降级时同步 usage_tracker.model_id 为 actual（F2-1：context 事件与
        ChatMessage.model 落库统一记实际模型，与前端 modelStore 同步值一致）。

        返回 (model_instance, provider_id, model_name, fallback_events)；
        fallback_events（model_fallback 事件）由主流程按序 yield。
        """
        provider_id = data.get("provider_id")
        model_name = data.get("model_name")
        fallback_events: list[dict[str, Any]] = []

        # 检测创建级降级：用户所选模型不可用，实际主模型已切换为 fallback 候选
        if model_instance is not None and provider_id:
            try:
                # LazyFallbackChatModel / RunnableWithFallbacks 包装了底层模型，需要从 .bound 获取
                bound_model = getattr(model_instance, "bound", model_instance)
                actual_provider = getattr(bound_model, "_provider_id", None)
                actual_model = getattr(bound_model, "model_name", None) or getattr(bound_model, "model", None)
                if actual_provider and actual_provider != provider_id:
                    fallback_events.append(
                        {
                            "type": "model_fallback",
                            "data": {
                                "original_provider": provider_id,
                                "original_model": model_name,
                                "actual_provider": actual_provider,
                                "actual_model": actual_model,
                                "message": (
                                    f"模型 {provider_id}/{model_name} 不可用，"
                                    f"已自动切换到 {actual_provider}/{actual_model}"
                                ),
                            },
                        }
                    )
                    # F2-1：usage/context/ChatMessage.model 统一记实际模型（先于事件 yield）
                    sync_usage_tracker_model(usage_tracker, fallback_events[0].get("data"))
                    # 自动更新 SystemConfig
                    try:
                        from Django_xm.apps.ai_engine.models import SystemConfig

                        SystemConfig.set_value(
                            "default_chat_model",
                            {
                                "provider_id": actual_provider,
                                "model_name": actual_model,
                            },
                        )
                    except Exception:
                        # 持久化 fallback 配置失败不影响当前会话，运行时已切换
                        logger.debug("持久化模型 fallback 配置到 SystemConfig 失败")
            except Exception:
                # fallback 检测失败不影响主流程，继续使用原模型
                logger.debug("检测 LLM 降级失败", exc_info=True)

        return model_instance, provider_id, model_name, fallback_events

    async def _prepare_stream_tool_and_research(
        self, data: dict[str, Any]
    ) -> tuple[dict[str, Any], Any]:
        """工具配置与研究上下文准备（研究上下文注入 system_prompt）。"""
        tool_config = self._build_tool_config(data)

        # 在创建 agent 之前加载研究上下文，以便注入到 system_prompt
        research_context = await sync_to_async(self._load_research_context)(
            data.get("research_task_id", ""), user_id=self.user_id, session_id=data.get("session_id")
        )
        if research_context:
            # 将研究上下文注入到 system_prompt，确保每次对话都能看到
            data["_research_system_prompt"] = (
                "## 深度研究参考内容\n\n"
                "以下是用户之前完成的深度研究报告，请在回答时参考这些信息：\n\n"
                f"{research_context}"
            )
            data["_has_research_context"] = True

        return tool_config, research_context

    async def _auto_reject_pending_interrupts(self, agent, thread_config, session_id: str) -> None:
        """检查并自动拒绝 checkpoint 中的 pending interrupt。

        session_id 为审批行定位键（Approval.source_id，与创建点 ctx.session_id
        同源，见 _finalize_superseded_approvals），自调用点 data["session_id"]
        透传（阶段 7 票①：原 self.thread_id 依赖已移除）。
        """
        try:
            state = await agent.graph.aget_state(thread_config)
            if state and state.tasks:
                from langgraph.types import Command as LgCommand

                for task in state.tasks:
                    if hasattr(task, "interrupts") and task.interrupts:
                        for intr in task.interrupts:
                            intr_id = intr.id if hasattr(intr, "id") else ""
                            if intr_id:
                                await agent.graph.ainvoke(
                                    LgCommand(resume={intr_id: False}),
                                    config=thread_config,
                                )
                                # B4 配套：自动拒绝后终态化对应审批行（按
                                # langgraph_resume_id 定位），否则该批次永远
                                # PENDING——审批恢复扫描
                                # （SessionManager._recover_chat_approvals）会
                                # 反复重建已向前推进的会话。语义为 TIMEOUT
                                # （用户未决策，会话已被新消息取代），非用户
                                # 主动拒绝。
                                try:
                                    await sync_to_async(
                                        self._finalize_superseded_approvals
                                    )(intr_id, session_id)
                                except Exception:
                                    logger.warning(
                                        f"终态化被取代审批行失败（非致命）: intr_id={intr_id}",
                                        exc_info=True,
                                    )
                                logger.info(f"自动拒绝 pending interrupt: {intr_id}（用户发送了新消息）")
        except Exception as e:
            logger.warning(f"检查/清理 pending interrupt 失败（非致命）: {e}")

    def _finalize_superseded_approvals(self, langgraph_resume_id: str, session_id: str) -> int:
        """终态化被自动拒绝 interrupt 对应的 PENDING 审批行（B4 配套，TIMEOUT 语义）。

        定位键与审批创建点同源（阶段 7 票①）：Approval(source=chat).source_id
        存 chat session_id——创建点 stream/loop.py request_approval_async 以
        ctx.session_id（=data["session_id"]）作 source_id；恢复扫描
        SessionManager._recover_chat_approvals 以 source_id 作会话键与
        ChatSession.session_id 对账。非 graph thread_id（agent_service 在空
        session 时 thread_id 兜底 uuid4，与 source_id="" 不同源）。历史实现
        引用 self.thread_id（ChatService 从未赋值）→ AttributeError 被调用点
        吞掉，审批行永挂 PENDING → 恢复扫描反复重建执行器。
        """
        from Django_xm.apps.approvals.models import Approval
        from Django_xm.apps.approvals.services.approval_service import timeout_approval

        stale_ids = list(
            Approval.objects.filter(
                source=Approval.Source.CHAT,
                source_id=session_id,
                extra__langgraph_resume_id=langgraph_resume_id,
                state=Approval.State.PENDING,
            ).values_list("interrupt_id", flat=True)
        )
        for interrupt_id in stale_ids:
            timeout_approval(interrupt_id, dispatch_resume=False)
        return len(stale_ids)

    def _init_stream_context(self, data: dict[str, Any]) -> StreamContext:
        """创建 StreamContext（流式可变状态封装）并注入会话定位字段。"""
        ctx = StreamContext()
        ctx.init_stream_state(data.get("_stream_state"))
        # 业务等待恢复模式：以挂起前已持久化的 content 作为 ctx 累积基线，
        # 使恢复轮最终 content = 基线 + 新段（与 content_state 基线一致），
        # 最终落库与建议生成均基于完整累积正文。
        if data.get("resume_content"):
            ctx.current_message_content = data["resume_content"]
        # P25修复：注入 session_id / message_id，供 _handle_updates_chunk
        # 创建 Approval DB 记录时使用
        ctx.session_id = data.get("session_id", "")
        ctx.message_id = str(data.get("_assistant_message_id") or data.get("message_id", ""))
        # 暴露 ctx 供执行服务 finalize 阶段读取（落库 content/tool_calls）
        data["_chat_ctx"] = ctx
        return ctx

    async def _precreate_degraded_agent(
        self,
        data: dict[str, Any],
        tool_config: dict[str, Any],
        tools: list,
    ) -> dict[str, Any]:
        """预创建降级 agent（用于 AgentExecutor 的 DEGRADE 分支）。

        AgentExecutor.rebuild_agent_fn 是同步回调，无法 await 异步 create_agent_with_memory，
        因此在主事件循环中预创建降级 agent（使用相同的降级工具集），
        rebuild_agent_fn 仅返回预创建的实例。若预创建失败，DEGRADE 将落入 FALLBACK。
        """
        degraded_tools_preview = get_degraded_tools(tools, DegradationLevel.REDUCED_TOOLS)
        degraded_agent_holder: dict[str, Any] = {}
        if tools and degraded_tools_preview and len(degraded_tools_preview) < len(tools):
            try:
                deg_agent, deg_config, _, _ = await asyncio.wait_for(
                    self._create_agent_with_memory(
                        data,
                        prompt_mode=data["mode"],
                        tool_config=tool_config,
                        tools=degraded_tools_preview,
                    ),
                    timeout=10.0,
                )
                if deg_config is None:
                    # chat 单轮对话：工具循环步数适中（常量取值依据见 ai_engine/config.py）
                    deg_config = {"recursion_limit": AGENT_RECURSION_LIMIT_CHAT}
                degraded_agent_holder["agent"] = deg_agent
                degraded_agent_holder["config"] = deg_config
            except TimeoutError:
                logger.warning("预创建降级 agent 超时（DEGRADE 将落入 FALLBACK）")
            except Exception as e:
                logger.warning(f"预创建降级 agent 失败（DEGRADE 将落入 FALLBACK）: {e}")
        return degraded_agent_holder

    def _make_rebuild_agent_fn(self, degraded_agent_holder: dict[str, Any]):
        """构造 AgentExecutor 降级回调（闭包仅捕获 degraded_agent_holder）。"""

        def rebuild_agent_fn(_degraded_tools: list) -> tuple[Any, dict]:
            """AgentExecutor 降级回调：返回预创建的降级 agent

            AgentExecutor 内部已通过 get_degraded_tools 计算降级工具集，
            并将其传入此回调。我们使用预创建的降级 agent（基于相同的降级工具集）。
            若预创建失败，抛异常使 AgentExecutor 落入 FALLBACK。
            """
            if "agent" not in degraded_agent_holder:
                raise RuntimeError("降级 agent 预创建失败或未创建")
            return degraded_agent_holder["agent"], degraded_agent_holder["config"]

        return rebuild_agent_fn

    def _build_stream_executor(
        self,
        data: dict[str, Any],
        tools: list,
        model_instance,
        rebuild_agent_fn,
        usage_tracker,
        token_detail_tracker,
    ) -> AgentExecutor:
        """创建 AgentExecutor（公共执行器，提供重试/降级/超时/回退）。

        替代原内联的 retry/timeout/degrade 循环。
        """
        # 创建无工具回退服务（AgentExecutor 的 FALLBACK 分支使用）
        fallback_service = FallbackStreamService(self)

        return AgentExecutor(
            fallback_service=fallback_service,
            rebuild_agent_fn=rebuild_agent_fn,
            tools=tools,
            model_instance=model_instance,
            data=data,
            usage_tracker=usage_tracker,
            token_detail_tracker=token_detail_tracker,
        )

    async def _run_stream_execution(
        self,
        plan: _StreamExecutionPlan,
        data: dict[str, Any],
        executor: AgentExecutor,
        interrupt_handler,
        cb_holder: dict[str, Any],
    ) -> AsyncGenerator[dict[str, Any], None]:
        """装配回调并执行 AgentExecutor 流式循环（cb/fb_callback 经 cb_holder 传出供 finalize 复用）。"""
        from Django_xm.apps.ai_engine.services.token_counter import TokenUsageCallbackHandler

        with TokenUsageCallbackHandler() as cb:
            from Django_xm.apps.ai_engine.services.llm_fallback import FallbackDetectionCallback

            # E11 附带核实（N-6 关联面）：请求未带 provider_id 时 plan.provider_id 为
            # None，expected_provider 落空串 → FallbackDetectionCallback 检测整体关闭
            # （on_llm_end 以 expected_provider 非空为闸）。补 bound_model 兜底，
            # 与 fallback.stream_without_tools（fallback.py 同构段）对齐。
            bound_model = getattr(plan.model_instance, "bound", plan.model_instance)
            fb_callback = FallbackDetectionCallback(
                expected_provider=plan.provider_id
                or getattr(bound_model, "_provider_id", "")
                or "",
                expected_model=plan.model_name
                or getattr(bound_model, "model_name", "")
                or getattr(bound_model, "model", "")
                or "",
            )
            plan.config["callbacks"] = [cb, fb_callback]
            cb_holder["cb"] = cb
            cb_holder["fb_callback"] = fb_callback

            # 主/子代理判定与事件路由（fix-deep-research-subagent-activation spec D1/D2）
            # 1) 主 agent configurable 不注入 subagent_thread_id（子代理唯一判定依据，
            #    仅 SubAgentRuntime 适配器 spawn 时写入）；depth/agent_path 仅为子代理
            #    spawn 提供父层级基础值（适配器据此计算绝对深度），主 agent 判定
            #    不依赖它们（SubAgentNestingMiddleware 恒写主 agent subagent_depth=0）。
            # 2) _on_tool_event：仅子代理工具事件转发回调（SubAgentToolEventMiddleware
            #    仅 subagent_thread_id 非空转发）；主 agent 工具事件由主执行链路唯一
            #    发布与持久化（spec D2 单路径，禁止双路径发布）。
            # 3) _on_subagent_content：仅子代理正文/思考转发（SubAgentContentMiddleware，
            #    同 subagent_thread_id 判定），按 subagent_thread_id 键累计到
            #    data["_subagent_contents"]（供 tool position 采集 + 最终落库，
            #    spec MODIFIED：废弃 agent_path 累计键）并发布 STREAM_SUBAGENT_CONTENT
            #    事件（携带 subagent_thread_id 定向路由到子代理卡片）。
            self._inject_main_agent_configurable(plan.config, data, plan.tools)
            state = self._init_subagent_stream_state(data)
            self._wire_subagent_callbacks(plan.config, data, state)

            # 执行 AgentExecutor（带韧性的流式执行）
            # 替代原内联的 retry/timeout/degrade 循环：
            # - GraphRecursionError → 优雅降级（用已收集内容），不重试
            # - 可恢复异常 → RETRY + 退避
            # - 不可恢复但可降级 → DEGRADE（用减少的工具重试，由 rebuild_agent_fn 重建）
            # - 不可恢复 → FALLBACK（无工具纯对话，由 fallback_service 处理）
            # - soft timeout → 警告一次
            # - hard timeout → FALLBACK
            # 单协程挂起模式（interrupt_handler 非 None）：run_stream_loop 内部
            # 在审批中断时挂起等待信令，Command(resume) 重入，AgentExecutor 韧性
            # 在整个会话生命周期持续生效。
            # 统一经 run_stream_loop 接入公共执行循环骨架（spec D8），并注入
            # AgentExecutor 的超时管理器与重复调用检测器（chat 补齐韧性）。
            async def _loop_fn(_agent, _graph_input, _config, _ctx, _strategy, _data):
                async for _event in run_stream_loop(
                    _agent,
                    _graph_input,
                    _config,
                    _ctx,
                    _strategy,
                    _data,
                    interrupt_handler=interrupt_handler,
                    timeout_mgr=executor.timeout_mgr,
                    duplicate_detector=executor.duplicate_detector,
                ):
                    yield _event

            loop_fn = _loop_fn

            async for event in executor.run(
                loop_fn,
                plan.agent,
                plan.graph_input,
                plan.config,
                plan.ctx,
                plan.strategy,
                data,
            ):
                yield event

    def _inject_main_agent_configurable(
        self,
        config: dict[str, Any],
        data: dict[str, Any],
        tools: list,
    ) -> None:
        """主 agent configurable 基础字段注入（事件路由判定依据，见上方 spec D1/D2 注释）。"""
        if isinstance(config, dict):
            config.setdefault("configurable", {})
            if "depth" not in config["configurable"]:
                config["configurable"]["depth"] = 0
            if "agent_path" not in config["configurable"]:
                config["configurable"]["agent_path"] = ["main"]
            # 父工具集与运行配置显式注入 configurable（spawn_sub_agent 继承工具集、
            # filesystem 解析落盘目录）。替代历史 get_parent_tool_context 全局旁路：
            # 子代理经 langgraph_adapter._build_configurable 写入自身工具集后逐层
            # 传递，任意深度嵌套不再依赖外部注册时机，也无需注册/清除。
            config["configurable"]["tool_names"] = [
                getattr(t, "name", "") for t in (tools or [])
            ]
            config["configurable"]["user_id"] = self.user_id
            config["configurable"]["session_id"] = data.get("session_id")
            config["configurable"]["model_name"] = data.get("model")
            config["configurable"]["store"] = data.get("store")
            # 深度思考开关（实际生效值，已含模型能力判定）
            config["configurable"]["enable_deep_thinking"] = bool(
                data.get("_enable_deep_thinking", False)
            )
            config["configurable"]["use_web_search"] = bool(
                data.get("use_web_search", False)
            )
            config["configurable"]["use_mcp"] = data.get("use_mcp", False)

    async def _finalize_stream_or_skip(
        self,
        plan: _StreamExecutionPlan,
        data: dict[str, Any],
        cb_holder: dict[str, Any],
        usage_tracker,
        token_detail_tracker,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """finalize 收尾：业务等待挂起时跳过，否则委托 finalize_stream。"""
        # 终态边界冲刷（阶段 2，D2 边界红线同构）：业务等待挂起/审批中断/
        # 正常收尾都会进入本方法，先冲刷全部子代理正文节流窗口——pending
        # 若不冲刷将随流结束永久丢失（最终态必须送达；幂等）。
        await self._flush_subagent_content_throttles(data)
        # 业务等待挂起（wait_for_subagent，spec D4）：跳过 finalize（补全检查/
        # 建议生成等收尾动作仅属于真正结束的会话；挂起态由 SessionExecutor
        # 保留会话槽，恢复后继续使用）。
        if data.get("_subagent_wait_suspend"):
            logger.info(
                f"[ChatExec] 业务等待挂起，跳过 finalize: session={data.get('session_id', '')}"
            )
            return

        # finalize 阶段：循环后统一处理（由 stream 子包统一实现）
        # 包括：update_usage / LLM fallback 检测 / tool_usage 统计 /
        #       审批中断收尾 / _pending_content 刷新 / 深度思考兜底 /
        #       reasoning 完成事件 / 补发 + 补全检查 + 建议生成
        async for event in finalize_stream(
            plan.ctx,
            data,
            plan.tools,
            plan.model_instance,
            plan.strategy,
            cb_holder["cb"],
            cb_holder["fb_callback"],
            usage_tracker,
            token_detail_tracker,
        ):
            yield event
