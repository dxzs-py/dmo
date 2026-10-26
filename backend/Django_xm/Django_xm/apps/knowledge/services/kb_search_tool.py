"""知识库检索工具（C2）：唯一 ``knowledge_base_search``。

设计：
- 输入 ``query`` + 可选 ``kb_ids``；``kb_ids`` 只能是创建工具时选中的子集（防越权）；
- 内部调用 ``RagService.retrieve_scopes``（逐 KB 检索 + 合并 + 统一去重/预算）；
- 返回 ``(text, artifact)``：text 供 LLM，artifact 带 ``sources``/``metrics``；
- 工具名固定 ``knowledge_base_search``，不再按 KB 生成 per-KB 工具。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.retrievers import BaseRetriever
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from Django_xm.apps.core.logging_utils import get_logger
from Django_xm.apps.knowledge.services.context_format import DATA_BOUNDARY_NOTICE
from Django_xm.apps.knowledge.services.rag_service import RagScope, RagService

logger = get_logger(__name__)

KB_RESULT_INSTRUCTION = (
    "以下为知识库中检索到的真实内容，请严格基于这些内容回答；"
    "引用时在对应表述末尾标注来源编号（如 [1]）。\n"
    f"{DATA_BOUNDARY_NOTICE}"
)


@dataclass
class KbToolBundle:
    """单个知识库的工具上下文（预构建，避免每次调用重建）。"""

    kb_id: str
    name: str
    description: str
    scope: RagScope
    retriever: BaseRetriever
    vector_store: Any | None = None


class KnowledgeBaseSearchInput(BaseModel):
    query: str = Field(description="检索查询（自然语言）")
    kb_ids: list[str] | None = Field(
        default=None,
        description="可选：只在指定的知识库中检索；必须是当前已选知识库的子集，不传则检索全部已选库",
    )
    intent: Literal["auto", "precise", "comprehensive"] = Field(
        default="auto",
        description=(
            "检索意图：precise=定位具体事实/配置/步骤；"
            "comprehensive=整体性/总结性提问（如“这个库主要讲了什么”“有哪些主题”“全文结构”），"
            "会放大召回数量以覆盖更多原文；auto=按关键词自动判断"
        ),
    )


def _format_result(result) -> str:
    if not result.docs:
        return "知识库中未找到与您问题相关的信息。"
    parts = [KB_RESULT_INSTRUCTION]
    for doc in result.docs:
        metadata = doc.metadata or {}
        source = metadata.get("original_name") or metadata.get("source") or "未知"
        parts.append(f"[来源: {source}]\n{doc.page_content}")
    return "\n\n".join(parts)


class KnowledgeBaseSearchTool(BaseTool):
    """跨知识库检索工具（唯一入口）。"""

    name: str = "knowledge_base_search"
    description: str = "搜索用户选中的知识库中的相关信息。"
    args_schema: type[BaseModel] = KnowledgeBaseSearchInput
    response_format: str = "content_and_artifact"

    bundles: dict[str, KbToolBundle] = Field(default_factory=dict)
    k: int = 5

    def _run(
        self,
        query: str,
        kb_ids: list[str] | None = None,
        intent: str = "auto",
    ):
        selected = [kb for kb in (kb_ids or list(self.bundles)) if kb in self.bundles]
        if not selected:
            return "未找到可检索的知识库（可能未选择知识库或索引不可用）。", {"sources": []}

        scopes = [
            (self.bundles[kb].scope, self.bundles[kb].retriever, self.bundles[kb].vector_store)
            for kb in selected
        ]
        result = RagService().retrieve_scopes(
            query, bundles=scopes, k=self.k, metrics_kind="agent_kb", intent=intent
        )
        text = _format_result(result)
        from Django_xm.apps.knowledge.rag_settings import get_rag_runtime_settings

        include_sources = get_rag_runtime_settings()["tool_include_sources"]
        artifact = {
            "sources": result.sources if include_sources else [],
            "metrics": result.metrics,
        }
        logger.info(
            f"knowledge_base_search: kbs={selected}, intent={result.metrics.get('intent')}, "
            f"hits={len(result.docs)}, cached={result.metrics.get('cached')}"
        )
        return text, artifact

    async def _arun(
        self,
        query: str,
        kb_ids: list[str] | None = None,
        intent: str = "auto",
    ):
        return await asyncio.to_thread(self._run, query, kb_ids, intent)


def build_knowledge_base_search_tool(
    *, bundles: dict[str, KbToolBundle], k: int = 5
) -> KnowledgeBaseSearchTool:
    """构建唯一知识库检索工具（描述中包含可用 KB 列表）。"""
    lines = [
        "搜索用户选中的知识库中的相关信息；可一次跨多个知识库检索。",
        "intent：整体/总结/概述/有哪些/对比类提问（如“这个库主要讲了什么”）传 comprehensive；"
        "定位具体事实/配置/步骤传 precise；不确定就不传（auto 按关键词判定）。",
        "可用知识库：",
    ]
    for kb_id, bundle in bundles.items():
        suffix = f"（{bundle.description}）" if bundle.description else ""
        lines.append(f"- {kb_id}: {bundle.name}{suffix}")
    return KnowledgeBaseSearchTool(bundles=bundles, k=k, description="\n".join(lines))
