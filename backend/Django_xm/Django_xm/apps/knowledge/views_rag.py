"""
RAG 查询/检索视图

提供 RAG 查询、跨库聚合检索和流式接口。
索引管理和文档管理视图已迁移至 views_kb.py。
"""

import json
from concurrent.futures import ThreadPoolExecutor

from django.db import connections
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import api_view, permission_classes, renderer_classes, throttle_classes
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from Django_xm.apps.ai_engine.config import settings as ai_settings
from Django_xm.apps.cache_manager.services.cache_service import QueryCacheService
from Django_xm.apps.core.logging_utils import get_logger
from Django_xm.apps.core.permissions import IsAuthenticatedOrQueryParam
from Django_xm.apps.core.throttling import KnowledgeRateThrottle
from Django_xm.common.error_codes import ErrorCode
from Django_xm.common.exceptions import BaseAppError
from Django_xm.common.responses import success_response
from Django_xm.common.serializers import EmptySerializer
from Django_xm.common.sse_utils import SSERenderer, sse_error_event, sse_response

from .models import IndexMetadata
from .serializers import (
    RagQuerySerializer,
    RagResponseSerializer,
    SearchAllRequestSerializer,
    SearchAllResultItemSerializer,
    SearchAllResponseSerializer,
)
from .services.embedding_service import get_embeddings
from .services.index_service import IndexManager
from .services.rag_retrieval import UnifiedRagPipeline
from .services.retrieval_service import create_retriever
from .services.strict_rag_chain import query_strict_rag, stream_strict_rag
from .views_utils import get_original_index_name, get_user_index_name

logger = get_logger(__name__)


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


# 跨库聚合检索的工作线程上限（单进程线程池规模，避免索引多时线程暴涨）
SEARCH_ALL_MAX_WORKERS = 8


class SearchAllView(APIView):
    """跨库聚合检索：单次 embedding → 并发检索用户全部（或指定）索引 → RRF 融合。

    排序语义：各库绝对 score 跨库不可比，融合按排名倒数（RRF，k=60），
    融合分 = Σ 1/(rrf_k + 库内排名)；同库内相对顺序保持不变。
    单库异常计入 failed_indexes 不中断整体；全部失败/空结果返回 200 + 空数组。
    """

    permission_classes = [IsAuthenticated]
    throttle_classes = [KnowledgeRateThrottle]

    @extend_schema(request=SearchAllRequestSerializer, responses={200: SearchAllResponseSerializer})
    def post(self, request):
        serializer = SearchAllRequestSerializer(data=request.data)
        if not serializer.is_valid():
            raise ValidationError(serializer.errors)

        data = serializer.validated_data
        user = request.user
        query = data["query"]
        k = data.get("k", 4)
        score_threshold = data.get("score_threshold")
        requested_names = data.get("index_names")

        logger.info(f"RAG 跨库检索: {query[:50]}...")

        # 归属校验：仅解析当前用户名下的索引（IndexMetadata 单一权威，objects 自动滤软删）
        prefix = f"user_{user.id}_"
        records = {
            name: (num_documents, embedding_dim)
            for name, num_documents, embedding_dim in IndexMetadata.objects.filter(
                user=user, name__startswith=prefix
            ).values_list("name", "num_documents", "embedding_dimension")
        }

        if requested_names:
            targets = [get_user_index_name(user, name) for name in requested_names]
            invalid = [name for name in targets if name not in records]
            if invalid:
                raise ValidationError(f"索引不存在或不属于当前用户: {', '.join(invalid)}")
        else:
            targets = sorted(records)

        # 空索引（num_documents=0）无文档可检索，跳过且不计入 failed
        targets = [name for name in targets if records[name][0] > 0]
        if not targets:
            return success_response(
                data={"results": [], "searched_indexes": 0, "failed_indexes": 0},
                message="操作成功",
            )

        # query embedding 仅计算 1 次（跨库复用）；embedding 服务异常按全部失败处理，不抛 500
        try:
            dims = [records[name][1] for name in targets if records[name][1]]
            embeddings = get_embeddings(required_dimension=dims[0] if dims else None)
            query_embedding = embeddings.embed_query(query)
        except Exception:
            logger.exception("search-all: query embedding 计算失败")
            return success_response(
                data={"results": [], "searched_indexes": 0, "failed_indexes": len(targets)},
                message="操作成功",
            )

        manager = IndexManager()
        rrf_k = ai_settings.rag_rrf_constant

        def _search_one(full_name: str):
            """单库检索 worker：异常捕获计入 failed，不中断整体聚合。"""
            try:
                vector_store = manager.load_index(full_name, embeddings)
                results = UnifiedRagPipeline.search_scored(
                    vector_store,
                    query,
                    k=k,
                    score_threshold=score_threshold,
                    reload_vector_store=lambda: manager.load_index(full_name, embeddings),
                    required_dim=records[full_name][1],
                    query_embedding=query_embedding,
                )
                return full_name, results
            except Exception as exc:
                logger.warning(f"search-all: 索引 {full_name} 检索失败: {exc}")
                return full_name, None
            finally:
                # worker 线程内打开的 Django 连接随线程结束显式释放
                connections.close_all()

        searched = 0
        failed = 0
        # RRF 融合：content 去重合并（同库排名唯一），跨库同文贡献累加；
        # 条目展示字段取首次出现（targets 顺序 + 库内 rank 升序，确定性）
        fused: dict[str, dict] = {}

        with ThreadPoolExecutor(max_workers=min(len(targets), SEARCH_ALL_MAX_WORKERS)) as executor:
            for full_name, results in executor.map(_search_one, targets):
                if results is None:
                    failed += 1
                    continue
                searched += 1
                source_index = get_original_index_name(full_name)
                # 库内排名（1 起）：score 降序，None 分数排末尾（稳定排序保持原相对顺序）
                ranked = sorted(
                    enumerate(results, 1),
                    key=lambda pair: pair[1][1] if pair[1][1] is not None else float("-inf"),
                    reverse=True,
                )
                for rank, (doc, _score) in ranked:
                    entry = fused.get(doc.page_content)
                    if entry is None:
                        entry = {"fused_score": 0.0, "first": (doc, _score, source_index)}
                        fused[doc.page_content] = entry
                    entry["fused_score"] += 1.0 / (rrf_k + rank)

        ordered = sorted(fused.values(), key=lambda e: e["fused_score"], reverse=True)
        results_payload = []
        for fused_rank, entry in enumerate(ordered[:k], 1):
            doc, score, source_index = entry["first"]
            item = {
                "content": doc.page_content,
                "metadata": doc.metadata,
                "source_index": source_index,
                "fused_rank": fused_rank,
            }
            if score is not None:
                item["score"] = float(score)
            results_payload.append(item)

        return success_response(
            data={
                "results": SearchAllResultItemSerializer(results_payload, many=True).data,
                "searched_indexes": searched,
                "failed_indexes": failed,
            },
            message="操作成功",
        )


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
