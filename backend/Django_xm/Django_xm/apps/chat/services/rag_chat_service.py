"""
RAG 模式聊天服务

从 chat_service.py 拆分出的 RAG 查询相关逻辑：
- RAG 流式查询
- RAG 检索器获取
"""
import logging
from typing import Dict, Any, List, Optional

from langchain_core.messages import HumanMessage, AIMessage, ToolMessage

from Django_xm.apps.ai_engine.services.token_counter import TokenUsageCallbackHandler
from Django_xm.apps.knowledge.services.rag_agent import create_rag_agent, query_rag_agent
from Django_xm.apps.knowledge.services.index_service import IndexManager
from Django_xm.apps.knowledge.services.embedding_service import get_embeddings
from Django_xm.apps.knowledge.services.retrieval_service import create_retriever
from ..utils import convert_chat_history

logger = logging.getLogger(__name__)


class RAGChatService:
    """RAG 模式聊天服务"""

    def __init__(self, user_id: Optional[int] = None):
        self.user_id = user_id

    def _get_user_index_name(self, index_name: str) -> str:
        return f"user_{self.user_id}_{index_name}" if self.user_id else index_name

    def get_rag_retriever(self, index_name: str):
        if not self.user_id:
            return None
        try:
            user_index_name = self._get_user_index_name(index_name)
            manager = IndexManager()
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
            retriever = create_retriever(vector_store, k=4)
            return retriever
        except Exception as e:
            logger.error(f"获取 RAG 检索器失败: {e}", exc_info=True)
            return None

    def process_rag_request(self, data: Dict[str, Any]) -> Dict[str, Any]:
        selected_kb = data.get('selected_knowledge_base')
        session_id = data.get('session_id')
        if not selected_kb:
            return None

        retriever = self.get_rag_retriever(selected_kb)
        if not retriever:
            return None

        logger.info(f"使用知识库 {selected_kb} 进行 RAG 查询")
        agent = create_rag_agent(
            retriever,
            user_id=self.user_id,
            session_id=session_id,
        )
        result = query_rag_agent(agent, data['message'], return_sources=True)

        return {
            'message': result.get('answer', ''),
            'mode': 'rag',
            'tools_used': ['rag_retrieve'],
            'success': True,
            'sources': result.get('sources', [])
        }

    async def process_rag_stream(self, data: Dict[str, Any], retriever, usage_tracker, cost_tracker):
        model_instance = None
        provider_id = data.get('provider_id')
        if provider_id:
            from Django_xm.apps.ai_engine.services.llm_factory import get_chat_model_by_provider
            try:
                model_instance = get_chat_model_by_provider(
                    provider_id=provider_id,
                    model_name=data.get('model_name') or None,
                    temperature=float(data['temperature']) if data.get('temperature') is not None else None,
                    max_tokens=int(data['max_tokens']) if data.get('max_tokens') is not None else None,
                    special_params=data.get('special_params') or None,
                    streaming=True,
                )
            except Exception:
                pass

        agent = create_rag_agent(
            retriever,
            model=model_instance,
            streaming=True,
            user_id=self.user_id,
            session_id=data.get('session_id'),
        )

        messages = []
        chat_history = data.get('chat_history', [])
        langchain_chat_history = convert_chat_history(chat_history)
        if langchain_chat_history:
            messages.extend(langchain_chat_history)

        human_msg = HumanMessage(content=data['message'])
        messages.append(human_msg)

        try:
            full_response = ""
            sources = []
            seen_ai_content = set()

            with TokenUsageCallbackHandler() as cb:
                async for chunk in agent.astream({"messages": messages}, stream_mode="messages"):
                    if isinstance(chunk, tuple) and len(chunk) == 2:
                        message, metadata = chunk
                        if isinstance(message, AIMessage) and message.content:
                            content_key = message.content
                            if content_key not in seen_ai_content:
                                seen_ai_content.add(content_key)
                            full_response += message.content
                            yield {"type": "chunk", "content": message.content}
                    elif isinstance(chunk, AIMessage) and chunk.content:
                        if chunk.content not in seen_ai_content:
                            seen_ai_content.add(chunk.content)
                        full_response += chunk.content
                        yield {"type": "chunk", "content": chunk.content}

                    if isinstance(chunk, tuple) and len(chunk) == 2:
                        message, metadata = chunk
                        if isinstance(message, ToolMessage):
                            try:
                                tool_content = message.content
                                if isinstance(tool_content, str):
                                    import json
                                    docs = json.loads(tool_content)
                                    if isinstance(docs, list):
                                        for doc in docs:
                                            if isinstance(doc, dict):
                                                sources.append({
                                                    'content': doc.get('page_content', doc.get('content', '')),
                                                    'source': doc.get('metadata', {}).get('source', 'Unknown') if isinstance(doc.get('metadata'), dict) else 'Unknown'
                                                })
                            except (json.JSONDecodeError, TypeError):
                                pass

            from .stream_helpers import update_usage_and_cost
            update_usage_and_cost(cb, usage_tracker, cost_tracker)

            if sources:
                yield {'type': 'sources', 'data': sources}

        except Exception as e:
            logger.error(f"RAG 流式查询失败: {e}", exc_info=True)
            from Django_xm.apps.ai_engine.services.exceptions import classify_exception
            classified = classify_exception(e)
            yield {"type": "error", "message": classified.user_message}
