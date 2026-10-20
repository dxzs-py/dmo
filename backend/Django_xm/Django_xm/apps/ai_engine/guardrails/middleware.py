"""
Guardrails 中间件 - 基于 LangChain AgentMiddleware 实现

使用 LangChain v1.2+ 的原生 Middleware 机制，
在 Agent 的 ReAct 循环内部拦截模型调用和工具调用。

核心能力：
1. wrap_model_call: 拦截每次模型调用，验证输入和输出
2. wrap_tool_call: 拦截每次工具调用，使用 ToolCallRequest 模式验证参数和结果
3. before_model / after_model: 节点式钩子，状态检查和日志
4. before_agent / after_agent: Agent 生命周期钩子，状态管理和审计
5. awrap_*: 全异步支持

参考：
- https://docs.langchain.com/oss/python/langchain/middleware/custom
"""

import asyncio
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from langchain.agents.middleware import (
    AgentMiddleware,
    AgentState,
    ExtendedModelResponse,
    ModelRequest,
    ModelResponse,
    ToolCallRequest,
)
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.runtime import Runtime
from langgraph.types import Command

from Django_xm.common.messages import content_to_str, message_text

from .content_filters import ContentFilter
from .input_validators import InputValidator
from .output_validators import OutputValidator

logger = logging.getLogger(__name__)


_LOOP_JUDGE_PROMPT = """你是一个循环检测器。分析以下 AI Agent 最近的工具调用历史，判断它是否陷入了循环。

重要背景信息：
- Agent 已执行 {total_tool_calls} 次工具调用、{total_model_calls} 次模型调用
- 研究任务早期阶段（前 15 次工具调用）连续搜索是正常的，不算循环
- Agent 在执行复杂任务时，可能多次调用同一工具但使用不同参数（如搜索不同关键词），这是正常的
- 只有当 Agent 重复调用相同工具和参数，或者反复尝试相同策略但没有取得进展时，才是循环

最近的工具调用历史：
{tool_calls_summary}

请判断：这个 Agent 是陷入了循环（loop），还是在正常推进任务（progress）？
只回复一个词：loop 或 progress"""


@dataclass(frozen=True)
class _LoopCheckOutcome:
    """循环检测锁内廉价阶段的判定结果（E1a）。

    - reason 非 None：已得出循环原因（L1 精确重复 / L2/L3 模式异常 / L4
      预算耗尽 / 无需检查），调用方直接以此结束，无需 judge。
    - needs_judge：L2/L3 疑似且预算允许，需在锁外执行辅助模型 judge。
      helper_model 与 summary/counts 为锁内快照——judge 不再读共享状态，
      避免锁外读取竞态；judge_started_at 供冷却时间戳使用（与原实现
      "judge 前取 now"一致）。
    """

    reason: str | None = None
    needs_judge: bool = False
    helper_model: Any = None
    summary: str = ""
    total_tool_calls: int = 0
    total_model_calls: int = 0
    judge_started_at: float = 0.0


