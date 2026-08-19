"""
文档检索节点 (Retrieval Node)

本节点根据用户选择的知识库（DocumentIndex）检索学习资料。
- 从 state 读取 user_id 与 knowledge_base_ids（前端 KnowledgeBaseSelector 的 v-model）
- 每个知识库名称按 `user_{id}_{name}` 规则还原为完整索引名（与 kb_service.get_user_index_name 一致）
- 对每个知识库独立加载向量库并检索，合并去重后返回
- 未选择知识库时跳过检索，仅以 LLM 内置知识继续生成（与原硬编码 test_index 的开发模式解耦）
"""

from typing import Any

from django.utils import timezone

from Django_xm.apps.core.logging_utils import get_logger
from Django_xm.apps.knowledge.services.cross_app import get_index_manager
from Django_xm.apps.knowledge.services.embedding_service import get_embeddings
from Django_xm.apps.knowledge.services.retrieval_service import create_retriever
from Django_xm.apps.knowledge.views_utils import get_original_index_name

from ..services.state import RetrievedDocument, StudyFlowState
from .stream_events import emit_step

logger = get_logger(__name__)


def _compose_user_index_name(user_id: int, kb_name: str) -> str:
    """与 knowledge.views_utils.get_user_index_name 保持一致的索引命名规则。

    不直接调用 get_user_index_name(user, name) 是因为 retrieval_node 仅持有 user_id
    （LangGraph 节点不接触 request 对象）。命名规则单一真相源在 views_utils，本函数
    通过构造等价字符串复用规则。
    """
    return f"user_{user_id}_{kb_name}"


def _load_and_retrieve(user_index_name: str, query: str, k: int = 5) -> tuple[list[Any], bool]:
    """加载单个知识库的向量库并执行检索。

    Returns:
        (检索结果列表, is_degraded): is_degraded 为 True 表示向量检索无结果、
        降级加载全库文档通过 UnifiedRagPipeline.retrieve_documents() 重排序获得结果。
    """
    manager = get_index_manager()
    embeddings = get_embeddings()
    try:
        vector_store = manager.load_index(user_index_name, embeddings)
        retriever = create_retriever(vector_store, k=k)
    except FileNotFoundError:
        logger.warning(f"[Retrieval Node] 索引不存在: {user_index_name}")
        return [], False
    except Exception as e:
        logger.warning(f"[Retrieval Node] 加载索引 {user_index_name} 失败: {e}")
        return [], False

    # Use UnifiedRagPipeline for retrieval with degradation
    from Django_xm.apps.knowledge.services.rag_retrieval import UnifiedRagPipeline

    pipeline = UnifiedRagPipeline(scenario="knowledge_base")
    docs = pipeline.retrieve_documents_from_retriever(
        retriever=retriever,
        query=query,
        vector_store=None,  # PGVector can't do InMemoryVectorStore keyword search
    )

    if docs:
        return docs, False

    # Pipeline couldn't find anything; try full document load as Level 2 fallback
    logger.info(f"[Retrieval Node] 向量检索无结果，降级加载全库文档: {user_index_name}")
    try:
        from Django_xm.apps.knowledge.services.rag_retrieval import load_kb_documents

        kb_docs, total_tokens = load_kb_documents([user_index_name])
        if not kb_docs:
            logger.warning(f"[Retrieval Node] 降级加载全库文档为空: {user_index_name}")
            return [], False

        degraded_docs = pipeline.retrieve_documents(query, kb_docs, total_tokens)
        logger.info(
            f"[Retrieval Node] 降级检索完成: {user_index_name}, "
            f"全库 {len(kb_docs)} 片段 -> 命中 {len(degraded_docs)} 个文档"
        )
        return degraded_docs, True
    except Exception as e:
        logger.warning(f"[Retrieval Node] 降级检索失败: {user_index_name}, {e}")
        return [], False


def _dedup_by_content(docs: list[Any]) -> list[Any]:
    """按 page_content 去重，保留首次出现的文档。"""
    seen = set()
    result = []
    for doc in docs:
        content = getattr(doc, "page_content", None)
        if content is None:
            continue
        if content in seen:
            continue
        seen.add(content)
        result.append(doc)
    return result


def _run_web_search(query: str) -> list[str]:
    """调用 web_search 工具执行联网搜索，返回搜索结果文本列表。

    复用聊天模块的 web_search 工具（apps/tools._get_web_search_tools），
    未配置 TAVILY_API_KEY / 无 duckduckgo-search 时静默降级返回空列表。
    """
    try:
        from Django_xm.apps.tools import _get_web_search_tools

        tools = _get_web_search_tools()
        results: list[str] = []
        for tool in tools:
            result = tool.invoke({"query": query})
            content = result.content if hasattr(result, "content") else str(result)
            if content and content not in results:
                results.append(content)
        if results:
            logger.info(f"[Retrieval Node] web_search 返回 {len(results)} 条结果")
        return results
    except Exception as e:
        logger.warning(f"[Retrieval Node] web_search 调用失败: {e}")
        return []


