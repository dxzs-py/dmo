"""research app 路由规范化防回归测试（dj-16）。

覆盖：
- 旧路由已死：GET /result/<task_id>/、DELETE /task/<task_id>/ 均不再匹配任何路由 → 404
- 新路由已注册：tasks_delete / tasks_retry_subagent 可 reverse 解析
- 旧路由 name（task_delete / retry_subagent / result）已注销，reverse 抛 NoReverseMatch

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.research.tests.test_route_normalization --noinput
"""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import NoReverseMatch, reverse
from rest_framework.test import APIClient

RESEARCH_URL = "/api/v1/research/"


class ResearchRouteNormalizationTests(TestCase):
    """dj-16：research 旧单数/动词路由移除，新资源路由注册。"""

    def setUp(self):
        self.client = APIClient()
        self.user = get_user_model().objects.create_user(username="research-route", password="pw123456")
        self.client.force_authenticate(user=self.user)

    def test_old_result_route_unavailable(self):
        """旧单数 result 路由不可达（results/ 为唯一结果端点）→ 404。"""
        resp = self.client.get(f"{RESEARCH_URL}result/some-task-id/")
        self.assertEqual(resp.status_code, 404, resp.content)

    def test_old_task_delete_route_unavailable(self):
        """旧单数 task 删除路由不可达（tasks/<task_id>/ 为唯一删除端点）→ 404。"""
        resp = self.client.delete(f"{RESEARCH_URL}task/some-task-id/")
        self.assertEqual(resp.status_code, 404, resp.content)

    def test_new_route_names_resolvable(self):
        """新路由 name 可 reverse 解析（路由已注册）。"""
        self.assertEqual(reverse("research:tasks_delete", kwargs={"task_id": "t1"}), f"{RESEARCH_URL}tasks/t1/")
        self.assertEqual(
            reverse("research:tasks_retry_subagent", kwargs={"task_id": "t1"}),
            f"{RESEARCH_URL}tasks/t1/retry-subagent/",
        )

    def test_old_route_names_removed(self):
        """旧路由 name 已注销，reverse 抛 NoReverseMatch。"""
        cases = [
            ("research:task_delete", {"task_id": "t1"}),
            ("research:retry_subagent", {"task_id": "t1"}),
            ("research:result", {"task_id": "t1"}),
        ]
        for name, kwargs in cases:
            with self.subTest(name=name):
                with self.assertRaises(NoReverseMatch):
                    reverse(name, kwargs=kwargs)
