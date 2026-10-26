"""
检索器工厂模块（B-3 拆分后的单一职责）

只负责"VectorStore → BaseRetriever"的构建策略：similarity/mmr/score_threshold
三种检索类型 + Reranker 增强 + MultiQuery 叠加。

职责拆分对应关系（B-3/A-4，消费点已同步）：
- 降级检索三件套 → services/keyword_fallback.py（is_embedding_error /
  keyword_search_fallback / record_degradation_metric / build_retrieval_grams）
- Agent 检索工具层 → services/kb_search_tool.py（唯一 knowledge_base_search，
  输入 query/kb_ids/intent）；附件工具 → tools/langchain/attachment_rag.py
- 检索意图 → services/query_intent.py（precise / comprehensive 规则分类）
"""

import asyncio
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal

from langchain_core.documents import Document
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.retrievers import BaseRetriever
from langchain_core.vectorstores import VectorStore
from pydantic import PrivateAttr

from Django_xm.apps.core.logging_utils import get_logger
from Django_xm.apps.knowledge.config import settings

logger = get_logger(__name__)

SearchType = Literal["similarity", "mmr", "similarity_score_threshold"]


# ── 用户知识库检索器/向量库统一门面（B-1 编排收敛，D-3 参数映射收口） ────────────


def load_user_vector_store(user_id: int, index_name: str):
    """按 (user_id, 知识库名) 构建就绪向量库上下文（B-1 底层单源）。

    编排：views_utils.build_user_index_name（D-1 命名单源）→ index_exists →
    load_metadata → required_dim 探测 → get_embeddings → load_index。

    Args:
        user_id: 用户 ID
        index_name: 原始知识库名（非全名）

    Returns:
        (vector_store, metadata, required_dim, reload_vector_store) 四元组；
        reload_vector_store 为维度自愈重建回调（闭包复用同一 manager/embeddings，
        语义与原调用方内联 lambda 等价）。索引不存在或加载失败返回 None
        （错误呈现形态由调用方决定：view 抛 4xx / chat 返回 None）
    """
    from ..views_utils import build_user_index_name
    from .embedding_service import get_embeddings
    from .index_service import IndexManager

    try:
        user_index_name = build_user_index_name(user_id, index_name)
        manager = IndexManager()
        # 3.2：归属校验随 user_id 下沉——即使索引名被外部构造为他人前缀
        # （user_{other}_xxx），此处也会被 index_exists/load_metadata 拒绝
        if not manager.index_exists(user_index_name, user_id=user_id):
            logger.warning(f"load_user_vector_store: 索引不存在: {user_index_name}")
            return None
        metadata = manager.load_metadata(user_index_name, user_id=user_id)
        required_dim = metadata.get("embedding_dimension") if metadata else None
        embeddings = get_embeddings(required_dimension=required_dim)
        vector_store = manager.load_index(user_index_name, embeddings)

        def reload_vector_store():
            return manager.load_index(user_index_name, embeddings)

        return vector_store, metadata, required_dim, reload_vector_store
    except Exception:
        logger.exception("load_user_vector_store: 向量库构建失败")
        return None


@dataclass(frozen=True)
class UserRetrieverBundle:
    """知识库检索器 + 其底层向量库（Level 1 降级需要 store 实例）。"""

    retriever: BaseRetriever
    vector_store: Any


def build_user_retriever_bundle(
    user_id: int,
    index_name: str,
    k: int | None = None,
    mode: str = "precise",
    search_type: SearchType | None = None,
) -> UserRetrieverBundle | None:
    """按 (user_id, 知识库名) 构建检索器并同时返回底层向量库（B-1 门面）。

    与 build_user_retriever 的差异仅在返回值：strict RAG 的 Level 1 降级
    （放宽阈值 + 扩大 k + 精排）需要 vector_store 实例，而检索器可能被
    rerank / MultiQuery 多层包装，向上查找 store 属于结构耦合。本函数只
    调用一次 load_user_vector_store（IndexManager 内部 TTL 缓存），视图层
    用它同时拿到两个对象，不重复编排。

    Args:
        user_id: 用户 ID
        index_name: 原始知识库名（非全名）
        k: precise 模式返回文档数，None 时走 settings.retriever_k
        mode: "precise" | "comprehensive"
        search_type: precise 模式检索类型，None 时走 settings 默认

    Returns:
        UserRetrieverBundle；索引不存在/加载失败返回 None
    """
    loaded = load_user_vector_store(user_id, index_name)
    if loaded is None:
        return None
    vector_store = loaded[0]
    return UserRetrieverBundle(
        retriever=_create_user_retriever(
            vector_store, k=k, mode=mode, search_type=search_type
        ),
        vector_store=vector_store,
    )


