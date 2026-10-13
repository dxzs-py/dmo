"""Agent 检索工具模块（B-3 拆分：自 retrieval_service.py 下沉独立）

职责单一：把"就绪检索器"暴露为 Agent 可用工具层——
- SyncSafeRetrieverTool / create_retriever_tool：知识库检索工具（precise/comprehensive
  双模式 + auto 意图分类）
- QueryIntentClassifier：查询意图分类（precise/comprehensive，LLM 驱动 + TTL 缓存）

与检索器工厂（retrieval_service）松耦合：检索执行统一走 UnifiedRagPipeline
的结构化入口 search_with_retriever（PipelineSearchResult.status 判定，Task 4/B2：
禁止正文子串判空、错误不再冒充知识内容）。
"""

import asyncio
import json
import re
import time
from collections import OrderedDict
from typing import TYPE_CHECKING, Any, ClassVar

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.retrievers import BaseRetriever
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from Django_xm.apps.core.logging_utils import get_logger
from Django_xm.common.messages import content_to_str

if TYPE_CHECKING:
    from Django_xm.apps.knowledge.services.rag_retrieval import PipelineSearchResult

logger = get_logger(__name__)


class _RetrieverToolInput(BaseModel):
    query: str = Field(description="搜索查询")
    retrieval_mode: str | None = Field(default=None, description="检索模式: auto/precise/comprehensive")


# 模块级常量：避免 Pydantic BaseModel 将 _ 前缀属性转为 ModelPrivateAttr
# 导致 @classmethod 中 cls._XXX 返回 ModelPrivateAttr 对象而非实际值
KB_RESULT_INSTRUCTION = (
    "【知识库检索结果 - 以下为真实检索到的知识库内容，你已成功访问知识库】\n"
    "【回答规则 - 你必须遵守：】\n"
    "1. 以下「检索内容」部分就是知识库的真实数据，绝不允许声称无法访问知识库或知识库为空\n"
    "2. 你必须基于「检索内容」回答用户问题，忠实于检索内容，绝不允许编造或推断\n"
    "3. 提取与用户问题相关的核心要点，每个要点必须保留其领域上下文\n"
    "4. 如果用户要求总结知识库内容，应给出完整的主题概览\n"
    "5. 绝对不要输出：意图分类JSON、检索查询语句、替代问题、中间处理过程、元数据\n"
    "6. 如果检索内容确实与问题无关，直接告知用户\n"
    "7. 回答末尾标注「如需了解特定主题的详细信息，请告诉我」\n"
    "---检索内容开始---\n"
)


