"""
上下文管理中间件

替代 SummarizationMiddleware，在 Agent 执行循环中自动检测 Token 用量并触发压缩。
使用 ProgressiveCompressor 实现多级渐进式压缩，支持长短时记忆分离。

继承 langchain.agents.middleware.AgentMiddleware，通过 before_model 钩子
在每轮模型调用前检测 Token 用量，超阈值触发渐进式压缩。
"""

import logging
from collections.abc import Sequence
from typing import Any

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.runtime import Runtime

from Django_xm.apps.context_manager.config import context_settings
from Django_xm.apps.context_manager.services.compression import (
    TokenEstimator,
)
from Django_xm.apps.context_manager.services.progressive_compressor import (
    CompressionLevel,
    ProgressiveCompressor,
)
from Django_xm.apps.context_manager.services.termination_judge import (
    ContextTerminationJudge,
    TerminationAction,
    TerminationSignal,
)
from Django_xm.common.messages import content_to_str

logger = logging.getLogger(__name__)


class ContextManagerMiddleware(AgentMiddleware):
    """
    上下文管理中间件 - 在 Agent 执行循环中自动触发渐进式压缩

    使用 ProgressiveCompressor 替代单一阈值压缩：
    1. 每轮 Agent 模型调用前检测 Token 用量
    2. 根据占用比例自动选择压缩级别（Level 1-4）
    3. 跳过 LONG_TERM 记忆，仅压缩 SHORT_TERM 消息
    """

    def __init__(
        self,
        model_name: str | None = None,
        trigger_tokens: int | None = None,
        keep_messages: int | None = None,
        strategy: str = "hybrid",
        user_id: str | None = None,
        store=None,
        thread_id: str | None = None,
    ):
        super().__init__()
        self._model_name = model_name or ""
        model_limit = TokenEstimator.get_model_limit(self._model_name)

        if trigger_tokens is not None:
            self._trigger_tokens = trigger_tokens
        else:
            self._trigger_tokens = int(model_limit * context_settings.compression_threshold_ratio)

        level_thresholds = {
            CompressionLevel.LEVEL_1_BASELINE: 0.0,
            CompressionLevel.LEVEL_2_SUMMARY: context_settings.compression_level_2_threshold,
            CompressionLevel.LEVEL_3_RELEVANCE: context_settings.compression_level_3_threshold,
            CompressionLevel.LEVEL_4_AGGRESSIVE: context_settings.compression_level_4_threshold,
        }

        self._progressive_compressor = ProgressiveCompressor(
            model_name=self._model_name,
            store=store,
            user_id=user_id,
            level_thresholds=level_thresholds,
            thread_id=thread_id,
        )
        self._termination_judge = ContextTerminationJudge(
            model_name=self._model_name,
        )
        self._user_id = user_id
        self._thread_id = thread_id
        logger.info(
            f"ContextManagerMiddleware 初始化: model={model_name}, "
            f"trigger={self._trigger_tokens} tokens, "
            f"level_thresholds={{{', '.join(f'{k.value}={v}' for k, v in level_thresholds.items())}}}, "
            f"thread_id={thread_id}"
        )

    def before_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        """模型调用前检测 Token 用量，根据压缩级别触发渐进式压缩，并清理孤立 ToolMessage"""
        messages = state.get("messages", [])
        if not messages:
            return None

        sanitized = self._sanitize_tool_messages(messages)

        total_tokens = self._estimate_messages_tokens(sanitized)
        max_tokens = TokenEstimator.get_model_limit(self._model_name)

        level = self._progressive_compressor.determine_level(total_tokens, max_tokens)
        ratio = total_tokens / max_tokens if max_tokens > 0 else 0.0

        # 每轮模型调用前输出 Token 用量，便于排查压缩是否触发
        logger.debug(
            f"[ContextMgr] before_model: tokens={total_tokens}/{max_tokens} "
            f"({ratio:.1%}), level={level.value}, msgs={len(sanitized)}, model={self._model_name}"
        )

        if level == CompressionLevel.LEVEL_1_BASELINE:
            if ratio < context_settings.compression_level_2_threshold:
                # 硬限制兜底：即使 token 估算有误差，消息数超过阈值也强制压缩
                # 粗略估算：每条消息至少 50 token
                hard_limit_msgs = int(max_tokens / 50) if max_tokens > 0 else 2000
                if len(sanitized) > hard_limit_msgs:
                    logger.warning(f"消息数 {len(sanitized)} 超过硬限制 {hard_limit_msgs}，强制触发压缩")
                    level = CompressionLevel.LEVEL_2_SUMMARY
                else:
                    if sanitized is not messages and sanitized != messages:
                        return {"messages": sanitized}
                    return None

        logger.info(
            f"Token 用量 {total_tokens}/{max_tokens} ({total_tokens / max_tokens:.1%})，"
            f"压缩级别={level.value}，触发渐进式压缩（消息数: {len(sanitized)}）"
        )

        compressed = self.process_messages(sanitized)
        return {"messages": compressed}

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler,
    ):
        """在模型调用前清理消息序列，确保 tool_calls/ToolMessage 合规

        关键：使用 request.override() 直接替换消息列表，
        而不是通过 before_model 返回值（add_messages reducer 无法完全替换）。
        """
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
        if not request.messages:
            return await handler(request)

        sanitized = self._sanitize_tool_messages(list(request.messages))

        if sanitized is not request.messages and sanitized != request.messages:
            return await handler(request.override(messages=sanitized))

        return await handler(request)

    def after_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        """模型调用后检测是否应该终止（渐进式：WARN → THROTTLE → TERMINATE）"""
        messages = state.get("messages", [])
        if messages:
            last_msg = messages[-1]
            if isinstance(last_msg, AIMessage) and last_msg.content:
                tokens = TokenEstimator.estimate_tokens(
                    content_to_str(last_msg.content),
                    self._model_name,
                )
                self._termination_judge.record_token_usage(tokens)

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

        # ── CONTINUE: 检查是否需要压缩 ──
        if verdict.should_compress:
            messages = state.get("messages", [])
            if messages:
                compressed = self.process_messages(messages)
                return {"messages": compressed}

        return None

    def _estimate_messages_tokens(self, messages: Sequence[BaseMessage]) -> int:
        """估算消息列表的 token 数（含工具调用、格式化开销）"""
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

    def _messages_to_dicts(self, messages: Sequence[BaseMessage]) -> list[dict[str, Any]]:
        """将 BaseMessage 列表转换为 Dict 列表"""
        result = []
        for msg in messages:
            role_map = {
                HumanMessage: "user",
                AIMessage: "assistant",
                SystemMessage: "system",
                ToolMessage: "tool",
            }
            role = role_map.get(type(msg), "unknown")
            content = content_to_str(msg.content)
            entry: dict[str, Any] = {"role": role, "content": content}
            if hasattr(msg, "id") and msg.id:
                entry["id"] = msg.id
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                entry["tool_calls"] = msg.tool_calls
            if hasattr(msg, "additional_kwargs") and "memory_tier" in msg.additional_kwargs:
                entry["memory_tier"] = msg.additional_kwargs["memory_tier"]
            if isinstance(msg, ToolMessage):
                entry["tool_call_id"] = msg.tool_call_id
            result.append(entry)
        return result

    def _dicts_to_messages(self, dicts: list[dict[str, Any]]) -> list[BaseMessage]:
        """将 Dict 列表转换回 BaseMessage 列表"""
        result = []
        for d in dicts:
            role = d.get("role", "unknown")
            content = d.get("content", "")
            msg_id = d.get("id")
            memory_tier = d.get("memory_tier")

            additional_kwargs = {}
            if memory_tier:
                additional_kwargs["memory_tier"] = memory_tier

            if role == "user":
                msg = HumanMessage(content=content, additional_kwargs=additional_kwargs)
            elif role == "assistant":
                tool_calls = d.get("tool_calls")
                if tool_calls:
                    msg = AIMessage(content=content, tool_calls=tool_calls, additional_kwargs=additional_kwargs)
                else:
                    msg = AIMessage(content=content, additional_kwargs=additional_kwargs)
            elif role == "system":
                msg = SystemMessage(content=content, additional_kwargs=additional_kwargs)
            elif role == "tool":
                tool_call_id = d.get("tool_call_id", "")
                msg = ToolMessage(content=content, tool_call_id=tool_call_id, additional_kwargs=additional_kwargs)
            else:
                msg = HumanMessage(content=content, additional_kwargs=additional_kwargs)

            # 保留消息 id，确保 add_messages reducer 能正确替换而非追加
            if msg_id:
                msg.id = msg_id

            result.append(msg)
        return result

    def process_messages(
        self,
        messages: Sequence[BaseMessage],
    ) -> list[BaseMessage]:
        """
        处理消息列表：检测 Token 用量，使用渐进式压缩

        此方法可在 Agent 每轮执行后手动调用，也可通过 before_model 钩子自动触发。
        """
        dict_messages = self._messages_to_dicts(messages)

        query = self._extract_latest_query(dict_messages)

        compressed_dicts, result = self._progressive_compressor.compress(dict_messages, query=query)

        if result.compressed_tokens < result.original_tokens:
            logger.info(
                f"渐进式压缩完成: level={result.level.value}, "
                f"{result.original_tokens} -> {result.compressed_tokens} tokens, "
                f"压缩率 {result.compression_ratio:.1%}, "
                f"策略={result.strategy_used}"
            )
            converted = self._dicts_to_messages(compressed_dicts)
            return self._sanitize_tool_messages(converted)

        return self._sanitize_tool_messages(list(messages))

    @staticmethod
    def _extract_latest_query(messages: list[dict[str, Any]]) -> str | None:
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, str) and content.strip():
                    return content.strip()[:500]
                elif isinstance(content, list):
                    text = content_to_str(content)
                    if text.strip():
                        return text.strip()[:500]
        return None

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