def build_user_retriever(
    user_id: int,
    index_name: str,
    k: int | None = None,
    mode: str = "precise",
    search_type: SearchType | None = None,
) -> BaseRetriever | None:
    """按 (user_id, 知识库名) 构建就绪检索器（B-1 单一门面）。

    此前同一编排序列散布 4 处（views_rag 两处 / kb_service / chat get_rag_retriever），
    收敛后 view/chat 只解析参数与呈现结果。comprehensive 模式的检索器参数映射
    （MMR + fetch_k + MultiQuery 叠加，原 chat 侧域知识外溢 D-3）一并收口。

    Reranker 接线（B8/D1）：FlashRank 重排序经 settings.retriever_use_reranker
    （默认 True）统一控制，precise/comprehensive 两分支均读取并透传给
    create_retriever——chat 工具链路（get_rag_retriever → 本函数）接线后自动生效。

    P0 检索改造：precise 分支显式以 settings.rag_score_threshold 作为相似度
    阈值（而非 retriever_score_threshold），使「阈值在精排之前生效」在知识库
    主链路统一口径；comprehensive 为 MMR（无分数），阈值不适用。

    Args:
        user_id: 用户 ID
        index_name: 原始知识库名（非全名）
        k: precise 模式返回文档数，None 时走 settings.retriever_k
        mode: "precise" | "comprehensive"
        search_type: precise 模式检索类型，None 时走 settings 默认

    Returns:
        BaseRetriever；索引不存在/为空/构建失败返回 None（调用方决定错误呈现）
    """
    bundle = build_user_retriever_bundle(
        user_id, index_name, k=k, mode=mode, search_type=search_type
    )
    return bundle.retriever if bundle is not None else None


def build_comprehensive_retriever(
    vector_store,
    *,
    k: int | None = None,
    use_multi_query: bool = False,
    use_reranker: bool | None = None,
    rerank_top_n: int | None = None,
    rerank_model: str | None = None,
) -> BaseRetriever:
    """全局分析意图检索器（MMR 多样性召回，可选叠加 MultiQuery）。

    用于两类场景（共用一套参数映射，避免两处域知识分叉）：
    - ``build_user_retriever(mode="comprehensive")``：深度研究的广谱检索；
    - ``RagService`` 按意图分发：Agent 工具显式/规则判定为 comprehensive 时，
      基于已加载的 vector_store 现构一个宽召回检索器（不重新加载索引）。

    Args:
        vector_store: 已加载的向量库实例
        k: 返回文档数，None 时取 ``settings.retriever_comprehensive_k``
        use_multi_query: 是否叠加 MultiQuery（会额外产生 LLM 调用，工具链路默认关）
        use_reranker / rerank_top_n / rerank_model: None 时读运行时设置

    Returns:
        BaseRetriever（MMR，可能被 MultiQuery 包装）
    """
    from Django_xm.apps.knowledge.rag_settings import get_rag_runtime_settings

    runtime = get_rag_runtime_settings()
    comp_k = int(k or settings.retriever_comprehensive_k)
    comp_fetch_k = max(comp_k * 3, 30)
    retriever = create_retriever(
        vector_store,
        k=comp_k,
        search_type="mmr",
        fetch_k=comp_fetch_k,
        use_reranker=runtime["use_reranker"] if use_reranker is None else use_reranker,
        rerank_top_n=runtime["rerank_top_n"] if rerank_top_n is None else rerank_top_n,
        rerank_model=runtime["rerank_model"] if rerank_model is None else rerank_model,
    )
    if use_multi_query:
        try:
            # A-5：helper LLM 与 get_chat_model 均走 knowledge.config 门面
            from Django_xm.apps.knowledge.config import get_chat_model, get_helper_model

            mq_llm = get_helper_model() or get_chat_model(streaming=False)
            retriever = create_multi_query_retriever(retriever, llm=mq_llm, include_original=True)
            logger.info(f"comprehensive 检索器已叠加 MultiQuery (k={comp_k})")
        except Exception as e:
            logger.warning(f"MultiQuery 叠加失败，使用基础 MMR 检索器: {e}")
    logger.info(
        f"创建 comprehensive 检索器: k={comp_k}, search_type=mmr, "
        f"fetch_k={comp_fetch_k}, use_reranker={settings.retriever_use_reranker}"
    )
    return retriever


