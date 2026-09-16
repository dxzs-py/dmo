"""
RAG 查询/检索视图

提供 RAG 查询、检索和流式接口。
索引管理和文档管理视图已迁移至 views_kb.py。
"""

import json

from drf_spectacular.utils import extend_schema
from rest_framework.decorators import api_view, permission_classes, renderer_classes, throttle_classes
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import BaseRenderer
from rest_framework.views import APIView

from Django_xm.apps.cache_manager.services.cache_service import (
    QueryCacheService,
    VectorSearchCacheService,
)
from Django_xm.apps.core.logging_utils import get_logger
from Django_xm.apps.core.permissions import IsAuthenticatedOrQueryParam
from Django_xm.apps.core.throttling import KnowledgeRateThrottle
from Django_xm.common.error_codes import ErrorCode
from Django_xm.common.exceptions import BaseAppError
from Django_xm.common.responses import success_response
from Django_xm.common.serializers import EmptySerializer
from Django_xm.common.sse_utils import sse_error_event, sse_response

from .serializers import (
    RagQuerySerializer,
    RagResponseSerializer,
    SearchRequestSerializer,
    SearchResultSerializer,
)
from .services.embedding_service import get_embeddings
from .services.index_service import IndexManager
from .services.retrieval_service import create_retriever
from .services.strict_rag_chain import query_strict_rag, stream_strict_rag
from .vector_store import search_vector_store
from .views_utils import get_user_index_name

logger = get_logger(__name__)


class SSERenderer(BaseRenderer):
    media_type = "text/event-stream"
    format = "txt"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        return data


class RAGQueryView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=RagQuerySerializer, responses={200: RagResponseSerializer})
    def post(self, request):
        serializer = RagQuerySerializer(data=request.data)
        if not serializer.is_valid():
            raise ValidationError(serializer.errors)

        data = serializer.validated_data
        user = request.user
        index_name = data["index_name"]
        user_index_name = get_user_index_name(user, index_name)
        k = data.get("k", 4)

        logger.info(f"RAG 查询: {data['query'][:50]}...")

        cached_result = QueryCacheService.get_cached_query(data["query"], user_index_name, k)
        if cached_result is not None:
            logger.info("RAG 查询缓存命中")
            return success_response(
                data=RagResponseSerializer(
                    {
                        "answer": cached_result["result"].get("answer", ""),
                        "sources": cached_result["result"].get("sources", []),
                        "success": True,
                        "cached": True,
                    }
                ).data,
                message="操作成功",
            )

        manager = IndexManager()
        if not manager.index_exists(user_index_name):
            raise NotFound(f"索引不存在: {index_name}")

        metadata = manager._load_metadata(user_index_name)
        num_documents = metadata.get("num_documents", 0) if metadata else 0
        if num_documents == 0:
            raise ValidationError("索引中暂无文档，请先上传文档后再查询")

        required_dim = metadata.get("embedding_dimension") if metadata else None
        embeddings = get_embeddings(required_dimension=required_dim)
        vector_store = manager.load_index(user_index_name, embeddings)
        retriever = create_retriever(vector_store, k=k)

        # Strict Chain 模式：强制检索 -> 注入上下文 -> 生成（防幻觉）
        result = query_strict_rag(
            retriever,
            data["query"],
            k=k,
            collection_name=user_index_name,
        )

        QueryCacheService.cache_query_result(data["query"], result, user_index_name, k)

        logger.info("查询完成")
        response_data = RagResponseSerializer(
            {"answer": result.get("answer", ""), "sources": result.get("sources", []), "success": True}
        ).data
        if result.get("degraded"):
            response_data["degraded"] = True
            response_data["degradation_notice"] = result.get("degradation_notice", "")
        return success_response(data=response_data, message="操作成功")


