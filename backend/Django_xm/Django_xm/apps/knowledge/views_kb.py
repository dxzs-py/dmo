"""
知识库管理视图

提供知识库 CRUD、文档管理、搜索等接口。
视图层只负责请求解析、服务调用、响应构建。
"""

from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import NotFound
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from Django_xm.apps.core.throttling import KnowledgeRateThrottle, MetaRateThrottle
from Django_xm.common.error_codes import ErrorCode
from Django_xm.common.exceptions import BaseAppError
from Django_xm.common.pagination import paginate_to_dict
from Django_xm.common.responses import success_response
from Django_xm.common.serializers import EmptySerializer

from .exceptions import KnowledgeBaseAlreadyExistsError
from .serializers import (
    CreateKnowledgeBaseSerializer,
    KnowledgeBaseSearchSerializer,
    UpdateKnowledgeBaseSerializer,
)
from .services.kb_service import (
    create_knowledge_base,
    delete_document,
    delete_knowledge_base,
    get_knowledge_base_detail,
    list_documents,
    list_knowledge_bases,
    save_uploaded_files,
    search_knowledge_base,
    update_knowledge_base,
)


class KnowledgeBaseListView(APIView):
    permission_classes = [IsAuthenticated]

    # 页面加载即请求的只读列表接口，独立 meta 额度（Task 3.2）
    throttle_classes = [MetaRateThrottle]

    @extend_schema(responses={200: EmptySerializer})
    def get(self, request):
        user_indexes = list_knowledge_bases(request.user)
        paginated = paginate_to_dict(user_indexes, request)
        return success_response(data=paginated)

    @extend_schema(request=CreateKnowledgeBaseSerializer, responses={200: EmptySerializer})
    def post(self, request):
        serializer = CreateKnowledgeBaseSerializer(data=request.data)
        if not serializer.is_valid():
            field, errors = next(iter(serializer.errors.items()))
            raise BaseAppError(f"{field}: {errors[0]}", business_code=ErrorCode.INVALID_PARAMS)
        name = serializer.validated_data["name"]
        description = serializer.validated_data.get("description", "")

        try:
            result = create_knowledge_base(request.user, name, description)
            if result.get("existing"):
                return success_response(data=result, message="知识库已存在")
            return success_response(data=result, message="知识库创建成功")
        except KnowledgeBaseAlreadyExistsError as e:
            # 领域异常翻译：service 抛出的异常未携带 business_code，需在视图边界
            # 转为统一业务码（DUPLICATE_RESOURCE 40901/409），否则冒泡后按 500 兜底
            raise BaseAppError(str(e), business_code=ErrorCode.DUPLICATE_RESOURCE) from e
        except ValueError as e:
            raise BaseAppError(str(e), business_code=ErrorCode.INVALID_PARAMS) from e


class KnowledgeBaseDetailView(APIView):
    permission_classes = [IsAuthenticated]

    # 显式 operation_id：避免与集合视图 KnowledgeBaseListView 的自动生成 id 冲突（W001）
    @extend_schema(operation_id="knowledge_knowledge_base_detail", responses={200: EmptySerializer})
    def get(self, request, kb_id):
        try:
            data = get_knowledge_base_detail(request.user, kb_id)
            return success_response(data=data)
        except FileNotFoundError as e:
            # service 层 FileNotFoundError 翻译为 DRF NotFound（40401/404）
            raise NotFound(str(e)) from e

    @extend_schema(request=UpdateKnowledgeBaseSerializer, responses={200: EmptySerializer})
    def patch(self, request, kb_id):
        serializer = UpdateKnowledgeBaseSerializer(data=request.data)
        if not serializer.is_valid():
            field, errors = next(iter(serializer.errors.items()))
            raise BaseAppError(f"{field}: {errors[0]}", business_code=ErrorCode.INVALID_PARAMS)
        description = serializer.validated_data.get("description", "")

        try:
            data = update_knowledge_base(request.user, kb_id, description)
            return success_response(data=data, message="知识库更新成功")
        except FileNotFoundError as e:
            raise NotFound(str(e)) from e

    @extend_schema(responses={200: EmptySerializer})
    def delete(self, request, kb_id):
        try:
            delete_knowledge_base(request.user, kb_id)
            return success_response(message="知识库删除成功")
        except FileNotFoundError as e:
            raise NotFound(str(e)) from e


