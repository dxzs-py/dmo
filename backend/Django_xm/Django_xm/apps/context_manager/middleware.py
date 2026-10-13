"""
上下文管理中间件

压缩核心委托官方 langchain.agents.middleware.SummarizationMiddleware
（langchain 1.3.x）：其 before_model 以
``[RemoveMessage(id=REMOVE_ALL_MESSAGES), *摘要HumanMessage, *保留消息]``
返回 state 更新，经 add_messages reducer 真正删除旧消息（旧自研实现返回
压缩后列表会被 reducer 追加而非替换，摘要重复堆积），且压缩结果随图状态
进入 checkpointer 持久化；摘要模型调用经 with_retry 重试后失败向上传播。

本中间件在官方压缩之上保留项目特有能力：
1. memory_tier 分层：SystemMessage 与显式标记 ``memory_tier=long_term``
   的消息永不压缩、原样保留在摘要之前；
2. _sanitize_tool_messages：tool_calls/ToolMessage 配对合规，
   经 wrap_model_call 的 request.override 送达模型；
3. 渐进式终止判断（after_model：WARN → THROTTLE → TERMINATE）。

另含 MemoryInjectionMiddleware（fix-review-round2 1.2）：chat agent 的
运行时记忆注入（wrap_model_call request.override，不写回 checkpoint state）。
"""

import logging
import uuid
from collections.abc import Sequence
from typing import Any

from langchain.agents.middleware import AgentMiddleware, AgentState, SummarizationMiddleware
from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, RemoveMessage, SystemMessage, ToolMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.runtime import Runtime

from Django_xm.apps.context_manager.config import context_settings
from Django_xm.apps.context_manager.services.runtime_providers import get_compression_model_provider
from Django_xm.apps.context_manager.services.termination_judge import (
    ContextTerminationJudge,
    TerminationAction,
    TerminationSignal,
)
from Django_xm.common.messages import content_to_str
from Django_xm.common.token_estimator import TokenEstimator

logger = logging.getLogger(__name__)