def _create_user_retriever(
    vector_store,
    k: int | None,
    mode: str,
    search_type: SearchType | None,
) -> BaseRetriever:
    """在已加载的向量库上构建检索器（build_user_retriever_bundle 内聚）。

    Phase 3：参数全部来自运行时设置（SystemConfig 覆盖），前端设置页修改后即时生效。
    """
    from Django_xm.apps.knowledge.rag_settings import get_rag_runtime_settings

    runtime = get_rag_runtime_settings()

    if mode == "comprehensive":
        return build_comprehensive_retriever(
            vector_store,
            use_multi_query=settings.retriever_use_multi_query,
        )

    return create_retriever(
        vector_store,
        k=k,
        search_type=search_type,
        use_reranker=runtime["use_reranker"],
        # 知识库主链路阈值/窗口/精排/混合全部走运行时设置
        score_threshold=runtime["score_threshold"],
        recall_k=runtime["recall_k"],
        rerank_top_n=runtime["rerank_top_n"],
        rerank_model=runtime["rerank_model"],
        use_hybrid=runtime["hybrid_enabled"],
        dense_weight=runtime["hybrid_dense_weight"],
        sparse_weight=runtime["hybrid_sparse_weight"],
    )

# ── 检索器创建 ────────────────────────────────────────────────────────────────


class ThresholdVectorStoreRetriever(BaseRetriever):
    """相似度召回 + 阈值过滤的标准检索器（精排之前的固定环节）。

    设计要点（P0 检索改造）：

    1. **阈值不依赖检索器结构**：此前 similarity 阈值只存在于
       ``vector_store.as_retriever(search_type="similarity")`` 的裸路径，
       一旦被 ContextualCompressionRetriever（rerank）/ MMR / MultiQuery 包装，
       上游鸭子判定落空，``rag_score_threshold`` 静默失效。本类把
       「召回 + 归一 + 阈值」内聚为检索器本体，rerank 仅作为其上的可插拔叠加。
    2. **分数语义单源**：走 ``vector_store.search_vector_store``（similarity/
       distance 归一、阈值过滤同一内核），pgvector 的 cosine distance 会先转成
       similarity 再与阈值比较，避免反向过滤。
    3. **不暴露 vector_store / search_type 公共属性**（仅 PrivateAttr）：
       ``UnifiedRagPipeline._initial_search_from_retriever`` 的 with_scores
       鸭子判定只应命中裸 VectorStoreRetriever。若本类被误判，会触发第二次
       向量检索，并用 rag_score_threshold 覆盖本类已应用的阈值。
    4. **精排前不写 metadata["score"]**：rerank 开启时分数由 FlashRank 写入
       ``relevance_score``；若这里也写 score，``_extract_sources`` 的
       「score 优先」会让前端显示精排前的相似度而非精排分。
    """

    _vector_store: Any = PrivateAttr()
    _k: int = PrivateAttr()
    _score_threshold: float | None = PrivateAttr()
    _score_semantics: str = PrivateAttr()
    _write_score: bool = PrivateAttr()
    _search_kwargs: dict = PrivateAttr()

    def __init__(
        self,
        vector_store,
        k: int,
        score_threshold: float | None = None,
        score_semantics: str = "similarity",
        write_score: bool = True,
        search_kwargs: dict | None = None,
    ):
        super().__init__()
        self._vector_store = vector_store
        self._k = k
        self._score_threshold = score_threshold
        self._score_semantics = score_semantics
        self._write_score = write_score
        self._search_kwargs = dict(search_kwargs or {})

    def _get_relevant_documents(self, query: str, **kwargs: Any) -> list[Document]:
        # 延迟导入：避免检索器工厂模块在 app 加载早期拉起向量库后端链
        from Django_xm.apps.knowledge.vector_store import search_vector_store

        pairs = search_vector_store(
            self._vector_store,
            query,
            k=self._k,
            score_threshold=self._score_threshold,
            score_semantics=self._score_semantics,
            search_kwargs=self._search_kwargs,
        )

        docs: list[Document] = []
        for doc, score in pairs:
            if self._write_score and score is not None:
                doc.metadata["score"] = float(score)
            docs.append(doc)
        return docs

    async def _aget_relevant_documents(self, query: str, **kwargs: Any) -> list[Document]:
        # 检索为重 IO（embed_query 远程调用 + 向量库查询），移出事件循环
        return await asyncio.to_thread(self._get_relevant_documents, query, **kwargs)