class SyncSafeRetrieverTool(BaseTool):
    name: str = "knowledge_base"
    description: str = "搜索知识库中的相关信息。当需要回答关于文档内容的问题时使用此工具。输入应该是一个搜索查询。"
    args_schema: type[BaseModel] = _RetrieverToolInput
    retriever: BaseRetriever | None = None
    comprehensive_retriever: BaseRetriever | None = None
    retrieval_mode: str = "auto"
    llm: Any | None = None
    kb_name: str = ""
    kb_description: str = ""

    def _build_kb_context(self) -> str:
        """构建知识库上下文前缀，注入到工具返回结果中"""
        parts = []
        if self.kb_name:
            parts.append(f"知识库名称: {self.kb_name}")
        if self.kb_description:
            parts.append(f"知识库描述: {self.kb_description}")
        if parts:
            return "【知识库信息】\n" + "\n".join(parts) + "\n\n"
        return ""

    # Task 4/B2：三类 pipeline 状态的统一返回形态——
    # ok：真实检索内容，加 KB_RESULT_INSTRUCTION 前缀注入；
    # empty：受控空结果文案原样返回（不冒充知识内容，不再全库重嵌入降级）；
    # error：明确错误信息返回给 LLM（不加"真实检索内容"前缀）。
    def _search_message(self, pipeline_result: "PipelineSearchResult") -> str:
        """将 PipelineSearchResult 转为工具对 LLM 的返回文本（状态驱动，禁止解析 text）"""
        if pipeline_result.status == "ok":
            return self._build_kb_context() + KB_RESULT_INSTRUCTION + pipeline_result.text
        if pipeline_result.status == "error":
            logger.warning(f"知识库检索工具返回错误: error={pipeline_result.error}")
            return f"知识库检索失败: {pipeline_result.error or '未知错误'}，本次无法基于知识库回答。"
        # empty：pipeline 的受控空结果文案
        return pipeline_result.text

    def _run(self, query: str, retrieval_mode: str | None = None) -> str:
        mode = retrieval_mode or self.retrieval_mode

        if mode == "auto":
            mode = self._classify_intent(query)

        if mode == "comprehensive":
            return self._run_comprehensive(query)
        return self._run_precise(query)

    async def _arun(self, query: str, retrieval_mode: str | None = None) -> str:
        mode = retrieval_mode or self.retrieval_mode

        if mode == "auto":
            mode = await self._classify_intent_async(query)

        if mode == "comprehensive":
            return await self._arun_comprehensive(query)
        return await asyncio.to_thread(self._run_precise, query)

    def _classify_intent(self, query: str) -> str:
        try:
            classifier = QueryIntentClassifier(llm=self.llm)
            return classifier.classify_sync(query)
        except Exception as e:
            logger.warning(f"意图分类异常，回退到 precise: {e}")
            return "precise"

    async def _classify_intent_async(self, query: str) -> str:
        try:
            classifier = QueryIntentClassifier(llm=self.llm)
            return await classifier.classify(query)
        except Exception as e:
            logger.warning(f"意图分类异常，回退到 precise: {e}")
            return "precise"

    def _run_precise(self, query: str) -> str:
        from Django_xm.apps.knowledge.services.rag_retrieval import UnifiedRagPipeline

        pipeline = UnifiedRagPipeline(scenario="knowledge_base")
        result = pipeline.search_with_retriever(
            retriever=self.retriever,
            query=query,
            vector_store=None,  # PGVector can't do InMemoryVectorStore keyword search
            documents=None,
            total_token_count=0,
        )
        logger.info(f"precise 检索 Pipeline: query='{query[:50]}...', status={result.status}")
        return self._search_message(result)

    def _run_comprehensive(self, query: str) -> str:
        from Django_xm.apps.knowledge.services.rag_retrieval import UnifiedRagPipeline

        retriever = self.comprehensive_retriever or self.retriever
        pipeline = UnifiedRagPipeline(scenario="knowledge_base")
        result = pipeline.search_with_retriever(
            retriever=retriever,
            query=query,
            vector_store=None,
            documents=None,
            total_token_count=0,
        )
        logger.info(f"comprehensive 检索 Pipeline: query='{query[:50]}...', status={result.status}")
        return self._search_message(result)

    async def _arun_comprehensive(self, query: str) -> str:
        from Django_xm.apps.knowledge.services.rag_retrieval import UnifiedRagPipeline

        retriever = self.comprehensive_retriever or self.retriever
        pipeline = UnifiedRagPipeline(scenario="knowledge_base")
        # RP-03 同构：检索为重 IO（embed_query 远程调用 + 向量库查询），经
        # asyncio.to_thread 离开事件循环（与 _arun 的 precise 分支同构）
        result = await asyncio.to_thread(
            pipeline.search_with_retriever,
            retriever=retriever,
            query=query,
            vector_store=None,
            documents=None,
            total_token_count=0,
        )
        logger.info(f"comprehensive 异步检索 Pipeline: query='{query[:50]}...', status={result.status}")
        return self._search_message(result)


def create_retriever_tool(
    retriever: BaseRetriever,
    name: str = "knowledge_base",
    description: str | None = None,
    retrieval_mode: str = "auto",
    comprehensive_retriever: BaseRetriever | None = None,
    llm: Any | None = None,
    kb_name: str = "",
    kb_description: str = "",
) -> BaseTool:
    if description is None:
        description = (
            f"搜索 {name} 知识库中的相关信息。当需要回答关于文档内容的问题时使用此工具。输入应该是一个搜索查询。"
        )

    logger.info(f"创建检索器工具: {name}, retrieval_mode={retrieval_mode}")

    try:
        tool = SyncSafeRetrieverTool(
            retriever=retriever,
            name=name,
            description=description,
            retrieval_mode=retrieval_mode,
            comprehensive_retriever=comprehensive_retriever,
            llm=llm,
            kb_name=kb_name,
            kb_description=kb_description,
        )
        logger.info(f"检索器工具创建成功（SyncSafe 模式, retrieval_mode={retrieval_mode}）")
        return tool
    except Exception:
        logger.exception("创建检索器工具失败")
        raise


_INTENT_CLASSIFICATION_PROMPT = """分析用户查询的意图类型：

1. precise - 精准查找：需要定位特定文档/数据/事实，答案通常在1-2个文档中。特征：包含具体名称、编号、日期等
2. comprehensive - 全局分析：需要广泛收集信息，答案分布在多个文档中。特征：包含"所有"、"全部"、"分析"、"总结"、\
"对比"、"概述"等词

用户查询: {query}

仅返回 JSON: {{"intent": "precise", "confidence": 0.9}} 或 {{"intent": "comprehensive", "confidence": 0.9}}"""


