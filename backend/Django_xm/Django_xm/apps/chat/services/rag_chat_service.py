"""
RAG 模式聊天服务

从 chat_service.py 拆分出的 RAG 检索器构建逻辑：
- RAG 检索器获取（agent/deep-research 模式的工具注入生产消费）

说明：本类曾承载 rag 分流编排（process_rag_request：strict chain 查询 →
二次检索评估 → 低分重试）及其评估链（_evaluate_rag_result 等）。该链唯一
调用点 _precheck_chat_request 属生产零调用的非流式孤儿链，已随 CT-01=A
（用户裁决删除）移除；知识库问答由 RAG 独立页面承载。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from Django_xm.apps.knowledge.services.retrieval_service import SearchType

from Django_xm.apps.core.logging_utils import get_logger

logger = get_logger(__name__)


class RAGChatService:
    """RAG 检索器服务（agent/deep-research 工具注入）"""

    def __init__(self, user_id: int | None = None, chat_service=None):
        self.user_id = user_id
        self._chat_service = chat_service

    def get_rag_retriever(
        self, index_name: str, k: int = 4, search_type: SearchType = "similarity", retrieval_mode: str = "precise"
    ):
        from Django_xm.apps.knowledge.services.retrieval_service import build_user_retriever

        if not self.user_id:
            logger.warning("get_rag_retriever: user_id 为空，跳过")
            return None
        try:
            logger.info(f"get_rag_retriever: index_name={index_name}, user_id={self.user_id}")
            # B-1/D-1：检索器构建编排（index_exists/空索引/metadata/维度探测/
            # load_index/create_retriever + comprehensive 参数映射 D-3）单源委托
            # knowledge.build_user_retriever；索引全名规则经 views_utils 单源
            retriever = build_user_retriever(
                self.user_id,
                index_name,
                k=k,
                mode=retrieval_mode,
                search_type=search_type,
            )
            if retriever is None:
                logger.warning(f"get_rag_retriever: 索引不可用: index_name={index_name}")
            return retriever
        except Exception:
            logger.exception("获取 RAG 检索器失败")
            return None
