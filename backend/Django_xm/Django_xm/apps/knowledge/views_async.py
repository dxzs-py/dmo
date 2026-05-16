"""
异步 RAG 操作视图

提供异步索引创建、文档上传、索引删除接口。
"""

import logging
from pathlib import Path

from rest_framework.views import APIView
from rest_framework import status
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.permissions import IsAuthenticated

from Django_xm.apps.core.throttling import KnowledgeRateThrottle

from Django_xm.apps.common.responses import (
    success_response, error_response, not_found_response,
)
from Django_xm.apps.common.error_codes import ErrorCode
from Django_xm.apps.ai_engine.config import settings as app_cfg
from Django_xm.tasks.rag_tasks import (
    create_index_task,
    add_documents_to_index_task,
    delete_index_task,
)
from Django_xm.apps.common.task_manager import get_task_manager, TaskType

from .services.index_service import IndexManager
from .views_utils import get_user_index_name

logger = logging.getLogger(__name__)


class AsyncRAGIndexCreateView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [KnowledgeRateThrottle]

    def post(self, request):
        name = request.data.get('name')
        description = request.data.get('description', '')
        directory_path = request.data.get('directory_path')
        chunk_size = request.data.get('chunk_size')
        chunk_overlap = request.data.get('chunk_overlap')
        overwrite = request.data.get('overwrite', False)

        if not name:
            return error_response(
                code=ErrorCode.INVALID_PARAMS,
                message='索引名称不能为空',
                http_status=status.HTTP_400_BAD_REQUEST
            )

        try:
            user = request.user
            user_index_name = get_user_index_name(user, name)

            manager = IndexManager()
            if manager.index_exists(user_index_name) and not overwrite:
                return error_response(
                    code=ErrorCode.DUPLICATE_RESOURCE,
                    message=f'索引已存在: {name}。使用 overwrite=true 来覆盖。',
                    http_status=status.HTTP_409_CONFLICT
                )

            task_manager = get_task_manager()
            task_id = task_manager.create_task(
                task_type=TaskType.RAG_INDEX,
                user_id=user.id,
                task_name=f'创建索引: {name}',
                task_params={
                    'name': user_index_name,
                    'original_name': name,
                    'description': description,
                    'directory_path': directory_path,
                    'chunk_size': chunk_size,
                    'chunk_overlap': chunk_overlap,
                    'overwrite': overwrite,
                }
            )

            celery_result = create_index_task.delay(
                task_id=task_id,
                index_name=user_index_name,
                original_name=name,
                user_id=user.id,
                description=description,
                directory_path=directory_path,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                overwrite=overwrite,
            )
            task_manager.update_task_status(task_id, {
                'celery_task_id': celery_result.id,
            })

            return success_response(
                data={'task_id': task_id},
                message='索引创建任务已提交，请稍后查询任务状态'
            )

        except Exception as e:
            logger.error(f"异步创建索引失败: {e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message=str(e),
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class AsyncRAGDocumentUploadView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [KnowledgeRateThrottle]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, name):
        try:
            user = request.user
            user_index_name = get_user_index_name(user, name)
            manager = IndexManager()

            if not manager.index_exists(user_index_name):
                return not_found_response(message=f'索引不存在: {name}')

            files = request.FILES.getlist('files')
            if not files and 'file' in request.FILES:
                files = [request.FILES['file']]
            if not files:
                return error_response(
                    code=ErrorCode.INVALID_PARAMS,
                    message='没有上传文件',
                    http_status=status.HTTP_400_BAD_REQUEST
                )

            upload_dir = Path(app_cfg.data_uploads_path) / user_index_name
            upload_dir.mkdir(parents=True, exist_ok=True)

            file_paths = []
            for uploaded_file in files:
                file_path = upload_dir / uploaded_file.name
                with open(file_path, 'wb') as f:
                    for chunk in uploaded_file.chunks():
                        f.write(chunk)
                file_paths.append(str(file_path))

            task_name = f'添加文档: {", ".join(f.name for f in files)}'

            task_manager = get_task_manager()
            task_id = task_manager.create_task(
                task_type=TaskType.RAG_ADD_DOCS,
                user_id=user.id,
                task_name=task_name,
                task_params={
                    'index_name': user_index_name,
                    'original_name': name,
                    'file_paths': file_paths,
                }
            )

            celery_result = add_documents_to_index_task.delay(
                task_id=task_id,
                index_name=user_index_name,
                original_name=name,
                user_id=user.id,
                file_paths=file_paths,
            )
            task_manager.update_task_status(task_id, {
                'celery_task_id': celery_result.id,
            })

            return success_response(
                data={'task_id': task_id},
                message='文档添加任务已提交，请稍后查询任务状态'
            )

        except Exception as e:
            logger.error(f"异步上传文档失败: {e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message=str(e),
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class AsyncRAGIndexDeleteView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [KnowledgeRateThrottle]

    def delete(self, request, name):
        try:
            user = request.user
            user_index_name = get_user_index_name(user, name)
            manager = IndexManager()

            if not manager.index_exists(user_index_name):
                return not_found_response(message=f'索引不存在: {name}')

            task_manager = get_task_manager()
            task_id = task_manager.create_task(
                task_type=TaskType.RAG_DELETE_INDEX,
                user_id=user.id,
                task_name=f'删除索引: {name}',
                task_params={
                    'name': user_index_name,
                    'original_name': name,
                }
            )

            celery_result = delete_index_task.delay(
                task_id=task_id,
                index_name=user_index_name,
                original_name=name,
                user_id=user.id,
            )
            task_manager.update_task_status(task_id, {
                'celery_task_id': celery_result.id,
            })

            return success_response(
                data={'task_id': task_id},
                message='索引删除任务已提交，请稍后查询任务状态'
            )

        except Exception as e:
            logger.error(f"异步删除索引失败: {e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message=str(e),
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