class QueryIntentClassifier:
    # B-3附：缓存有界化（原类级无界 dict，TTL 60s 只挡命中不回收条目，长驻
    # 进程缓慢累积且每条含完整 query 文本）。OrderedDict LRU 逐出 + TTL 判定：
    # 容量上限 _CACHE_MAXSIZE，写入时移动到尾部、超限逐出最旧条目。
    _cache: ClassVar[OrderedDict] = OrderedDict()
    _cache_ttl: int = 60
    _cache_maxsize: int = 256

    def __init__(self, llm: BaseChatModel | None = None):
        self._llm = llm

    def _get_llm(self) -> BaseChatModel | None:
        if self._llm is not None:
            return self._llm
        try:
            # A-5：统一走 knowledge.config 门面（原直连 ai_engine.llm_factory）
            from Django_xm.apps.knowledge.config import get_chat_model, get_helper_model

            llm = get_helper_model()
            if llm is not None:
                return llm
            return get_chat_model(streaming=False)
        except Exception:
            return None

    def _parse_intent(self, response_text: Any) -> str:
        response_text = response_text if isinstance(response_text, str) else str(response_text)
        match = re.search(r"\{[^}]+\}", response_text)
        if match:
            try:
                data = json.loads(match.group())
                intent = data.get("intent", "precise")
                if intent in ("precise", "comprehensive"):
                    return intent
                # E5-11：intent 越界与 JSON 解析失败同属降级，需留痕
                logger.warning(f"意图分类: LLM 返回 intent 越界({intent!r})，回退到 precise")
            except (json.JSONDecodeError, KeyError) as e:
                # E5-11：解析失败静默默认 precise 曾不可观测，补日志（语义不变）
                logger.warning(f"意图分类: LLM 输出解析失败，回退到 precise: {e}（raw={response_text[:80]!r}）")
        return "precise"

    def _check_cache(self, query: str) -> str | None:
        now = time.time()
        cached = self._cache.get(query)
        if cached and (now - cached["ts"]) < self._cache_ttl:
            # LRU：命中移到尾部，保证逐出的是最久未使用条目
            self._cache.move_to_end(query)
            return cached["intent"]
        return None

    def _set_cache(self, query: str, intent: str) -> None:
        # B-3附：LRU 有界写入——命中/写入均移到尾部，超限逐出最旧条目
        self._cache[query] = {"intent": intent, "ts": time.time()}
        self._cache.move_to_end(query)
        while len(self._cache) > self._cache_maxsize:
            self._cache.popitem(last=False)

    def classify_sync(self, query: str) -> str:
        from Django_xm.apps.knowledge.config import settings as app_cfg

        if not app_cfg.retriever_intent_classification_enabled:
            return "precise"

        cached = self._check_cache(query)
        if cached is not None:
            logger.debug(f"意图分类缓存命中: query='{query[:30]}...' -> {cached}")
            return cached

        llm = self._get_llm()
        if llm is None:
            logger.warning("意图分类: LLM 不可用，回退到 precise")
            return "precise"

        try:
            from langchain_core.messages import HumanMessage

            prompt = _INTENT_CLASSIFICATION_PROMPT.format(query=query)
            response = llm.invoke(
                [HumanMessage(content=prompt)],
                config={"timeout": 3, "callbacks": [], "tags": ["nostream"]},  # type: ignore[arg-type]  # RunnableConfig accepts arbitrary keys
            )
            intent = self._parse_intent(content_to_str(response.content) if hasattr(response, "content") else str(response))
            self._set_cache(query, intent)
            logger.info(f"意图分类: query='{query[:50]}...' -> {intent}")
            return intent
        except Exception as e:
            logger.warning(f"意图分类失败，回退到 precise: {e}")
            return "precise"

    async def classify(self, query: str) -> str:
        from Django_xm.apps.knowledge.config import settings as app_cfg

        if not app_cfg.retriever_intent_classification_enabled:
            return "precise"

        cached = self._check_cache(query)
        if cached is not None:
            logger.debug(f"意图分类缓存命中: query='{query[:30]}...' -> {cached}")
            return cached

        llm = self._get_llm()
        if llm is None:
            logger.warning("意图分类: LLM 不可用，回退到 precise")
            return "precise"

        try:
            import asyncio

            from langchain_core.messages import HumanMessage

            prompt = _INTENT_CLASSIFICATION_PROMPT.format(query=query)
            response = await asyncio.wait_for(
                llm.ainvoke([HumanMessage(content=prompt)], config={"callbacks": [], "tags": ["nostream"]}),  # type: ignore[arg-type]  # RunnableConfig accepts arbitrary keys
                timeout=3.0,
            )
            intent = self._parse_intent(content_to_str(response.content) if hasattr(response, "content") else str(response))
            self._set_cache(query, intent)
            logger.info(f"意图分类: query='{query[:50]}...' -> {intent}")
            return intent
        except TimeoutError:
            logger.warning("意图分类超时(3s)，回退到 precise")
            return "precise"
        except Exception as e:
            logger.warning(f"意图分类失败，回退到 precise: {e}")
            return "precise"
