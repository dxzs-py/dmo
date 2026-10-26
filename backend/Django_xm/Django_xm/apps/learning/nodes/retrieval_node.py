"""
文档检索节点 (Retrieval Node)

本节点根据用户选择的知识库（IndexMetadata）检索学习资料。
- 从 state 读取 user_id 与 knowledge_base_ids（前端 KnowledgeBaseSelector 的 v-model）
- 每个知识库名称按 `user_{id}_{name}` 规则还原为完整索引名（与 kb_service.get_user_index_name 一致）
- 对每个知识库独立加载向量库并检索，合并去重后返回
- 未选择知识库时跳过检索，仅以 LLM 内置知识继续生成（与原硬编码 test_index 的开发模式解耦）

分层说明（ARCH-08 入口收敛，分层非重复）：
- 检索内核统一委托 UnifiedRagPipeline（retrieve_documents_from_retriever / retrieve_documents），
  与 strict_rag_chain、SyncSafeRetrieverTool 共享同一检索与降级实现，无复制逻辑；
- 本节点保留的仅为学习工作流特有编排：多知识库逐库检索合并、按 page_content
  全文精确去重（口径与管线 Level 1 的 80 字符前缀去重不同，服务于跨库合并场景）、
  key_points 补充检索与 relevance_score 递减赋值；
- 全库降级（Level 2）经 load_kb_documents + pipeline.retrieve_documents 复用管线实现，
  输出 Document 列表（学习工作流编排需要原始文档对象）。
"""

from typing import Any

from django.utils import timezone

from Django_xm.apps.core.logging_utils import get_logger
from Django_xm.apps.knowledge.services.cross_app import get_index_manager
from Django_xm.apps.knowledge.services.embedding_service import get_embeddings
from Django_xm.apps.knowledge.services.retrieval_service import create_retriever
from Django_xm.apps.knowledge.views_utils import build_user_index_name, get_original_index_name

from ..models import WorkflowStep
from ..services.state import RetrievedDocument, StudyFlowState
from .stream_events import emit_step

logger = get_logger(__name__)


def _compose_user_index_name(user_id: int, kb_name: str) -> str:
    """索引全名构造（D-1 单源委托：knowledge.views_utils.build_user_index_name）。

    原实现为等价 f-string 副本（因节点仅持 user_id 无法调 user 对象版
    get_user_index_name）；views_utils 现提供 user_id 版单源函数，本函数保留
    调用点兼容名、内部转委托。
    """
    return build_user_index_name(user_id, kb_name)


