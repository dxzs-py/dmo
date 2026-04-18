"""
会话压缩服务
参考 claw-code-main 的 compact.rs 实现
当会话超过 Token 阈值时自动压缩旧消息为摘要
"""

import json
import logging
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass

from Django_xm.apps.core.config import get_logger
from Django_xm.apps.core.models import get_chat_model

logger = get_logger(__name__)

COMPACT_TOKEN_THRESHOLD = 80000
COMPACT_KEEP_RECENT_MESSAGES = 6
COMPACT_SUMMARY_MAX_LENGTH = 2000


@dataclass
class CompactResult:
    compressed: bool
    original_message_count: int
    kept_message_count: int
    summary: Optional[str] = None
    summary_token_estimate: int = 0


class SessionCompactor:
    """会话压缩器 - 将长对话历史压缩为摘要"""

    def __init__(self, token_threshold: int = COMPACT_TOKEN_THRESHOLD):
        self.token_threshold = token_threshold

    @staticmethod
    def estimate_tokens(text: str) -> int:
        if not text:
            return 0
        chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
        other_chars = len(text) - chinese_chars
        return int(chinese_chars * 1.5 + other_chars * 0.25)

    def should_compact(self, messages: List[Dict[str, Any]]) -> bool:
        if len(messages) < COMPACT_KEEP_RECENT_MESSAGES * 2:
            return False
        total_tokens = 0
        for msg in messages:
            content = msg.get('content', '')
            if isinstance(content, str):
                total_tokens += self.estimate_tokens(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        total_tokens += self.estimate_tokens(block.get('text', ''))
                    elif isinstance(block, str):
                        total_tokens += self.estimate_tokens(block)
        return total_tokens > self.token_threshold

    def compact(
        self,
        messages: List[Dict[str, Any]],
        keep_recent: int = COMPACT_KEEP_RECENT_MESSAGES,
    ) -> CompactResult:
        if not self.should_compact(messages):
            return CompactResult(
                compressed=False,
                original_message_count=len(messages),
                kept_message_count=len(messages),
            )

        if len(messages) <= keep_recent:
            return CompactResult(
                compressed=False,
                original_message_count=len(messages),
                kept_message_count=len(messages),
            )

        old_messages = messages[:-keep_recent]
        recent_messages = messages[-keep_recent:]

        summary = self._generate_summary(old_messages)

        summary_tokens = self.estimate_tokens(summary) if summary else 0

        logger.info(
            f"会话压缩完成: {len(old_messages)} 条旧消息 -> 摘要 "
            f"({summary_tokens} tokens), 保留 {keep_recent} 条最近消息"
        )

        return CompactResult(
            compressed=True,
            original_message_count=len(messages),
            kept_message_count=keep_recent,
            summary=summary,
            summary_token_estimate=summary_tokens,
        )

    def _generate_summary(self, old_messages: List[Dict[str, Any]]) -> str:
        if not old_messages:
            return ""

        conversation_text = self._format_messages_for_summary(old_messages)

        if len(conversation_text) > 15000:
            conversation_text = conversation_text[:15000] + "\n...(内容过长已截断)"

        stats = self._compute_stats(old_messages)

        try:
            model = get_chat_model()
            prompt = (
                "请将以下对话历史压缩为一段简洁的摘要，保留关键信息。摘要应包含：\n"
                "1. 讨论的主要话题和结论\n"
                "2. 重要的决定和结果\n"
                "3. 用户的核心需求\n"
                "4. 使用过的工具和获取的关键信息\n\n"
                f"对话统计：共 {stats['total_messages']} 条消息，"
                f"用户消息 {stats['user_messages']} 条，"
                f"助手消息 {stats['assistant_messages']} 条，"
                f"工具调用 {stats['tool_calls']} 次\n\n"
                f"对话内容：\n{conversation_text}\n\n"
                "请用中文生成不超过500字的摘要："
            )
            response = model.invoke([{"role": "user", "content": prompt}])
            summary = getattr(response, "content", "")
            if summary and len(summary) > COMPACT_SUMMARY_MAX_LENGTH:
                summary = summary[:COMPACT_SUMMARY_MAX_LENGTH]
            return summary or ""
        except Exception as e:
            logger.error(f"生成会话摘要失败: {e}")
            return self._generate_fallback_summary(old_messages, stats)

    def _format_messages_for_summary(self, messages: List[Dict[str, Any]]) -> str:
        lines = []
        for msg in messages:
            role = msg.get('role', 'unknown')
            content = msg.get('content', '')
            if isinstance(content, list):
                text_parts = []
                for block in content:
                    if isinstance(block, dict):
                        text_parts.append(block.get('text', ''))
                    elif isinstance(block, str):
                        text_parts.append(block)
                content = ' '.join(text_parts)

            if not content or not content.strip():
                continue

            prefix = {"user": "用户", "assistant": "助手", "system": "系统"}.get(role, role)
            truncated = content[:500] + "..." if len(content) > 500 else content
            lines.append(f"[{prefix}]: {truncated}")

            tool_calls = msg.get('tool_calls', [])
            if tool_calls:
                for tc in tool_calls:
                    if isinstance(tc, dict):
                        tool_name = tc.get('name', tc.get('function', {}).get('name', 'unknown'))
                        lines.append(f"  [工具调用]: {tool_name}")

        return "\n".join(lines)

    @staticmethod
    def _compute_stats(messages: List[Dict[str, Any]]) -> Dict[str, int]:
        stats = {
            'total_messages': len(messages),
            'user_messages': 0,
            'assistant_messages': 0,
            'tool_calls': 0,
        }
        for msg in messages:
            role = msg.get('role', '')
            if role == 'user':
                stats['user_messages'] += 1
            elif role == 'assistant':
                stats['assistant_messages'] += 1
            tool_calls = msg.get('tool_calls', [])
            if tool_calls:
                stats['tool_calls'] += len(tool_calls)
        return stats

    @staticmethod
    def _generate_fallback_summary(
        messages: List[Dict[str, Any]], stats: Dict[str, int]
    ) -> str:
        topics = []
        for msg in messages:
            if msg.get('role') == 'user':
                content = msg.get('content', '')
                if isinstance(content, str) and content.strip():
                    first_line = content.strip().split('\n')[0][:100]
                    topics.append(first_line)

        summary_parts = [
            f"<summary>",
            f"对话统计: 共 {stats['total_messages']} 条消息 "
            f"(用户 {stats['user_messages']} 条, 助手 {stats['assistant_messages']} 条)",
            f"工具调用: {stats['tool_calls']} 次",
        ]
        if topics:
            unique_topics = list(dict.fromkeys(topics))[:5]
            summary_parts.append("讨论话题: " + "; ".join(unique_topics))
        summary_parts.append("</summary>")

        return "\n".join(summary_parts)


def apply_compaction_to_chat_history(
    chat_history: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Optional[CompactResult]]:
    compactor = SessionCompactor()
    result = compactor.compact(chat_history)

    if not result.compressed or not result.summary:
        return chat_history, result

    summary_message = {
        'role': 'system',
        'content': f"以下是之前对话的压缩摘要：\n\n{result.summary}",
    }

    recent_messages = chat_history[-COMPACT_KEEP_RECENT_MESSAGES:]
    compacted = [summary_message] + recent_messages

    return compacted, result
