from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from Django_xm.apps.core.logging_utils import get_logger
from Django_xm.common.messages import content_to_str

logger = get_logger(__name__)

# 长期记忆层级值（与 ContextManagerMiddleware._is_long_term_message / manager.mark_long_term 同语义）
_LONG_TERM_TIER = "long_term"


def _is_protected(msg: dict[str, Any]) -> bool:
    """结构化保护判定：显式 memory_tier=long_term / pinned / system 角色的消息不可删除

    与压缩层分层语义一致（结构化标记唯一实现，无内容关键词启发式）。
    """
    if msg.get("memory_tier") == _LONG_TERM_TIER:
        return True
    if msg.get("pinned") is True:
        return True
    return msg.get("role") == "system"

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


def _extract_text(msg: dict[str, Any]) -> str:
    """提取消息纯文本（list content 归一为字符串，非字符串内容按空串处理）"""
    content = msg.get("content", "")
    if isinstance(content, list):
        content = content_to_str(content)
    return content if isinstance(content, str) else ""


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
        """连续同名工具输出合并去重（fix-review-round3 2.1 收紧）。

        合并判定红线：仅当两侧 ``tool_call_id`` 存在且相同才允许合并——
        AIMessage.tool_calls 按引用完整性要求每个 tool_call_id 都有对应
        ToolMessage；同名不同 id（并行调用/重试）合并会丢结果引用，
        经 _sanitize_tool_messages 清理后必然破坏配对（400 错误）。

        name 相同但 tool_call_id 不同 / 任一侧缺失 tool_call_id → 一律保留两条
        （去重是优化，保守不合并永远安全）。任一侧受保护（pinned/long_term/
        system）同样保留两条原样。
        """
        if not messages:
            return [], 0

        deduped: list[dict[str, Any]] = [messages[0]]
        removed_count = 0

        for i in range(1, len(messages)):
            prev = deduped[-1]
            curr = messages[i]

            prev_call_id = prev.get("tool_call_id")
            curr_call_id = curr.get("tool_call_id")
            if (
                prev.get("role") == "tool"
                and curr.get("role") == "tool"
                # tool_call_id 存在且相同才合并（引用完整性红线，缺失不合并）
                and prev_call_id is not None
                and prev_call_id == curr_call_id
            ):
                # 结构化保护：任一侧为 pinned/long_term 时不合并（保留两条原样）
                if _is_protected(prev) or _is_protected(curr):
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
        # 结构化保护：pinned / memory_tier=long_term / system 角色消息不参与无意义过滤
        if _is_protected(msg):
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
        """基于语义相关性筛选历史消息，保留与当前查询最相关的 top-k 条（A10）

        输出保持原消息时序：相关性分数只决定消息的去留，不参与重排
        （原实现按分数降序输出 kept，会打乱对话时序）。

        Args:
            messages: 历史消息列表
            query: 当前查询文本
            keep_count: 保留的消息数量

        Returns:
            (筛选后的消息列表, 被移除的消息数量)
        """
        if not messages or keep_count >= len(messages):
            return messages, 0

        protected_flags = [_is_protected(msg) for msg in messages]
        unpinned_indices = [i for i, protected in enumerate(protected_flags) if not protected]

        # 受保护消息始终保留，只需从 unpinned 中筛选
        available_slots = max(0, keep_count - (len(messages) - len(unpinned_indices)))
        if available_slots >= len(unpinned_indices):
            return messages, 0

        # 批量计算相关性分数（A10：query embedding 1 次 + embed_documents 批量 1 次，
        # 原实现逐条 embed_query 共 2N 次远程调用）
        contents = [_extract_text(messages[i]) for i in unpinned_indices]
        scores = self._compute_relevance_scores(contents, query)

        # 按相似度降序取 top-k；分数只决定去留
        ranked = sorted(zip(unpinned_indices, scores, strict=True), key=lambda pair: pair[1], reverse=True)
        kept_indices = {index for index, _ in ranked[:available_slots]}
        removed_count = len(unpinned_indices) - available_slots

        # 按原消息顺序输出：受保护消息与被保留消息均在原位
        result = [msg for i, msg in enumerate(messages) if protected_flags[i] or i in kept_indices]

        if removed_count > 0:
            logger.info(f"语义相关性筛选: 保留 {len(result)} 条消息, 移除 {removed_count} 条低相关消息")

        return result, removed_count

    def _compute_relevance_scores(self, contents: list[str], query: str) -> list[float]:
        """批量计算消息内容与查询的相关性分数（与 contents 一一对应）

        优先批量 Embedding 余弦相似度；Embedding 不可用/失败时整体回退词重叠。
        """
        scores = [0.0] * len(contents)
        non_empty = [i for i, text in enumerate(contents) if text.strip()]
        if not non_empty:
            return scores

        batch_scores = self._batch_embedding_scores([contents[i] for i in non_empty], query)
        if batch_scores is None:
            for i in non_empty:
                scores[i] = self._word_overlap_score(contents[i], query)
        else:
            for j, i in enumerate(non_empty):
                scores[i] = batch_scores[j]
        return scores

    @staticmethod
    def _word_overlap_score(content: str, query: str) -> float:
        """词重叠相关性（Embedding 不可用时的回退路径）"""
        content_words = set(content.lower().split())
        query_words = set(query.lower().split())
        if not query_words:
            return 0.0
        overlap = content_words & query_words
        return len(overlap) / len(query_words)

    @staticmethod
    def _batch_embedding_scores(texts: list[str], query: str) -> list[float] | None:
        """批量 Embedding 余弦相似度：query 1 次 embed_query + 消息 1 次 embed_documents。

        任一环节不可用/失败时返回 None（调用方回退词重叠），不抛异常。
        """
        # 环 1 消边：经 runtime_providers 注入（knowledge apps.ready() 注册）
        import numpy as np

        from Django_xm.apps.context_manager.services.runtime_providers import get_embedding_provider

        embedding_provider = get_embedding_provider()
        if embedding_provider is None:
            logger.debug("Embedding 相似度计算失败，回退词重叠: Embedding 提供者未注册")
            return None
        try:
            embeddings = embedding_provider()
            query_vec = np.array(embeddings.embed_query(query[:500]), dtype=float)
            query_norm = np.linalg.norm(query_vec)
            if query_norm == 0:
                return None

            # 优先批量接口（单次远程调用）；不可用/失败退化为逐条 embed_query
            # （query 向量已缓存复用，仍显著优于原逐条 2N 次调用）
            doc_vectors: list | None = None
            if hasattr(embeddings, "embed_documents"):
                try:
                    doc_vectors = [
                        np.array(vec, dtype=float) for vec in embeddings.embed_documents([t[:500] for t in texts])
                    ]
                except Exception as e:
                    logger.debug(f"embed_documents 批量失败，退化为逐条 embed_query: {e}")
            if doc_vectors is None:
                doc_vectors = [np.array(embeddings.embed_query(text[:500]), dtype=float) for text in texts]

            scores: list[float] = []
            for vec in doc_vectors:
                norm = np.linalg.norm(vec)
                if norm == 0 or vec.shape != query_vec.shape:
                    scores.append(0.0)
                else:
                    scores.append(float(np.dot(vec, query_vec) / (norm * query_norm)))
            return scores
        except Exception as e:
            logger.debug(f"Embedding 相似度计算失败，回退词重叠: {e}")
            return None