# 3.6 实测：langchain-community 0.4.x 顶层 __init__ 不再导出 FlashRank 重排器
# （旧顶层名 FlashRankReranker 已不存在），必须从子模块导入；类名为
# FlashrankRerank，构造字段为 model（pydantic extra="forbid"，传 model_name
# 会 ValidationError）。别名保持项目内既有调用名不变——否则 FLASHRANK_AVAILABLE
# 恒 False，reranker 全链路静默禁用（settings.retriever_use_reranker 成死配置）。
try:
    from langchain_community.document_compressors.flashrank_rerank import (
        FlashrankRerank as FlashRankReranker,
    )

    FLASHRANK_AVAILABLE = True
except ImportError:
    FLASHRANK_AVAILABLE = False

try:
    from langchain_classic.retrievers import ContextualCompressionRetriever, MultiQueryRetriever

    ADVANCED_RETRIEVERS_AVAILABLE = True
except ImportError:
    ADVANCED_RETRIEVERS_AVAILABLE = False


def create_retriever(
    vector_store: VectorStore,
    search_type: SearchType | None = None,
    k: int | None = None,
    score_threshold: float | None = None,
    fetch_k: int | None = None,
    use_reranker: bool = False,
    recall_k: int | None = None,
    use_hybrid: bool | None = None,
    rerank_top_n: int | None = None,
    rerank_model: str | None = None,
    dense_weight: float | None = None,
    sparse_weight: float | None = None,
    **kwargs,
) -> BaseRetriever:
    """构建检索器（召回窗口 / 阈值 / 精排三层解耦）。

    结构（顺序固定，精排可插拔）:

        ThresholdVectorStoreRetriever(k=recall_window, threshold)
            → ContextualCompressionRetriever(FlashRank(top_n))

    参数语义：

    - ``k``：调用方期望的**最终**文档数（交付契约）。启用 reranker 时它是
      精排保留数上限，**不是**向量召回数。
    - ``recall_k``：向量召回候选数（仅 rerank 开启时生效，默认
      ``settings.rag_recall_k``）。必须 ≥ 精排保留数，否则会负向剪枝。
    - ``use_hybrid``：是否启用 dense+sparse 混合召回；None 读
      ``settings.rag_hybrid_enabled``（评测消融可显式覆盖）。
    - ``score_threshold``：相似度阈值（0~1 相似度语义，后端 distance 由
      search_vector_store 归一），**在精排之前**过滤，与是否 rerank 无关。
      显式传 0.0 表示不过滤（B15：用 is not None 判定，不能用 or 兜底）。
    - ``search_type``：``similarity`` / ``similarity_score_threshold`` 走
      阈值检索器；``mmr`` 走多样性召回（MMR 无分数，阈值不适用）。

    Args:
        vector_store: 向量库实例
        search_type: 检索类型，None 时读 settings.retriever_search_type
        k: 最终文档数，None 时读 settings.retriever_k
        score_threshold: 相似度阈值，None 时读 settings.retriever_score_threshold
        fetch_k: MMR 候选数，None 时读 settings.retriever_fetch_k
        use_reranker: 是否叠加 FlashRank 精排
        recall_k: 向量召回窗口，仅 use_reranker=True 时生效
        use_hybrid: 是否启用混合召回，None 读配置
        **kwargs: 透传到底层检索的额外参数（如 metadata filter）

    Returns:
        BaseRetriever（可能是 ThresholdVectorStoreRetriever 或
        ContextualCompressionRetriever）

    Raises:
        ValueError: search_type 不在支持列表内
    """
    search_type: str = search_type or settings.retriever_search_type
    final_k = k or settings.retriever_k
    # B15：显式 is not None 判断——score_threshold=0.0 是合法显式取值
    # （不筛分数），`or` 兜底会把它误替换为全局默认阈值
    if score_threshold is None:
        score_threshold = settings.retriever_score_threshold
    fetch_k = fetch_k or settings.retriever_fetch_k

    # 召回窗口与精排窗口解耦（P0）：reranker 需要大候选集才有筛选价值。
    # 改造前 k=4 → 向量只取 4 条，而 top_n=6，精排仅能换序（PR 无收益）。
    if use_reranker:
        recall_window = max(int(recall_k or settings.rag_recall_k), final_k)
        max_rerank = settings.rag_rerank_top_n if rerank_top_n is None else rerank_top_n
        rerank_limit = min(final_k, max_rerank)
    else:
        recall_window = final_k
        rerank_limit = None

    if search_type in ("similarity", "similarity_score_threshold"):
        from Django_xm.apps.knowledge.vector_store import resolve_score_semantics

        semantics = resolve_score_semantics(vector_store)
        # P0-O6：PGVector 且 collection 名可用时启用 hybrid（dense + sparse → RRF）。
        # use_hybrid=None 时读配置；评测/消融可显式传 False/True（否则 dense 与 hybrid 无法区分）。
        # isinstance 校验兼顾测试替身（MagicMock 的 collection_name 会返回子 mock）
        hybrid_setting = settings.rag_hybrid_enabled if use_hybrid is None else use_hybrid
        collection_name = getattr(vector_store, "collection_name", None)
        hybrid_enabled = bool(
            hybrid_setting
            and isinstance(collection_name, str)
            and collection_name
            and settings.rag_hybrid_sparse_weight > 0
        )

        if hybrid_enabled:
            from Django_xm.apps.knowledge.services.hybrid_retrieval import (
                HybridVectorStoreRetriever,
            )

            retriever: BaseRetriever = HybridVectorStoreRetriever(
                vector_store,
                collection_name=collection_name,
                k=recall_window,
                score_threshold=score_threshold,
                score_semantics=semantics,
                write_score=not use_reranker,
                search_kwargs=kwargs,
                dense_weight=settings.rag_hybrid_dense_weight if dense_weight is None else dense_weight,
                sparse_weight=settings.rag_hybrid_sparse_weight if sparse_weight is None else sparse_weight,
            )
            logger.info(
                f"hybrid 召回已启用: collection={collection_name}, "
                f"weights=({settings.rag_hybrid_dense_weight}, {settings.rag_hybrid_sparse_weight})"
            )
        else:
            retriever = ThresholdVectorStoreRetriever(
                vector_store,
                k=recall_window,
                score_threshold=score_threshold,
                score_semantics=semantics,
                # rerank 开启时不写精排前的 similarity：sources 应以 reranker 的
                # relevance_score 为准（_extract_sources 的 score 优先级更高）
                write_score=not use_reranker,
                search_kwargs=kwargs,
            )
    elif search_type == "mmr":
        # MMR 返回无分数，阈值不可用；召回窗口同样放大后交由精排筛选
        search_kwargs: dict[str, Any] = {
            "k": recall_window,
            "fetch_k": max(fetch_k, recall_window * 2),
        }
        search_kwargs.update(kwargs)
        retriever = vector_store.as_retriever(search_type="mmr", search_kwargs=search_kwargs)
    else:
        raise ValueError(
            f"不支持的检索类型: {search_type!r}（可选 similarity/mmr/similarity_score_threshold）"
        )

    logger.info(
        f"创建检索器: search_type={search_type}, final_k={final_k}, "
        f"recall_window={recall_window}, use_reranker={use_reranker}, rerank_top_n={rerank_limit}"
    )

    if use_reranker:
        # 3.1：top_n 显式透传（无参调用会落到 create_reranker 默认 5）；
        # P0：改为 min(k, rag_rerank_top_n)，让调用方 k 才是交付条数契约
        reranker = create_reranker(top_n=rerank_limit, model_name=rerank_model)
        if reranker is not None:
            retriever = create_reranking_retriever(retriever, reranker)
            logger.info("检索器已添加 Reranker 增强（精排前阈值过滤生效）")

    return retriever


