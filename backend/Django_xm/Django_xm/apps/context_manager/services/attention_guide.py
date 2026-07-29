"""
注意力引导器 - 解决 Lost in the Middle 问题

基于 LLM 注意力分布特征，对上下文分区进行重排序：
1. 关键指令置于上下文首部（首因效应）
2. 重要约束置于上下文尾部（近因效应）
3. 动态维护 <state> 块，跟踪当前目标、进度、待办
4. 对优先级指令使用 !!!重要!!! 高亮标记

参考：
- Lost in the Middle: How Language Models Use Long Contexts (Liu et al., 2023)
- https://arxiv.org/abs/2307.03172
"""

import re
from dataclasses import dataclass, field
from enum import Enum

from Django_xm.apps.core.config import get_logger

logger = get_logger(__name__)


class SectionPriority(Enum):
    CRITICAL = "critical"
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


@dataclass
class AgentState:
    current_goal: str = ""
    progress: str = ""
    todos: list[str] = field(default_factory=list)


@dataclass
class Section:
    name: str
    content: str
    priority: SectionPriority = SectionPriority.NORMAL
    has_critical: bool = False


_CRITICAL_KEYWORDS = re.compile(r"(?:必须|禁止|不要|务必|严禁|绝不能|不可|切勿|一定不能|决不能)")


class AttentionGuide:
    def __init__(self, state: AgentState | None = None):
        self._state = state or AgentState()
        self._sections: dict[str, Section] = {}

    def update_state(
        self,
        goal: str | None = None,
        progress: str | None = None,
        todos: list[str] | None = None,
    ) -> AgentState:
        if goal is not None:
            self._state.current_goal = goal
        if progress is not None:
            self._state.progress = progress
        if todos is not None:
            self._state.todos = list(todos)
        logger.debug(
            f"状态已更新: goal={self._state.current_goal[:30]}, "
            f"progress={self._state.progress[:30]}, "
            f"todos={len(self._state.todos)}"
        )
        return self._state

    def get_state_block(self) -> str:
        if not self._state.current_goal and not self._state.progress and not self._state.todos:
            return ""
        lines = ["<state>"]
        if self._state.current_goal:
            lines.append(f"  当前目标: {self._state.current_goal}")
        if self._state.progress:
            lines.append(f"  进度: {self._state.progress}")
        if self._state.todos:
            lines.append("  待办:")
            for i, todo in enumerate(self._state.todos, 1):
                lines.append(f"    {i}. {todo}")
        lines.append("</state>")
        return "\n".join(lines)

    def highlight_critical(self, text: str) -> str:
        if not text or not _CRITICAL_KEYWORDS.search(text):
            return text

        sentences = re.split(r"(?<=[。！？\n])", text)
        result = []
        for sentence in sentences:
            if _CRITICAL_KEYWORDS.search(sentence):
                sentence = sentence.strip()
                if sentence and not sentence.startswith("!!!重要!!!"):
                    result.append(f"!!!重要!!! {sentence}")
                else:
                    result.append(sentence)
            else:
                result.append(sentence)
        return "".join(result)

    def reorder_sections(self, sections_dict: dict[str, str]) -> str:
        if not sections_dict:
            return ""

        parsed: list[Section] = []
        for name, content in sections_dict.items():
            has_critical = bool(_CRITICAL_KEYWORDS.search(content))
            if has_critical:
                priority = SectionPriority.CRITICAL
            elif name in ("system", "instruction", "role", "指令", "系统", "角色"):
                priority = SectionPriority.HIGH
            elif name in ("context", "background", "背景", "上下文", "参考"):
                priority = SectionPriority.NORMAL
            elif name in ("examples", "reference", "示例", "参考文档"):
                priority = SectionPriority.LOW
            else:
                priority = SectionPriority.NORMAL
            parsed.append(Section(name=name, content=content, priority=priority, has_critical=has_critical))

        head_sections: list[Section] = []
        middle_sections: list[Section] = []
        tail_sections: list[Section] = []

        for sec in parsed:
            if sec.priority == SectionPriority.CRITICAL or sec.has_critical or sec.priority == SectionPriority.HIGH:
                head_sections.append(sec)
            elif sec.priority == SectionPriority.LOW:
                tail_sections.append(sec)
            else:
                middle_sections.append(sec)

        constraint_sections: list[Section] = []
        remaining_middle: list[Section] = []
        for sec in middle_sections:
            if sec.has_critical or _CRITICAL_KEYWORDS.search(sec.content):
                constraint_sections.append(sec)
            else:
                remaining_middle.append(sec)

        ordered = head_sections + remaining_middle + constraint_sections + tail_sections

        parts: list[str] = []
        state_block = self.get_state_block()

        if state_block:
            parts.append(state_block)

        for sec in ordered:
            highlighted = self.highlight_critical(sec.content)
            parts.append(highlighted)

        return "\n\n".join(parts)

    @property
    def state(self) -> AgentState:
        return self._state
