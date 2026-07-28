"""
上下文 XML 分区组装器

将多源上下文按 XML 标签分区组装为结构化字符串，供 LLM 消费：
1. 六大分区：<system> / <memory> / <tools> / <history> / <state> / <user_query>
2. 空分区自动跳过，不输出空标签
3. BuildMode 控制是否注入 tools / memory 分区
4. 链式 API：add_xxx() 返回 self，支持流畅调用
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from Django_xm.apps.core.config import get_logger

logger = get_logger(__name__)


class BuildMode(Enum):
    FULL = "full"
    AGENT = "agent"
    CHAT = "chat"
    MINIMAL = "minimal"


@dataclass(frozen=True)
class ContextSection:
    tag: str
    required_modes: tuple[BuildMode, ...] | None = None

    @property
    def is_conditional(self) -> bool:
        return self.required_modes is not None

    def allowed_in(self, mode: BuildMode) -> bool:
        if not self.is_conditional:
            return True
        return mode in self.required_modes


SECTIONS: dict[str, ContextSection] = {
    "system": ContextSection(tag="system"),
    "memory": ContextSection(tag="memory", required_modes=(BuildMode.FULL, BuildMode.AGENT)),
    "tools": ContextSection(tag="tools", required_modes=(BuildMode.FULL, BuildMode.AGENT)),
    "history": ContextSection(tag="history"),
    "state": ContextSection(tag="state"),
    "user_query": ContextSection(tag="user_query"),
}

_SECTION_ORDER = ("system", "memory", "tools", "history", "state", "user_query")


class ContextBuilder:

    def __init__(self, mode: BuildMode = BuildMode.FULL):
        self._mode = mode
        self._parts: dict[str, list[str]] = {key: [] for key in _SECTION_ORDER}

    @property
    def mode(self) -> BuildMode:
        return self._mode

    def add_system(self, content: str) -> ContextBuilder:
        return self._append("system", content)

    def add_memory(self, content: str) -> ContextBuilder:
        return self._append("memory", content)

    def add_tools(self, content: str) -> ContextBuilder:
        return self._append("tools", content)

    def add_history(self, content: str) -> ContextBuilder:
        return self._append("history", content)

    def add_state(self, content: str) -> ContextBuilder:
        return self._append("state", content)

    def add_query(self, content: str) -> ContextBuilder:
        return self._append("user_query", content)

    def build(self) -> str:
        blocks: list[str] = []
        for key in _SECTION_ORDER:
            section = SECTIONS[key]
            if section.is_conditional and not section.allowed_in(self._mode):
                continue
            merged = "\n".join(self._parts[key]).strip()
            if not merged:
                continue
            blocks.append(f"<{section.tag}>\n{merged}\n</{section.tag}>")

        result = "\n\n".join(blocks)
        logger.debug(
            "上下文组装完成: mode=%s, 分区数=%d, 字符数=%d",
            self._mode.value,
            len(blocks),
            len(result),
        )
        return result

    def reset(self) -> ContextBuilder:
        self._parts = {key: [] for key in _SECTION_ORDER}
        return self

    def _append(self, key: str, content: str) -> ContextBuilder:
        if content and content.strip():
            self._parts[key].append(content.strip())
        return self


def create_context_builder(mode: str = "full") -> ContextBuilder:
    return ContextBuilder(mode=BuildMode(mode))
