"""
RAG 查询/检索视图

提供 RAG 查询、跨库聚合检索和流式接口。
索引管理和文档管理视图已迁移至 views_kb.py。

RP-01 执行模型（daphne ASGI）：DRF 3.17 的 APIView.dispatch 为纯同步管道，
单独 async 化 handler 不可行（coroutine 会被 sync dispatch 直接送进
finalize_response 而非被 await）。本模块以 AsyncAPIView 基类重写 dispatch：
DRF 管道轻活（认证/权限/节流，毫秒级 DB/Redis 访问）经 sync_to_async 默认
thread_sensitive=True 走主执行器；async handler 内的秒级~几十秒级重活经
sync_to_async(thread_sensitive=False) 调度进独立线程池，不再占用主执行器
串行化全站同步视图。流式端点保持 @api_view sync 前置段（鉴权/校验/空索引
以 JSON 错误返回的既有契约），同步流核心经线程泵（对齐
apps/learning/services/learning_stream.pump_sync_events 先例）桥接为
async 生成器出帧。
"""

import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from asgiref.sync import sync_to_async
from django.db import connections
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import api_view, permission_classes, renderer_classes, throttle_classes
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from Django_xm.apps.cache_manager.services.cache_service import QueryCacheService
from Django_xm.apps.core.logging_utils import get_logger
from Django_xm.apps.core.throttling import KnowledgeRateThrottle

# A-5：ai_engine 配置统一经 knowledge.config 门面（settings 为其 re-export）
from Django_xm.apps.knowledge.config import get_model_string, settings as ai_settings
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
    SearchAllResponseSerializer,
    SearchAllResultItemSerializer,
)
from .services.embedding_service import get_embeddings
from .services.index_service import IndexManager
from .services.rag_retrieval import UnifiedRagPipeline, rrf_fuse
from .services.retrieval_service import build_user_retriever
from .services.strict_rag_chain import query_strict_rag, sanitize_source_metadata, stream_strict_rag
from .views_utils import build_user_index_name, get_original_index_name, get_user_index_name

logger = get_logger(__name__)

# B5：查询缓存键的模式段。本文件所有查询缓存写入方均为 strict chain
# （query_strict_rag / stream_strict_rag），模式切换后键自然失配。
_RAG_QUERY_CACHE_MODE = "strict"


def _query_cache_segments() -> tuple[str, str]:
    """B5：查询缓存键的模型/模式段——同一请求的读与写必须用同一段值。

    模型标识经 get_model_string()（SystemConfig → settings 兜底，换模型后
    旧缓存不得命中）；解析异常时用 "default" 占位（键段变化后旧键自然失配，
    随 TTL 过期，无需迁移）。
    """
    try:
        model_id = get_model_string()
    except Exception:
        logger.warning("查询缓存模型段解析失败，使用 default 占位")
        model_id = "default"
    return model_id, _RAG_QUERY_CACHE_MODE