@lru_cache(maxsize=8)
def get_flashrank_reranker(model_name: str, top_n: int):
    """FlashRank 重排器进程级缓存单例（RP-10）

    原实现每次 Level 1 降级 / 评估路径都重新构造 FlashRankReranker——构造含
    ONNX 模型加载（百 MB 量级磁盘读 + 内存加载），同进程本可零成本复用。

    缓存键为 (model_name, top_n)：top_n 烤在实例上，不能仅按模型名缓存
    （否则第二个 top_n 不同的消费者拿到错值）；生产两条路径的 top_n 各自
    恒定（settings.rag_rerank_top_n / evaluate_rag），maxsize=8 有界。

    不违反阶段 1 不变量：FlashRank 为纯 CPU ONNX 推理对象，不绑事件循环、
    非 async client（3.5 审计 RP-10 已论证）。构造失败不缓存（lru_cache 不
    缓存异常），调用方失败降级路径保持既有行为。
    """
    # 构造字段为 model（0.4.x FlashrankRerank pydantic 字段，extra="forbid"）；
    # 形参名 model_name 保持项目内语义（模型标识）不变
    reranker = FlashRankReranker(model=model_name, top_n=top_n)
    logger.info(f"FlashRankReranker 创建成功: model={model_name}, top_n={top_n}")
    return reranker


