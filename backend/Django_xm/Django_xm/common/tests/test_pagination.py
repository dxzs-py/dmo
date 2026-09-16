"""ProjectPagination 单元测试。"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from Django_xm.common.pagination import ProjectPagination

User = get_user_model()


def _make_request(query_string: str = "") -> Request:
    """构造带 query_params 的 DRF Request。"""
    factory = APIRequestFactory()
    return Request(factory.get(f"/?{query_string}"))


class ProjectPaginationTests(TestCase):
    """ProjectPagination 分页行为与响应结构测试。"""

    def test_paginate_queryset(self) -> None:
        """page=2&page_size=10：返回第二页 10 条，响应含统一分页元数据。"""
        items = list(range(25))
        request = _make_request("page=2&page_size=10")
        paginator = ProjectPagination()

        page_items = paginator.paginate_queryset(items, request)

        self.assertEqual(len(page_items), 10)
        self.assertEqual(page_items[0], 10)

        response = paginator.get_paginated_response(page_items)
        self.assertEqual(response.data["code"], 200)
        self.assertEqual(response.data["data"]["items"], page_items)
        self.assertEqual(response.data["data"]["total"], 25)
        self.assertEqual(response.data["data"]["page"], 2)
        self.assertEqual(response.data["data"]["page_size"], 10)
        self.assertEqual(response.data["data"]["total_pages"], 3)

    def test_default_page_size(self) -> None:
        """无 page_size 参数：默认每页 20 条。"""
        items = list(range(25))
        request = _make_request("page=1")
        paginator = ProjectPagination()

        page_items = paginator.paginate_queryset(items, request)

        self.assertEqual(len(page_items), 20)
        response = paginator.get_paginated_response(page_items)
        self.assertEqual(response.data["data"]["page_size"], 20)
        self.assertEqual(response.data["data"]["total_pages"], 2)

    def test_max_page_size(self) -> None:
        """page_size=500 超上限：被截断为 100。"""
        items = list(range(120))
        request = _make_request("page=1&page_size=500")
        paginator = ProjectPagination()

        page_items = paginator.paginate_queryset(items, request)

        self.assertEqual(len(page_items), 100)
        response = paginator.get_paginated_response(page_items)
        self.assertEqual(response.data["data"]["page_size"], 100)
        self.assertEqual(response.data["data"]["total"], 120)

    def test_queryset_input(self) -> None:
        """queryset 输入：与 list 走同一分页流程。"""
        User.objects.create(username="alice")
        User.objects.create(username="bob")
        User.objects.create(username="carol")
        request = _make_request("page=1&page_size=2")
        paginator = ProjectPagination()

        page_items = paginator.paginate_queryset(User.objects.order_by("id"), request)

        self.assertEqual(len(page_items), 2)
        response = paginator.get_paginated_response(page_items)
        self.assertEqual(response.data["data"]["total"], 3)
        self.assertEqual(response.data["data"]["total_pages"], 2)
