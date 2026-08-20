"""
知识库管理视图

提供知识库 CRUD、文档管理、搜索等接口。
视图层只负责请求解析、服务调用、响应构建。
"""

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from Django_xm.apps.core.logging_utils import get_logger
from Django_xm.apps.core.throttling import KnowledgeRateThrottle, MetaRateThrottle
from Django_xm.common.error_codes import ErrorCode
from Django_xm.common.pagination import paginate_to_dict
from Django_xm.common.responses import (
    error_response,
    not_found_response,
    success_response,
)
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

logger = get_logger(__name__)


class KnowledgeBaseListView(APIView):
    permission_classes = [IsAuthenticated]

    # 页面加载即请求的只读列表接口，独立 meta 额度（Task 3.2）
    throttle_classes = [MetaRateThrottle]

    @extend_schema(responses={200: EmptySerializer})
    def get(self, request):
        try:
            user_indexes = list_knowledge_bases(request.user)
            paginated = paginate_to_dict(user_indexes, request)
            return success_response(data=paginated)
        except Exception:
            logger.exception("获取知识库列表失败")
            return error_response(
                code=ErrorCode.SERVER_ERROR, message="获取知识库列表失败", http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @extend_schema(request=CreateKnowledgeBaseSerializer, responses={200: EmptySerializer})
    def post(self, request):
        serializer = CreateKnowledgeBaseSerializer(data=request.data)
        if not serializer.is_valid():
            field, errors = next(iter(serializer.errors.items()))
            return error_response(
                code=ErrorCode.INVALID_PARAMS,
                message=f"{field}: {errors[0]}",
                http_status=status.HTTP_400_BAD_REQUEST,
            )
        name = serializer.validated_data["name"]
        description = serializer.validated_data.get("description", "")

        try:
            result = create_knowledge_base(request.user, name, description)
            if result.get("existing"):
                return success_response(data=result, message="知识库已存在")
            return success_response(data=result, message="知识库创建成功")
        except KnowledgeBaseAlreadyExistsError as e:
            return error_response(
                code=ErrorCode.DUPLICATE_RESOURCE, message=str(e), http_status=status.HTTP_409_CONFLICT
            )
        except ValueError as e:
            return error_response(
                code=ErrorCode.INVALID_PARAMS, message=str(e), http_status=status.HTTP_400_BAD_REQUEST
            )
        except Exception:
            logger.exception("创建知识库失败")
            return error_response(
                code=ErrorCode.SERVER_ERROR, message="创建知识库失败", http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class KnowledgeBaseDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: EmptySerializer})
    def get(self, request, kb_id):
        try:
            data = get_knowledge_base_detail(request.user, kb_id)
            return success_response(data=data)
        except FileNotFoundError as e:
            return not_found_response(message=str(e))
        except Exception:
            logger.exception("获取知识库详情失败")
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message="获取知识库详情失败",
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(request=UpdateKnowledgeBaseSerializer, responses={200: EmptySerializer})
    def patch(self, request, kb_id):
        serializer = UpdateKnowledgeBaseSerializer(data=request.data)
        if not serializer.is_valid():
            field, errors = next(iter(serializer.errors.items()))
            return error_response(
                code=ErrorCode.INVALID_PARAMS,
                message=f"{field}: {errors[0]}",
                http_status=status.HTTP_400_BAD_REQUEST,
            )
        description = serializer.validated_data.get("description", "")

        try:
            data = update_knowledge_base(request.user, kb_id, description)
            return success_response(data=data, message="知识库更新成功")
        except FileNotFoundError as e:
            return not_found_response(message=str(e))
        except Exception:
            logger.exception("更新知识库失败")
            return error_response(
                code=ErrorCode.SERVER_ERROR, message="更新知识库失败", http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @extend_schema(responses={200: EmptySerializer})
    def delete(self, request, kb_id):
        try:
            delete_knowledge_base(request.user, kb_id)
            return success_response(message="知识库删除成功")
        except FileNotFoundError as e:
            return not_found_response(message=str(e))
        except Exception:
            logger.exception("删除知识库失败")
            return error_response(
                code=ErrorCode.SERVER_ERROR, message="删除知识库失败", http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


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
            return not_found_response(message=str(e))
        except Exception:
            logger.exception("获取文档列表失败")
            return error_response(
                code=ErrorCode.SERVER_ERROR, message="获取文档列表失败", http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @extend_schema(request=EmptySerializer, responses={200: EmptySerializer})
    def post(self, request, kb_id):
        files = request.FILES.getlist("files")

        if not files:
            return error_response(
                code=ErrorCode.INVALID_PARAMS, message="请选择要上传的文件", http_status=status.HTTP_400_BAD_REQUEST
            )

        try:
            # 同步快操作：仅保存文件落盘，返回秒级响应
            saved_files = save_uploaded_files(request.user, kb_id, files)
        except FileNotFoundError as e:
            return not_found_response(message=str(e))
        except ValueError as e:
            return error_response(
                code=ErrorCode.INVALID_PARAMS, message=str(e), http_status=status.HTTP_400_BAD_REQUEST
            )

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
        except Exception:
            logger.exception("提交知识库上传任务失败")
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message="上传任务队列不可用，请确认 Celery worker 已启动",
                http_status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

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
            return not_found_response(message=str(e))
        except Exception:
            logger.exception("删除文档失败")
            return error_response(
                code=ErrorCode.SERVER_ERROR, message="删除文档失败", http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class KnowledgeBaseSearchView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=KnowledgeBaseSearchSerializer, responses={200: EmptySerializer})
    def post(self, request, kb_id):
        serializer = KnowledgeBaseSearchSerializer(data=request.data)
        if not serializer.is_valid():
            field, errors = next(iter(serializer.errors.items()))
            return error_response(
                code=ErrorCode.INVALID_PARAMS,
                message=f"{field}: {errors[0]}",
                http_status=status.HTTP_400_BAD_REQUEST,
            )
        query = serializer.validated_data["query"]
        top_k = serializer.validated_data.get("top_k", 5)

        try:
            results = search_knowledge_base(request.user, kb_id, query, top_k)
            return success_response(data={"results": results})
        except FileNotFoundError as e:
            return not_found_response(message=str(e))
        except ValueError as e:
            return error_response(
                code=ErrorCode.INVALID_PARAMS, message=str(e), http_status=status.HTTP_400_BAD_REQUEST
            )
        except Exception:
            logger.exception("检索测试失败")
            return error_response(
                code=ErrorCode.SERVER_ERROR, message="检索测试失败", http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
