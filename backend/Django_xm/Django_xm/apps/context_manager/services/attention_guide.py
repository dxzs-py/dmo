"""
注意力引导器 - 解决 Lost in the Middle 问题

基于 LLM 注意力分布特征，对上下文分区进行重排序：
1. 关键指令置于上下文首部（首因效应）
2. 重要约束置于上下文尾部（近因效应）
3. 对优先级指令使用 !!!重要!!! 高亮标记

（A15 死链清理：原 AgentState/<state> 块动态维护链路从未被任何调用方驱动
（update_state 全仓零调用，state 恒空 → get_state_block 恒返回空串），
已整体移除；highlight/reorder 为存活能力保留。）

参考：
- Lost in the Middle: How Language Models Use Long Contexts (Liu et al., 2023)
- https://arxiv.org/abs/2307.03172
"""

import re
from dataclasses import dataclass
from enum import Enum

from Django_xm.apps.core.logging_utils import get_logger

logger = get_logger(__name__)


class SectionPriority(Enum):
    CRITICAL = "critical"
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


@dataclass
class Section:
    name: str
    content: str
    priority: SectionPriority = SectionPriority.NORMAL
    has_critical: bool = False


_CRITICAL_KEYWORDS = re.compile(r"(?:必须|禁止|不要|务必|严禁|绝不能|不可|切勿|一定不能|决不能)")


class AttentionGuide:
    """注意力引导器实现。

    作用域说明（P0-1 prompt caching 裁决）：本类输出（highlight_critical /
    reorder_sections）不参与 chat checkpointer 主链路的记忆注入文本构建
    （ContextManager.get_injection_context 直接拼接 KG/跨会话段，不经本类），
    注入位置由 MemoryInjectionMiddleware 管理；reorder_sections 的
    structured_prompt 产物在 chat 侧仅被丢弃（apply_context_engineering 与
    /compact 均只消费 metadata），不进任何模型 prompt。唯一实际产出链路是
    build_prompt_context（含易变 KG/文档分区，消费方为 deep agent 动态
    system_prompt，见 agent_hub/builders/_common.py）。
    """

    def __init__(self) -> None:
        pass

    def highlight_critical(self, text: str) -> str:
        if not text or not _CRITICAL_KEYWORDS.search(text):
            return text

        sentences = re.split(r"(?<=[。！？\n])", text)
        result = []
        for sentence in sentences:
            if _CRITICAL_KEYWORDS.search(sentence):
                stripped = sentence.strip()
                if stripped and not stripped.startswith("!!!重要!!!"):
                    result.append(f"!!!重要!!! {stripped}")
                else:
                    result.append(stripped)
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
        for sec in ordered:
            highlighted = self.highlight_critical(sec.content)
            parts.append(highlighted)

        return "\n\n".join(parts)
