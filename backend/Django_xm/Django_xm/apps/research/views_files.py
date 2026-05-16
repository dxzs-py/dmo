import logging
from urllib.parse import quote

from rest_framework.views import APIView
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from django.http import FileResponse

from Django_xm.apps.common.responses import success_response, error_response, not_found_response
from Django_xm.apps.common.error_codes import ErrorCode
from Django_xm.apps.core.permissions import IsAuthenticatedOrQueryParam
from Django_xm.apps.core.services.file_manager import get_file_manager

from .models import ResearchTask
from .serializers import FileInfoSerializer

logger = logging.getLogger(__name__)
file_manager = get_file_manager()


def _get_user_task_or_error(request, task_id):
    task = ResearchTask.objects.filter(
        task_id=task_id,
        created_by=request.user if request.user.is_authenticated else None,
        is_deleted=False,
    ).first()
    if not task:
        return None, error_response(
            code=ErrorCode.NOT_FOUND,
            message='研究任务不存在或无权访问',
            http_status=status.HTTP_404_NOT_FOUND,
        )
    return task, None


class DeepResearchFilesListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, task_id):
        try:
            task, err = _get_user_task_or_error(request, task_id)
            if err:
                return err

            subdirectory = request.query_params.get('subdirectory')
            files = file_manager.list_task_files(task_id, 'research', subdirectory)

            serializer = FileInfoSerializer([f.to_dict() for f in files], many=True)

            return success_response(
                data={
                    'task_id': task_id,
                    'files': serializer.data,
                    'total': len(files),
                }
            )

        except Exception as e:
            logger.error(f"列出研究文件失败：{e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message='获取文件列表失败',
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class DeepResearchFileDownloadView(APIView):
    permission_classes = [IsAuthenticatedOrQueryParam]

    def get(self, request, task_id, filename):
        try:
            user = request.user if request.user.is_authenticated else None
            if not user:
                return error_response(
                    code=ErrorCode.UNAUTHORIZED,
                    message='未认证',
                    http_status=status.HTTP_401_UNAUTHORIZED,
                )

            task, err = _get_user_task_or_error(request, task_id)
            if err:
                return err

            file_info = file_manager.get_file_info(task_id, filename, 'research')
            if not file_info:
                return not_found_response(message='文件不存在')

            file_path = file_info.path

            content_type = 'application/octet-stream'
            if file_path.suffix.lower() in ['.md', '.txt']:
                content_type = 'text/plain; charset=utf-8'
            elif file_path.suffix.lower() == '.json':
                content_type = 'application/json'
            elif file_path.suffix.lower() == '.pdf':
                content_type = 'application/pdf'

            response = FileResponse(
                open(file_path, 'rb'),
                content_type=content_type,
                as_attachment=True,
                filename=quote(file_path.name),
            )
            return response

        except Exception as e:
            logger.error(f"下载文件失败：{e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message='文件下载失败',
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class DeepResearchFileContentView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, task_id, filename):
        try:
            task, err = _get_user_task_or_error(request, task_id)
            if err:
                return err

            file_info = file_manager.get_file_info(task_id, filename, 'research')
            if not file_info:
                return not_found_response(message='文件不存在')

            content = file_manager.read_file_content(task_id, filename, 'research')

            return success_response(
                data={
                    'filename': filename,
                    'content': content,
                    'file_info': FileInfoSerializer(file_info.to_dict()).data,
                }
            )

        except Exception as e:
            logger.error(f"读取文件内容失败：{e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message='读取文件内容失败',
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class DeepResearchGlobalSearchView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            keyword = request.query_params.get('keyword', '')
            if not keyword:
                return error_response(
                    code=ErrorCode.VALIDATION_FAILED,
                    message='搜索关键词不能为空',
                    http_status=status.HTTP_400_BAD_REQUEST,
                )

            task_type = request.query_params.get('task_type')
            file_types = request.query_params.getlist('file_type')

            files = file_manager.search_files(
                keyword=keyword,
                task_type=task_type,
                file_types=file_types if file_types else None,
            )

            serializer = FileInfoSerializer([f.to_dict() for f in files], many=True)

            return success_response(
                data={
                    'keyword': keyword,
                    'files': serializer.data,
                    'total': len(files),
                }
            )

        except Exception as e:
            logger.error(f"搜索文件失败：{e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message=str(e),
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
