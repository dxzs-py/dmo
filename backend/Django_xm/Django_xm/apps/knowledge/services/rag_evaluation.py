"""
RAG 评估框架
提供检索质量和生成质量的评估能力
"""

import re

from langchain_core.documents import Document
from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import BaseModel, Field

from Django_xm.apps.core.logging_utils import get_logger

logger = get_logger(__name__)


class RetrievalMetrics(BaseModel):
    precision: float = Field(default=0.0, ge=0, le=1)
    recall: float = Field(default=0.0, ge=0, le=1)
    hit_rate: float = Field(default=0.0, ge=0, le=1)
    mrr: float = Field(default=0.0, ge=0, le=1)


class GenerationMetrics(BaseModel):
    faithfulness: float = Field(default=0.0, ge=0, le=1)
    relevance: float = Field(default=0.0, ge=0, le=1)
    completeness: float = Field(default=0.0, ge=0, le=1)


class RAGEvaluationResult(BaseModel):
    retrieval: RetrievalMetrics = Field(default_factory=RetrievalMetrics)
    generation: GenerationMetrics = Field(default_factory=GenerationMetrics)
    overall_score: float = Field(default=0.0, ge=0, le=1)


EVALUATION_PROMPT = """请评估以下 RAG 系统的生成质量。

用户问题: {query}

检索到的源文档:
{source_docs}

系统回答:
{response}

请从以下三个维度评分（0-1之间的浮点数）:
1. faithfulness（忠实度）: 回答是否严格基于源文档内容，没有编造信息
2. relevance（相关性）: 回答是否与用户问题紧密相关
3. completeness（完整性）: 回答是否完整覆盖了用户问题的各个方面

请严格按以下 JSON 格式返回，不要包含其他内容:
{{"faithfulness": 0.0, "relevance": 0.0, "completeness": 0.0}}"""


# CJK 表意文字区（含扩展 A 与兼容区）：无空格分词语言，词级 split 无法切分
_CJK_RUN_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
_HAS_CJK_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def _tokenize_for_overlap(text: str) -> set[str]:
    """CJK 感知重叠分词（RP-05）

    原实现恒 ``text.lower().split()``：中文查询/文本无空格 → 整句成单个 token，
    词集交集恒空 → keyword_overlap 对 CJK 恒 0（评估重试判据恒真无收敛）。

    规则：
    - 拉丁/数字词：保持整词匹配（与原 split 行为等价，小写化）；
    - CJK 连续段：按二元组（bigram）匹配，单字成段时保留单字；
    - 混合块（如 "文本embedding模型"）：拉丁片段保持整词、CJK 段按二元组。
    """
    tokens: set[str] = set()
    for chunk in text.lower().split():
        if not _HAS_CJK_PATTERN.search(chunk):
            tokens.add(chunk)
            continue
        for cjk_run in _CJK_RUN_PATTERN.findall(chunk):
            if len(cjk_run) == 1:
                tokens.add(cjk_run)
            else:
                tokens.update(cjk_run[i : i + 2] for i in range(len(cjk_run) - 1))
        for piece in _CJK_RUN_PATTERN.split(chunk):
            if piece:
                tokens.add(piece)
    return tokens


def keyword_overlap(query: str, text: str) -> float:
    query_tokens = _tokenize_for_overlap(query)
    text_tokens = _tokenize_for_overlap(text)
    if not query_tokens:
        return 0.0
    overlap = query_tokens & text_tokens
    return len(overlap) / len(query_tokens)


def _compute_mrr(retrieved_ids: list[str], relevant_ids: set) -> float:
    for i, doc_id in enumerate(retrieved_ids):
        if doc_id in relevant_ids:
            return 1.0 / (i + 1)
    return 0.0


