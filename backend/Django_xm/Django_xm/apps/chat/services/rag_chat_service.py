"""
RAG 模式聊天服务

从 chat_service.py 拆分出的 RAG 查询相关逻辑：
- RAG 流式查询
- RAG 检索器获取
- RAG 评估闭环（检索质量 + 生成质量评估，低分触发重检索）
"""
import logging
from typing import Dict, Any, List, Optional

from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
from langchain_core.documents import Document

from ..utils import convert_chat_history

logger = logging.getLogger(__name__)

# RAG 评估阈值配置
RETRIEVAL_QUALITY_THRESHOLD: float = 0.5
GENERATION_QUALITY_THRESHOLD: float = 0.6
MAX_RETRY_COUNT: int = 1


class RAGChatService:
    """RAG 模式聊天服务"""

    def __init__(self, user_id: Optional[int] = None, chat_service=None):
        from Django_xm.apps.knowledge.services.rag_evaluation import RAGEvaluator

        self.user_id = user_id
        self._chat_service = chat_service
        self._evaluator = RAGEvaluator()

    def _get_user_index_name(self, index_name: str) -> str:
        return f"user_{self.user_id}_{index_name}" if self.user_id else index_name

    def get_rag_retriever(self, index_name: str, k: int = 4, search_type: str = "similarity", retrieval_mode: str = "precise"):
        from Django_xm.apps.knowledge.services.cross_app import get_index_manager
        from Django_xm.apps.knowledge.services.embedding_service import get_embeddings
        from Django_xm.apps.knowledge.services.retrieval_service import create_retriever, create_multi_query_retriever
        from Django_xm.apps.ai_engine.config import settings as app_cfg

        if not self.user_id:
            logger.warning(f"get_rag_retriever: user_id 为空，跳过")
            return None
        try:
            user_index_name = self._get_user_index_name(index_name)
            logger.info(f"get_rag_retriever: index_name={index_name}, user_index_name={user_index_name}, user_id={self.user_id}")
            manager = get_index_manager()
            if not manager.index_exists(user_index_name):
                logger.warning(f"索引不存在: {user_index_name}")
                return None
            metadata = manager._load_metadata(user_index_name)
            num_documents = metadata.get('num_documents', 0) if metadata else 0
            if num_documents == 0:
                logger.warning(f"索引中无文档: {user_index_name}")
                return None
            embeddings = get_embeddings()
            vector_store = manager.load_index(user_index_name, embeddings)

            if retrieval_mode == "comprehensive":
                comp_k = app_cfg.retriever_comprehensive_k
                comp_fetch_k = max(comp_k * 3, 30)
                retriever = create_retriever(
                    vector_store, k=comp_k, search_type="mmr", fetch_k=comp_fetch_k,
                )

                if app_cfg.retriever_use_multi_query:
                    try:
                        from Django_xm.apps.ai_engine.services.llm_factory import get_helper_model, get_chat_model
                        mq_llm = get_helper_model() or get_chat_model(streaming=False)
                        retriever = create_multi_query_retriever(retriever, llm=mq_llm, include_original=True)
                        logger.info(f"comprehensive 检索器已叠加 MultiQuery (k={comp_k})")
                    except Exception as e:
                        logger.warning(f"MultiQuery 叠加失败，使用基础 MMR 检索器: {e}")

                logger.info(f"创建 comprehensive 检索器: k={comp_k}, search_type=mmr, fetch_k={comp_fetch_k}")
                return retriever

            retriever = create_retriever(vector_store, k=k, search_type=search_type)
            return retriever
        except Exception as e:
            logger.error(f"获取 RAG 检索器失败: {e}", exc_info=True)
            return None

    def _compute_retrieval_quality(self, query: str, docs: List[Document]) -> float:
        """计算检索质量：基于查询与检索文档的关键词重叠度均值"""
        from Django_xm.apps.knowledge.services.rag_evaluation import _keyword_overlap

        if not docs:
            return 0.0
        overlaps = [_keyword_overlap(query, doc.page_content) for doc in docs]
        return sum(overlaps) / len(overlaps)

    def _compute_generation_quality(self, evaluation_result) -> float:
        """计算生成质量：faithfulness、relevance、completeness 的加权平均"""
        gen = evaluation_result.generation
        return gen.faithfulness * 0.4 + gen.relevance * 0.35 + gen.completeness * 0.25

    def _evaluate_rag_result(
        self,
        query: str,
        answer: str,
        retrieved_docs: List[Document],
    ) -> Dict[str, Any]:
        """
        评估 RAG 结果质量

        Returns:
            包含 retrieval_quality、generation_quality、evaluation_result 的字典
        """
        # 评估检索质量（关键词重叠度）
        retrieval_quality = self._compute_retrieval_quality(query, retrieved_docs)

        # 评估生成质量（忠实度、相关性、完整性）
        evaluation_result = self._evaluator.evaluate(
            query=query,
            response=answer,
            retrieved_docs=retrieved_docs,
        )
        generation_quality = self._compute_generation_quality(evaluation_result)

        logger.info(
            f"RAG 评估结果: 检索质量={retrieval_quality:.4f}, "
            f"生成质量={generation_quality:.4f}, "
            f"综合评分={evaluation_result.overall_score:.4f}"
        )
        logger.info(
            f"  生成详情: faithfulness={evaluation_result.generation.faithfulness:.4f}, "
            f"relevance={evaluation_result.generation.relevance:.4f}, "
            f"completeness={evaluation_result.generation.completeness:.4f}"
        )

        return {
            "retrieval_quality": retrieval_quality,
            "generation_quality": generation_quality,
            "evaluation_result": evaluation_result,
        }

    def process_rag_request(self, data: Dict[str, Any]) -> Dict[str, Any]:
        from Django_xm.apps.knowledge.services.strict_rag_chain import query_strict_rag

        selected_kb = data.get('selected_knowledge_base')
        if not selected_kb:
            return None

        retriever = self.get_rag_retriever(selected_kb)
        if not retriever:
            return None

        query = data['message']
        logger.info(f"使用知识库 {selected_kb} 进行 RAG 查询")

        # Strict Chain 模式：强制检索 -> 注入上下文 -> 生成（防幻觉）
        result = query_strict_rag(retriever, query, k=4, collection_name=selected_kb)
        answer = result.get('answer', '')

        # 获取检索文档用于评估
        try:
            retrieved_docs = retriever.invoke(query)
        except Exception as e:
            logger.warning(f"获取检索文档失败: {e}")
            retrieved_docs = []

        # 评估 RAG 结果
        eval_info = self._evaluate_rag_result(query, answer, retrieved_docs)
        retrieval_quality = eval_info["retrieval_quality"]
        generation_quality = eval_info["generation_quality"]

        # 低分触发重检索（最多重试 1 次）
        retry_count = 0
        final_answer = answer
        final_eval = eval_info

        if (retrieval_quality < RETRIEVAL_QUALITY_THRESHOLD
                or generation_quality < GENERATION_QUALITY_THRESHOLD):
            retry_count += 1
            logger.info(
                f"RAG 评估低分，触发重检索 (第{retry_count}次): "
                f"检索质量={retrieval_quality:.4f}, 生成质量={generation_quality:.4f}"
            )

            # 根据低分类型调整检索策略
            new_k = 8 if retrieval_quality < RETRIEVAL_QUALITY_THRESHOLD else 4
            new_search_type = "mmr" if generation_quality < GENERATION_QUALITY_THRESHOLD else "similarity"

            # 创建新检索器并重新查询
            new_retriever = self.get_rag_retriever(
                selected_kb, k=new_k, search_type=new_search_type
            )
            if new_retriever:
                new_result = query_strict_rag(new_retriever, query, k=new_k, collection_name=selected_kb)
                new_answer = new_result.get('answer', '')

                # 重新评估
                try:
                    new_docs = new_retriever.invoke(query)
                except Exception:
                    new_docs = []

                new_eval = self._evaluate_rag_result(query, new_answer, new_docs)

                # 使用重检索后的结果
                final_answer = new_answer
                final_eval = new_eval
                result = new_result

                logger.info(
                    f"重检索评估结果: 检索质量={new_eval['retrieval_quality']:.4f}, "
                    f"生成质量={new_eval['generation_quality']:.4f}"
                )

        # 记录最终评估结果到日志和 analytics
        logger.info(
            f"RAG 查询最终评估: 检索质量={final_eval['retrieval_quality']:.4f}, "
            f"生成质量={final_eval['generation_quality']:.4f}, "
            f"综合评分={final_eval['evaluation_result'].overall_score:.4f}, "
            f"重试次数={retry_count}"
        )

        return {
            'message': final_answer,
            'mode': 'rag',
            'tools_used': ['rag_retrieve'],
            'success': True,
            'sources': result.get('sources', []),
            'evaluation': {
                'retrieval_quality': round(final_eval['retrieval_quality'], 4),
                'generation_quality': round(final_eval['generation_quality'], 4),
                'overall_score': final_eval['evaluation_result'].overall_score,
                'retry_count': retry_count,
            },
        }

    async def process_rag_stream(self, data: Dict[str, Any], retriever, usage_tracker, token_detail_tracker):
        from Django_xm.apps.ai_engine.services.token_counter import TokenUsageCallbackHandler
        from Django_xm.apps.knowledge.services.strict_rag_chain import astream_strict_rag

        # Strict Chain 模式：强制检索 -> 注入上下文 -> 生成（防幻觉）
        logger.info("使用 Strict RAG Chain 模式进行流式查询")
        try:
            full_response = ""
            sources = []

            with TokenUsageCallbackHandler() as cb:
                async for event in astream_strict_rag(
                    retriever, data['message'], k=4,
                    collection_name=data.get('selected_knowledge_base', '')
                ):
                    if event["type"] == "chunk":
                        full_response += event["content"]
                        yield {"type": "chunk", "content": event["content"]}
                    elif event["type"] == "sources":
                        sources = event["data"]
                    elif event["type"] == "degradation":
                        yield {"type": "degradation", "message": event["message"]}
                    elif event["type"] == "error":
                        yield {"type": "error", "message": event["message"]}
                        return

            from .stream_helpers import update_usage_and_tokens
            update_usage_and_tokens(cb, usage_tracker, token_detail_tracker)

            if sources:
                yield {'type': 'sources', 'data': sources}

            return

        except Exception as e:
            logger.error(f"Strict RAG Chain 流式查询失败: {e}", exc_info=True)
            from Django_xm.apps.ai_engine.services.exceptions import classify_exception
            classified = classify_exception(e)
            yield {"type": "error", "message": classified.user_message}
            return
