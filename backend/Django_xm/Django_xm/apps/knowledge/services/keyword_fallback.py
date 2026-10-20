"""关键词降级检索模块（B-3 拆分：自 retrieval_service.py 下沉独立）

职责单一：Embedding 服务不可用时的 PostgreSQL 关键词兜底检索及其配套件——
- is_embedding_error：embedding 故障类型化判定（CT-06 契约，字符串模式为兜底）
- build_retrieval_grams：CJK 2-gram / ASCII 整词检索单元构造
- keyword_search_fallback：PG gram 子串匹配降级检索
- record_degradation_metric：降级事件写入 IndexMetadata.metadata_json

A-4 转正说明：本模块四函数此前以 ``_`` 私有名散布于 retrieval_service.py，
被 strict_rag_chain / evaluate_rag 跨模块穿透访问；现升格为公开 API，
消费点已全部同步至本模块。
"""

import json
import re
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
    """判断异常是否为 Embedding/向量检索服务不可用导致（B14 收窄版）

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


# ── 关键词降级检索（CJK 友好） ────────────────────────────────────────────────

# 连续 CJK 字符片段（含扩展 A 区）或 ASCII 词
_GRAM_TOKEN_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]+|[A-Za-z0-9_]+")

# CJK 2-gram：中文无空格，单字召回噪声过大，3-gram 又会显著漏召回
_CJK_GRAM_SIZE = 2
_MAX_GRAMS = 16


def build_retrieval_grams(query: str, max_grams: int = _MAX_GRAMS) -> list[str]:
    """把查询拆成适合 PostgreSQL 子串匹配的检索单元。

    - 连续 CJK 片段切为 2-gram：无需分词词典即可覆盖中文，兼顾精度与召回
    - ASCII 词整词保留（长度 >= 2）：避免 "SQL" 被切成 "SQ"/"QL" 造成误召回
    - 按 gram 长度降序（长 gram 区分度更高）去重后截断

    Args:
        query: 用户查询文本
        max_grams: 最多保留的 gram 数量，防止超长查询导致 SQL 开销失控

    Returns:
        gram 列表；查询不含有效字符时返回空列表
    """
    grams: list[str] = []

    for token in _GRAM_TOKEN_RE.findall(query or ""):
        if token.isascii():
            if len(token) >= 2:
                grams.append(token.lower())
        elif len(token) < _CJK_GRAM_SIZE:
            grams.append(token)
        else:
            grams.extend(token[i : i + _CJK_GRAM_SIZE] for i in range(len(token) - _CJK_GRAM_SIZE + 1))

    seen: set[str] = set()
    ordered: list[str] = []
    for gram in sorted(grams, key=len, reverse=True):
        if gram not in seen:
            seen.add(gram)
            ordered.append(gram)

    return ordered[:max_grams]


# 中文全文检索降级 SQL。
# 关键点：不能使用 to_tsvector/to_tsquery('simple', ...) —— PostgreSQL 的 simple
# 配置不做中文分词，整段连续中文会被解析成**单一词元**，导致任何中文子串查询都无法命中
# （实测 30 题中文评测集召回率为 0）。此处改为 unnest gram 数组后做 ILIKE 子串计数，
# 以命中 gram 数排序，命中数相同者优先短文档（主题更集中）。
_KEYWORD_FALLBACK_SQL = """
WITH grams AS (
    SELECT unnest(%s::text[]) AS gram
)
SELECT e.document,
       e.cmetadata,
       m.hit_count
  FROM {embedding_table} e
  JOIN {collection_table} c ON e.collection_id = c.uuid
 CROSS JOIN LATERAL (
       SELECT count(*) AS hit_count
         FROM grams g
        WHERE e.document ILIKE '%%' || g.gram || '%%'
 ) m
 WHERE c.name = %s
   AND m.hit_count > 0
 ORDER BY m.hit_count DESC, length(e.document) ASC
 LIMIT %s
"""


def keyword_search_fallback(query: str, collection_name: str, k: int = 4) -> list[Document]:
    """PostgreSQL 关键词降级检索（Embedding 服务不可用时的兜底路径）

    以字符 2-gram 子串匹配替代 PostgreSQL 全文检索，从而支持中文查询。
    代价是失去 GIN 索引支持、退化为顺序扫描；作为兜底路径可接受。
    如需恢复索引加速，可在 PostgreSQL 侧引入 zhparser / pg_jieba 分词扩展或
    pg_bigm / pg_trgm 三元组索引（见 README Roadmap）。

    Args:
        query: 用户查询文本
        collection_name: PGVector collection 名称
        k: 返回文档数量

    Returns:
        Document 列表，metadata 含 degraded=True、degraded_score（∈[0,1] 的 gram 命中率）
        与 degraded_hits（命中 gram 数）；无可检索内容或出错时返回空列表
    """
    from django.db import connections

    from Django_xm.apps.knowledge.vector_store.pgvector_backend import (
        check_table_exists,
        get_collection_table_name,
        get_embedding_table_name,
        quote_identifier,
    )

    grams = build_retrieval_grams(query)
    if not grams:
        logger.warning("降级关键词检索: 查询未提取到有效检索单元，返回空结果")
        return []

    collection_table = get_collection_table_name()
    embedding_table = get_embedding_table_name()
    # 使用 quote_identifier 包装表名，防止 SQL 注入与保留字冲突
    collection_table_quoted = quote_identifier(collection_table)
    embedding_table_quoted = quote_identifier(embedding_table)

    if not check_table_exists(collection_table) or not check_table_exists(embedding_table):
        logger.warning("降级关键词检索: PGVector 表不存在，返回空结果")
        return []

    sql = _KEYWORD_FALLBACK_SQL.format(
        embedding_table=embedding_table_quoted,
        collection_table=collection_table_quoted,
    )

    try:
        with connections["default"].cursor() as cursor:
            # 表名来自固定内部函数并经 quote_identifier 白名单包装（非用户输入），
            # 查询值（grams/collection_name/k）已全部 %s 参数化
            cursor.execute(sql, [grams, collection_name, k])
            rows = cursor.fetchall()

        results: list[Document] = []
        for document, raw_cmetadata, hit_count in rows:
            cmetadata = raw_cmetadata
            # cmetadata 可能已是 dict，也可能是未反序列化的 JSON 字符串
            # （取决于驱动对 json/jsonb 列的解析行为），两种形态都要兼容。
            # 历史实现因 tsquery 从不命中、循环体从未执行，此处一直未被覆盖。
            if isinstance(cmetadata, str):
                try:
                    cmetadata = json.loads(cmetadata)
                except ValueError:
                    logger.warning("降级关键词检索: cmetadata 反序列化失败，按空元数据处理")
                    cmetadata = {}
            metadata = dict(cmetadata) if isinstance(cmetadata, dict) else {}
            metadata["degraded"] = True
            metadata["degraded_hits"] = int(hit_count)
            metadata["degraded_score"] = round(int(hit_count) / len(grams), 4)
            results.append(Document(page_content=document, metadata=metadata))

        logger.info(
            f"降级关键词检索: query='{query[:50]}', collection={collection_name}, "
            f"grams={len(grams)}, 返回 {len(results)} 个文档"
        )
        return results

    except Exception as e:
        logger.warning(f"降级关键词检索也失败，返回空结果: {e}")
        return []


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
