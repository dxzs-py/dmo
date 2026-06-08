from __future__ import annotations

import re
from enum import Enum
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field

from langchain_core.messages import BaseMessage, AIMessage, ToolMessage

from Django_xm.apps.context_manager.config import get_logger
from Django_xm.apps.context_manager.config import context_settings
from Django_xm.apps.context_manager.services.compression import TokenEstimator

logger = get_logger(__name__)

_SUMMARY_PATTERNS = [
    re.compile(r"以上是", re.IGNORECASE),
    re.compile(r"总结", re.IGNORECASE),
    re.compile(r"完成", re.IGNORECASE),
    re.compile(r"希望这", re.IGNORECASE),
    re.compile(r"综上所述", re.IGNORECASE),
    re.compile(r"总的来说", re.IGNORECASE),
    re.compile(r"in summary", re.IGNORECASE),
    re.compile(r"to summarize", re.IGNORECASE),
    re.compile(r"in conclusion", re.IGNORECASE),
]


class TerminationSignal(Enum):
    GOAL_COMPLETED = "goal_completed"
    INFO_GAIN_DECAY = "info_gain_decay"
    LOOP_DETECTED = "loop_detected"
    BUDGET_EXHAUSTED = "budget_exhausted"
    CONTINUE = "continue"


class TerminationAction(Enum):
    """循环检测渐进式动作（Claude Code 信任模型 + Trae 动态轮次）"""
    CONTINUE = "continue"      # 正常继续
    WARN = "warn"              # 注入警告消息，让 Agent 自我纠正
    THROTTLE = "throttle"      # 注入强警告，引导 Agent 停止重复操作
    TERMINATE = "terminate"    # 强制终止（安全网兜底）


@dataclass
class TerminationVerdict:
    signal: TerminationSignal = TerminationSignal.CONTINUE
    action: TerminationAction = TerminationAction.CONTINUE
    confidence: float = 0.0
    reason: str = ""
    should_terminate: bool = False
    should_compress: bool = False
    warning_message: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


