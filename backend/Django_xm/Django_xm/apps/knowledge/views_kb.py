"""
知识库管理视图

提供知识库 CRUD、文档管理、搜索等接口。
视图层只负责请求解析、服务调用、响应构建。
"""

import logging

from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from Django_xm.apps.core.throttling import KnowledgeRateThrottle
from Django_xm.common.error_codes import ErrorCode
from Django_xm.common.responses import (
    error_response,
    not_found_response,
    success_response,
)

from .services.kb_service import (
    create_knowledge_base,
    delete_document,
    delete_knowledge_base,
    get_knowledge_base_detail,
    list_documents,
    list_knowledge_bases,
    search_knowledge_base,
    update_knowledge_base,
    upload_documents,
)

logger = logging.getLogger(__name__)


def _paginate_list(items, page, page_size):
    total = len(items)
    start = (page - 1) * page_size
    end = start + page_size
    return {
        'items': items[start:end],
        'total': total,
        'page': page,
        'page_size': page_size,
        'total_pages': (total + page_size - 1) // page_size if total > 0 else 0,
    }


def _parse_page_params(request):
    try:
        page = max(int(request.query_params.get('page', 1)), 1)
    except (ValueError, TypeError):
        page = 1
    try:
        page_size = max(min(int(request.query_params.get('page_size', 20)), 100), 1)
    except (ValueError, TypeError):
        page_size = 20
    return page, page_size


class KnowledgeBaseListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            page, page_size = _parse_page_params(request)
            user_indexes = list_knowledge_bases(request.user)
            paginated = _paginate_list(user_indexes, page, page_size)
            return success_response(data=paginated)
        except Exception as e:
            logger.error(f"获取知识库列表失败: {e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message=str(e),
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    def post(self, request):
        name = request.data.get('name')
        description = request.data.get('description', '')

        if not name:
            return error_response(
                code=ErrorCode.INVALID_PARAMS,
                message='知识库名称不能为空',
                http_status=status.HTTP_400_BAD_REQUEST
            )

        try:
            result = create_knowledge_base(request.user, name, description)
            if result.pop('existing', False):
                return success_response(data=result, message='知识库已存在')
            return success_response(data=result, message='知识库创建成功')
        except ValueError as e:
            if '已存在' in str(e):
                return error_response(
                    code=ErrorCode.DUPLICATE_RESOURCE,
                    message=str(e),
                    http_status=status.HTTP_409_CONFLICT
                )
            return error_response(
                code=ErrorCode.INVALID_PARAMS,
                message=str(e),
                http_status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            logger.error(f"创建知识库失败: {e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message=str(e),
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class KnowledgeBaseDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, kb_id):
        try:
            data = get_knowledge_base_detail(request.user, kb_id)
            return success_response(data=data)
        except FileNotFoundError as e:
            return not_found_response(message=str(e))
        except Exception as e:
            logger.error(f"获取知识库详情失败: {e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message='获取知识库详情失败',
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    def patch(self, request, kb_id):
        description = request.data.get('description', '')

        try:
            data = update_knowledge_base(request.user, kb_id, description)
            return success_response(data=data, message='知识库更新成功')
        except FileNotFoundError as e:
            return not_found_response(message=str(e))
        except Exception as e:
            logger.error(f"更新知识库失败: {e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message='更新知识库失败',
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    def delete(self, request, kb_id):
        try:
            delete_knowledge_base(request.user, kb_id)
            return success_response(message='知识库删除成功')
        except FileNotFoundError as e:
            return not_found_response(message=str(e))
        except Exception as e:
            logger.error(f"删除知识库失败: {e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message='删除知识库失败',
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class KnowledgeBaseDocumentListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, kb_id):
        try:
            page, page_size = _parse_page_params(request)
            files = list_documents(request.user, kb_id)
            paginated = _paginate_list(files, page, page_size)
            headers = {
                'Cache-Control': 'no-cache, no-store, must-revalidate',
                'Pragma': 'no-cache',
                'Expires': '0',
            }
            return success_response(data=paginated, headers=headers)
        except FileNotFoundError as e:
            return not_found_response(message=str(e))
        except Exception as e:
            logger.error(f"获取文档列表失败: {e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message=str(e),
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class KnowledgeBaseUploadView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [KnowledgeRateThrottle]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, kb_id):
        files = request.FILES.getlist('files')

        if not files:
            return error_response(
                code=ErrorCode.INVALID_PARAMS,
                message='请选择要上传的文件',
                http_status=status.HTTP_400_BAD_REQUEST
            )

        try:
            result = upload_documents(request.user, kb_id, files)
            return success_response(data=result, message='文档上传成功')
        except FileNotFoundError as e:
            return not_found_response(message=str(e))
        except ValueError as e:
            return error_response(
                code=ErrorCode.INVALID_PARAMS,
                message=str(e),
                http_status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            logger.error(f"上传文档失败: {e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message=str(e),
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class KnowledgeBaseDocumentDeleteView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, kb_id, filename):
        try:
            result = delete_document(request.user, kb_id, filename)
            return success_response(data=result, message='操作成功')
        except FileNotFoundError as e:
            return not_found_response(message=str(e))
        except Exception as e:
            logger.error(f"删除文档失败: {e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message='删除文档失败',
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class KnowledgeBaseSearchView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, kb_id):
        query = request.data.get('query', '')
        top_k = request.data.get('top_k', 5)

        if not query:
            return error_response(
                code=ErrorCode.INVALID_PARAMS,
                message='查询内容不能为空',
                http_status=status.HTTP_400_BAD_REQUEST
            )

        try:
            results = search_knowledge_base(request.user, kb_id, query, top_k)
            return success_response(data={'results': results})
        except FileNotFoundError as e:
            return not_found_response(message=str(e))
        except ValueError as e:
            return error_response(
                code=ErrorCode.INVALID_PARAMS,
                message=str(e),
                http_status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            logger.error(f"检索测试失败: {e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message=str(e),
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
