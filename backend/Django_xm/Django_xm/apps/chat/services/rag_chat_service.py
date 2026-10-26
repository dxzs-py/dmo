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

    def get_rag_retriever_bundle(
        self, index_name: str, k: int = 4, search_type: SearchType = "similarity", retrieval_mode: str = "precise"
    ):
        """构建检索器 + 底层向量库（P0-O2：工具链路 Level 1 需要 store）。

        Returns:
            UserRetrieverBundle | None；未登录/索引不可用时返回 None
        """
        from Django_xm.apps.knowledge.services.retrieval_service import build_user_retriever_bundle

        if not self.user_id:
            logger.warning("get_rag_retriever_bundle: user_id 为空，跳过")
            return None
        try:
            logger.info(f"get_rag_retriever_bundle: index_name={index_name}, user_id={self.user_id}")
            bundle = build_user_retriever_bundle(
                self.user_id,
                index_name,
                k=k,
                mode=retrieval_mode,
                search_type=search_type,
            )
            if bundle is None:
                logger.warning(f"get_rag_retriever_bundle: 索引不可用: index_name={index_name}")
            return bundle
        except Exception:
            logger.exception("获取 RAG 检索器 bundle 失败")
            return None

    def get_rag_retriever(
        self, index_name: str, k: int = 4, search_type: SearchType = "similarity", retrieval_mode: str = "precise"
    ):
        """兼容旧接口：只返回检索器（bundle 的薄封装）。"""
        bundle = self.get_rag_retriever_bundle(
            index_name, k=k, search_type=search_type, retrieval_mode=retrieval_mode
        )
        return bundle.retriever if bundle is not None else None