def _emit_rerank_degradation(reason: str) -> None:
    """rerank 不可用降级留痕（3.8）：warning 升级为 error 级结构化日志。

    FlashRank 构造失败是**静默降级**（检索继续，重排序缺失），旧实现只打
    warning，运维不可见。本函数以 ``degradation=rerank_unavailable`` 结构化
    关键字升级为 error 级留痕，供日志采集/告警规则捕获；前端感知面：
    strict_rag 的 sources payload 分数由检索内核透出，不受本降级影响，
    重排序缺失表现为相关性排序质量下降。

    Args:
        reason: 降级原因（依赖缺失 / 构造异常 / 压缩检索器不可用等）
    """
    logger.error(f"pipeline=retrieval_service degradation=rerank_unavailable reason={reason}")


def create_reranker(model_name: str | None = None, top_n: int = 5):
    """构造 FlashRank 重排器（P0-O9：模型名可配置，默认读运行时设置）。

    Args:
        model_name: 模型标识，None 时读运行时设置（前端可改）
        top_n: 精排保留数
    """
    if model_name is None:
        from Django_xm.apps.knowledge.rag_settings import get_rag_runtime_settings

        model_name = get_rag_runtime_settings()["rerank_model"]
    if not FLASHRANK_AVAILABLE:
        _emit_rerank_degradation("flashrank package not installed")
        return None

    try:
        return get_flashrank_reranker(model_name, top_n)
    except Exception as e:
        _emit_rerank_degradation(f"reranker construction failed: {e}")
        return None