class KnowledgeBaseDocumentListView(APIView):
    permission_classes = [IsAuthenticated]
    # 上传（POST）为重操作，独立 knowledge 额度；GET 列表不受影响
    throttle_classes = [KnowledgeRateThrottle]
    # POST 上传走 multipart/form；GET 无请求体，不受 parser 影响
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    @extend_schema(responses={200: EmptySerializer})
    def get(self, request, kb_id):
        try:
            files = list_documents(request.user, kb_id)
            paginated = paginate_to_dict(files, request)
            headers = {
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            }
            return success_response(data=paginated, headers=headers)
        except FileNotFoundError as e:
            raise NotFound(str(e)) from e

    @extend_schema(request=EmptySerializer, responses={200: EmptySerializer})
    def post(self, request, kb_id):
        files = request.FILES.getlist("files")

        if not files:
            raise BaseAppError("请选择要上传的文件", business_code=ErrorCode.INVALID_PARAMS)

        try:
            # 同步快操作：仅保存文件落盘，返回秒级响应
            saved_files = save_uploaded_files(request.user, kb_id, files)
        except FileNotFoundError as e:
            raise NotFound(str(e)) from e
        except ValueError as e:
            raise BaseAppError(str(e), business_code=ErrorCode.INVALID_PARAMS) from e

        # 提交 Celery 异步任务：文档加载/分块/向量化在 worker 中执行，
        # 进度通过 WebSocket task:{task_id} 频道实时推送（前端 subscribeTask 订阅）
        file_names = [f["name"] for f in saved_files]

        try:
            from Django_xm.apps.core.task_redis_manager import TaskType, get_task_manager
            from Django_xm.tasks.rag_tasks import upload_documents_task

            task_manager = get_task_manager()
            task_id = task_manager.create_task(
                task_type=TaskType.RAG_UPLOAD,
                user_id=request.user.id,
                task_name=f"上传文档到知识库: {kb_id}",
                task_params={"kb_id": kb_id, "file_names": file_names},
            )
            celery_result = upload_documents_task.delay(
                user_id=request.user.id,
                kb_id=kb_id,
                file_names=file_names,
                task_id=task_id,
            )
            task_manager.update_task_status(task_id, {"celery_task_id": celery_result.id})
        except Exception as e:
            # Celery 队列不可用属服务级故障：特定业务错误翻译，保留 except 结构
            # （原 code=50001/http=503 不一致，统一为 SERVICE_UNAVAILABLE 50301/503，
            # 保留 HTTP 503 语义，code 由 50001 变更为 50301，见改造报告）
            raise BaseAppError(
                "上传任务队列不可用，请确认 Celery worker 已启动",
                business_code=ErrorCode.SERVICE_UNAVAILABLE,
            ) from e

        return success_response(
            data={
                "task_id": task_id,
                "status": "pending",
                "files": saved_files,
            },
            message="文档已接收，正在后台处理",
        )


class KnowledgeBaseDocumentDeleteView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: EmptySerializer})
    def delete(self, request, kb_id, filename):
        try:
            result = delete_document(request.user, kb_id, filename)
            return success_response(data=result, message="操作成功")
        except FileNotFoundError as e:
            raise NotFound(str(e)) from e


class KnowledgeBaseSearchView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=KnowledgeBaseSearchSerializer, responses={200: EmptySerializer})
    def post(self, request, kb_id):
        serializer = KnowledgeBaseSearchSerializer(data=request.data)
        if not serializer.is_valid():
            field, errors = next(iter(serializer.errors.items()))
            raise BaseAppError(f"{field}: {errors[0]}", business_code=ErrorCode.INVALID_PARAMS)
        query = serializer.validated_data["query"]
        top_k = serializer.validated_data.get("top_k", 5)

        try:
            results = search_knowledge_base(request.user, kb_id, query, top_k)
            return success_response(data={"results": results})
        except FileNotFoundError as e:
            raise NotFound(str(e)) from e
        except ValueError as e:
            raise BaseAppError(str(e), business_code=ErrorCode.INVALID_PARAMS) from e
