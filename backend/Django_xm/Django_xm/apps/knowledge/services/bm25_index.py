"""应用层 BM25（Phase 3）：纯 Python Okapi BM25 + CJK 2-gram tokenizer。

为什么不在 PG 内做 BM25：
- PostgreSQL 原生没有 BM25；``pg_trgm`` 只是子串匹配 + 命中计数，缺 TF 饱和、
  IDF 与文档长度归一，排序质量弱于 BM25；
- PG 内真正的 BM25 需要 ParadeDB ``pg_search``（换镜像/编译扩展）或
  ``zhparser + ts_rank_cd``（TF-IDF 变体，不是严格 BM25），部署成本高。

本模块用「应用层倒排索引 + Okapi BM25」实现，零第三方依赖、零 PG 扩展：
- tokenizer：CJK 连续片段切 2-gram（等价 Lucene CJKAnalyzer）+ ASCII 整词，保留词频；
- 索引来源：PGVector 表按 collection 拉取全部 document（一次 SQL），进程级 TTL 缓存；
- 适用规模：万级 chunk（当前 4k）构建 < 1s、查询 < 20ms；十万级仍可用；
- 已知限制：索引在进程内（多实例各自构建）、文档更新靠 TTL 失效（默认 300s）。

工业对应：Elasticsearch / Lucene 的 BM25 打分 + 倒排索引；很多检索系统在应用层
（而非数据库内）构建 BM25 索引，本实现属于这一类。
"""

from __future__ import annotations

import json
import math
import re
import time
from collections import Counter

from langchain_core.documents import Document

from Django_xm.apps.core.logging_utils import get_logger

logger = get_logger(__name__)

# CJK 连续片段 / ASCII 词
_TOKEN_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]+|[A-Za-z0-9_]+")

DEFAULT_K1 = 1.5
DEFAULT_B = 0.75
_INDEX_TTL_SECONDS = 300.0

# {collection_name: (built_at, row_count, BM25Index)}
_INDEX_CACHE: dict[str, tuple[float, int, BM25Index]] = {}


def tokenize(text: str) -> list[str]:
    """CJK 2-gram + ASCII 整词，保留重复（BM25 需要词频）。"""
    tokens: list[str] = []
    for token in _TOKEN_RE.findall((text or "").lower()):
        if token.isascii() or len(token) == 1:
            tokens.append(token)
        else:
            tokens.extend(token[i : i + 2] for i in range(len(token) - 1))
    return tokens


class BM25Index:
    """内存倒排索引 + Okapi BM25 打分。"""

    def __init__(self, docs: list[Document], *, k1: float = DEFAULT_K1, b: float = DEFAULT_B):
        self.docs = list(docs)
        self.k1 = k1
        self.b = b
        self.doc_freqs: list[Counter] = []
        self.doc_len: list[int] = []
        document_freq: Counter = Counter()

        for doc in self.docs:
            freqs = Counter(tokenize(doc.page_content))
            self.doc_freqs.append(freqs)
            self.doc_len.append(sum(freqs.values()))
            document_freq.update(freqs.keys())

        self.df = document_freq
        self.n_docs = len(self.docs)
        self.avgdl = (sum(self.doc_len) / self.n_docs) if self.n_docs else 0.0

    def search(self, query: str, k: int = 4) -> list[Document]:
        terms = tokenize(query)
        if not terms or not self.n_docs:
            return []

        scores = [0.0] * self.n_docs
        for term in set(terms):
            df = self.df.get(term, 0)
            if df == 0:
                continue
            idf = math.log(1 + (self.n_docs - df + 0.5) / (df + 0.5))
            for idx, freqs in enumerate(self.doc_freqs):
                tf = freqs.get(term, 0)
                if tf == 0:
                    continue
                norm = self.k1 * (1 - self.b + self.b * self.doc_len[idx] / max(self.avgdl, 1e-9))
                scores[idx] += idf * tf * (self.k1 + 1) / (tf + norm)

        ranked = sorted(range(self.n_docs), key=lambda i: scores[i], reverse=True)[:k]
        return [self.docs[i] for i in ranked if scores[i] > 0]


def _load_documents(collection_name: str) -> list[Document]:
    """从 PGVector 拉取某 collection 的全部 chunk（一次 SQL）。"""
    from django.db import connections

    from Django_xm.apps.knowledge.vector_store.pgvector_backend import (
        get_collection_table_name,
        get_embedding_table_name,
        quote_identifier,
    )

    embedding_table = quote_identifier(get_embedding_table_name())
    collection_table = quote_identifier(get_collection_table_name())
    sql = (
        f"SELECT e.document, e.cmetadata FROM {embedding_table} e "  # noqa: S608
        f"JOIN {collection_table} c ON e.collection_id = c.uuid "
        "WHERE c.name = %s"
    )

    docs: list[Document] = []
    with connections["default"].cursor() as cursor:
        cursor.execute(sql, [collection_name])
        for document, cmetadata in cursor.fetchall():
            metadata = cmetadata
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except ValueError:
                    metadata = {}
            docs.append(
                Document(
                    page_content=document or "",
                    metadata=dict(metadata) if isinstance(metadata, dict) else {},
                )
            )
    return docs


def get_bm25_index(collection_name: str, *, force: bool = False) -> BM25Index | None:
    """获取（或构建）collection 的 BM25 索引；不可用时返回 None。

    失效策略：TTL（300s）+ 行数变化检测（每次一次轻量 count 查询）。
    """
    if not collection_name:
        return None

    now = time.monotonic()
    cached = _INDEX_CACHE.get(collection_name)
    if cached is not None and not force and now - cached[0] < _INDEX_TTL_SECONDS:
        try:
            row_count = _count_rows(collection_name)
        except Exception:
            row_count = cached[1]
        if row_count == cached[1]:
            return cached[2]

    try:
        docs = _load_documents(collection_name)
    except Exception as e:
        logger.warning(f"BM25 索引构建失败（{collection_name}），sparse 将退化: {e}")
        return None

    if not docs:
        return None

    started = time.perf_counter()
    index = BM25Index(docs)
    elapsed_ms = (time.perf_counter() - started) * 1000
    _INDEX_CACHE[collection_name] = (now, len(docs), index)
    logger.info(
        f"BM25 索引已构建: collection={collection_name}, docs={len(docs)}, "
        f"avgdl={index.avgdl:.1f}, build={elapsed_ms:.0f}ms"
    )
    return index


def _count_rows(collection_name: str) -> int:
    from django.db import connections

    from Django_xm.apps.knowledge.vector_store.pgvector_backend import (
        get_collection_table_name,
        get_embedding_table_name,
        quote_identifier,
    )

    embedding_table = quote_identifier(get_embedding_table_name())
    collection_table = quote_identifier(get_collection_table_name())
    sql = (
        f"SELECT count(*) FROM {embedding_table} e "  # noqa: S608
        f"JOIN {collection_table} c ON e.collection_id = c.uuid "
        "WHERE c.name = %s"
    )
    with connections["default"].cursor() as cursor:
        cursor.execute(sql, [collection_name])
        return int(cursor.fetchone()[0])


def invalidate_bm25_cache(collection_name: str | None = None) -> None:
    """清空 BM25 缓存（索引重建/删除后调用；None 表示全部）。"""
    if collection_name is None:
        _INDEX_CACHE.clear()
    else:
        _INDEX_CACHE.pop(collection_name, None)
