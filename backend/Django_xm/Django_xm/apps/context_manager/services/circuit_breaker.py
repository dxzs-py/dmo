from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from Django_xm.apps.context_manager.config import context_settings
from Django_xm.apps.core.config import get_logger

logger = get_logger(__name__)

_INJECTION_TAG_PATTERNS = [
    re.compile(r"<\s*system\s*>", re.IGNORECASE),
    re.compile(r"<\s*instruction\s*>", re.IGNORECASE),
    re.compile(r"<\s*command\s*>", re.IGNORECASE),
    re.compile(r"<\s*prompt\s*>", re.IGNORECASE),
    re.compile(r"<\s*/\s*system\s*>", re.IGNORECASE),
    re.compile(r"<\s*/\s*instruction\s*>", re.IGNORECASE),
    re.compile(r"<\s*/\s*command\s*>", re.IGNORECASE),
    re.compile(r"<\s*/\s*prompt\s*>", re.IGNORECASE),
]

_INJECTION_PHRASE_PATTERNS = [
    re.compile(r"忽略\s*之前\s*的\s*指令", re.IGNORECASE),
    re.compile(r"忽略\s*以上\s*指令", re.IGNORECASE),
    re.compile(r"ignore\s+previous\s+instructions?", re.IGNORECASE),
    re.compile(r"disregard\s+all", re.IGNORECASE),
    re.compile(r"forget\s+previous\s+instructions?", re.IGNORECASE),
    re.compile(r"disregard\s+previous\s+instructions?", re.IGNORECASE),
    re.compile(r"你\s*现在\s*是", re.IGNORECASE),
    re.compile(r"you\s+are\s+now", re.IGNORECASE),
    re.compile(r"jailbreak", re.IGNORECASE),
    re.compile(r"DAN\s+mode", re.IGNORECASE),
]

_LOOP_THRESHOLD = context_settings.loop_same_call_terminate_threshold


@dataclass
class CircuitBreakerState:
    tripped: bool = False
    trip_reason: str | None = None
    tool_call_history: list[dict[str, Any]] = field(default_factory=list)
    consecutive_same_calls: int = 0
    last_tool_name: str | None = None
    last_tool_args: str | None = None
    checkpoint_stack: list[list[dict[str, Any]]] = field(default_factory=list)
    injection_detected: bool = False


class ContextCircuitBreaker:

    def __init__(self, loop_threshold: int = _LOOP_THRESHOLD) -> None:
        self._loop_threshold = loop_threshold
        self._state = CircuitBreakerState()

    @property
    def state(self) -> CircuitBreakerState:
        return self._state

    def detect_injection(self, text: str) -> bool:
        if not text:
            return False
        for pattern in _INJECTION_TAG_PATTERNS:
            if pattern.search(text):
                self._state.injection_detected = True
                logger.warning(f"指令注入检测-标签匹配: {pattern.pattern}")
                return True
        for pattern in _INJECTION_PHRASE_PATTERNS:
            if pattern.search(text):
                self._state.injection_detected = True
                logger.warning(f"指令注入检测-短语匹配: {pattern.pattern}")
                return True
        return False

    def sanitize_user_input(self, text: str) -> str:
        if not text:
            return "<user_input></user_input>"
        escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return f"<user_input>{escaped}</user_input>"

    def record_tool_call(self, tool_name: str, args: Any) -> bool:
        args_repr = str(args)
        self._state.tool_call_history.append({
            "tool_name": tool_name,
            "args": args_repr,
        })

        if (
            tool_name == self._state.last_tool_name
            and args_repr == self._state.last_tool_args
        ):
            self._state.consecutive_same_calls += 1
        else:
            self._state.consecutive_same_calls = 1

        self._state.last_tool_name = tool_name
        self._state.last_tool_args = args_repr

        if self._state.consecutive_same_calls >= self._loop_threshold:
            self._state.tripped = True
            self._state.trip_reason = (
                f"死循环检测: 工具 {tool_name} 连续调用 "
                f"{self._state.consecutive_same_calls} 次"
            )
            logger.warning(self._state.trip_reason)
            return False

        return True

    def save_checkpoint(self, messages: list[dict[str, Any]]) -> None:
        import copy
        self._state.checkpoint_stack.append(copy.deepcopy(messages))
        logger.debug(f"安全检查点已保存, 栈深度={len(self._state.checkpoint_stack)}")

    def rollback(self) -> list[dict[str, Any]]:
        if not self._state.checkpoint_stack:
            logger.warning("无可用检查点，回滚返回空列表")
            return []
        messages = self._state.checkpoint_stack.pop()
        logger.info(f"已回滚至安全检查点, 剩余栈深度={len(self._state.checkpoint_stack)}")
        return messages

    def reset(self) -> None:
        self._state = CircuitBreakerState()
        logger.info("熔断器状态已重置")