def retrieval_node(state: StudyFlowState) -> dict[str, Any]:
    """文档检索节点

    功能：
    1. 从 state 读取 user_id 与 knowledge_base_ids
    2. 对每个知识库构造完整索引名并加载向量库
    3. 合并检索结果并去重
    4. 返回最相关的文档列表（未选择知识库时返回空列表）
    """
    logger.info("[Retrieval Node] 开始检索相关文档")
    emit_step("retrieval", "正在检索相关资料...")

    learning_plan = state.get("learning_plan")
    user_id = state.get("user_id")
    knowledge_base_ids = state.get("knowledge_base_ids") or []
    use_web_search = state.get("use_web_search", False)

    if not learning_plan:
        logger.warning("[Retrieval Node] 学习计划不存在，跳过文档检索")
        return {
            "retrieved_docs": [],
            "messages": [{"role": "assistant", "content": "\n\n⚠️ 学习计划生成失败，跳过文档检索。"}],
            "current_step": "retrieval",
            "updated_at": timezone.now().isoformat(),
        }

    topic = learning_plan["topic"]
    key_points = learning_plan["key_points"]

    # 网络查询：开启时调用 web_search 工具（并入最终检索上下文）
    web_docs: list[str] = []
    if use_web_search:
        web_docs = _run_web_search(topic)

    # 未选择知识库：明确跳过 RAG；若开启网络查询则仅返回联网结果
    if not user_id or not knowledge_base_ids:
        if web_docs:
            logger.info(
                f"[Retrieval Node] 未选择知识库，仅使用网络查询结果 ({len(web_docs)} 条)"
            )
            return {
                "retrieved_docs": _build_web_retrieved_docs(web_docs),
                "messages": [
                    {
                        "role": "assistant",
                        "content": "\n\nℹ️ 未选择知识库，已使用网络查询获取学习资料。",
                    }
                ],
                "current_step": "retrieval",
                "updated_at": timezone.now().isoformat(),
            }
        logger.info(
            f"[Retrieval Node] 未选择知识库 (user_id={user_id}, kb_count={len(knowledge_base_ids)})，"
            "跳过 RAG 检索，使用 LLM 内置知识生成内容"
        )
        return {
            "retrieved_docs": [],
            "messages": [
                {
                    "role": "assistant",
                    "content": (
                        "\n\nℹ️ 未选择知识库，将使用 AI 内置知识生成学习内容。"
                        "如需基于专属资料学习，请在启动工作流前选择知识库。"
                    ),
                }
            ],
            "current_step": "retrieval",
            "updated_at": timezone.now().isoformat(),
        }

    main_query = f"{topic}"
    logger.info(f"[Retrieval Node] 主查询: {main_query}, 知识库数量: {len(knowledge_base_ids)}")

    # 对每个知识库独立检索后合并去重
    all_docs: list[Any] = []
    has_degraded = False
    for kb_name in knowledge_base_ids:
        user_index_name = _compose_user_index_name(user_id, kb_name)
        original_name = get_original_index_name(user_index_name)
        logger.info(f"[Retrieval Node] 检索知识库: {original_name} (full_index={user_index_name})")
        docs, degraded = _load_and_retrieve(user_index_name, main_query, k=5)
        all_docs.extend(docs)
        if degraded:
            has_degraded = True

    all_docs = _dedup_by_content(all_docs)
    logger.info(f"[Retrieval Node] 主查询检索到 {len(all_docs)} 个去重文档{' (含降级)' if has_degraded else ''}")

    # 文档较少时使用关键点补充检索
    if len(all_docs) < 3 and key_points:
        logger.info("[Retrieval Node] 文档较少，使用关键点补充检索...")
        for point in key_points[:2]:
            for kb_name in knowledge_base_ids:
                user_index_name = _compose_user_index_name(user_id, kb_name)
                additional_docs, _degraded = _load_and_retrieve(user_index_name, point, k=2)
                for doc in additional_docs[:2]:
                    if getattr(doc, "page_content", None) and doc.page_content not in {
                        getattr(d, "page_content", None) for d in all_docs
                    }:
                        all_docs.append(doc)

    logger.info(f"[Retrieval Node] 最终检索到 {len(all_docs)} 个文档")

    retrieved_docs: list[RetrievedDocument] = []
    # 网络查询结果优先并入
    retrieved_docs.extend(_build_web_retrieved_docs(web_docs))
    for i, doc in enumerate(all_docs):
        retrieved_doc: RetrievedDocument = {
            "content": doc.page_content,
            "metadata": doc.metadata,
            "relevance_score": 1.0 - (i * 0.1),
        }
        retrieved_docs.append(retrieved_doc)

    retrieval_summary = (
        f"\n\n📄 已从 {len(knowledge_base_ids)} 个知识库检索到 "
        f"{len(retrieved_docs)} 个相关文档，将用于生成学习内容和练习题。"
    )

    return {
        "retrieved_docs": retrieved_docs,
        "messages": [{"role": "assistant", "content": retrieval_summary}],
        "current_step": "retrieval",
        "updated_at": timezone.now().isoformat(),
    }


def _build_web_retrieved_docs(web_docs: list[str]) -> list[RetrievedDocument]:
    """将 web_search 结果转为 RetrievedDocument 列表（source 标注 web_search）。"""
    return [
        {
            "content": content,
            "metadata": {"source": "web_search"},
            "relevance_score": 1.0,
        }
        for content in web_docs
    ]
