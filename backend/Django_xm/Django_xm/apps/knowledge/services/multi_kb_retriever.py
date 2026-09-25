"""
多知识库联合检索服务

支持从多个知识库中联合检索，通过 UnifiedRagPipeline.search_multi_kb 执行 RRF 融合。
支持自定义权重分配和自动权重推荐。
为深度研究的文档分析节点提供 retriever_tool。
"""

from langchain_core.tools import BaseTool

from Django_xm.apps.core.logging_utils import get_logger

logger = get_logger(__name__)


def _normalize_weights(weights: list[float], count: int) -> list[float]:
    total = sum(weights)
    if total <= 0:
        logger.warning(f"权重总和 {total} <= 0，回退到等权分配")
        return [1.0 / count] * count
    return [w / total for w in weights]


def create_retriever_tool_for_multi_kb(
    kb_full_names: list[str],
    k: int = 4,
    weights: list[float] | None = None,
) -> BaseTool:
    """创建多知识库统一检索工具。

    工具运行时调用 UnifiedRagPipeline.search_multi_kb 执行 RRF 融合检索。
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
    """
    if not knowledge_base_ids:
        return None

    kb_full_names = []
    for kb_id in knowledge_base_ids:
        kb_full_names.append(f"user_{user_id}_{kb_id}")

    logger.info(f"构建研究用KB检索工具: user_id={user_id}, KBs={knowledge_base_ids}")

    return create_retriever_tool_for_multi_kb(kb_full_names, k=k, weights=weights)
