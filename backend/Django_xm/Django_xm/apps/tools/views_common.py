"""tools app 视图层共享工具。

抽出 `StandardPagination` / `_handle_crud_result` / `ERROR_CODE_TO_STATUS`
供 `views_mcp.py` 与未来其他视图模块复用，消除 `views_custom.py` 与
`views_mcp.py` 之间的重复定义（Task 14 死代码清理）。
"""

import logging

from rest_framework.pagination import PageNumberPagination

from Django_xm.common.responses import error_response, success_response

logger = logging.getLogger(__name__)


class StandardPagination(PageNumberPagination):
    """tools app 通用分页器。

    仅当请求带 `page` 参数时启用分页，否则返回全量数据（兼容既有调用方）。
    """

    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100

    def paginate_queryset(self, queryset, request, view=None):
        if 'page' not in request.query_params:
            return None
        return super().paginate_queryset(queryset, request, view)


# Manager 返回的 error_code 到 HTTP 状态码的映射
ERROR_CODE_TO_STATUS = {'FORBIDDEN': 403, 'NOT_FOUND': 404, 'VALIDATION': 400}


def _handle_crud_result(result, success_msg="操作成功", data=None):
    """统一处理 Manager 返回结果。

    Args:
        result: Manager 返回的 dict，包含 ``success`` / ``message`` / ``data`` / ``error_code``
        success_msg: 成功时的响应消息
        data: 显式覆盖响应数据（不为 None 时使用该值）

    Returns:
        DRF Response：成功返回 ``success_response``，失败返回 ``error_response`` + 对应 HTTP 状态码
    """
    if result.get('success'):
        response_data = data if data is not None else result.get('data')
        return success_response(message=success_msg, data=response_data)
    error_code = result.get('error_code', 'UNKNOWN')
    http_status = ERROR_CODE_TO_STATUS.get(error_code, 400)
    return error_response(message=result.get('message', '操作失败'), http_status=http_status)