class RAGSearchView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [KnowledgeRateThrottle]

    @extend_schema(request=SearchRequestSerializer, responses={200: SearchResultSerializer})
    def post(self, request):
        serializer = SearchRequestSerializer(data=request.data)
        if not serializer.is_valid():
            raise ValidationError(serializer.errors)

        data = serializer.validated_data
        user = request.user
        index_name = data["index_name"]
        user_index_name = get_user_index_name(user, index_name)
        k = data.get("k", 4)
        score_threshold = data.get("score_threshold")

        logger.info(f"RAG 检索: {data['query'][:50]}...")

        cached_results = VectorSearchCacheService.get_cached_search(data["query"], user_index_name, k)
        if cached_results is not None:
            logger.info("向量搜索缓存命中")
            return success_response(data=SearchResultSerializer(cached_results, many=True).data, message="操作成功")

        manager = IndexManager()
        if not manager.index_exists(user_index_name):
            raise NotFound(f"索引不存在: {index_name}")

        metadata = manager._load_metadata(user_index_name)
        num_documents = metadata.get("num_documents", 0) if metadata else 0
        if num_documents == 0:
            raise ValidationError("索引中暂无文档，请先上传文档后再检索")

        required_dim = metadata.get("embedding_dimension") if metadata else None
        embeddings = get_embeddings(required_dimension=required_dim)
        vector_store = manager.load_index(user_index_name, embeddings)

        results = search_vector_store(vector_store, data["query"], k=k, score_threshold=score_threshold)

        search_results = []
        for doc, score in results:
            item = {
                "content": doc.page_content,
                "metadata": doc.metadata,
            }
            if score is not None:
                item["score"] = float(score)
            search_results.append(item)

        VectorSearchCacheService.cache_search_result(data["query"], search_results, user_index_name, k)

        return success_response(data=SearchResultSerializer(search_results, many=True).data, message="操作成功")


@extend_schema(request=RagQuerySerializer, responses={200: EmptySerializer})
@api_view(["POST"])
@renderer_classes([SSERenderer])
@permission_classes([IsAuthenticatedOrQueryParam])
@throttle_classes([KnowledgeRateThrottle])
def rag_query_stream(request):
    # 鉴权/限流由装饰器在进视图前经 DRF pipeline 执行：
    # 失败在响应头阶段抛出（尚未建立 SSE 流），全局 handler 返回 JSON 错误。
    serializer = RagQuerySerializer(data=request.data)
    if not serializer.is_valid():
        raise BaseAppError(
            "数据验证失败",
            business_code=ErrorCode.VALIDATION_FAILED,
            data={"details": serializer.errors},
        )

    data = serializer.validated_data
    user = request.user
    index_name = data["index_name"]
    user_index_name = get_user_index_name(user, index_name)

    logger.info(f"RAG 流式查询: {data['query'][:50]}...")

    manager = IndexManager()
    if not manager.index_exists(user_index_name):
        raise NotFound(f"索引不存在: {index_name}")

    metadata = manager._load_metadata(user_index_name)
    required_dim = metadata.get("embedding_dimension") if metadata else None
    embeddings = get_embeddings(required_dimension=required_dim)
    vector_store = manager.load_index(user_index_name, embeddings)
    retriever = create_retriever(vector_store, k=data.get("k", 4))

    # Strict Chain 模式流式查询（防幻觉）
    def event_stream():
        # SSE 白名单：响应已进入流式阶段，无法回退为普通 JSON 错误响应，
        # 生成器内部异常必须 yield 错误事件结束流，保留原错误处理结构
        try:
            yield f"data: {json.dumps({'type': 'start', 'message': '检索中...'})}\n\n"
            full_response = ""

            for event in stream_strict_rag(
                retriever,
                data["query"],
                k=data.get("k", 4),
                collection_name=user_index_name,
            ):
                if event["type"] == "chunk":
                    full_response += event["content"]
                    yield f"data: {json.dumps({'type': 'chunk', 'content': event['content']})}\n\n"
                elif event["type"] == "heartbeat":
                    yield f"data: {json.dumps({'type': 'heartbeat', 'message': event.get('message', '')})}\n\n"
                elif event["type"] == "sources":
                    yield f"data: {json.dumps({'type': 'sources', 'data': event['data']})}\n\n"
                elif event["type"] == "degradation":
                    yield f"data: {json.dumps({'type': 'degradation', 'message': event['message']})}\n\n"
                elif event["type"] == "error":
                    yield sse_error_event(code="50002", message=event["message"])

            logger.info(f"[RAG Chain] 流式生成完成, total_len={len(full_response)}")
            yield f"data: {json.dumps({'type': 'end', 'message': '生成完成'})}\n\n"
        except Exception:
            logger.exception("[RAG Chain] 流式生成异常")
            yield sse_error_event(code="50001", message="流式生成失败，请稍后重试")

    return sse_response(event_stream())