class ContextTerminationJudge:

    def __init__(
        self,
        goal_complete_window: Optional[int] = None,
        info_gain_threshold: Optional[float] = None,
        info_gain_window: Optional[int] = None,
        token_budget_limit: Optional[int] = None,
        model_name: str = "",
    ) -> None:
        self._goal_complete_window = goal_complete_window or context_settings.termination_goal_complete_window
        self._info_gain_threshold = info_gain_threshold or context_settings.termination_info_gain_threshold
        self._info_gain_window = info_gain_window or context_settings.termination_info_gain_window
        self._token_budget_limit = token_budget_limit or context_settings.token_budget_limit
        self._model_name = model_name

        # 循环检测阈值（从 config 读取，可配置）
        self._warn_threshold: int = context_settings.loop_same_call_warn_threshold
        self._throttle_threshold: int = context_settings.loop_same_call_throttle_threshold
        self._terminate_threshold: int = context_settings.loop_same_call_terminate_threshold
        self._window_size: int = context_settings.loop_window_size
        self._diversity_warn_threshold: float = context_settings.loop_diversity_warn_threshold
        self._diversity_terminate_threshold: float = context_settings.loop_diversity_terminate_threshold
        self._args_diversity_threshold: float = context_settings.loop_args_diversity_threshold
        self._args_progressive_threshold: float = context_settings.loop_args_progressive_threshold
        self._safe_tools: set = {
            t.strip() for t in context_settings.loop_multi_call_safe_tools.split(",") if t.strip()
        }

        self._cumulative_tokens: int = 0
        self._recent_tool_calls: List[Dict[str, Any]] = []
        self._recent_info_gains: List[float] = []
        self._last_compressed_index: int = 0

    def judge(self, state: Dict[str, Any]) -> TerminationVerdict:
        try:
            budget_verdict = self._check_budget_exhausted(state)
            if budget_verdict is not None:
                return budget_verdict

            loop_verdict = self._check_loop_detected(state)
            if loop_verdict is not None:
                return loop_verdict

            goal_verdict = self._check_goal_completed(state)
            if goal_verdict is not None:
                return goal_verdict

            info_gain_verdict = self._check_info_gain_decay(state)
            if info_gain_verdict is not None:
                return info_gain_verdict

            return TerminationVerdict(
                signal=TerminationSignal.CONTINUE,
                action=TerminationAction.CONTINUE,
                confidence=0.0,
                reason="无终止信号触发",
                should_terminate=False,
                should_compress=False,
            )
        except Exception as e:
            logger.error(f"终止判断异常: {e}")
            return TerminationVerdict(
                signal=TerminationSignal.CONTINUE,
                action=TerminationAction.CONTINUE,
                confidence=0.0,
                reason=f"判断异常: {e}",
                should_terminate=False,
                should_compress=False,
            )

    def record_token_usage(self, tokens: int) -> None:
        self._cumulative_tokens += tokens

    def reset(self) -> None:
        self._cumulative_tokens = 0
        self._recent_tool_calls = []
        self._recent_info_gains = []
        self._last_compressed_index = 0

    def _check_budget_exhausted(self, state: Dict[str, Any]) -> Optional[TerminationVerdict]:
        if self._cumulative_tokens >= self._token_budget_limit:
            reason = (
                f"Token 预算耗尽: 累计 {self._cumulative_tokens} >= 上限 {self._token_budget_limit}"
            )
            logger.warning(reason)
            return TerminationVerdict(
                signal=TerminationSignal.BUDGET_EXHAUSTED,
                action=TerminationAction.TERMINATE,
                confidence=1.0,
                reason=reason,
                should_terminate=True,
                should_compress=True,
                metadata={
                    "cumulative_tokens": self._cumulative_tokens,
                    "budget_limit": self._token_budget_limit,
                },
            )
        return None

    def _check_loop_detected(self, state: Dict[str, Any]) -> Optional[TerminationVerdict]:
        messages = state.get("messages", [])
        if not messages:
            return None

        recent_msgs = messages[-self._window_size * 2:] if len(messages) > self._window_size * 2 else messages

        tool_calls_in_window: List[Dict[str, Any]] = []
        for msg in recent_msgs:
            if isinstance(msg, AIMessage) and hasattr(msg, "tool_calls") and msg.tool_calls:
                for tc in msg.tool_calls:
                    tool_calls_in_window.append({
                        "name": tc.get("name", ""),
                        "args_repr": str(tc.get("args", {})),
                    })

        if not tool_calls_in_window:
            return None

        self._recent_tool_calls = tool_calls_in_window[-self._window_size:]

        # ── 0. 参数模式检测：递增/递减/遍历模式=合理重复，跳过 ──
        if self._detect_progressive_pattern(self._recent_tool_calls):
            logger.debug("循环检测: 检测到参数递进模式，跳过循环检测")
            return None

        # ── 1. 连续相同调用检测（渐进式） ──
        consecutive_same = 1
        for i in range(len(self._recent_tool_calls) - 1, 0, -1):
            current = self._recent_tool_calls[i]
            prev = self._recent_tool_calls[i - 1]
            if current["name"] == prev["name"] and current["args_repr"] == prev["args_repr"]:
                consecutive_same += 1
            else:
                break

        if consecutive_same >= self._warn_threshold:
            tool_name = self._recent_tool_calls[-1]["name"]

            # 终止：安全网兜底
            if consecutive_same >= self._terminate_threshold:
                reason = (
                    f"循环检测: 工具 {tool_name} 连续相同调用 {consecutive_same} 次"
                )
                logger.warning(reason)
                return TerminationVerdict(
                    signal=TerminationSignal.LOOP_DETECTED,
                    action=TerminationAction.TERMINATE,
                    confidence=0.95,
                    reason=reason,
                    should_terminate=True,
                    metadata={
                        "tool_name": tool_name,
                        "consecutive_same": consecutive_same,
                    },
                )

            # 限流：强警告引导 Agent 停止
            if consecutive_same >= self._throttle_threshold:
                reason = (
                    f"循环检测: 工具 {tool_name} 连续相同调用 {consecutive_same} 次"
                )
                logger.warning(reason)
                return TerminationVerdict(
                    signal=TerminationSignal.LOOP_DETECTED,
                    action=TerminationAction.THROTTLE,
                    confidence=0.9,
                    reason=reason,
                    should_terminate=False,
                    warning_message=(
                        f"⚠️ 检测到工具 {tool_name} 已连续调用 {consecutive_same} 次（相同参数）。"
                        f"请立即停止重复操作，基于已有信息给出回答。"
                        f"如果确实需要继续，请使用不同的方法或参数。"
                    ),
                    metadata={
                        "tool_name": tool_name,
                        "consecutive_same": consecutive_same,
                    },
                )

            # 警告：注入警告消息让 Agent 自我纠正
            reason = (
                f"循环检测: 工具 {tool_name} 连续相同调用 {consecutive_same} 次"
            )
            logger.info(reason)
            return TerminationVerdict(
                signal=TerminationSignal.LOOP_DETECTED,
                action=TerminationAction.WARN,
                confidence=0.8,
                reason=reason,
                should_terminate=False,
                warning_message=(
                    f"⚠️ 检测到工具 {tool_name} 已连续调用 {consecutive_same} 次（相同参数）。"
                    f"请检查是否陷入循环。如果任务仍在正常推进，请继续；否则请总结当前进展。"
                ),
                metadata={
                    "tool_name": tool_name,
                    "consecutive_same": consecutive_same,
                },
            )

        # ── 2. ToolUsageGuard 交接 ──
        try:
            from Django_xm.apps.ai_engine.services.tool_usage_guard import (
                get_tool_usage_guard,
            )
            thread_id = state.get("thread_id") or state.get("configurable", {}).get(
                "thread_id", "default"
            )
            if get_tool_usage_guard().is_in_hard_stop_state(str(thread_id)):
                write_file_calls = [
                    tc for tc in self._recent_tool_calls
                    if tc["name"] == "fs_write_file"
                ]
                if len(write_file_calls) >= 3:
                    reason = (
                        f"ToolUsageGuard 已连续阻断，fs_write_file 在最近窗口内仍被 "
                        f"调用 {len(write_file_calls)} 次，判定为不可收敛循环"
                    )
                    logger.warning(reason)
                    return TerminationVerdict(
                        signal=TerminationSignal.LOOP_DETECTED,
                        action=TerminationAction.TERMINATE,
                        confidence=0.95,
                        reason=reason,
                        should_terminate=True,
                        metadata={
                            "tool_name": "fs_write_file",
                            "write_count": len(write_file_calls),
                            "source": "tool_usage_guard_handoff",
                        },
                    )
        except Exception as e:
            logger.debug(f"ToolUsageGuard 状态检查失败: {e}")

        # ── 3. 多样性检测（参数感知） ──
        if len(self._recent_tool_calls) >= self._window_size:
            unique_tools = len(set(tc["name"] for tc in self._recent_tool_calls))
            diversity_ratio = unique_tools / len(self._recent_tool_calls)

            # 安全工具豁免（扩展白名单）
            all_safe = all(
                tc["name"] in self._safe_tools for tc in self._recent_tool_calls
            )
            if all_safe:
                unique_args = len(set(
                    (tc["name"], tc["args_repr"]) for tc in self._recent_tool_calls
                ))
                if unique_args >= len(self._recent_tool_calls) * self._args_progressive_threshold:
                    logger.debug(
                        "循环检测: 多调用安全工具但参数多样性正常 (%d/%d)，跳过",
                        unique_args,
                        len(self._recent_tool_calls),
                    )
                    return None

            # 参数多样性计算
            unique_args = len(set(
                (tc["name"], tc["args_repr"]) for tc in self._recent_tool_calls
            ))
            args_diversity = unique_args / len(self._recent_tool_calls)

            # 参数多样性高=合理重复，跳过
            if args_diversity >= self._args_progressive_threshold:
                logger.debug(
                    "循环检测: 工具多样性低但参数多样性正常 (%.1f%%)，跳过",
                    args_diversity * 100,
                )
                return None

            # 终止：工具多样性 + 参数多样性都过低
            if diversity_ratio < self._diversity_terminate_threshold:
                if args_diversity < self._args_diversity_threshold:
                    reason = (
                        f"循环检测: 工具多样性过低 ({diversity_ratio:.1%})，"
                        f"参数多样性也过低 ({args_diversity:.1%})"
                    )
                    logger.warning(reason)
                    return TerminationVerdict(
                        signal=TerminationSignal.LOOP_DETECTED,
                        action=TerminationAction.TERMINATE,
                        confidence=0.9,
                        reason=reason,
                        should_terminate=True,
                        metadata={
                            "diversity_ratio": diversity_ratio,
                            "args_diversity": args_diversity,
                            "unique_tools": unique_tools,
                            "total_calls": len(self._recent_tool_calls),
                        },
                    )

            # 警告：工具多样性偏低
            if diversity_ratio < self._diversity_warn_threshold:
                reason = f"循环检测: 工具多样性偏低 ({diversity_ratio:.1%})"
                logger.info(reason)
                return TerminationVerdict(
                    signal=TerminationSignal.LOOP_DETECTED,
                    action=TerminationAction.WARN,
                    confidence=0.7,
                    reason=reason,
                    should_terminate=False,
                    warning_message=(
                        f"⚠️ 检测到工具多样性偏低 ({diversity_ratio:.0%})，"
                        f"请检查是否陷入重复操作。如果任务仍在正常推进，请继续。"
                    ),
                    metadata={
                        "diversity_ratio": diversity_ratio,
                        "unique_tools": unique_tools,
                        "total_calls": len(self._recent_tool_calls),
                    },
                )

        return None

    @staticmethod
    def _detect_progressive_pattern(tool_calls: List[Dict[str, Any]]) -> bool:
        """检测工具调用参数中是否存在递增/递减/遍历模式

        核心思想：同工具不同参数=合理重复（浏览器自动化、多文件编辑等）
        只有同工具同参数反复调用才是真正的循环

        例如:
        - shell_exec(command="agent-browser open url1")
        - shell_exec(command="agent-browser snapshot")   ← 不同子命令=遍历模式
        - fs_write_file(path="file1.py")                  ← 不同文件=遍历模式
        - fs_write_file(path="file2.py")
        """
        if len(tool_calls) < 3:
            return False

        same_tool_calls = [tc for tc in tool_calls
                           if tc["name"] == tool_calls[-1]["name"]]
        if len(same_tool_calls) < 3:
            return False

        # 提取参数指纹，检查是否全部相同
        unique_args = set(tc["args_repr"] for tc in same_tool_calls)
        if len(unique_args) <= 1:
            return False  # 所有参数完全相同=不是递进模式

        # 参数多样性 >= 阈值 → 认为是递进模式
        args_diversity = len(unique_args) / len(same_tool_calls)
        return args_diversity >= context_settings.loop_args_progressive_threshold

    def _check_goal_completed(self, state: Dict[str, Any]) -> Optional[TerminationVerdict]:
        messages = state.get("messages", [])
        if not messages:
            return None

        # 深度研究模式需要更多轮次才能判定完成，避免浅尝辄止
        # 统计已有 AI 消息轮数，至少需要 min_rounds 轮才允许 goal_completed 判定
        ai_msg_count = sum(1 for m in messages if isinstance(m, AIMessage))
        min_rounds = 6
        if ai_msg_count < min_rounds:
            logger.debug(
                f"目标完成检测: AI 消息轮数不足 ({ai_msg_count} < {min_rounds})，跳过"
            )
            return None

        recent = self._extract_recent_messages(state, self._goal_complete_window)
        if not recent:
            return None

        has_tool_calls = self._has_tool_calls_in_messages(recent)
        if has_tool_calls:
            return None

        for msg in reversed(recent):
            if isinstance(msg, AIMessage) and msg.content:
                if self._has_summary_language(msg.content):
                    reason = "目标完成检测: 最近 N 轮无工具调用且 AI 回复包含总结性语言"
                    logger.info(reason)
                    return TerminationVerdict(
                        signal=TerminationSignal.GOAL_COMPLETED,
                        action=TerminationAction.TERMINATE,
                        confidence=0.7,
                        reason=reason,
                        should_terminate=True,
                        metadata={
                            "window_size": self._goal_complete_window,
                            "summary_detected": True,
                        },
                    )

        return None

    def _check_info_gain_decay(self, state: Dict[str, Any]) -> Optional[TerminationVerdict]:
        messages = state.get("messages", [])
        if not messages:
            return None

        ai_messages = [msg for msg in messages if isinstance(msg, AIMessage) and msg.content]
        if len(ai_messages) < self._info_gain_window + 1:
            return None

        recent_ai = ai_messages[-(self._info_gain_window + 1):]

        existing_text = " ".join(
            msg.content[:500] for msg in recent_ai[:-1] if isinstance(msg.content, str)
        )

        novelties: List[float] = []
        for i in range(max(1, len(recent_ai) - self._info_gain_window), len(recent_ai)):
            new_text = recent_ai[i].content if isinstance(recent_ai[i].content, str) else str(recent_ai[i].content)
            context_text = " ".join(
                msg.content[:500] for msg in recent_ai[:i] if isinstance(msg.content, str)
            )
            novelty = self._compute_semantic_novelty(new_text, context_text)
            novelties.append(novelty)

        self._recent_info_gains = novelties

        if len(novelties) >= self._info_gain_window:
            all_below_threshold = all(n < self._info_gain_threshold for n in novelties[-self._info_gain_window:])
            if all_below_threshold:
                avg_novelty = sum(novelties[-self._info_gain_window:]) / self._info_gain_window
                reason = (
                    f"信息增益衰减: 连续 {self._info_gain_window} 轮新颖度低于阈值 "
                    f"(平均={avg_novelty:.3f}, 阈值={self._info_gain_threshold})"
                )
                logger.info(reason)
                return TerminationVerdict(
                    signal=TerminationSignal.INFO_GAIN_DECAY,
                    action=TerminationAction.TERMINATE,
                    confidence=0.5,
                    reason=reason,
                    should_terminate=True,
                    should_compress=True,
                    metadata={
                        "avg_novelty": avg_novelty,
                        "threshold": self._info_gain_threshold,
                        "window_size": self._info_gain_window,
                        "recent_novelties": novelties[-self._info_gain_window:],
                    },
                )

        return None

    @staticmethod
    def _compute_semantic_novelty(new_text: str, existing_text: str) -> float:
        if not new_text or not existing_text:
            return 1.0

        new_words = set(re.findall(r"\w+", new_text.lower()))
        existing_words = set(re.findall(r"\w+", existing_text.lower()))

        if not existing_words:
            return 1.0

        overlap = new_words & existing_words
        word_overlap = len(overlap) / len(new_words) if new_words else 0.0

        embedding_sim = ContextTerminationJudge._embedding_similarity(new_text[:500], existing_text[:500])

        if embedding_sim is not None:
            novelty = 1.0 - (0.4 * word_overlap + 0.6 * embedding_sim)
        else:
            novelty = 1.0 - word_overlap

        return max(0.0, min(1.0, novelty))

    @staticmethod
    def _extract_recent_messages(state: Dict[str, Any], n_rounds: int) -> List[Any]:
        messages = state.get("messages", [])
        if not messages:
            return []

        ai_indices = []
        for i, msg in enumerate(messages):
            if isinstance(msg, AIMessage):
                ai_indices.append(i)

        if not ai_indices:
            return []

        if len(ai_indices) <= n_rounds:
            return list(messages)

        start_index = ai_indices[-n_rounds]
        return messages[start_index:]

    @staticmethod
    def _has_tool_calls_in_messages(messages: List[Any]) -> bool:
        for msg in messages:
            if isinstance(msg, AIMessage) and hasattr(msg, "tool_calls") and msg.tool_calls:
                return True
            if isinstance(msg, ToolMessage):
                return True
        return False

    @staticmethod
    def _has_summary_language(text: str) -> bool:
        if not text:
            return False
        for pattern in _SUMMARY_PATTERNS:
            if pattern.search(text):
                return True
        return False

    @staticmethod
    def _embedding_similarity(text_a: str, text_b: str) -> Optional[float]:
        try:
            from Django_xm.apps.knowledge.services.embedding_service import get_embeddings
            import numpy as np
            embeddings = get_embeddings()
            vec_a = embeddings.embed_query(text_a[:500])
            vec_b = embeddings.embed_query(text_b[:500])
            vec_a_arr = np.array(vec_a)
            vec_b_arr = np.array(vec_b)
            norm_a = np.linalg.norm(vec_a_arr)
            norm_b = np.linalg.norm(vec_b_arr)
            if norm_a == 0 or norm_b == 0:
                return None
            return float(np.dot(vec_a_arr, vec_b_arr) / (norm_a * norm_b))
        except Exception:
            return None
