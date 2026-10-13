"""
检索器工厂模块（B-3 拆分后的单一职责）

只负责"VectorStore → BaseRetriever"的构建策略：similarity/mmr/score_threshold
三种检索类型 + Reranker 增强 + MultiQuery 叠加。

职责拆分对应关系（B-3/A-4，消费点已同步）：
- 降级检索三件套 → services/keyword_fallback.py（is_embedding_error /
  keyword_search_fallback / record_degradation_metric / build_retrieval_grams）
- Agent 检索工具层 → services/retriever_tool.py（SyncSafeRetrieverTool /
  create_retriever_tool / QueryIntentClassifier）
"""

from functools import lru_cache
from typing import Any, Literal

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.retrievers import BaseRetriever
from langchain_core.vectorstores import VectorStore

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

    Args:
        user_id: 用户 ID
        index_name: 原始知识库名（非全名）
        k: precise 模式返回文档数，None 时走 settings.retriever_k
        mode: "precise" | "comprehensive"
        search_type: precise 模式检索类型，None 时走 settings 默认

    Returns:
        BaseRetriever；索引不存在/为空/构建失败返回 None（调用方决定错误呈现）
    """
    loaded = load_user_vector_store(user_id, index_name)
    if loaded is None:
        return None
    vector_store, _metadata, _required_dim, _reload = loaded

    if mode == "comprehensive":
        comp_k = settings.retriever_comprehensive_k
        comp_fetch_k = max(comp_k * 3, 30)
        retriever = create_retriever(
            vector_store,
            k=comp_k,
            search_type="mmr",
            fetch_k=comp_fetch_k,
            use_reranker=settings.retriever_use_reranker,
        )
        if settings.retriever_use_multi_query:
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

    return create_retriever(
        vector_store,
        k=k,
        search_type=search_type,
        use_reranker=settings.retriever_use_reranker,
    )

# ── 检索器创建 ────────────────────────────────────────────────────────────────

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
    **kwargs,
) -> BaseRetriever:
    search_type: str = search_type or settings.retriever_search_type
    k = k or settings.retriever_k
    # B15：显式 is not None 判断——score_threshold=0.0 是合法显式取值
    # （不筛分数），`or` 兜底会把它误替换为全局默认阈值
    if score_threshold is None:
        score_threshold = settings.retriever_score_threshold
    fetch_k = fetch_k or settings.retriever_fetch_k

    logger.info(f"创建检索器: search_type={search_type}, k={k}, use_reranker={use_reranker}")

    search_kwargs: dict[str, Any] = {"k": k}

    if search_type == "mmr":
        search_kwargs["fetch_k"] = fetch_k
    elif search_type == "similarity_score_threshold":
        search_kwargs["score_threshold"] = score_threshold

    search_kwargs.update(kwargs)

    try:
        retriever: BaseRetriever = vector_store.as_retriever(
            search_type=search_type,
            search_kwargs=search_kwargs,
        )
        logger.info("检索器创建成功")

        if use_reranker:
            # 3.1：top_n 显式接线 settings.rag_rerank_top_n——无参调用会落到
            # create_reranker 的默认 5，配置项成为死配置
            reranker = create_reranker(top_n=settings.rag_rerank_top_n)
            if reranker is not None:
                retriever = create_reranking_retriever(retriever, reranker)
                logger.info("检索器已添加 Reranker 增强")

        return retriever
    except Exception:
        logger.exception("创建检索器失败")
        raise


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


def create_reranker(model_name: str = "ms-marco-MiniLM-L-12-v2", top_n: int = 5):
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
