"""检索意图：``precise``（精确定位） / ``comprehensive``（全局分析）。

背景：旧 ``create_retriever_tool`` 曾内置 precise/comprehensive 分类（LLM + 3s 超时），
统一到 ``RagService`` 后该能力丢失——「整个附件/知识库写了什么」这类总结性问题
和「某条配置叫什么」走的是同一套 top-k 参数，总结质量明显偏弱。

当前实现（目标态，零额外 LLM 调用）：
- 工具入参 ``intent`` 显式声明优先（LLM 看得懂工具描述就能直接选对）；
- ``intent="auto"`` 时用**确定性关键词规则**兜底（无网络、无等待、可预期），
  命中总结/概述/整篇/有哪些/对比等词 → comprehensive；
- 阈值仅影响召回数与上下文预算（见 ``rag_service``），不改变结果正确性。

映射（``rag_service`` 消费）：
- precise      → 原参数（k 由调用方给定，工具预算 ``tool_max_context_tokens``）
- comprehensive→ 放大召回数 ``comprehensive_k``（MMR 多样性召回）+ 页面级上下文预算
"""

from __future__ import annotations

PRECISE = "precise"
COMPREHENSIVE = "comprehensive"
AUTO = "auto"

# 工具/请求允许的显式取值（auto 之外）
EXPLICIT_INTENTS = (PRECISE, COMPREHENSIVE)
ALL_INTENTS = (AUTO, PRECISE, COMPREHENSIVE)

# 全局分析信号词（覆盖中文总结/盘点/对比类提问）
_COMPREHENSIVE_HINTS: tuple[str, ...] = (
    "总结",
    "概括",
    "概述",
    "综述",
    "归纳",
    "梳理",
    "整体",
    "全局",
    "全面",
    "全文",
    "全篇",
    "通篇",
    "整个",
    "整篇",
    "所有",
    "全部",
    "目录",
    "大纲",
    "结构",
    "框架",
    "主要讲",
    "讲了什么",
    "写了什么",
    "说了什么",
    "有哪些",
    "有什么内容",
    "包含哪些",
    "分几部分",
    "对比",
    "比较",
    "异同",
    "差异",
    "清单",
    "列举",
)


def classify_query_intent(query: str) -> str:
    """规则分类（确定性，零 LLM 调用）。

    Args:
        query: 检索 query（已展开/改写后的文本）

    Returns:
        ``precise`` 或 ``comprehensive``
    """
    text = (query or "").strip()
    if not text:
        return PRECISE
    if any(hint in text for hint in _COMPREHENSIVE_HINTS):
        return COMPREHENSIVE
    return PRECISE


def resolve_intent(intent: str | None, query: str) -> str:
    """把入参意图解析成生效意图。

    - 显式 ``precise`` / ``comprehensive`` → 直接采用（不改写调用方意图）；
    - ``auto`` / None / 非法值 → 按运行时开关 + 规则分类；
    - 分类开关关闭时一律按 precise（保守，保持既有体验）。
    """
    normalized = (intent or AUTO).strip().lower()
    if normalized in EXPLICIT_INTENTS:
        return normalized

    from Django_xm.apps.knowledge.rag_settings import get_rag_runtime_settings

    try:
        enabled = bool(get_rag_runtime_settings()["intent_classification_enabled"])
    except Exception:
        enabled = True
    if not enabled:
        return PRECISE
    return classify_query_intent(query)
