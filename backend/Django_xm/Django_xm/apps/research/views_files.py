import logging
from urllib.parse import quote

from django.http import FileResponse
from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from Django_xm.apps.core.permissions import IsAuthenticatedOrQueryParam
from Django_xm.apps.core.services.file_manager import get_file_manager
from Django_xm.common.error_codes import ErrorCode
from Django_xm.common.exceptions import BaseAppError
from Django_xm.common.responses import success_response
from Django_xm.common.serializers import EmptySerializer, FileInfoSerializer

from .models import ResearchTask

logger = logging.getLogger(__name__)
file_manager = get_file_manager()


def _get_user_task(request, task_id):
    """获取当前用户的研究任务，不存在或无权访问时抛出 NotFound。"""
    task = ResearchTask.objects.filter(
        task_id=task_id,
        created_by=request.user if request.user.is_authenticated else None,
        is_deleted=False,
    ).first()
    if not task:
        raise NotFound("研究任务不存在或无权访问")
    return task


class DeepResearchFilesListView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: EmptySerializer})
    def get(self, request, task_id):
        _get_user_task(request, task_id)

        subdirectory = request.query_params.get("subdirectory")
        files = file_manager.list_task_files(task_id, "research", subdirectory)

        serializer = FileInfoSerializer([f.to_dict() for f in files], many=True)

        return success_response(
            data={
                "task_id": task_id,
                "files": serializer.data,
                "total": len(files),
            }
        )


class DeepResearchFileDownloadView(APIView):
    permission_classes = [IsAuthenticatedOrQueryParam]

    @extend_schema(responses={200: EmptySerializer})
    def get(self, request, task_id, filename):
        user = request.user if request.user.is_authenticated else None
        if not user:
            raise BaseAppError("未认证", business_code=ErrorCode.UNAUTHORIZED)

        _get_user_task(request, task_id)

        file_info = file_manager.get_file_info(task_id, filename, "research")
        if not file_info:
            raise NotFound("文件不存在")

        file_path = file_info.path

        content_type = "application/octet-stream"
        if file_path.suffix.lower() in [".md", ".txt"]:
            content_type = "text/plain; charset=utf-8"
        elif file_path.suffix.lower() == ".json":
            content_type = "application/json"
        elif file_path.suffix.lower() == ".pdf":
            content_type = "application/pdf"

        # 说明：FileResponse 惰性读取文件，响应关闭时自动关闭句柄，
        # 若用 with 提前关闭会导致流式读取失败，故保持 Django 官方 open() 模式。
        response = FileResponse(
            open(file_path, "rb"),  # noqa: SIM115
            content_type=content_type,
            as_attachment=True,
            filename=quote(file_path.name),
        )
        return response


class DeepResearchFileContentView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: EmptySerializer})
    def get(self, request, task_id, filename):
        _get_user_task(request, task_id)

        file_info = file_manager.get_file_info(task_id, filename, "research")
        if not file_info:
            raise NotFound("文件不存在")

        content = file_manager.read_file_content(task_id, filename, "research")

        return success_response(
            data={
                "filename": filename,
                "content": content,
                "file_info": FileInfoSerializer(file_info.to_dict()).data,
            }
        )


class DeepResearchGlobalSearchView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: EmptySerializer})
    def get(self, request):
        keyword = request.query_params.get("keyword", "")
        if not keyword:
            raise BaseAppError("搜索关键词不能为空", business_code=ErrorCode.VALIDATION_FAILED)

        task_type = request.query_params.get("task_type")
        file_types = request.query_params.getlist("file_type")

        # Task 15.4: 调用方负责解析 user_id → task_ids
        # （file_manager 不再直接依赖 research app）
        from Django_xm.apps.research.services.cross_app import get_user_research_task_ids

        user_task_ids = get_user_research_task_ids(request.user.id)

        files = file_manager.search_files(
            keyword=keyword,
            task_type=task_type,
            file_types=file_types if file_types else None,
            task_ids=user_task_ids,
        )

        serializer = FileInfoSerializer([f.to_dict() for f in files], many=True)

        return success_response(
            data={
                "keyword": keyword,
                "files": serializer.data,
                "total": len(files),
            }
        )
