"""
RAG 查询/检索视图

提供 RAG 查询、检索和流式接口。
索引管理和文档管理视图已迁移至 views_kb.py。
"""

import json
import logging

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import api_view, renderer_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import BaseRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from Django_xm.apps.cache_manager.services.cache_service import (
    QueryCacheService,
    VectorSearchCacheService,
)
from Django_xm.common.error_codes import ErrorCode
from Django_xm.common.responses import (
    error_response,
    not_found_response,
    success_response,
    validation_error_response,
)
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

logger = logging.getLogger(__name__)


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
            return validation_error_response(errors=serializer.errors, message="数据验证失败")

        data = serializer.validated_data
        user = request.user
        index_name = data["index_name"]
        user_index_name = get_user_index_name(user, index_name)
        k = data.get("k", 4)

        logger.info(f"RAG 查询: {data['query'][:50]}...")

        try:
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
                return not_found_response(message=f"索引不存在: {index_name}")

            metadata = manager._load_metadata(user_index_name)
            num_documents = metadata.get("num_documents", 0) if metadata else 0
            if num_documents == 0:
                return validation_error_response(message="索引中暂无文档，请先上传文档后再查询", errors=[])

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

        except Exception as e:
            logger.exception("查询失败")
            return error_response(
                code=ErrorCode.SERVER_ERROR, message=str(e), http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class RAGSearchView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=SearchRequestSerializer, responses={200: SearchResultSerializer})
    def post(self, request):
        serializer = SearchRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(errors=serializer.errors, message="数据验证失败")

        data = serializer.validated_data
        user = request.user
        index_name = data["index_name"]
        user_index_name = get_user_index_name(user, index_name)
        k = data.get("k", 4)
        score_threshold = data.get("score_threshold")

        logger.info(f"RAG 检索: {data['query'][:50]}...")

        try:
            cached_results = VectorSearchCacheService.get_cached_search(data["query"], user_index_name, k)
            if cached_results is not None:
                logger.info("向量搜索缓存命中")
                return success_response(data=SearchResultSerializer(cached_results, many=True).data, message="操作成功")

            manager = IndexManager()
            if not manager.index_exists(user_index_name):
                return not_found_response(message=f"索引不存在: {index_name}")

            metadata = manager._load_metadata(user_index_name)
            num_documents = metadata.get("num_documents", 0) if metadata else 0
            if num_documents == 0:
                return validation_error_response(message="索引中暂无文档，请先上传文档后再检索", errors=[])

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

        except Exception as e:
            logger.exception("检索失败")
            return error_response(
                code=ErrorCode.SERVER_ERROR, message=str(e), http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


@extend_schema(request=RagQuerySerializer, responses={200: EmptySerializer})
@api_view(["POST"])
@renderer_classes([SSERenderer])
def rag_query_stream(request):
    from Django_xm.common.permissions import IsAuthenticatedOrQueryParam

    perm = IsAuthenticatedOrQueryParam()
    if not perm.has_permission(request, rag_query_stream):
        return error_response(
            message="未登录或登录已过期",
            code=ErrorCode.TOKEN_INVALID,
            http_status=status.HTTP_401_UNAUTHORIZED,
        )

    serializer = RagQuerySerializer(data=request.data)
    if not serializer.is_valid():
        return error_response(
            message="数据验证失败",
            code=ErrorCode.VALIDATION_FAILED,
            data={"details": serializer.errors},
            http_status=status.HTTP_400_BAD_REQUEST,
        )

    data = serializer.validated_data
    user = request.user
    index_name = data["index_name"]
    user_index_name = get_user_index_name(user, index_name)

    logger.info(f"RAG 流式查询: {data['query'][:50]}...")

    try:
        manager = IndexManager()
        if not manager.index_exists(user_index_name):
            return error_response(
                message=f"索引不存在: {index_name}",
                code=ErrorCode.NOT_FOUND,
                http_status=status.HTTP_404_NOT_FOUND,
            )

        metadata = manager._load_metadata(user_index_name)
        required_dim = metadata.get("embedding_dimension") if metadata else None
        embeddings = get_embeddings(required_dimension=required_dim)
        vector_store = manager.load_index(user_index_name, embeddings)
        retriever = create_retriever(vector_store, k=data.get("k", 4))

        # Strict Chain 模式流式查询（防幻觉）
        def event_stream():
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
            except Exception as e:
                logger.exception("[RAG Chain] 流式生成异常")
                yield sse_error_event(code="50001", message=str(e))

        return sse_response(event_stream())

    except Exception as e:
        logger.exception("流式查询失败")
        return Response({"code": 500, "message": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
