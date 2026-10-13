"""
多知识库联合检索服务

支持从多个知识库中联合检索，通过 UnifiedRagPipeline.search_multi_kb 执行 RRF 融合。
支持自定义权重分配和自动权重推荐。
为深度研究的文档分析节点提供 retriever_tool。
"""

from langchain_core.tools import BaseTool

from Django_xm.apps.core.logging_utils import get_logger

logger = get_logger(__name__)


def create_retriever_tool_for_multi_kb(
    kb_full_names: list[str],
    k: int = 4,
    weights: list[float] | None = None,
    user_id: int | None = None,
) -> BaseTool:
    """创建多知识库统一检索工具。

    工具运行时调用 UnifiedRagPipeline.search_multi_kb 执行 RRF 融合检索；
    user_id 透传给管线做运行时前缀越权守卫（3.5 双保险之二，构建期 DB
    归属校验见 build_retriever_tool_for_research）。
    """
    from langchain_core.tools import tool

    @tool
    def search_knowledge_base(query: str) -> str:
        """搜索知识库中的相关信息。当需要回答关于文档内容的问题时使用此工具。"""
        from Django_xm.apps.knowledge.services.rag_retrieval import UnifiedRagPipeline

        pipeline = UnifiedRagPipeline(scenario="knowledge_base")
        return pipeline.search_multi_kb(
            kb_names=kb_full_names,
            query=query,
            k=k,
            weights=weights,
            user_id=user_id,
        )

    return search_knowledge_base


def build_retriever_tool_for_research(
    knowledge_base_ids: list[str],
    user_id: int,
    k: int = 4,
    weights: list[float] | None = None,
) -> BaseTool | None:
    """构建深度研究用的知识库检索工具。

    通过 UnifiedRagPipeline.search_multi_kb 统一检索入口。

    3.5 归属校验：knowledge_base_ids 来自请求参数，构建期先按 IndexMetadata
    的 user 归属过滤（匹配 index_exists 口径：本人拥有或历史遗留无主行），
    非本人库剔除并 warning 留痕——后续检索即使带上他人 kb_id 也只会得到
    "库为空"的工具返回，不泄露他人索引内容。全部越权时返回 None（不建工具）。
    """
    if not knowledge_base_ids:
        return None

    from ..views_utils import build_user_index_name

    # D-1：索引全名规则单源 views_utils.build_user_index_name（原内联 f-string 副本删除）
    kb_full_names = [build_user_index_name(user_id, kb_id) for kb_id in knowledge_base_ids]

    from ..models import IndexMetadata

    owned_names = set(
        IndexMetadata.objects.filter(user_id=user_id, name__in=kb_full_names).values_list(
            "name", flat=True
        )
    )
    denied = [
        (kb_id, full_name)
        for kb_id, full_name in zip(knowledge_base_ids, kb_full_names, strict=True)
        if full_name not in owned_names
    ]
    if denied:
        logger.warning(
            f"build_retriever_tool_for_research: 剔除非归属知识库 "
            f"user_id={user_id}, denied={[kb_id for kb_id, _ in denied]}"
        )
        owned_ids = [
            kb_id
            for kb_id, full_name in zip(knowledge_base_ids, kb_full_names, strict=True)
            if full_name in owned_names
        ]
    else:
        owned_ids = list(knowledge_base_ids)

    if not owned_ids:
        logger.warning(f"build_retriever_tool_for_research: 全部知识库均非本人所有，不构建检索工具 user_id={user_id}")
        return None

    logger.info(f"构建研究用KB检索工具: user_id={user_id}, KBs={owned_ids}")

    return create_retriever_tool_for_multi_kb(
        [build_user_index_name(user_id, kb_id) for kb_id in owned_ids],
        k=k,
        weights=weights,
        user_id=user_id,
    )