class RateLimitMiddleware(AgentMiddleware):
    """
    多层智能循环检测中间件

    四层检测架构：
    - Layer 1: 精确重复检测（零成本，即时终止）
    - Layer 2: 模式异常检测（零成本，标记疑似）
    - Layer 3: 进展停滞检测（零成本，标记疑似）
    - Layer 4: 辅助模型智能判断（低成本，按需触发）

    安全兜底：总调用 > max_total_calls 或频率 > max_calls_per_second 才终止

    graceful_degradation 模式（推荐用于聊天）：
    检测到循环时不抛异常，而是返回 ToolMessage 告诉 LLM 停下来总结，
    让 Agent 自然退出循环，保留已收集的上下文。

    职责边界（ARCH-12 限流双轨收敛）：
    本中间件是 agent 工具调用频率限制的唯一实现（限流器单轨），
    ToolUsageGuard 已退化为重复调用去重 + 循环检测，不再做频率限制；
    阈值单源维护于 capabilities.builtin.RateLimitCapability。

    依赖倒置（ARCH-03 forbidden 契约）：
    ai_engine 禁止 import research/knowledge/learning，任务取消检查
    通过 task_cancelled_checker 由调用方注入（如 deep research 链路
    传入基于 research.models 的检查函数），本模块不感知具体任务模型。
    """

    PROGRESS_TOOLS = frozenset(
        {
            "write_file",
            "write_research_file",
            "write",
            "create_file",
            "save_file",
        }
    )

    # 循环检测时注入的引导消息，促使 LLM 自然停止工具调用
    _LOOP_STOP_MESSAGE = (
        "⚠️ 检测到你可能陷入了重复操作循环（{reason}）。"
        "请立即停止调用工具，基于已收集的信息整理并输出最终结果。"
        "如果正在做研究，请立即使用 write_file 写出最终研究报告，不要再搜索或重复操作。"
    )

    def __init__(
        self,
        max_total_calls: int = 300,
        max_calls_per_second: float = 5.0,
        exact_dup_window: int = 5,
        exact_dup_threshold: int = 3,
        consecutive_same_tool_limit: int = 10,
        diversity_window: int = 15,
        diversity_min_ratio: float = 0.25,
        progress_window: int = 20,
        min_calls_before_check: int = 20,
        llm_judge_max_calls: int = 3,
        task_id: str | None = None,
        task_cancelled_checker: Callable[[], None] | None = None,
        graceful_degradation: bool = True,
        warning_milestones: list[int] | None = None,
    ):
        super().__init__()
        self.max_total_calls = max_total_calls
        self.max_calls_per_second = max_calls_per_second
        self.exact_dup_window = exact_dup_window
        self.exact_dup_threshold = exact_dup_threshold
        self.consecutive_same_tool_limit = consecutive_same_tool_limit
        self.diversity_window = diversity_window
        self.diversity_min_ratio = diversity_min_ratio
        self.progress_window = progress_window
        self.min_calls_before_check = min_calls_before_check
        self.llm_judge_max_calls = llm_judge_max_calls
        self._task_id = task_id
        # 任务取消检查器（依赖倒置，ARCH-03）：由调用方注入，抛 RuntimeError
        # 表示任务已取消。未注入时不做取消检查（ai_engine 不感知 research 模型）。
        self._task_cancelled_checker = task_cancelled_checker
        self.graceful_degradation = graceful_degradation
        self.warning_milestones = warning_milestones or [50, 100, 200]
        self._model_call_count = 0
        self._tool_call_count = 0
        self._call_timestamps: list[float] = []
        self._recent_tool_calls: list[dict[str, Any]] = []
        self._consecutive_same_count = 0
        self._last_tool_name: str | None = None
        self._llm_judge_count = 0
        self._llm_judge_skip_until: float = 0.0
        self._lock = threading.Lock()
        self._loop_detected_reason: str | None = None
        self._warned_milestones: set = set()

    def _check_task_cancelled(self) -> None:
        """任务取消检查（依赖倒置，ARCH-03）。

        checker 由调用方注入；checker 内部抛 RuntimeError 表示任务已取消，
        向上透传中断执行；其余异常降级为不检查（不阻断主流程）。
        未注入 checker 时直接跳过。
        """
        if self._task_cancelled_checker is None:
            return
        try:
            self._task_cancelled_checker()
        except RuntimeError:
            raise
        except Exception:
            # 检查任务状态失败不应阻断主流程，降级为不检查
            logger.debug("检查任务取消状态失败", exc_info=True)

    def _check_rate_limit(self, call_type: str) -> str | None:
        """检查速率和总量限制，返回循环原因字符串或 None"""
        now = time.time()
        with self._lock:
            self._call_timestamps = [t for t in self._call_timestamps if now - t < 1.0]
            if len(self._call_timestamps) >= self.max_calls_per_second:
                reason = f"{call_type}调用频率超过{self.max_calls_per_second}/s"
                if not self.graceful_degradation:
                    raise RuntimeError(f"速率限制: {reason}，Agent 可能陷入循环，已终止执行。")
                return reason
            self._call_timestamps.append(now)

            total = self._model_call_count + self._tool_call_count
            if total >= self.max_total_calls:
                reason = f"总调用次数({total})超过{self.max_total_calls}"
                if not self.graceful_degradation:
                    raise RuntimeError(f"总量限制: Agent {reason}，可能陷入无限循环，已终止执行。")
                return reason
        return None

    def _check_exact_duplicate(self, tool_name: str, tool_args: Any) -> str | None:
        """精确重复检测，返回循环原因或 None"""
        self._recent_tool_calls.append(
            {
                "name": tool_name,
                "args_hash": self._args_fingerprint(tool_args),
                "args_preview": self._args_preview(tool_args),
            }
        )
        if len(self._recent_tool_calls) > max(self.exact_dup_window, self.diversity_window, self.progress_window):
            self._recent_tool_calls = self._recent_tool_calls[
                -max(self.exact_dup_window, self.diversity_window, self.progress_window) :
            ]

        if len(self._recent_tool_calls) >= self.exact_dup_threshold:
            recent = self._recent_tool_calls[-self.exact_dup_window :]

            # 参数递进检测：同工具但参数多样性高=合理重复，跳过
            same_tool_calls = [tc for tc in recent if tc["name"] == tool_name]
            if len(same_tool_calls) >= 3:
                unique_args = {tc["args_hash"] for tc in same_tool_calls}
                args_diversity = len(unique_args) / len(same_tool_calls)
                if args_diversity >= 0.5:
                    return None  # 参数递进模式，跳过精确重复检测

            name_counts: dict[str, int] = {}
            for tc in recent:
                key = f"{tc['name']}:{tc['args_hash']}"
                name_counts[key] = name_counts.get(key, 0) + 1
            max_dup = max(name_counts.values())
            if max_dup >= self.exact_dup_threshold:
                dup_key = max(name_counts, key=lambda k: name_counts[k])
                reason = f"工具{dup_key.split(':')[0]}在最近{self.exact_dup_window}次调用中重复{max_dup}次"
                if not self.graceful_degradation:
                    raise RuntimeError(f"精确重复检测(L1): {reason}，Agent 陷入循环，已终止执行。")
                return reason
        return None

    def _check_consecutive_same_tool(self, tool_name: str) -> bool:
        if tool_name == self._last_tool_name:
            self._consecutive_same_count += 1
        else:
            self._consecutive_same_count = 1
            self._last_tool_name = tool_name
        return self._consecutive_same_count >= self.consecutive_same_tool_limit

    def _check_diversity_drop(self) -> bool:
        if len(self._recent_tool_calls) < self.diversity_window:
            return False
        recent = self._recent_tool_calls[-self.diversity_window :]
        unique_tools = len({tc["name"] for tc in recent})
        ratio = unique_tools / len(recent)
        return ratio < self.diversity_min_ratio

    def _check_progress_stall(self, tool_name: str) -> bool:
        if tool_name in self.PROGRESS_TOOLS:
            return False
        if len(self._recent_tool_calls) < self.progress_window:
            return False
        recent = self._recent_tool_calls[-self.progress_window :]
        return not any(tc["name"] in self.PROGRESS_TOOLS for tc in recent)

    def _evaluate_tool_loop(self, tool_name: str, tool_args: Any) -> _LoopCheckOutcome:
        """锁内廉价判定阶段（E1a：原 _check_tool_loop 的非 judge 部分）。

        锁内只做内存计数与模式判定（L1 精确重复 / L2 模式 / L3 停滞 / L4
        预算检查）；辅助模型的同步 invoke 属秒级网络调用，一律移至锁外
        （_resolve_loop_reason / _aresolve_loop_reason），避免长持锁阻塞
        其他工具调用的计数与判定。hard 模式的 RuntimeError 在锁内抛出，
        with 保证释放。
        """
        with self._lock:
            self._tool_call_count += 1
            # L1: 精确重复检测
            dup_reason = self._check_exact_duplicate(tool_name, tool_args)
            if dup_reason:
                return _LoopCheckOutcome(reason=dup_reason)

            if self._tool_call_count < self.min_calls_before_check:
                return _LoopCheckOutcome()

            pattern_suspect = self._check_consecutive_same_tool(tool_name) or self._check_diversity_drop()
            progress_suspect = self._check_progress_stall(tool_name)

            if not pattern_suspect and not progress_suspect:
                return _LoopCheckOutcome()

            now = time.time()
            if now < self._llm_judge_skip_until:
                return _LoopCheckOutcome()

            helper_model = self._get_helper_model()
            if helper_model is None:
                reason = f"模式异常检测(L2/L3): 连续同工具={pattern_suspect}, 进展停滞={progress_suspect}"
                if not self.graceful_degradation:
                    raise RuntimeError(f"{reason}，已终止执行。")
                return _LoopCheckOutcome(reason=reason)

            if self._llm_judge_count >= self.llm_judge_max_calls:
                reason = "辅助模型判断次数耗尽(L4)，疑似循环模式持续存在"
                if not self.graceful_degradation:
                    raise RuntimeError(f"{reason}，已终止执行。")
                return _LoopCheckOutcome(reason=reason)

            # 本轮需要 judge：锁内预扣预算（原实现为 judge 后 +1 且无论结果
            # 都 +1，预扣语义等价，并防止锁外并发窗口超发名额）。
            self._llm_judge_count += 1
            return _LoopCheckOutcome(
                needs_judge=True,
                helper_model=helper_model,
                summary=self._build_call_summary(),
                total_tool_calls=self._tool_call_count,
                total_model_calls=self._model_call_count,
                judge_started_at=now,
            )

    def _finalize_judge(self, outcome: _LoopCheckOutcome, is_loop: bool) -> str | None:
        """L4 judge 完成后的状态落地（judge 在锁外执行，此处短暂加锁更新）。"""
        if is_loop:
            reason = "辅助模型判断(L4)确认Agent陷入循环"
            if not self.graceful_degradation:
                raise RuntimeError(f"{reason}，已终止执行。")
            return reason
        with self._lock:
            self._llm_judge_skip_until = outcome.judge_started_at + 10.0
            judge_count = self._llm_judge_count
        logger.info(
            f"辅助模型判断(L4): Agent 正常推进，继续执行 (判断 {judge_count}/{self.llm_judge_max_calls})"
        )
        return None

    def _resolve_loop_reason(self, tool_name: str, tool_args: Any) -> str | None:
        """同步路径总控：锁内廉价判定 → 锁外执行 L4 judge。"""
        outcome = self._evaluate_tool_loop(tool_name, tool_args)
        if outcome.needs_judge:
            is_loop = self._llm_judge_loop(
                outcome.helper_model,
                summary=outcome.summary,
                total_tool_calls=outcome.total_tool_calls,
                total_model_calls=outcome.total_model_calls,
            )
            return self._finalize_judge(outcome, is_loop)
        return outcome.reason

    async def _aresolve_loop_reason(self, tool_name: str, tool_args: Any) -> str | None:
        """异步路径总控：锁内廉价判定 → asyncio.to_thread 执行 L4 judge。

        judge 的 model.invoke 是同步秒级网络调用，必须移出事件循环线程，
        否则阻塞该 loop 上所有协程（E1a 修复点）。
        """
        outcome = self._evaluate_tool_loop(tool_name, tool_args)
        if outcome.needs_judge:
            is_loop = await asyncio.to_thread(
                self._llm_judge_loop,
                outcome.helper_model,
                summary=outcome.summary,
                total_tool_calls=outcome.total_tool_calls,
                total_model_calls=outcome.total_model_calls,
            )
            return self._finalize_judge(outcome, is_loop)
        return outcome.reason

    def _get_helper_model(self):
        try:
            from Django_xm.apps.ai_engine.services.llm_factory import get_helper_model

            return get_helper_model()
        except Exception as e:
            logger.warning(f"获取辅助模型失败: {e}")
            return None

    def _llm_judge_loop(
        self,
        model,
        summary: str | None = None,
        total_tool_calls: int | None = None,
        total_model_calls: int | None = None,
    ) -> bool:
        """辅助模型 L4 判断（fail-open：任何异常返回 False 继续执行）。

        summary/counts 由调用方传锁内快照（E1a：judge 已移出锁，读快照
        避免锁外与并发计数竞争）；缺省时退回读当前实例状态（兼容直接调用）。
        """
        try:
            if summary is None:
                summary = self._build_call_summary()
            if total_tool_calls is None:
                total_tool_calls = self._tool_call_count
            if total_model_calls is None:
                total_model_calls = self._model_call_count
            prompt = _LOOP_JUDGE_PROMPT.format(
                tool_calls_summary=summary,
                total_tool_calls=total_tool_calls,
                total_model_calls=total_model_calls,
            )
            from langchain_core.messages import HumanMessage

            response = model.invoke([HumanMessage(content=prompt)])
            answer = response.content.strip().lower()
            logger.info(f"辅助模型判断结果: {answer} (工具={total_tool_calls}, 模型={total_model_calls})")
            first_word = answer.split()[0] if answer.split() else ""
            return first_word == "loop"
        except Exception as e:
            logger.warning(f"辅助模型判断失败，默认继续: {e}")
            return False

    def _build_call_summary(self) -> str:
        recent = self._recent_tool_calls[-10:]
        lines = []
        for i, tc in enumerate(recent, 1):
            lines.append(f"{i}. {tc['name']}({tc['args_preview']})")
        return "\n".join(lines)

    @staticmethod
    def _args_fingerprint(args: Any) -> str:
        if args is None:
            return ""
        if isinstance(args, dict):
            try:
                import hashlib
                import json

                serialized = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
                return hashlib.md5(serialized.encode(), usedforsecurity=False).hexdigest()[:12]
            except (TypeError, ValueError):
                return str(args)[:64]
        return str(args)[:64]

    @staticmethod
    def _args_preview(args: Any) -> str:
        if args is None:
            return ""
        if isinstance(args, dict):
            parts = []
            for k, v in list(args.items())[:3]:
                s = str(v)
                parts.append(f"{k}={s[:40]}{'...' if len(s) > 40 else ''}")
            preview = ", ".join(parts)
            if len(args) > 3:
                preview += f", ... (+{len(args) - 3})"
            return preview
        return str(args)[:60]

    def before_model(self, state: AgentState, runtime: Runtime) -> None:
        with self._lock:
            self._model_call_count += 1
        rate_reason = self._check_rate_limit("model")
        if rate_reason and self.graceful_degradation:
            logger.warning(f"模型调用速率/总量限制触发(优雅模式): {rate_reason}")

    def _make_loop_stop_message(self, tool_call_id: str, reason: str) -> ToolMessage:
        """生成循环停止引导消息，促使 LLM 自然停止工具调用"""
        self._loop_detected_reason = reason
        logger.warning(
            f"循环检测触发(优雅降级): {reason}, 工具调用={self._tool_call_count}, 模型调用={self._model_call_count}"
        )
        return ToolMessage(
            content=self._LOOP_STOP_MESSAGE.format(reason=reason),
            tool_call_id=tool_call_id,
        )

    def _check_milestone_warnings(self) -> None:
        """检查是否达到告警里程碑，发出多级告警"""
        total = self._tool_call_count + self._model_call_count
        for milestone in self.warning_milestones:
            if total >= milestone and milestone not in self._warned_milestones:
                self._warned_milestones.add(milestone)
                remaining = self.max_total_calls - total
                pct = total / self.max_total_calls * 100
                logger.warning(
                    f"📊 Agent 调用里程碑: 已执行 {total} 次调用 "
                    f"(工具={self._tool_call_count}, 模型={self._model_call_count}), "
                    f"预算使用 {pct:.0f}%, 剩余 {remaining} 次"
                )

    def wrap_tool_call(
        self,
        tool_call: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        tool_name = tool_call.tool_call.get("name", "unknown")
        tool_args = tool_call.tool_call.get("args", {})
        tool_call_id = tool_call.tool_call.get("id", "")
        # E1a：计数与廉价判定在 _evaluate_tool_loop 锁内完成，L4 judge 在锁外执行
        loop_reason = self._resolve_loop_reason(tool_name, tool_args)
        if loop_reason and self.graceful_degradation:
            return self._make_loop_stop_message(tool_call_id, loop_reason)
        rate_reason = self._check_rate_limit("tool")
        if rate_reason and self.graceful_degradation:
            return self._make_loop_stop_message(tool_call_id, rate_reason)
        self._check_task_cancelled()
        self._check_milestone_warnings()
        return handler(tool_call)

    async def awrap_tool_call(
        self,
        tool_call: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        tool_name = tool_call.tool_call.get("name", "unknown")
        tool_args = tool_call.tool_call.get("args", {})
        tool_call_id = tool_call.tool_call.get("id", "")
        # E1a：L4 judge 经 asyncio.to_thread 在线程池执行，不阻塞事件循环
        loop_reason = await self._aresolve_loop_reason(tool_name, tool_args)
        if loop_reason and self.graceful_degradation:
            return self._make_loop_stop_message(tool_call_id, loop_reason)
        rate_reason = self._check_rate_limit("tool")
        if rate_reason and self.graceful_degradation:
            return self._make_loop_stop_message(tool_call_id, rate_reason)
        self._check_task_cancelled()
        self._check_milestone_warnings()
        return await handler(tool_call)

    def after_agent(self, state: AgentState, runtime: Runtime) -> None:
        logger.info(
            f"Agent 执行完成 - 模型调用: {self._model_call_count}, "
            f"工具调用: {self._tool_call_count}, "
            f"辅助模型判断: {self._llm_judge_count}"
        )


def create_rate_limit_middleware(
    max_total_calls: int = 300,
    max_calls_per_second: float = 5.0,
    exact_dup_window: int = 5,
    exact_dup_threshold: int = 3,
    consecutive_same_tool_limit: int = 8,
    diversity_window: int = 15,
    diversity_min_ratio: float = 0.2,
    progress_window: int = 20,
    min_calls_before_check: int = 15,
    llm_judge_max_calls: int = 3,
    task_id: str | None = None,
    task_cancelled_checker: Callable[[], None] | None = None,
    graceful_degradation: bool = True,
) -> RateLimitMiddleware:
    return RateLimitMiddleware(
        max_total_calls=max_total_calls,
        max_calls_per_second=max_calls_per_second,
        exact_dup_window=exact_dup_window,
        exact_dup_threshold=exact_dup_threshold,
        consecutive_same_tool_limit=consecutive_same_tool_limit,
        diversity_window=diversity_window,
        diversity_min_ratio=diversity_min_ratio,
        progress_window=progress_window,
        min_calls_before_check=min_calls_before_check,
        llm_judge_max_calls=llm_judge_max_calls,
        task_id=task_id,
        task_cancelled_checker=task_cancelled_checker,
        graceful_degradation=graceful_degradation,
    )


class GuardrailsMiddleware(AgentMiddleware):
    """
    基于 LangChain AgentMiddleware 的 Guardrails 中间件

    在 Agent 的 ReAct 循环内部生效：
    - wrap_model_call: 拦截每次模型调用，验证输入和输出
    - wrap_tool_call: 拦截每次工具调用，使用 ToolCallRequest 模式验证参数和结果
    - before_model / after_model: 节点式钩子，状态检查和日志
    - before_agent / after_agent: Agent 生命周期钩子
    """

    DANGEROUS_TOOLS = frozenset(
        {
            "delete_file",
            "rm",
            "execute_code",
            "shell_exec",
            "bash_execute",
            "repl_execute",
            "notebook_edit",
        }
    )

    def __init__(
        self,
        input_validator: InputValidator | None = None,
        output_validator: OutputValidator | None = None,
        on_input_error: Callable | None = None,
        on_output_error: Callable | None = None,
        raise_on_error: bool = True,
        validate_tool_calls: bool = True,
        max_message_count: int = 100,
        blocked_tools: set | None = None,
    ):
        super().__init__()
        self.input_validator = input_validator or InputValidator()
        self.output_validator = output_validator or OutputValidator()
        self.on_input_error = on_input_error
        self.on_output_error = on_output_error
        self.raise_on_error = raise_on_error
        self.validate_tool_calls = validate_tool_calls
        self.max_message_count = max_message_count
        self.blocked_tools = self.DANGEROUS_TOOLS | (blocked_tools or set())
        self._agent_start_time: float | None = None
        self._tool_call_count: int = 0
        self._model_call_count: int = 0
        self._count_lock = threading.Lock()

    def before_agent(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        self._agent_start_time = time.time()
        with self._count_lock:
            self._tool_call_count = 0
            self._model_call_count = 0
        query = ""
        messages = state.get("messages", [])
        for msg in reversed(messages):
            if isinstance(msg, HumanMessage):
                query = message_text(msg)[:100]
                break
        logger.info(f"[Guardrails] Agent 开始执行, 查询: {query}...")
        return None

    def after_agent(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        duration = time.time() - self._agent_start_time if self._agent_start_time else 0
        with self._count_lock:
            model_calls = self._model_call_count
            tool_calls = self._tool_call_count
        logger.info(
            f"[Guardrails] Agent 执行完成, 耗时: {duration:.2f}s, 模型调用: {model_calls} 次, 工具调用: {tool_calls} 次"
        )
        self._agent_start_time = None
        return None

    def before_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        messages = state.get("messages", [])
        if len(messages) > self.max_message_count:
            logger.warning(f"消息数量超过 {self.max_message_count} 条，可能影响性能")
        return None

    def after_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        with self._count_lock:
            self._model_call_count += 1
        messages = state.get("messages", [])
        if messages:
            last_msg = messages[-1]
            if isinstance(last_msg, AIMessage) and last_msg.content:
                logger.debug(f"模型输出长度: {len(last_msg.content)} 字符")
        return None

    def _validate_input(self, text: str, context: str = "输入") -> None:
        validation_result = self.input_validator.validate(text)
        if not validation_result.is_valid:
            logger.warning(f"{context}验证失败: {validation_result.errors}")
            if self.on_input_error:
                self.on_input_error(text, validation_result)
            if self.raise_on_error:
                raise ValueError(f"{context}验证失败: {', '.join(validation_result.errors)}")

    def _validate_output(self, text: str, context: str = "输出") -> None:
        validation_result = self.output_validator.validate(text)
        if not validation_result.is_valid:
            logger.warning(f"{context}验证失败: {validation_result.errors}")
            if self.on_output_error:
                self.on_output_error(text, validation_result)
            if self.raise_on_error:
                raise ValueError(f"{context}验证失败: {', '.join(validation_result.errors)}")

    def _extract_response_text(self, response: ModelResponse | ExtendedModelResponse) -> str:
        if hasattr(response, "message") and response.message:
            return response.message.content if hasattr(response.message, "content") else str(response.message)
        if hasattr(response, "output") and response.output:
            return str(response.output)
        return ""

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse | ExtendedModelResponse:
        messages = request.state.get("messages", [])
        if messages:
            last_user_msg = None
            for msg in reversed(messages):
                if isinstance(msg, HumanMessage):
                    last_user_msg = msg.content
                    break
            if last_user_msg:
                self._validate_input(message_text(last_user_msg), "模型输入")

        response = handler(request)

        response_text = self._extract_response_text(response)
        if response_text:
            self._validate_output(response_text, "模型输出")

        return response

    def wrap_tool_call(
        self,
        tool_call: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        if not self.validate_tool_calls:
            return handler(tool_call)

        tool_name = tool_call.tool_call.get("name", "")
        tool_args = tool_call.tool_call.get("args", {})

        if tool_name in self.blocked_tools:
            logger.warning(f"检测到危险工具调用: {tool_name}")
            if self.raise_on_error:
                raise ValueError(f"工具 {tool_name} 被安全策略禁止")
            return ToolMessage(
                content=f"工具 {tool_name} 被安全策略禁止",
                tool_call_id=tool_call.tool_call.get("id", ""),
            )

        for key, value in tool_args.items():
            if isinstance(value, str):
                validation_result = self.input_validator.validate(value)
                if not validation_result.is_valid:
                    logger.warning(f"工具参数验证失败 ({tool_name}.{key}): {validation_result.errors}")
                    if self.raise_on_error:
                        raise ValueError(f"工具参数验证失败: {', '.join(validation_result.errors)}")

        with self._count_lock:
            self._tool_call_count += 1
        result = handler(tool_call)

        output_text = ""
        if isinstance(result, ToolMessage):
            output_text = content_to_str(result.content)
        elif isinstance(result, str):
            output_text = result

        if output_text:
            output_result = self.output_validator.validate(output_text)
            if not output_result.is_valid:
                logger.warning(f"工具输出验证失败 ({tool_name}): {output_result.errors}")
                if self.raise_on_error:
                    raise ValueError(f"工具输出验证失败: {', '.join(output_result.errors)}")

        return result

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse | ExtendedModelResponse:
        messages = request.state.get("messages", [])
        if messages:
            last_user_msg = None
            for msg in reversed(messages):
                if isinstance(msg, HumanMessage):
                    last_user_msg = msg.content
                    break
            if last_user_msg:
                self._validate_input(last_user_msg, "模型输入(异步)")

        response = await handler(request)

        response_text = self._extract_response_text(response)
        if response_text:
            self._validate_output(response_text, "模型输出(异步)")

        return response

    async def awrap_tool_call(
        self,
        tool_call: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        if not self.validate_tool_calls:
            return await handler(tool_call)

        tool_name = tool_call.tool_call.get("name", "")
        tool_args = tool_call.tool_call.get("args", {})

        if tool_name in self.blocked_tools:
            logger.warning(f"检测到危险工具调用: {tool_name}")
            if self.raise_on_error:
                raise ValueError(f"工具 {tool_name} 被安全策略禁止")
            return ToolMessage(
                content=f"工具 {tool_name} 被安全策略禁止",
                tool_call_id=tool_call.tool_call.get("id", ""),
            )

        for key, value in tool_args.items():
            if isinstance(value, str):
                validation_result = self.input_validator.validate(value)
                if not validation_result.is_valid:
                    logger.warning(f"工具参数验证失败 ({tool_name}.{key}): {validation_result.errors}")
                    if self.raise_on_error:
                        raise ValueError(f"工具参数验证失败: {', '.join(validation_result.errors)}")

        with self._count_lock:
            self._tool_call_count += 1
        result = await handler(tool_call)

        output_text = ""
        if isinstance(result, ToolMessage):
            output_text = content_to_str(result.content)
        elif isinstance(result, str):
            output_text = result

        if output_text:
            output_result = self.output_validator.validate(output_text)
            if not output_result.is_valid:
                logger.warning(f"工具输出验证失败 ({tool_name}): {output_result.errors}")
                if self.raise_on_error:
                    raise ValueError(f"工具输出验证失败: {', '.join(output_result.errors)}")

        return result


class PIIMiddleware(AgentMiddleware):
    """
    PII（个人身份信息）检测与脱敏中间件

    在模型调用前后检测 PII，自动脱敏或拒绝包含敏感信息的请求。
    """

    def __init__(
        self,
        mask_pii: bool = True,
        reject_on_pii: bool = False,
        pii_patterns: dict[str, str] | None = None,
    ):
        super().__init__()
        self.mask_pii = mask_pii
        self.reject_on_pii = reject_on_pii
        self.pii_patterns = pii_patterns or {}
        self._content_filter = ContentFilter(
            enable_pii_detection=True,
            enable_content_safety=False,
            enable_injection_detection=False,
            mask_pii=mask_pii,
        )

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse | ExtendedModelResponse:
        messages = request.state.get("messages", [])
        if messages:
            for i, msg in enumerate(messages):
                if isinstance(msg, HumanMessage):
                    filter_result = self._content_filter.filter_input(message_text(msg))
                    if not filter_result.is_safe:
                        if self.reject_on_pii:
                            raise ValueError("输入包含个人身份信息(PII)，已被安全策略拒绝")
                        logger.warning(f"检测到 PII: {filter_result.issues}")
                        # 实际脱敏：用过滤后的内容替换原始消息
                        if self.mask_pii and filter_result.filtered_content:
                            messages[i] = HumanMessage(content=filter_result.filtered_content)
                            logger.info("已对消息中的 PII 进行脱敏处理")
        return handler(request)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse | ExtendedModelResponse:
        messages = request.state.get("messages", [])
        if messages:
            for i, msg in enumerate(messages):
                if isinstance(msg, HumanMessage):
                    filter_result = self._content_filter.filter_input(message_text(msg))
                    if not filter_result.is_safe:
                        if self.reject_on_pii:
                            raise ValueError("输入包含个人身份信息(PII)，已被安全策略拒绝")
                        logger.warning(f"检测到 PII: {filter_result.issues}")
                        # 实际脱敏：用过滤后的内容替换原始消息
                        if self.mask_pii and filter_result.filtered_content:
                            messages[i] = HumanMessage(content=filter_result.filtered_content)
                            logger.info("已对消息中的 PII 进行脱敏处理")
        return await handler(request)


def create_guardrails_middleware(
    input_validator: InputValidator | None = None,
    output_validator: OutputValidator | None = None,
    strict_mode: bool = False,
    validate_tool_calls: bool = True,
    raise_on_error: bool = True,
    blocked_tools: set | None = None,
) -> GuardrailsMiddleware:
    content_filter = ContentFilter(
        enable_pii_detection=True,
        enable_content_safety=True,
        enable_injection_detection=True,
        mask_pii=True,
    )

    return GuardrailsMiddleware(
        input_validator=input_validator
        or InputValidator(
            content_filter=content_filter,
            strict_mode=strict_mode,
        ),
        output_validator=output_validator
        or OutputValidator(
            content_filter=content_filter,
            require_sources=False,
            strict_mode=strict_mode,
        ),
        validate_tool_calls=validate_tool_calls,
        raise_on_error=raise_on_error,
        blocked_tools=blocked_tools,
    )


def create_pii_middleware(
    mask_pii: bool = True,
    reject_on_pii: bool = False,
) -> PIIMiddleware:
    return PIIMiddleware(mask_pii=mask_pii, reject_on_pii=reject_on_pii)


def build_middleware_stack(
    enable_guardrails: bool = True,
    enable_pii: bool = False,
    enable_rate_limit: bool = True,
    guardrails_strict: bool = False,
    pii_reject: bool = False,
    approval_tools: set | None = None,
    on_approval_request: Callable | None = None,
    extra_middleware: list[AgentMiddleware] | None = None,
) -> list[AgentMiddleware]:
    """
    构建 Middleware 栈，按优先级排列：
    RateLimitMiddleware -> PIIMiddleware -> GuardrailsMiddleware
    """
    stack: list[AgentMiddleware] = []

    if enable_rate_limit:
        stack.append(create_rate_limit_middleware())

    if enable_pii:
        stack.append(create_pii_middleware(reject_on_pii=pii_reject))

    if enable_guardrails:
        stack.append(
            create_guardrails_middleware(
                strict_mode=guardrails_strict,
                raise_on_error=guardrails_strict,
            )
        )

    if extra_middleware:
        stack.extend(extra_middleware)

    return stack


class GroqToolCallCompatMiddleware(AgentMiddleware):
    """
    Groq 模型工具调用兼容性中间件

    Groq 的 llama 模型偶尔生成格式不规范的 tool call（将工具名和参数拼成一个字符串），
    导致 Groq API 返回 tool call validation 错误。

    本中间件在模型调用失败时，自动重试不带工具的纯对话模式，
    并在响应中附加提示信息告知用户工具不可用。
    """

    PROVIDER_KEYWORDS = ("groq", "llama")

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse | ExtendedModelResponse:
        tools = getattr(request, "tools", None)
        if not tools:
            return await handler(request)

        model = getattr(request, "model", None)
        model_name = ""
        if model:
            model_name = getattr(model, "model_name", "") or getattr(model, "model", "") or ""
            model_name = str(model_name).lower()

        is_groq = any(kw in model_name for kw in self.PROVIDER_KEYWORDS)
        if not is_groq and not isinstance(model, type(None)):
            model_cls = type(model).__name__.lower()
            is_groq = "groq" in model_cls

        if not is_groq:
            return await handler(request)

        try:
            return await handler(request)
        except Exception as e:
            err_msg = str(e).lower()
            is_tool_validation = (
                "tool call validation" in err_msg
                or "not in request.tools" in err_msg
                or "tool_call_validation" in err_msg
            )
            if not is_tool_validation:
                raise

            logger.warning(f"[GroqCompat] Groq tool call 验证失败，降级为无工具模式: {e}")
            no_tool_request = request.override(tools=[]) if hasattr(request, "override") else request
            response = await handler(no_tool_request)

            if hasattr(response, "messages") and response.messages:
                last_msg = response.messages[-1]
                if isinstance(last_msg, AIMessage) and last_msg.content:
                    notice = "\n\n> ⚠️ 当前模型暂不支持工具调用，已切换为纯对话模式。"
                    patched = AIMessage(
                        content=last_msg.content + notice,
                        id=getattr(last_msg, "id", None),
                    )
                    new_messages = [*list(response.messages[:-1]), patched]
                    if hasattr(response, "model_copy") and callable(response.model_copy):
                        response = response.model_copy(update={"messages": new_messages})
                    elif hasattr(response, "__dict__"):
                        response = type(response)(
                            **{k: new_messages if k == "messages" else v for k, v in response.__dict__.items()}
                        )
                    else:
                        response.messages = new_messages
            return response
