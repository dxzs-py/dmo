"""
RAG 检索增强

1. HyDE 查询改写 - 使用 LLM 生成假设性文档，用假设文档的 Embedding 进行检索

大型 JSON 工具结果的结构化摘要已下沉共享模块
``Django_xm.apps.context_manager.services.context_summary``（context_manager
与 agent_hub/wait_for_subagent 两处复用）。

参考：
- https://python.langchain.com/docs/concepts/retrieval/
- https://arxiv.org/abs/2212.10496 (HyDE)
"""

import json

from langchain_core.language_models import BaseChatModel

from Django_xm.apps.core.logging_utils import get_logger

logger = get_logger(__name__)


class RetrievalAugmenter:
    @staticmethod
    async def hyde_rewrite(query: str, llm: BaseChatModel) -> str:
        prompt = f"请写一段详细的回答来回答以下问题：{query}"
        try:
            response = await llm.ainvoke([{"role": "user", "content": prompt}])
            hypothetical_doc = getattr(response, "content", "")
            if not hypothetical_doc:
                logger.warning("HyDE 改写返回空内容，回退到原始查询")
                return query
            logger.info(f"HyDE 改写完成: query='{query[:50]}...' -> doc_len={len(hypothetical_doc)}")
            return hypothetical_doc
        except Exception:
            logger.exception("HyDE 改写失败")
            return query

    @staticmethod
    def hyde_rewrite_sync(query: str, llm: BaseChatModel) -> str:
        prompt = f"请写一段详细的回答来回答以下问题：{query}"
        try:
            response = llm.invoke([{"role": "user", "content": prompt}])
            hypothetical_doc = getattr(response, "content", "")
            if not hypothetical_doc:
                logger.warning("HyDE 改写返回空内容，回退到原始查询")
                return query
            logger.info(f"HyDE 改写完成: query='{query[:50]}...' -> doc_len={len(hypothetical_doc)}")
            return hypothetical_doc
        except Exception:
            logger.exception("HyDE 改写失败")
            return query

    @staticmethod
    def is_json(text: str) -> bool:
        stripped = text.strip()
        if not stripped:
            return False
        if stripped[0] not in ("{", "["):
            return False
        try:
            json.loads(stripped)
            return True
        except (json.JSONDecodeError, ValueError):
            return False