class ContextManagerMiddleware(AgentMiddleware):
    """
    上下文管理中间件 - 官方 SummarizationMiddleware 组合 + 项目特有治理

    职责：
    1. before_model：token 用量检测，超阈值时委托官方 SummarizationMiddleware
       生成摘要并以 RemoveMessage(REMOVE_ALL_MESSAGES) 真正替换消息列表；
    2. wrap_model_call：tool_calls/ToolMessage 配对清理（request.override）；
    3. after_model：渐进式终止判断（WARN/THROTTLE/TERMINATE）。
    """

    def __init__(
        self,
        model_name: str | None = None,
        trigger_tokens: int | None = None,
        thread_id: str | None = None,
    ):
        super().__init__()
        self._model_name = model_name or ""
        model_limit = TokenEstimator.get_model_limit(self._model_name)

        # 压缩触发阈值对齐自研体系 Level 2（摘要）语义：模型上限 × level_2 比例
        if trigger_tokens is not None:
            self._trigger_tokens = trigger_tokens
        else:
            self._trigger_tokens = int(model_limit * context_settings.compression_level_2_threshold)

        self._termination_judge = ContextTerminationJudge(
            model_name=self._model_name,
            # B1/N-2：注入 hard-stop 交接身份兜底（运行期优先取图 config 的
            # thread_id，见 termination_judge._resolve_handoff_thread_id）
            thread_id=thread_id,
        )
        self._thread_id = thread_id
        # round3 2.10：系统提示词 token（wrap_model_call 从 request.system_prompt
        # 捕获估算，全量 token 预算口径的组成部分；after_model 的 state.messages
        # 不含 system prompt，须单独计入）
        self._system_prompt_tokens = 0
        self._summarizer = self._build_summarizer(model_limit)
        logger.info(
            f"ContextManagerMiddleware 初始化: model={model_name}, "
            f"trigger={self._trigger_tokens} tokens, "
            f"keep={context_settings.compression_keep_recent} messages, "
            f"summarizer={'ready' if self._summarizer is not None else 'disabled'}, "
            f"thread_id={thread_id}"
        )

    def _build_summarizer(self, model_limit: int) -> SummarizationMiddleware | None:
        """构建官方 SummarizationMiddleware。

        模型走 compression_bridge 解析链（轻量模型 → 辅助模型 → SystemConfig
        默认 → 兜底，经 runtime_providers 由 ai_engine apps.ready() 注入）。
        构建失败 = 压缩能力不可用（error 留痕），不阻塞 agent 装配——
        收敛点保底注入（agent_hub.build_middleware）要求本中间件构建不抛异常。
        """
        if not context_settings.compression_enabled:
            logger.info("上下文自动压缩已禁用（context_settings.compression_enabled=False）")
            return None
        try:
            provider = get_compression_model_provider()
            if provider is None:
                logger.error(
                    "压缩模型提供者未注册（ai_engine apps.ready() 接线缺失），上下文自动压缩不可用；"
                    "context_manager 禁止 import ai_engine（环 1 契约）"
                )
                return None
            model = provider(context_settings.compression_lightweight_model)
            trigger: list[Any] = []
            if self._trigger_tokens > 0:
                trigger.append(("tokens", self._trigger_tokens))
            # 硬限制兜底：消息数超限也触发（与自研体系 hard_limit 语义一致）
            hard_limit_msgs = int(model_limit / 50) if model_limit > 0 else 0
            if hard_limit_msgs > 0:
                trigger.append({"messages": hard_limit_msgs})
            if not trigger:
                logger.error("压缩触发阈值配置无效（trigger_tokens<=0 且无消息数兜底），上下文自动压缩不可用")
                return None
            return SummarizationMiddleware(
                model,
                trigger=trigger,
                keep=("messages", context_settings.compression_keep_recent),
                token_counter=self._estimate_messages_tokens,
            )
        except Exception:
            logger.exception("SummarizationMiddleware 构建失败，上下文自动压缩不可用")
            return None

    def before_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        """模型调用前检测 Token 用量，超阈值委托官方中间件执行真实压缩"""
        messages = state.get("messages", [])
        if not messages:
            return None

        sanitized = self._sanitize_tool_messages(list(messages))
        self._log_token_usage(sanitized)

        if self._summarizer is None:
            return None

        # memory_tier 分层：长期记忆永不压缩，官方中间件仅处理短期消息
        long_term, short_term = self._split_memory_tiers(sanitized)

        update = self._summarizer.before_model({"messages": short_term}, runtime)
        if update is None:
            return None

        # update["messages"] = [RemoveMessage(REMOVE_ALL_MESSAGES), 摘要, *保留消息]
        # 摘要 HumanMessage 位于最近消息之前（官方语义）；
        # 长期记忆原样保留在摘要之前（system 上下文前置）。
        summarized = update["messages"][1:]
        new_messages = self._ensure_message_ids([*long_term, *summarized])
        logger.info(
            f"上下文压缩触发: 压缩后 {len(new_messages)} 条消息 "
            f"（长期记忆 {len(long_term)} 条原样保留，摘要 1 条）"
        )
        return {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *new_messages]}

    async def abefore_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        """异步版本：委托官方中间件的原生异步摘要（不经 executor 兜底）"""
        messages = state.get("messages", [])
        if not messages:
            return None

        sanitized = self._sanitize_tool_messages(list(messages))
        self._log_token_usage(sanitized)

        if self._summarizer is None:
            return None

        long_term, short_term = self._split_memory_tiers(sanitized)

        update = await self._summarizer.abefore_model({"messages": short_term}, runtime)
        if update is None:
            return None

        summarized = update["messages"][1:]
        new_messages = self._ensure_message_ids([*long_term, *summarized])
        logger.info(
            f"上下文压缩触发(异步): 压缩后 {len(new_messages)} 条消息 "
            f"（长期记忆 {len(long_term)} 条原样保留，摘要 1 条）"
        )
        return {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *new_messages]}

    def _capture_system_prompt_tokens(self, request: ModelRequest) -> None:
        """捕获系统提示词 token（round3 2.10 全量预算口径；幂等估算，廉价）"""
        system_prompt = getattr(request, "system_prompt", None)
        if isinstance(system_prompt, str) and system_prompt:
            self._system_prompt_tokens = TokenEstimator.estimate_tokens(system_prompt, self._model_name)

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler,
    ):
        """在模型调用前清理消息序列，确保 tool_calls/ToolMessage 合规

        关键：使用 request.override() 直接替换送达模型的消息列表。
        """
        self._capture_system_prompt_tokens(request)
        if not request.messages:
            return handler(request)

        sanitized = self._sanitize_tool_messages(list(request.messages))

        if sanitized is not request.messages and sanitized != request.messages:
            return handler(request.override(messages=sanitized))

        return handler(request)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler,
    ):
        """异步版本：在模型调用前清理消息序列"""
        self._capture_system_prompt_tokens(request)
        if not request.messages:
            return await handler(request)

        sanitized = self._sanitize_tool_messages(list(request.messages))

        if sanitized is not request.messages and sanitized != request.messages:
            return await handler(request.override(messages=sanitized))

        return await handler(request)

    def after_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        """模型调用后检测是否应该终止（渐进式：WARN → THROTTLE → TERMINATE）

        注意：压缩只在 before_model 触发（官方 SummarizationMiddleware 语义）。
        after_model 返回压缩消息列表会经 add_messages reducer 追加而非替换
        （旧自研实现的缺陷），故此处不做任何压缩。

        Token 预算口径（round3 2.10）：改为全量消息估算——系统提示词（wrap_model_call
        捕获）+ 输入 + 工具调用/结果 + 输出，统一经 _estimate_messages_tokens
        （TokenEstimator 对齐），替代旧实现的"仅累计最后一条 AIMessage"
        （严重低估、预算永不触发）。
        """
        messages = state.get("messages", [])
        if messages:
            total_tokens = self._system_prompt_tokens + self._estimate_messages_tokens(messages)
            self._termination_judge.set_token_usage(total_tokens)

        verdict = self._termination_judge.judge(state)

        # ── TERMINATE: 强制终止（安全网兜底） ──
        if verdict.action == TerminationAction.TERMINATE:
            signal_reason_map = {
                TerminationSignal.BUDGET_EXHAUSTED: "Token 预算已耗尽，自动终止执行",
                TerminationSignal.LOOP_DETECTED: "检测到重复循环，自动终止执行",
                TerminationSignal.GOAL_COMPLETED: "目标已完成，自动终止执行",
                TerminationSignal.INFO_GAIN_DECAY: "信息增益衰减，自动终止执行",
            }
            termination_msg = signal_reason_map.get(verdict.signal, verdict.reason)
            logger.info(
                f"终止判断触发: signal={verdict.signal.value}, "
                f"confidence={verdict.confidence:.2f}, reason={verdict.reason}"
            )
            return {
                "messages": [
                    AIMessage(
                        content=termination_msg,
                        additional_kwargs={"_termination_signal": True},
                    )
                ],
                "jump_to": "end",
            }

        # ── WARN: 注入警告消息，让 Agent 自我纠正（Claude Code 思想） ──
        if verdict.action == TerminationAction.WARN:
            warn_msg = verdict.warning_message or (
                "⚠️ 检测到可能的重复操作模式，请检查是否陷入循环。如果任务仍在正常推进，请继续；否则请总结当前进展。"
            )
            logger.info(f"循环警告注入: {verdict.reason}")
            return {"messages": [HumanMessage(content=warn_msg)]}

        # ── THROTTLE: 注入强警告，引导 Agent 停止重复操作 ──
        if verdict.action == TerminationAction.THROTTLE:
            throttle_msg = verdict.warning_message or (
                "⚠️ 检测到重复操作模式持续存在。"
                "请立即停止重复操作，基于已有信息给出回答。"
                "如果确实需要继续，请使用不同的方法或参数。"
            )
            logger.warning(f"循环限流警告注入: {verdict.reason}")
            return {"messages": [HumanMessage(content=throttle_msg)]}

        return None

    def _log_token_usage(self, messages: Sequence[BaseMessage]) -> None:
        """每轮模型调用前输出 Token 用量，便于排查压缩是否触发"""
        total_tokens = self._estimate_messages_tokens(messages)
        max_tokens = TokenEstimator.get_model_limit(self._model_name)
        ratio = total_tokens / max_tokens if max_tokens > 0 else 0.0
        logger.debug(
            f"[ContextMgr] before_model: tokens={total_tokens}/{max_tokens} "
            f"({ratio:.1%}), msgs={len(messages)}, model={self._model_name}"
        )

    def _estimate_messages_tokens(self, messages: Sequence[BaseMessage]) -> int:
        """估算消息列表的 token 数（含工具调用、格式化开销）

        同时作为官方 SummarizationMiddleware 的 token_counter（官方契约：
        Callable[[Iterable[MessageLikeRepresentation]], int]）。
        """
        total = 0
        for msg in messages:
            # 1. 消息内容
            content = content_to_str(msg.content)
            total += TokenEstimator.estimate_tokens(content, self._model_name)

            # 2. 工具调用：name + args + id
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                for tc in msg.tool_calls:
                    total += TokenEstimator.estimate_tokens(tc.get("name", ""), self._model_name)
                    total += TokenEstimator.estimate_tokens(str(tc.get("args", {})), self._model_name)
                    tc_id = tc.get("id", "")
                    if tc_id:
                        total += TokenEstimator.estimate_tokens(tc_id, self._model_name)

            # 3. ToolMessage 的 tool_call_id
            if isinstance(msg, ToolMessage) and msg.tool_call_id:
                total += TokenEstimator.estimate_tokens(msg.tool_call_id, self._model_name)

            # 4. 消息格式化开销（role 标记 + 分隔符，每条约 6 token）
            total += 6

        return total

    @staticmethod
    def _is_long_term_message(msg: BaseMessage) -> bool:
        """memory_tier 分层判定：SystemMessage 与显式 long_term 标记永不压缩

        仅认显式标记（additional_kwargs.memory_tier）与 system 角色，
        不做内容关键词启发式标记——关键词误标会阻止压缩、使上下文只增不减。
        """
        if isinstance(msg, SystemMessage):
            return True
        tier = msg.additional_kwargs.get("memory_tier") if msg.additional_kwargs else None
        return tier == "long_term"

    @classmethod
    def _split_memory_tiers(
        cls, messages: list[BaseMessage]
    ) -> tuple[list[BaseMessage], list[BaseMessage]]:
        """切分长期/短期消息：长期原样保留，短期交由官方中间件摘要"""
        long_term = [m for m in messages if cls._is_long_term_message(m)]
        short_term = [m for m in messages if not cls._is_long_term_message(m)]
        return long_term, short_term

    @staticmethod
    def _ensure_message_ids(messages: list[BaseMessage]) -> list[BaseMessage]:
        """确保消息有唯一 id（官方同款语义：add_messages reducer 依赖 id）"""
        for msg in messages:
            if msg.id is None:
                msg.id = str(uuid.uuid4())
        return messages

    @staticmethod
    def _sanitize_tool_messages(messages: list[BaseMessage]) -> list[BaseMessage]:
        """移除孤立 ToolMessage 和缺少 ToolMessage 的 tool_calls

        处理三种不合规情况：
        1. ToolMessage 无对应 AIMessage（压缩删除了 AIMessage 但保留了 ToolMessage）
        2. AIMessage 含 tool_calls 但无对应 ToolMessage（工具执行失败导致）
        3. AIMessage 含 tool_calls 但 ToolMessage 非紧跟其后（中间插入了其他消息）

        对于情况2/3，有两种修复策略：
        a) 如果 AIMessage 同时有文本内容，移除 tool_calls（保留文本）
        b) 如果 AIMessage 无文本内容，补充错误 ToolMessage
        """
        # 第一遍：收集所有有效的 tool_call_id
        valid_tool_call_ids: set[str] = set()
        for msg in messages:
            if isinstance(msg, AIMessage) and hasattr(msg, "tool_calls") and msg.tool_calls:
                for tc in msg.tool_calls:
                    tc_id = tc.get("id", "") if isinstance(tc, dict) else getattr(tc, "id", "")
                    if tc_id:
                        valid_tool_call_ids.add(tc_id)

        # 第二遍：移除孤立的 ToolMessage
        sanitized: list[BaseMessage] = []
        removed_tools = 0
        for msg in messages:
            if isinstance(msg, ToolMessage):
                if msg.tool_call_id and msg.tool_call_id in valid_tool_call_ids:
                    sanitized.append(msg)
                else:
                    removed_tools += 1
            else:
                sanitized.append(msg)

        if removed_tools > 0:
            logger.warning(f"移除 {removed_tools} 条孤立 ToolMessage（无对应 tool_calls）")

        # 第三遍：处理含 tool_calls 的 AIMessage，确保每个 tool_call_id 都有紧跟的 ToolMessage
        result = []
        stripped = 0
        i = 0
        while i < len(sanitized):
            msg = sanitized[i]

            if not (isinstance(msg, AIMessage) and hasattr(msg, "tool_calls") and msg.tool_calls):
                result.append(msg)
                i += 1
                continue

            # 收集该 AIMessage 的所有 tool_call_id
            msg_tc_ids = []
            for tc in msg.tool_calls:
                tc_id = tc.get("id", "") if isinstance(tc, dict) else getattr(tc, "id", "")
                if tc_id:
                    msg_tc_ids.append(tc_id)
            if not msg_tc_ids:
                result.append(msg)
                i += 1
                continue

            # 检查紧跟的消息是否都是对应的 ToolMessage
            expected_tc_ids = set(msg_tc_ids)
            responded_ids = set()
            j = i + 1
            has_intervening = False
            while j < len(sanitized):
                next_msg = sanitized[j]
                if isinstance(next_msg, ToolMessage) and next_msg.tool_call_id in expected_tc_ids:
                    responded_ids.add(next_msg.tool_call_id)
                    j += 1
                else:
                    break

            # 检查 j 之后是否还有属于该 AIMessage 的 ToolMessage（被间隔的情况3）
            for k in range(j, len(sanitized)):
                later_msg = sanitized[k]
                if isinstance(later_msg, ToolMessage) and later_msg.tool_call_id in expected_tc_ids:
                    has_intervening = True
                    break
                if isinstance(later_msg, AIMessage):
                    break

            missing_ids = expected_tc_ids - responded_ids

            if not missing_ids and not has_intervening:
                # 所有 tool_call_id 都有紧跟的 ToolMessage，合规
                result.append(msg)
                i += 1
                continue

            # 不合规：有缺失的 ToolMessage 或 ToolMessage 被间隔
            # 统一使用策略a：移除 tool_calls，保留/补充文本
            # 不再使用策略b（补充错误 ToolMessage），因为：
            # 1. interrupt 导致的缺失 ToolMessage 不是"执行失败"，语义错误
            # 2. 补充的 ToolMessage 可能导致消息序列不合规（400 错误）
            new_content = msg.content or ""
            if not new_content.strip():
                new_content = "[工具调用等待审批中，暂未执行]"
            new_msg = AIMessage(
                content=new_content,
                additional_kwargs={k: v for k, v in msg.additional_kwargs.items() if k != "tool_calls"},
            )
            if hasattr(msg, "id") and msg.id:
                new_msg.id = msg.id
            result.append(new_msg)
            stripped += 1
            logger.warning(
                f"移除 AIMessage 中的 {len(expected_tc_ids)} 个 tool_calls"
                f"（缺失={len(missing_ids)}, 间隔={has_intervening}，补充文本内容）"
            )
            # 跳过紧跟的对应 ToolMessage（它们引用了已移除的 tool_call_id）
            i += 1
            while i < len(sanitized):
                next_msg = sanitized[i]
                if isinstance(next_msg, ToolMessage) and next_msg.tool_call_id in expected_tc_ids:
                    i += 1
                else:
                    break
            continue

        if stripped > 0:
            logger.warning(f"移除 {stripped} 个 AIMessage 中的孤立 tool_calls（补充文本）")

        # 第四遍：最终清理——移除策略a导致的残留孤立 ToolMessage
        # 策略a移除 tool_calls 后，对应的 ToolMessage 可能仍在 result 中
        final_valid_tc_ids: set[str] = set()
        for msg in result:
            if isinstance(msg, AIMessage) and hasattr(msg, "tool_calls") and msg.tool_calls:
                for tc in msg.tool_calls:
                    tc_id = tc.get("id", "") if isinstance(tc, dict) else getattr(tc, "id", "")
                    if tc_id:
                        final_valid_tc_ids.add(tc_id)

        if final_valid_tc_ids != valid_tool_call_ids:
            # 有 tool_calls 被移除，需要清理对应的孤立 ToolMessage
            final_result = []
            orphan_removed = 0
            for msg in result:
                if isinstance(msg, ToolMessage):
                    if msg.tool_call_id and msg.tool_call_id in final_valid_tc_ids:
                        final_result.append(msg)
                    else:
                        orphan_removed += 1
                else:
                    final_result.append(msg)
            if orphan_removed > 0:
                logger.warning(f"最终清理: 移除 {orphan_removed} 条残留孤立 ToolMessage")
            return final_result

        return result