def _load_and_retrieve(user_index_name: str, query: str, embeddings: Any, k: int = 5) -> tuple[list[Any], bool]:
    """加载单个知识库的向量库并执行检索。

    Args:
        user_index_name: 索引全名（user_{id}_{name}）
        query: 检索查询文本
        embeddings: 调用方注入的 Embeddings 实例（3.5：多库循环下单次构建，
            不再每次调用内部重建——get_embeddings 为即时构建，含 SystemConfig
            读取与 provider 初始化开销）
        k: 每库返回文档数

    Returns:
        (检索结果列表, is_degraded): is_degraded 为 True 表示向量检索无结果、
        降级加载全库文档通过 UnifiedRagPipeline.retrieve_documents() 重排序获得结果。
    """
    manager = get_index_manager()
    try:
        vector_store = manager.load_index(user_index_name, embeddings)
        retriever = create_retriever(vector_store, k=k)
    except FileNotFoundError:
        logger.warning(f"[Retrieval Node] 索引不存在: {user_index_name}")
        return [], False
    except Exception as e:
        logger.warning(f"[Retrieval Node] 加载索引 {user_index_name} 失败: {e}")
        return [], False

    # 统一走 RagService（检索 + 去重 + 邻块 + token 预算）
    from Django_xm.apps.knowledge.services.rag_service import (
        RagRequest,
        RagScope,
        RagService,
    )

    parts = user_index_name.split("_")
    owner_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
    try:
        result = RagService().retrieve(
            RagRequest(
                query=query,
                scope=RagScope(
                    kind="persistent_kb", name=user_index_name, user_id=owner_id
                ),
                k=k,
                metrics_kind="learning",
                # 学习流程保持既有检索行为（不做总结/全局分析意图分发）
                intent="precise",
            ),
            retriever=retriever,
            vector_store=vector_store,
        )
    except Exception as e:
        logger.warning(f"[Retrieval Node] 检索失败: {user_index_name}, {e}")
        return [], False

    if result.docs:
        return result.docs, result.degraded

    # Pipeline couldn't find anything; try full document load as Level 2 fallback
    logger.info(f"[Retrieval Node] 向量检索无结果，降级加载全库文档: {user_index_name}")
    try:
        from Django_xm.apps.knowledge.services.rag_retrieval import UnifiedRagPipeline, load_kb_documents

        kb_docs, total_tokens = load_kb_documents([user_index_name])
        if not kb_docs:
            logger.warning(f"[Retrieval Node] 降级加载全库文档为空: {user_index_name}")
            return [], False

        pipeline = UnifiedRagPipeline(scenario="knowledge_base")
        degraded_docs = pipeline.retrieve_documents(query, kb_docs, total_tokens)
        logger.info(
            f"[Retrieval Node] 降级检索完成: {user_index_name}, "
            f"全库 {len(kb_docs)} 片段 -> 命中 {len(degraded_docs)} 个文档"
        )
        return degraded_docs, True
    except Exception as e:
        logger.warning(f"[Retrieval Node] 降级检索失败: {user_index_name}, {e}")
        return [], False


def _run_web_search(query: str) -> list[str]:
    """调用 web_search 工具执行联网搜索，返回搜索结果文本列表。

    复用聊天模块的 web_search 工具（apps/tools._get_web_search_tools），
    未配置 TAVILY_API_KEY / 无 ddgs 时静默降级返回空列表。
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
            "current_step": WorkflowStep.RETRIEVAL,
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
                "current_step": WorkflowStep.RETRIEVAL,
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
            "current_step": WorkflowStep.RETRIEVAL,
            "updated_at": timezone.now().isoformat(),
        }

    main_query = f"{topic}"
    logger.info(f"[Retrieval Node] 主查询: {main_query}, 知识库数量: {len(knowledge_base_ids)}")

    # 3.5：Embeddings 实例循环外单次构建，主查询与关键点补充检索共用
    embeddings = get_embeddings()

    # 对每个知识库独立检索后合并去重
    all_docs: list[Any] = []
    has_degraded = False
    for kb_name in knowledge_base_ids:
        user_index_name = _compose_user_index_name(user_id, kb_name)
        original_name = get_original_index_name(user_index_name)
        logger.info(f"[Retrieval Node] 检索知识库: {original_name} (full_index={user_index_name})")
        docs, degraded = _load_and_retrieve(user_index_name, main_query, embeddings, k=5)
        all_docs.extend(docs)
        if degraded:
            has_degraded = True

    from Django_xm.apps.knowledge.services.rag_context_pipeline import build_context

    all_docs = build_context(all_docs, apply_budget=True).docs if all_docs else []
    logger.info(f"[Retrieval Node] 主查询检索到 {len(all_docs)} 个去重文档{' (含降级)' if has_degraded else ''}")

    # 文档较少时使用关键点补充检索
    if len(all_docs) < 3 and key_points:
        logger.info("[Retrieval Node] 文档较少，使用关键点补充检索...")
        for point in key_points[:2]:
            for kb_name in knowledge_base_ids:
                user_index_name = _compose_user_index_name(user_id, kb_name)
                additional_docs, _degraded = _load_and_retrieve(user_index_name, point, embeddings, k=2)
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
        "current_step": WorkflowStep.RETRIEVAL,
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
