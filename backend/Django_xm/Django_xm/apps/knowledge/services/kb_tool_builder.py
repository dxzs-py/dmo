"""研究/Agent 知识库工具构建（C4）：归属校验 + 预构建 bundle + 唯一工具。

替代旧 ``multi_kb_retriever`` 入口；工具名统一 ``knowledge_base_search``。
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool

from Django_xm.apps.core.logging_utils import get_logger

logger = get_logger(__name__)


def build_kb_tool_for_research(
    knowledge_base_ids: list[str],
    user_id: int,
    k: int = 4,
) -> BaseTool | None:
    """构建研究/Agent 用的知识库检索工具（归属过滤 + 预构建 bundle）。"""
    if not knowledge_base_ids:
        return None

    from ..models import IndexMetadata
    from ..views_utils import build_user_index_name

    kb_full_names = [build_user_index_name(user_id, kb_id) for kb_id in knowledge_base_ids]
    owned_names = set(
        IndexMetadata.objects.filter(user_id=user_id, name__in=kb_full_names).values_list(
            "name", flat=True
        )
    )
    owned_ids = [
        kb_id
        for kb_id, full_name in zip(knowledge_base_ids, kb_full_names, strict=True)
        if full_name in owned_names
    ]
    denied = [kb_id for kb_id in knowledge_base_ids if kb_id not in owned_ids]
    if denied:
        logger.warning(
            f"build_kb_tool_for_research: 剔除非归属知识库 user_id={user_id}, denied={denied}"
        )
    if not owned_ids:
        logger.warning(
            f"build_kb_tool_for_research: 全部知识库均非本人所有，不构建检索工具 user_id={user_id}"
        )
        return None

    from .kb_search_tool import KbToolBundle, build_knowledge_base_search_tool
    from .rag_service import RagScope
    from .retrieval_service import build_user_retriever_bundle

    bundles: dict[str, KbToolBundle] = {}
    for kb_id in owned_ids:
        try:
            bundle: Any = build_user_retriever_bundle(user_id, kb_id, k=k)
        except Exception:
            logger.exception(f"build_kb_tool_for_research: 加载知识库失败 kb={kb_id}")
            continue
        if bundle is None or bundle.retriever is None:
            logger.warning(f"build_kb_tool_for_research: 知识库索引不可用 kb={kb_id}")
            continue
        bundles[kb_id] = KbToolBundle(
            kb_id=kb_id,
            name=kb_id,
            description="",
            scope=RagScope(
                kind="persistent_kb",
                name=build_user_index_name(user_id, kb_id),
                user_id=user_id,
            ),
            retriever=bundle.retriever,
            vector_store=bundle.vector_store,
        )

    if not bundles:
        return None
    logger.info(f"构建研究用KB检索工具: user_id={user_id}, KBs={list(bundles)}")
    return build_knowledge_base_search_tool(bundles=bundles, k=k)