class MemoryInjectionMiddleware(AgentMiddleware):
    """运行时记忆注入中间件（fix-review-round2 1.2）：模型调用前动态前插注入 SystemMessage。

    注入语义（与旧 graph_input 前置注入的本质区别）：
    - 注入文本（KG/跨会话记忆，chat 侧每请求经 ContextManager.get_injection_context
      获取后构造本中间件）仅经 ``wrap_model_call`` 的 ``request.override`` 前插到
      **送达模型**的消息列表，**不写回图 state**——checkpoint state messages 不残留
      注入消息（旧实现把注入 SystemMessage 放进 graph_input，随 checkpoint 持久化
      且逐轮堆积）；
    - 每次模型调用（含工具循环内的多轮调用）都重新前插，注入内容始终新鲜；
    - 注入文本为空（无 KG/跨会话记忆或获取失败降级）时完全透传，零开销。

    system prompt（含研究上下文，经 AgentConfig.system_prompt 注入）由 langchain
    factory 在模型调用时单独拼接，不在 request.messages 内，本中间件的前插不会
    挤占或重复 system prompt。

    装配点：仅 chat agent（AgentService.create_agent_with_memory，use_checkpointer
    分支）经 AgentConfig.middleware 显式栈接线；deep_research/子代理等其他
    builder 链路不受影响。
    """

    def __init__(self, injection_text: str):
        super().__init__()
        self._injection_text = (injection_text or "").strip()

    def wrap_model_call(self, request: ModelRequest, handler):
        """模型调用前把注入文本前插到送达模型的消息列表（request.override，不写 state）"""
        if not self._injection_text:
            return handler(request)
        injected = [SystemMessage(content=self._injection_text), *request.messages]
        return handler(request.override(messages=injected))

    async def awrap_model_call(self, request: ModelRequest, handler):
        """异步版本：语义同 wrap_model_call"""
        if not self._injection_text:
            return await handler(request)
        injected = [SystemMessage(content=self._injection_text), *request.messages]
        return await handler(request.override(messages=injected))