class AsyncAPIView(APIView):
    """重活型 APIView 的 async 化基类（RP-01）。

    重写 dispatch：DRF 管道轻活（initialize_request/initial——认证、权限、节流）
    经 sync_to_async 默认 thread_sensitive=True 在主执行器执行；handler 本体
    ``async def post`` 中由调用方以 ``sync_to_async(..., thread_sensitive=False)``
    把重活调度进独立线程池。异常处理与响应收尾（handle_exception /
    finalize_response 为纯内存操作，custom_exception_handler 无 DB/Redis 访问）
    在事件循环线程内直接执行。视图内不得出现同步 ORM/长阻塞调用——一律
    sync_to_async 包裹。
    """

    async def dispatch(self, request, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        request = await sync_to_async(self.initialize_request)(request, *args, **kwargs)
        self.request = request
        self.headers = self.default_response_headers

        try:
            await sync_to_async(self.initial)(request, *args, **kwargs)
            if request.method.lower() in self.http_method_names:
                handler = getattr(self, request.method.lower(), self.http_method_not_allowed)
            else:
                handler = self.http_method_not_allowed
            response = await handler(request, *args, **kwargs)
        except Exception as exc:
            response = self.handle_exception(exc)

        self.response = self.finalize_response(request, response, *args, **kwargs)
        return self.response


def _run_strict_query_sync(query, user_id, index_name, k):
    """strict RAG 全链同步重活（供 RAGQueryView.async post 经线程池调度）。

    索引校验/空索引拒绝（用户可见 4xx 语义保留在 view 层）；向量库构建
    （metadata/维度探测/embeddings/load_index/create_retriever）经
    build_user_retriever 单一门面（B-1 收敛，此前本函数与流式构建各写一遍）。
    异常（NotFound/ValidationError/其余）原样上抛，由 AsyncAPIView.dispatch
    的 except 链转统一 JSON 错误响应。
    """
    manager = IndexManager()
    user_index_name = build_user_index_name(user_id, index_name)
    if not manager.index_exists(user_index_name, user_id=user_id):
        raise NotFound(f"索引不存在: {user_index_name}")

    metadata = manager.load_metadata(user_index_name, user_id=user_id)
    num_documents = metadata.get("num_documents", 0) if metadata else 0
    if num_documents == 0:
        raise ValidationError("索引中暂无文档，请先上传文档后再查询")

    retriever = build_user_retriever(user_id, index_name, k=k)
    if retriever is None:
        # 前置校验后索引被并发删除/构建失败（不可达防御）
        raise NotFound(f"索引不存在: {user_index_name}")

    # Strict Chain 模式：强制检索 -> 注入上下文 -> 生成（防幻觉）
    return query_strict_rag(
        retriever,
        query,
        k=k,
        collection_name=user_index_name,
    )


def _pump_strict_rag_events(query, k, user_index_name, retriever, loop, queue):
    """在专用线程中迭代 stream_strict_rag 同步生成器，事件经线程泵投递队列。

    对齐 apps/learning/services/learning_stream.pump_sync_events 先例：daphne
    ASGI 下同步生成器直接在事件循环内迭代会以 LLM 调用阻塞 loop，daphne 无法
    即时 flush 事件 chunk。同步核心放独立线程后，事件循环始终空闲，每产出一个
    事件即 ``call_soon_threadsafe`` 投递；迭代正常结束投递 None（EOF 哨兵），
    异常时投递 error 事件（SSE 白名单契约：已进入流式阶段无法回退 JSON 错误）。
    """
    try:
        for event in stream_strict_rag(retriever, query, k=k, collection_name=user_index_name):
            loop.call_soon_threadsafe(queue.put_nowait, event)
    except Exception:
        logger.exception("[RAG Chain] 流式生成异常")
        loop.call_soon_threadsafe(
            queue.put_nowait, {"type": "error", "message": "流式生成失败，请稍后重试"}
        )
    finally:
        loop.call_soon_threadsafe(queue.put_nowait, None)


def _cached_sse_events(result):
    """C-8：流式命中查询缓存时合成与 stream_strict_rag 一致的事件序列。

    序列对齐生成链：start → chunk(s) → [degradation] → [sources] → end；
    合成帧为纯内存操作（无检索/LLM 调用），同步生成器直接交付。
    """
    yield f"data: {json.dumps({'type': 'start', 'message': '检索中...（缓存命中）'})}\n\n"
    answer = result.get("answer", "")
    if answer:
        yield f"data: {json.dumps({'type': 'chunk', 'content': answer})}\n\n"
    if result.get("degraded"):
        yield f"data: {json.dumps({'type': 'degradation', 'message': result.get('degradation_notice', '')})}\n\n"
    sources = result.get("sources", [])
    if sources:
        yield f"data: {json.dumps({'type': 'sources', 'data': sources})}\n\n"
    yield f"data: {json.dumps({'type': 'end', 'message': '生成完成'})}\n\n"


class RAGQueryView(AsyncAPIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=RagQuerySerializer, responses={200: RagResponseSerializer})
    async def post(self, request):
        # 段1（主执行器，毫秒级）：校验 + 归属构造 + 查询缓存读
        prepared = await sync_to_async(self._prepare_request)(request)
        if prepared.cached_response is not None:
            return prepared.cached_response

        # 段2（独立线程池）：strict RAG 全链重活（RP-01）
        result = await sync_to_async(_run_strict_query_sync, thread_sensitive=False)(
            prepared.query, prepared.user_id, prepared.index_name, prepared.k
        )

        # 段3（主执行器，毫秒级）：缓存写 + 序列化
        return await sync_to_async(self._finalize_response)(prepared, result)

    @staticmethod
    def _prepare_request(request):
        serializer = RagQuerySerializer(data=request.data)
        if not serializer.is_valid():
            raise ValidationError(serializer.errors)

        data = serializer.validated_data
        user_index_name = get_user_index_name(request.user, data["index_name"])
        k = data.get("k", 4)
        # B5：模型/模式段在段1解析一次并随 prepared 透传段3——读写同键
        model_seg, mode_seg = _query_cache_segments()

        logger.info(f"RAG 查询: {data['query'][:50]}...")

        cached_result = QueryCacheService.get_cached_query(
            data["query"], user_index_name, k, model_name=model_seg, rag_mode=mode_seg
        )
        if cached_result is not None:
            logger.info("RAG 查询缓存命中")
            # CT-05：缓存命中重组补 degraded/degradation_notice（与新鲜响应形状一致）；
            # serializer 会丢弃未声明字段，degraded 需在 .data 之后按新鲜路径同模式补键
            inner = cached_result.get("result", {})
            payload = {
                "answer": inner.get("answer", ""),
                "sources": inner.get("sources", []),
                "success": True,
                "cached": True,
            }
            response_data = RagResponseSerializer(payload).data
            if inner.get("degraded"):
                response_data["degraded"] = True
                response_data["degradation_notice"] = inner.get("degradation_notice", "")
            return SimpleNamespace(
                query=data["query"],
                user_id=request.user.id,
                index_name=data["index_name"],
                user_index_name=user_index_name,
                k=k,
                model_seg=model_seg,
                mode_seg=mode_seg,
                cached_response=success_response(data=response_data, message="操作成功"),
            )
        return SimpleNamespace(
            query=data["query"],
            user_id=request.user.id,
            index_name=data["index_name"],
            user_index_name=user_index_name,
            k=k,
            model_seg=model_seg,
            mode_seg=mode_seg,
            cached_response=None,
        )

    @staticmethod
    def _finalize_response(prepared, result):
        # Task 4/B3：空结果（受控未找到应答）与降级结果不写查询缓存——
        # 避免缓存 TTL 内持续返回低质量/受控文案
        if result.get("degraded") or not result.get("answer"):
            logger.info("查询完成（空结果或降级，跳过缓存写入）")
        else:
            # B5：与 _prepare_request 读缓存同模型/模式段（读写同键）
            QueryCacheService.cache_query_result(
                prepared.query,
                result,
                prepared.user_index_name,
                prepared.k,
                model_name=prepared.model_seg,
                rag_mode=prepared.mode_seg,
            )

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


class SearchAllView(AsyncAPIView):
    """跨库聚合检索：按维度分组 embed → 并发检索用户全部（或指定）索引 → RRF 融合。

    排序语义：各库绝对 score 跨库不可比，融合按排名倒数（RRF，k=60），
    融合分 = Σ 1/(rrf_k + 库内排名)；同库内相对顺序保持不变。
    单库异常计入 failed_indexes 不中断整体；全部失败/空结果返回 200 + 空数组。
    3.6：不同库可能由不同维度 embedding 建库——按维度分组，每组一次 embed，
    组内共享 query 向量；某组 embedding 失败仅该组计入 failed。

    RP-01：段1（归属校验/目标索引解析）走主执行器；段2（query embedding +
    ThreadPoolExecutor 并发检索 + RRF 融合）经 thread_sensitive=False 进线程池。
    """

    permission_classes = [IsAuthenticated]
    throttle_classes = [KnowledgeRateThrottle]

    @extend_schema(request=SearchAllRequestSerializer, responses={200: SearchAllResponseSerializer})
    async def post(self, request):
        prepared = await sync_to_async(self._prepare_request)(request)
        if prepared.early_response is not None:
            return prepared.early_response

        data, message = await sync_to_async(self._run_search, thread_sensitive=False)(prepared)
        return success_response(data=data, message=message)

    @staticmethod
    def _prepare_request(request):
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
            return SimpleNamespace(
                query=query,
                k=k,
                score_threshold=score_threshold,
                targets=[],
                records=records,
                early_response=success_response(
                    data={"results": [], "searched_indexes": 0, "failed_indexes": 0},
                    message="操作成功",
                ),
            )
        return SimpleNamespace(
            query=query,
            k=k,
            score_threshold=score_threshold,
            targets=targets,
            records=records,
            early_response=None,
        )

    @staticmethod
    def _run_search(prepared):
        targets = prepared.targets
        records = prepared.records

        # 3.6 维度分组：不同索引可能由不同维度的 embedding 建库，单一
        # query embedding 无法跨维度复用（原实现只取第一个库的维度，
        # 其余维度不匹配的库全部检索失败）——按维度分组，每组独立构造
        # embeddings 并各 embed 一次，组内索引共享同一 query 向量。
        dim_groups: dict = {}
        for name in targets:
            dim_groups.setdefault(records[name][1], []).append(name)

        group_ctx: dict = {}
        for dim in dim_groups:
            try:
                embeddings = get_embeddings(required_dimension=dim)
                group_ctx[dim] = (embeddings, embeddings.embed_query(prepared.query))
            except Exception:
                logger.exception(f"search-all: 维度 {dim} 组 query embedding 计算失败")
                group_ctx[dim] = None

        if all(ctx is None for ctx in group_ctx.values()):
            # E5-09：全部组检索服务不可达时协议 message 必须是故障文案（数据结构照常回传）
            return (
                {"results": [], "searched_indexes": 0, "failed_indexes": len(targets)},
                "检索服务暂时不可用，请稍后重试",
            )

        manager = IndexManager()
        rrf_k = ai_settings.rag_rrf_constant

        def _search_one(full_name: str, dim):
            """单库检索 worker：异常捕获计入 failed，不中断整体聚合。"""
            ctx = group_ctx[dim]
            if ctx is None:
                # 本维度组 embedding 失败：组内索引全部计入 failed
                return full_name, None
            group_embeddings, query_embedding = ctx
            try:
                vector_store = manager.load_index(full_name, group_embeddings)
                results = UnifiedRagPipeline.search_scored(
                    vector_store,
                    prepared.query,
                    k=prepared.k,
                    score_threshold=prepared.score_threshold,
                    reload_vector_store=lambda: manager.load_index(full_name, group_embeddings),
                    required_dim=dim,
                    query_embedding=query_embedding,
                    # C1/KV-2 + C-2arch：score_semantics 由后端 native_score_semantics
                    # 声明收敛（pgvector=distance 归一为相似度，否则阈值反向过滤 +
                    # RRF 库内排名倒序），调用方不再按 store_type 硬判
                    score_semantics=manager.get_score_semantics(full_name),
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
        # 各库已按 score 降序的 (doc, score) 列表，交 rrf_fuse 融合
        #（B-2 单源：原内联 60 行 RRF 删除，融合算法与 search_multi_kb 同源）
        ranked_lists: list[list[tuple[object, float | None]]] = []
        ranked_sources: list[str] = []  # 与 ranked_lists 一一对应的索引全名

        with ThreadPoolExecutor(max_workers=min(len(targets), SEARCH_ALL_MAX_WORKERS)) as executor:
            # executor.map 按提交序返回（分组构造序）；ranked_sources 与
            # ranked_lists 一一对应，source_index 映射不依赖提交顺序
            task_names = [name for names in dim_groups.values() for name in names]
            task_dims = [dim for dim, names in dim_groups.items() for _ in names]
            for full_name, results in executor.map(_search_one, task_names, task_dims):
                if results is None:
                    failed += 1
                    continue
                searched += 1
                # 库内排名（1 起）：score 降序，None 分数排末尾（稳定排序保持原相对顺序）
                ranked = sorted(
                    ((doc, score) for doc, score in results),
                    key=lambda pair: pair[1] if pair[1] is not None else float("-inf"),
                    reverse=True,
                )
                ranked_lists.append(ranked)
                ranked_sources.append(full_name)

        # 等权融合（跨库绝对 score 不可比，按排名倒数累加；同库内相对顺序不变）
        fused = rrf_fuse(ranked_lists, rrf_k=rrf_k)
        results_payload = []
        for fused_rank, (doc, _fused_score, first_score, first_list_idx) in enumerate(fused[: prepared.k], 1):
            source_index = get_original_index_name(ranked_sources[first_list_idx])
            item = {
                "content": doc.page_content,
                # C-7：与 strict_rag_chain._extract_sources 同口径，剥离服务器绝对路径
                "metadata": sanitize_source_metadata(doc.metadata),
                "source_index": source_index,
                "fused_rank": fused_rank,
            }
            if first_score is not None:
                item["score"] = float(first_score)
            results_payload.append(item)

        # E5-09：存在检索目标但全部失败时，协议 message 不得宣称"操作成功"
        all_failed = searched == 0 and failed > 0
        return (
            {
                "results": SearchAllResultItemSerializer(results_payload, many=True).data,
                "searched_indexes": searched,
                "failed_indexes": failed,
            },
            "检索服务暂时不可用，请稍后重试" if all_failed else "操作成功",
        )


@extend_schema(request=RagQuerySerializer, responses={200: EmptySerializer})
@api_view(["POST"])
@renderer_classes([SSERenderer])
# C-6：SSE 端点回归标准认证。前端 fetchSSE 已注入 Authorization 头（utils/sse.js），
# query-param 认证仅剩 <a href> 下载场景——token 走 URL 会进访问日志/浏览器历史。
@permission_classes([IsAuthenticated])
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
    user_index_name = get_user_index_name(request.user, data["index_name"])
    k = data.get("k", 4)

    logger.info(f"RAG 流式查询: {data['query'][:50]}...")

    # 前置校验保持轻量（毫秒级 DB/元数据读，主执行器），错误以 JSON 返回（进流前契约）
    manager = IndexManager()
    if not manager.index_exists(user_index_name, user_id=request.user.id):
        raise NotFound(f"索引不存在: {data['index_name']}")

    # C-8：空索引语义与非流式对齐——两链一致 400 拒绝
    #（原流式对空索引直接走 NO_RESULT_ANSWER 生成链，与非流式行为漂移）
    metadata = manager.load_metadata(user_index_name, user_id=request.user.id)
    num_documents = metadata.get("num_documents", 0) if metadata else 0
    if num_documents == 0:
        raise ValidationError("索引中暂无文档，请先上传文档后再查询")

    # C-8：缓存策略对齐——与非流式共用同一查询缓存；命中合成事件序列（零检索/LLM）
    # B5：模型/模式段在请求前段解析一次，读（此处）与写（event_stream 内）同键
    model_seg, mode_seg = _query_cache_segments()
    cached_result = QueryCacheService.get_cached_query(
        data["query"], user_index_name, k, model_name=model_seg, rag_mode=mode_seg
    )
    if cached_result is not None:
        return sse_response(_cached_sse_events(cached_result.get("result", {})))

    # Strict Chain 模式流式查询（防幻觉）：同步核心进线程泵，async 生成器出帧
    async def event_stream():
        # SSE 白名单：响应已进入流式阶段，无法回退为普通 JSON 错误响应，
        # 生成器内部异常必须 yield 错误事件结束流，保留原错误处理结构
        try:
            yield f"data: {json.dumps({'type': 'start', 'message': '检索中...'})}\n\n"
            full_response = ""

            # RP-01：检索器构建（可能触发 PG engine 建立）经线程池离开主执行器；
            # B-1：构建编排经 build_user_retriever 单一门面（原 _build_stream_retriever
            # 专建函数删除），前置校验已过，None 为并发删除/构建失败防御
            retriever = await sync_to_async(build_user_retriever, thread_sensitive=False)(
                request.user.id, data["index_name"], k
            )
            if retriever is None:
                yield sse_error_event(code="50002", message="索引暂不可用，请稍后重试")
                return
            loop = asyncio.get_running_loop()
            queue: asyncio.Queue = asyncio.Queue()
            worker = threading.Thread(
                target=_pump_strict_rag_events,
                args=(data["query"], k, user_index_name, retriever, loop, queue),
                name="rag-stream-pump",
                daemon=True,
            )
            worker.start()

            sources_payload = None
            degraded_notice = None
            while True:
                event = await queue.get()
                if event is None:
                    break
                if event["type"] == "chunk":
                    full_response += event["content"]
                    yield f"data: {json.dumps({'type': 'chunk', 'content': event['content']})}\n\n"
                elif event["type"] == "heartbeat":
                    yield f"data: {json.dumps({'type': 'heartbeat', 'message': event.get('message', '')})}\n\n"
                elif event["type"] == "sources":
                    sources_payload = event.get("data", [])
                    yield f"data: {json.dumps({'type': 'sources', 'data': event['data']})}\n\n"
                elif event["type"] == "degradation":
                    degraded_notice = event.get("message", "")
                    yield f"data: {json.dumps({'type': 'degradation', 'message': event['message']})}\n\n"
                elif event["type"] == "error":
                    yield sse_error_event(code="50002", message=event["message"])

            logger.info(f"[RAG Chain] 流式生成完成, total_len={len(full_response)}")

            # C-8：流式完成后写回与非流式共享的查询缓存；
            # Task 4/B3：空结果/降级结果不写缓存（与 _finalize_response 同条件）
            if degraded_notice or not full_response:
                logger.info("[RAG Chain] 空结果或降级，跳过缓存写入")
            else:
                result_for_cache = {
                    "answer": full_response,
                    "sources": sources_payload or [],
                    "success": True,
                }
                await sync_to_async(QueryCacheService.cache_query_result)(
                    data["query"],
                    result_for_cache,
                    user_index_name,
                    k,
                    model_name=model_seg,
                    rag_mode=mode_seg,
                )

            yield f"data: {json.dumps({'type': 'end', 'message': '生成完成'})}\n\n"
        except Exception:
            logger.exception("[RAG Chain] 流式生成异常")
            yield sse_error_event(code="50001", message="流式生成失败，请稍后重试")

    return sse_response(event_stream())