class RAGEvaluator:
    def __init__(self, llm: BaseChatModel | None = None):
        self.llm = llm

    def evaluate_retrieval(
        self,
        query: str,
        retrieved_docs: list[Document],
        relevant_doc_ids: list[str],
    ) -> RetrievalMetrics:
        if not retrieved_docs or not relevant_doc_ids:
            return RetrievalMetrics()

        relevant_set = set(relevant_doc_ids)
        retrieved_ids = []
        for doc in retrieved_docs:
            doc_id = doc.metadata.get("source") or doc.metadata.get("id") or doc.metadata.get("filename", "")
            retrieved_ids.append(doc_id)

        # 注意：同一文档可能被切分为多个 chunk 并同时命中，必须按 doc id 去重后再统计，
        # 否则 hits 数量会超过 relevant_set 大小，导致 recall > 1（违反 [0,1] 约束）。
        hit_ids = {doc_id for doc_id in retrieved_ids if doc_id in relevant_set}

        precision = len(hit_ids) / len(retrieved_ids) if retrieved_ids else 0.0
        recall = len(hit_ids) / len(relevant_set) if relevant_set else 0.0
        hit_rate = 1.0 if hit_ids else 0.0
        mrr = _compute_mrr(retrieved_ids, relevant_set)

        return RetrievalMetrics(
            precision=precision,
            recall=recall,
            hit_rate=hit_rate,
            mrr=mrr,
        )

    def evaluate_generation(
        self,
        query: str,
        response: str,
        source_docs: list[Document],
    ) -> GenerationMetrics:
        if self.llm is not None:
            return self._llm_evaluate_generation(query, response, source_docs)
        return self._heuristic_evaluate_generation(query, response, source_docs)

    def _llm_evaluate_generation(
        self,
        query: str,
        response: str,
        source_docs: list[Document],
    ) -> GenerationMetrics:
        source_text = "\n---\n".join(doc.page_content for doc in source_docs) if source_docs else "（无源文档）"

        prompt = EVALUATION_PROMPT.format(
            query=query,
            source_docs=source_text[:3000],
            response=response[:3000],
        )

        try:
            from langchain_core.messages import HumanMessage

            result = self.llm.invoke([HumanMessage(content=prompt)])
            content = result.content.strip()

            import json
            import re

            json_match = re.search(r"\{[^}]+\}", content)
            if json_match:
                scores = json.loads(json_match.group())
                return GenerationMetrics(
                    faithfulness=min(max(float(scores.get("faithfulness", 0.0)), 0.0), 1.0),
                    relevance=min(max(float(scores.get("relevance", 0.0)), 0.0), 1.0),
                    completeness=min(max(float(scores.get("completeness", 0.0)), 0.0), 1.0),
                )
        except Exception as e:
            logger.warning(f"LLM 评估失败，回退到启发式评估: {e}")

        return self._heuristic_evaluate_generation(query, response, source_docs)

    def _heuristic_evaluate_generation(
        self,
        query: str,
        response: str,
        source_docs: list[Document],
    ) -> GenerationMetrics:
        relevance = keyword_overlap(query, response)

        # RP-05：与 keyword_overlap 同源 CJK 感知分词（原 .lower().split() 对
        # 中文恒产出整句单 token，faithfulness/completeness 同型恒 0/恒 1 缺陷）
        source_text = " ".join(doc.page_content for doc in source_docs)
        response_tokens = _tokenize_for_overlap(response)
        source_tokens = _tokenize_for_overlap(source_text)
        if response_tokens:
            faithfulness = len(response_tokens & source_tokens) / len(response_tokens)
        else:
            faithfulness = 0.0

        query_tokens = _tokenize_for_overlap(query)
        if query_tokens:
            response_lower = response.lower()
            covered = sum(1 for t in query_tokens if t in response_lower)
            completeness = covered / len(query_tokens)
        else:
            completeness = 0.0

        return GenerationMetrics(
            faithfulness=min(faithfulness, 1.0),
            relevance=min(relevance, 1.0),
            completeness=min(completeness, 1.0),
        )

    def evaluate(
        self,
        query: str,
        response: str,
        retrieved_docs: list[Document],
        relevant_doc_ids: list[str] | None = None,
    ) -> RAGEvaluationResult:
        retrieval_metrics = RetrievalMetrics()
        if relevant_doc_ids is not None:
            retrieval_metrics = self.evaluate_retrieval(query, retrieved_docs, relevant_doc_ids)

        generation_metrics = self.evaluate_generation(query, response, retrieved_docs)

        overall = (
            retrieval_metrics.precision * 0.2
            + retrieval_metrics.recall * 0.2
            + generation_metrics.faithfulness * 0.25
            + generation_metrics.relevance * 0.2
            + generation_metrics.completeness * 0.15
        )

        return RAGEvaluationResult(
            retrieval=retrieval_metrics,
            generation=generation_metrics,
            overall_score=round(overall, 4),
        )
