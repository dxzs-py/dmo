"""
上下文熔断器 - 指令注入检测与用户输入消毒

（A15 死链清理：原 record_tool_call 工具死循环检测与 save_checkpoint/rollback
检查点回滚链路无真实驱动方——record_tool_call 全仓零调用（tripped 恒 False）、
save_checkpoint 仅被 manager 每次构建上下文时调用一次且产物永不回滚（deepcopy
全量消息纯开销），已整体移除；注入检测与输入消毒为存活能力保留。）
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from Django_xm.apps.core.logging_utils import get_logger

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


@dataclass
class CircuitBreakerState:
    """熔断器状态（A15 瘦身后仅剩注入检测标记）"""

    injection_detected: bool = False


class ContextCircuitBreaker:
    def __init__(self) -> None:
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

    def reset(self) -> None:
        self._state = CircuitBreakerState()
        logger.info("熔断器状态已重置")
