from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from Django_xm.apps.core.logging_utils import get_logger
from Django_xm.common.messages import content_to_str

logger = get_logger(__name__)

_TRIVIAL_PATTERNS = frozenset(
    {
        "好的",
        "收到",
        "明白了",
        "了解了",
        "谢谢",
        "感谢",
        "嗯",
        "哦",
        "好",
        "是",
        "对",
        "行",
        "ok",
        "OK",
        "好的。",
        "收到。",
        "谢谢！",
        "感谢！",
    }
)

_TRIVIAL_MAX_LENGTH = 10


@dataclass
class PruneResult:
    original_count: int = 0
    pruned_count: int = 0
    deduped_count: int = 0
    filtered_count: int = 0


class ContextPruner:
    def prune(self, messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], PruneResult]:
        result = PruneResult(original_count=len(messages))

        deduped, deduped_count = self.dedup_tool_outputs(messages)
        result.deduped_count = deduped_count

        filtered, filtered_count = self.filter_trivial(deduped)
        result.filtered_count = filtered_count

        result.pruned_count = deduped_count + filtered_count
        return filtered, result

    def dedup_tool_outputs(self, messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
        if not messages:
            return [], 0

        deduped: list[dict[str, Any]] = [messages[0]]
        removed_count = 0

        for i in range(1, len(messages)):
            prev = deduped[-1]
            curr = messages[i]

            if (
                prev.get("role") == "tool"
                and curr.get("role") == "tool"
                and prev.get("name") == curr.get("name")
                and prev.get("name") is not None
            ):
                if prev.get("pinned") is True:
                    deduped.append(curr)
                else:
                    deduped[-1] = curr
                    removed_count += 1
            else:
                deduped.append(curr)

        if removed_count > 0:
            logger.info(f"工具输出去重: 移除 {removed_count} 条连续重复工具消息")

        return deduped, removed_count

    def filter_trivial(self, messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
        if not messages:
            return [], 0

        filtered: list[dict[str, Any]] = []
        removed_count = 0

        for msg in messages:
            if self._is_trivial(msg):
                removed_count += 1
                continue
            filtered.append(msg)

        if removed_count > 0:
            logger.info(f"无意义消息过滤: 移除 {removed_count} 条短消息")

        return filtered, removed_count

    @staticmethod
    def _is_trivial(msg: dict[str, Any]) -> bool:
        if msg.get("pinned") is True:
            return False

        content = msg.get("content", "")
        if not isinstance(content, str):
            return False

        stripped = content.strip()
        if len(stripped) > _TRIVIAL_MAX_LENGTH:
            return False

        return stripped in _TRIVIAL_PATTERNS

    def prune_by_relevance(
        self,
        messages: list[dict[str, Any]],
        query: str,
        keep_count: int,
    ) -> tuple[list[dict[str, Any]], int]:
        """基于语义相关性筛选历史消息，保留与当前查询最相关的 top-k 条

        Args:
            messages: 历史消息列表
            query: 当前查询文本
            keep_count: 保留的消息数量

        Returns:
            (筛选后的消息列表, 被移除的消息数量)
        """
        if not messages or keep_count >= len(messages):
            return messages, 0

        # 分离 pinned 消息和普通消息
        pinned: list[dict[str, Any]] = []
        unpinned: list[dict[str, Any]] = []
        for msg in messages:
            if msg.get("pinned") is True:
                pinned.append(msg)
            else:
                unpinned.append(msg)

        # pinned 消息始终保留，只需从 unpinned 中筛选
        available_slots = max(0, keep_count - len(pinned))
        if available_slots >= len(unpinned):
            return messages, 0

        # 计算每条消息与查询的语义相似度
        scored: list[tuple[float, dict[str, Any]]] = []
        for msg in unpinned:
            score = self._compute_message_relevance(msg, query)
            scored.append((score, msg))

        # 按相似度降序排序，保留 top-k
        scored.sort(key=lambda x: x[0], reverse=True)
        kept = [msg for _, msg in scored[:available_slots]]
        removed_count = len(unpinned) - available_slots

        # 合并结果：pinned + 按相关性保留的消息
        result = pinned + kept

        if removed_count > 0:
            logger.info(f"语义相关性筛选: 保留 {len(result)} 条消息, 移除 {removed_count} 条低相关消息")

        return result, removed_count

    def _compute_message_relevance(self, msg: dict[str, Any], query: str) -> float:
        """计算单条消息与查询的语义相关性分数"""
        content = msg.get("content", "")
        if isinstance(content, list):
            content = content_to_str(content)
        if not isinstance(content, str) or not content.strip():
            return 0.0

        # 尝试 Embedding 语义相似度
        similarity = self._embedding_similarity(content, query)
        if similarity is not None:
            return similarity

        # 回退到词重叠计算
        content_words = set(content.lower().split())
        query_words = set(query.lower().split())
        if not query_words:
            return 0.0
        overlap = content_words & query_words
        return len(overlap) / len(query_words)

    @staticmethod
    def _embedding_similarity(text_a: str, text_b: str) -> float | None:
        """使用 Embedding 计算余弦相似度，不可用时返回 None"""
        try:
            import numpy as np

            from Django_xm.apps.knowledge.services.embedding_service import get_embeddings

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
        except Exception as e:
            logger.debug(f"Embedding 相似度计算失败，回退词重叠: {e}")
            return None
