"""关键词检索 / embedding 故障降级模块（Phase 3：统一应用层 BM25）。

职责：
- ``is_embedding_error``：embedding 故障类型化判定（CT-06 契约，字符串模式为兜底）
- ``keyword_search_fallback``：BM25 关键词检索（embedding 故障兜底 + 评测 keyword 策略）
- ``record_degradation_metric``：降级事件写入 IndexMetadata.metadata_json

Phase 3 变更（按评测结论统一检索实现）：
- 删除 ``build_retrieval_grams`` 与 pg_trgm ILIKE 子串匹配——它没有 TF 饱和、IDF 与
  文档长度归一，排序质量弱于 BM25；
- 删除 ``has_trigram_index`` / ``prepare_rag_indexes --trigram`` 依赖；
- 现在只保留 BM25（``bm25_index``，CJK 2-gram tokenizer 在索引层内部实现）。
"""

import time

from langchain_core.documents import Document

from Django_xm.apps.core.logging_utils import get_logger
from Django_xm.common.exceptions import EmbeddingUnavailableError

logger = get_logger(__name__)


def _collect_embedding_exception_types() -> tuple[type[BaseException], ...]:
    """收集 embedding provider 与连接层的典型异常类（B14 类型化判定）。

    可选依赖（requests/httpx/openai/ollama）缺失时逐项跳过；收集在模块导入
    时执行一次。openai.APIStatusError 涵盖 401/403/429/5xx 状态类错误，
    APIConnectionError 涵盖连接失败（含 APITimeoutError 子类）。
    """
    types: list[type[BaseException]] = [
        ConnectionError,
        TimeoutError,
        ConnectionRefusedError,
        ConnectionResetError,
    ]
    try:
        import requests

        types += [requests.exceptions.ConnectionError, requests.exceptions.Timeout]
    except ImportError:
        pass
    try:
        import httpx

        types += [httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.RemoteProtocolError]
    except ImportError:
        pass
    try:
        from openai import APIConnectionError, APIStatusError

        types += [APIConnectionError, APIStatusError]
    except ImportError:
        pass
    try:
        import ollama

        types += [ollama.RequestError, ollama.ResponseError]
    except ImportError:
        pass
    return tuple(types)


_EMBEDDING_EXCEPTION_TYPES = _collect_embedding_exception_types()

# B14：字符串兜底收窄为 embedding 明确特征（provider 名/接口名）。
# 旧模式 "connect"/"timeout"/"refused"/"403"/"503" 过宽——PG 连接错误
# （"connection to server ... refused"）会被误判为 embedding 故障转入关键词
# 降级，而降级检索仍打同一 PG，只会二次失败。
_EMBEDDING_ERROR_PATTERNS = (
    "embedding",
    "embed_query",
    "embedding_model",
    "ollama",
    "openai",
    "dashscope",
    "insufficient_balance",
)

# B14：数据库层异常所属模块前缀（向量库经 SQLAlchemy 直连 PG，异常不经过
# Django 包装，需按异常类 __module__ 识别 psycopg / sqlalchemy 家族）
_DATABASE_ERROR_MODULE_PREFIXES = ("psycopg", "sqlalchemy")


def _is_database_layer_error(exc: BaseException) -> bool:
    """显式识别数据库层异常（B14）。

    PG 连接错误不得触发关键词降级——降级检索查询的就是同一个 PG，误判
    只会二次失败并产生误导性"降级"标记。
    """
    try:
        from django.db.utils import Error as DjangoDbError

        if isinstance(exc, DjangoDbError):
            return True
    except Exception:  # pragma: no cover - Django 恒可用，防御性兜底
        logger.debug("django.db.utils.Error 导入失败，跳过 Django DB 异常排除判定")
    return type(exc).__module__.startswith(_DATABASE_ERROR_MODULE_PREFIXES)


def is_embedding_error(exc: Exception) -> bool:
    """判断异常是否为 Embedding/向量检索服务不可用导致（B14 收窄版）。

    判定顺序：
    1. CT-06 类型化契约：ai_engine FallbackEmbedding 全 provider 失败时包装
       抛出的 ``EmbeddingUnavailableError``（common.exceptions）恒命中；
    2. 数据库层异常显式排除（django.db / psycopg / sqlalchemy）——PG 故障
       不得转入关键词降级；
    3. 类型化判定：embedding provider / 连接层典型异常类（含 requests /
       httpx / openai / ollama，按可用性收集）；
    4. 字符串兜底：仅匹配 embedding 明确特征词，不再匹配 "connect" /
       "timeout" / "403" 等过宽子串。
    """
    if isinstance(exc, EmbeddingUnavailableError):
        return True
    if _is_database_layer_error(exc):
        return False
    if isinstance(exc, _EMBEDDING_EXCEPTION_TYPES):
        return True
    exc_msg = str(exc).lower()
    return any(pat in exc_msg for pat in _EMBEDDING_ERROR_PATTERNS)


def keyword_search_fallback(
    query: str, collection_name: str, k: int = 4, *, mark_degraded: bool = True
) -> list[Document]:
    """BM25 关键词检索（embedding 故障兜底 / 评测 keyword 策略）。

    Args:
        query: 用户查询文本
        collection_name: PGVector collection 名称
        k: 返回文档数量
        mark_degraded: True 时打 ``degraded`` 标记（embedding 故障降级场景）；
            False 时仅标注 ``sparse_mode``（Hybrid/评测常开场景）

    Returns:
        Document 副本列表（不修改 BM25 缓存中的原对象）；索引不可用/异常时返回 []
    """
    from Django_xm.apps.knowledge.services.bm25_index import get_bm25_index

    try:
        index = get_bm25_index(collection_name)
    except Exception as e:
        logger.warning(f"关键词检索: BM25 索引构建失败，返回空结果: {e}")
        return []

    if index is None:
        logger.warning("关键词检索: BM25 索引不可用（集合为空或 PG 不可达），返回空结果")
        return []

    results: list[Document] = []
    for doc in index.search(query, k):
        metadata = dict(doc.metadata or {})
        if mark_degraded:
            metadata["degraded"] = True
            metadata["degraded_mode"] = "bm25"
        else:
            metadata["sparse_mode"] = "bm25"
        results.append(Document(page_content=doc.page_content, metadata=metadata))
    return results


def record_degradation_metric(collection_name: str, error: Exception) -> None:
    """在 IndexMetadata.metadata_json 中记录降级次数"""
    try:
        from Django_xm.apps.knowledge.models import IndexMetadata

        meta = IndexMetadata.objects.filter(name=collection_name).first()
        if meta is None:
            return

        mj = meta.metadata_json or {}
        degradation = mj.get("degradation", {})
        degradation["total_count"] = degradation.get("total_count", 0) + 1
        degradation["last_error"] = str(error)[:500]
        degradation["last_time"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        mj["degradation"] = degradation
        meta.metadata_json = mj
        meta.save(update_fields=["metadata_json", "updated_at"])
    except Exception as e:
        logger.debug(f"记录降级指标失败（可忽略）: {e}")
