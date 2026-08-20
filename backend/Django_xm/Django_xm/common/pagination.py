"""项目统一分页器。

所有分页列表端点经 ProjectPagination 返回统一结构：
success_response(data={items, total, page, page_size, total_pages})
（dj-15：消除全局配置形同虚设与三套手写平行实现）
"""

from django.core.paginator import Paginator
from rest_framework.pagination import PageNumberPagination

from Django_xm.common.responses import success_response


class ProjectPagination(PageNumberPagination):
    """项目统一分页器（queryset 与 Python list 均可分页）。

    查询参数：page（页码，默认 1）、page_size（每页条数，默认 20，上限 100）。
    """

    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100

    def get_paginated_response(self, data):
        """返回统一 {code, message, data} 包裹的分页响应。"""
        return success_response(
            data={
                "items": data,
                "total": self.page.paginator.count,
                "page": self.page.number,
                "page_size": self.get_page_size(self.request),
                "total_pages": self.page.paginator.num_pages,
            }
        )


def paginate_to_dict(data_list, request, page_size=None):
    """用 ProjectPagination 分页并返回统一 dict（items/total/page/page_size/total_pages）。

    供 APIView 风格视图复用（响应结构等价于 get_paginated_response 的 data 部分）。
    page_size 为默认页大小；查询参数 page_size 优先生效（受 max_page_size 上限约束）。
    页码处理直接透传 Django Paginator.get_page 原生语义（单一权威）：
    非数字 → 第 1 页；page < 1 或越界 → 最后一页。
    """
    paginator = ProjectPagination()
    if page_size is not None:
        paginator.page_size = page_size
    effective_size = paginator.get_page_size(request)
    page_obj = Paginator(data_list, effective_size).get_page(request.query_params.get("page", 1))
    return {
        "items": page_obj.object_list,
        "total": page_obj.paginator.count,
        "page": page_obj.number,
        "page_size": effective_size,
        "total_pages": page_obj.paginator.num_pages,
    }
