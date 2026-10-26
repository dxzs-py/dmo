"""多路查询扩展（原 query + HyDE + MultiQuery，Phase 2）。

现状（改造前）：strict 链路只有 HyDE，且是**替换**原 query；MultiQuery 只存在于
comprehensive 检索器。本模块把两者变成 **并行新增的召回路**：

    [原 query] + [HyDE 假设文档] + [MQ 3 个改写]

约定：
- 原 query 恒为第 1 路（改写失败也不会失去基本召回）；
- HyDE / MQ 并行生成（sync 线程池 / async gather），互不阻塞；
- 任一路失败/超时只少一路，绝不影响其他；超时用 ``rag_query_expansion_timeout``；
- 输出做去空、去重、截断（400 字符），保持顺序稳定。
"""

from __future__ import annotations

import asyncio
import re
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError

from Django_xm.apps.core.logging_utils import get_logger
from Django_xm.apps.knowledge.config import settings

logger = get_logger(__name__)

MULTI_QUERY_PROMPT = (
    "你是检索查询改写助手。请围绕用户问题生成 {count} 个不同视角的检索问题，"
    "分别覆盖：同义表达、上位概念、关键细节。\n"
    "要求：每行一个问题；不要编号；不要解释；不要输出 JSON。\n\n"
    "用户问题：{question}\n\n替代问题："
)

_LINE_PREFIX = re.compile(r"^\s*(?:[-*•]|\d+[.、)]|[①②③④⑤⑥⑦⑧⑨⑩])\s*")
_MAX_QUERY_CHARS = 400


def _content_text(response) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            item.get("text", "") if isinstance(item, dict) else str(item) for item in content
        )
    return str(content or "")


def _clean_lines(raw: str, limit: int) -> list[str]:
    items: list[str] = []
    for line in (raw or "").splitlines():
        text = _LINE_PREFIX.sub("", line).strip()
        if len(text) < 2:
            continue
        text = text[:_MAX_QUERY_CHARS]
        if text not in items:
            items.append(text)
        if len(items) >= limit:
            break
    return items


def _dedupe_keep_order(queries: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for query in queries:
        text = (query or "").strip()[:_MAX_QUERY_CHARS]
        if not text or text in seen:
            continue
        seen.add(text)
        ordered.append(text)
    return ordered


def _hyde_sync(query: str, llm) -> str:
    from Django_xm.apps.context_manager.services.retrieval_augmenter import RetrievalAugmenter

    return RetrievalAugmenter.hyde_rewrite_sync(query, llm)


async def _hyde_async(query: str, llm) -> str:
    from Django_xm.apps.context_manager.services.retrieval_augmenter import RetrievalAugmenter

    return await RetrievalAugmenter.hyde_rewrite(query, llm)


def _multi_query_sync(query: str, llm, count: int) -> list[str]:
    prompt = MULTI_QUERY_PROMPT.format(count=count, question=query)
    response = llm.invoke([{"role": "user", "content": prompt}])
    return _clean_lines(_content_text(response), count)


async def _multi_query_async(query: str, llm, count: int) -> list[str]:
    prompt = MULTI_QUERY_PROMPT.format(count=count, question=query)
    response = await llm.ainvoke([{"role": "user", "content": prompt}])
    return _clean_lines(_content_text(response), count)


def build_retrieval_queries_sync(
    query: str,
    llm=None,
    *,
    use_hyde: bool = True,
    use_multi_query: bool | None = None,
) -> list[str]:
    """同步构建多路检索查询；llm 为 None 时只返回原 query。"""
    base = (query or "").strip()[:_MAX_QUERY_CHARS]
    if not base:
        return []
    queries = [base]
    if llm is None:
        return queries

    from Django_xm.apps.knowledge.rag_settings import get_rag_runtime_settings

    runtime = get_rag_runtime_settings()
    use_mq = runtime["multi_query_enabled"] if use_multi_query is None else use_multi_query
    count = runtime["multi_query_count"]
    timeout = settings.rag_query_expansion_timeout

    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="query-expansion")
    try:
        futures: list[tuple[str, Future]] = []
        if use_hyde:
            futures.append(("hyde", executor.submit(_hyde_sync, base, llm)))
        if use_mq:
            futures.append(("mq", executor.submit(_multi_query_sync, base, llm, count)))

        for kind, future in futures:
            try:
                result = future.result(timeout=timeout)
            except FuturesTimeoutError:
                logger.warning(f"查询扩展超时（{kind}，>{timeout}s），跳过该路")
                continue
            except Exception as e:
                logger.warning(f"查询扩展失败（{kind}），跳过该路: {e}")
                continue
            if kind == "hyde":
                if result:
                    queries.append(result)
            else:
                queries.extend(result or [])
    finally:
        # 不等待慢任务：与 strict_rag_chain._blocking_step_with_heartbeat 同策略
        executor.shutdown(wait=False, cancel_futures=True)

    routes = _dedupe_keep_order(queries)
    logger.info(f"多路查询: routes={len(routes)} (hyde={use_hyde}, mq={use_mq})")
    return routes


async def build_retrieval_queries_async(
    query: str,
    llm=None,
    *,
    use_hyde: bool = True,
    use_multi_query: bool | None = None,
) -> list[str]:
    """异步构建多路检索查询；llm 为 None 时只返回原 query。"""
    base = (query or "").strip()[:_MAX_QUERY_CHARS]
    if not base:
        return []
    queries = [base]
    if llm is None:
        return queries

    from Django_xm.apps.knowledge.rag_settings import get_rag_runtime_settings

    runtime = get_rag_runtime_settings()
    use_mq = runtime["multi_query_enabled"] if use_multi_query is None else use_multi_query
    count = runtime["multi_query_count"]
    timeout = settings.rag_query_expansion_timeout

    tasks: dict[str, asyncio.Task] = {}
    if use_hyde:
        tasks["hyde"] = asyncio.create_task(_hyde_async(base, llm))
    if use_mq:
        tasks["mq"] = asyncio.create_task(_multi_query_async(base, llm, count))
    if not tasks:
        return queries

    try:
        results = await asyncio.wait_for(
            asyncio.gather(*tasks.values(), return_exceptions=True), timeout=timeout
        )
    except TimeoutError:
        for task in tasks.values():
            task.cancel()
        logger.warning(f"查询扩展超时（>{timeout}s），仅使用已完成的路")
        return queries

    for kind, result in zip(tasks.keys(), results, strict=False):
        if isinstance(result, BaseException):
            logger.warning(f"查询扩展失败（{kind}），跳过该路: {result}")
            continue
        if kind == "hyde":
            if result:
                queries.append(result)
        else:
            queries.extend(result or [])

    routes = _dedupe_keep_order(queries)
    logger.info(f"多路查询: routes={len(routes)} (hyde={use_hyde}, mq={use_mq})")
    return routes