def create_reranking_retriever(
    base_retriever: BaseRetriever,
    reranker=None,
) -> BaseRetriever:
    if reranker is None:
        # 3.1：同 create_retriever——默认重排器 top_n 显式走 settings 配置
        reranker = create_reranker(top_n=settings.rag_rerank_top_n)
        if reranker is None:
            logger.warning("Reranker 不可用，返回基础检索器")
            return base_retriever

    if not ADVANCED_RETRIEVERS_AVAILABLE:
        _emit_rerank_degradation("ContextualCompressionRetriever unavailable")
        logger.warning("ContextualCompressionRetriever 不可用，返回基础检索器")
        return base_retriever

    try:
        compression_retriever = ContextualCompressionRetriever(
            base_compressor=reranker,
            base_retriever=base_retriever,
        )
        logger.info("Reranking 检索器创建成功")
        return compression_retriever
    except Exception as e:
        _emit_rerank_degradation(f"compression retriever construction failed: {e}")
        logger.warning(f"创建 Reranking 检索器失败: {e}，返回基础检索器")
        return base_retriever


MULTI_QUERY_PROMPT = None
try:
    from langchain_core.prompts import PromptTemplate

    MULTI_QUERY_PROMPT = PromptTemplate.from_template(
        "你是一个AI语言模型助手。你的任务是生成3个不同版本的用户问题，"
        "用于从向量数据库中检索相关文档。通过生成多个视角的问题，"
        "你可以帮助用户克服基于距离相似度检索的一些局限性。"
        "请提供这些用换行符分隔的替代问题。\n\n"
        "原始问题: {question}\n\n替代问题:"
    )
except ImportError:
    pass


def create_multi_query_retriever(
    base_retriever: BaseRetriever,
    llm: BaseChatModel | None = None,
    include_original: bool = True,
) -> BaseRetriever:
    if not ADVANCED_RETRIEVERS_AVAILABLE:
        logger.warning("MultiQueryRetriever 不可用，返回基础检索器")
        return base_retriever

    if llm is None:
        logger.warning("LLM 未提供，MultiQueryRetriever 不可用，返回基础检索器")
        return base_retriever

    try:
        # 使用 RunnableLambda 包装 LLM，强制将 callbacks 设为空列表，
        # 防止 MultiQuery 内部 LLM 调用的输出被父级 callback 捕获
        # 并流式传输到前端（表现为 reasoning_content 或 content 泄漏）
        from langchain_core.output_parsers import BaseOutputParser
        from langchain_core.runnables import RunnableLambda

        class _LineListParser(BaseOutputParser[list[str]]):
            """将 LLM 输出按换行拆分为字符串列表，替代 langchain_classic 的 LineListOutputParser"""

            def parse(self, text: str) -> list[str]:
                return [line.strip() for line in text.strip().split("\n") if line.strip()]

        def _quiet_invoke(input, config=None, **kwargs):
            config = config or {}
            config["callbacks"] = []
            config["tags"] = [*config.get("tags", []), "nostream"]
            return llm.invoke(input, config=config, **kwargs)

        async def _quiet_ainvoke(input, config=None, **kwargs):
            config = config or {}
            config["callbacks"] = []
            config["tags"] = [*config.get("tags", []), "nostream"]
            return await llm.ainvoke(input, config=config, **kwargs)

        quiet_llm = RunnableLambda(func=_quiet_invoke, afunc=_quiet_ainvoke)
        prompt = MULTI_QUERY_PROMPT
        if prompt is None:
            from langchain.retrievers.multi_query import DEFAULT_QUERY_PROMPT

            prompt = DEFAULT_QUERY_PROMPT

        output_parser = _LineListParser()
        llm_chain = prompt | quiet_llm | output_parser

        multi_query_retriever = MultiQueryRetriever(
            retriever=base_retriever,
            llm_chain=llm_chain,
            include_original=include_original,
        )
        logger.info(f"MultiQueryRetriever 创建成功 (include_original={include_original}, callback隔离已启用)")
        return multi_query_retriever
    except Exception as e:
        logger.warning(f"MultiQueryRetriever 创建失败: {e}，返回基础检索器")
        return base_retriever
