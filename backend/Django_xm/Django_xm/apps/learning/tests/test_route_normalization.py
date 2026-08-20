"""learning app 路由规范化防回归测试（dj-16 延伸：learning 路由收敛）。

覆盖：
- 旧路由已死：DELETE /task/<thread_id>/ 不再匹配任何路由 → 404
- 新路由已注册：tasks_delete 可 reverse 解析
- 旧路由 name（delete）已注销，reverse 抛 NoReverseMatch
- tasks 列表路由（dj-16 延伸前已存在）保持可用，防误删

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.learning.tests.test_route_normalization --noinput
"""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import NoReverseMatch, reverse
from rest_framework.test import APIClient

LEARNING_URL = "/api/v1/learning/"


class LearningRouteNormalizationTests(TestCase):
    """learning 路由收敛：旧单数删除路由移除，新复数资源路由注册。"""

    def setUp(self):
        self.client = APIClient()
        self.user = get_user_model().objects.create_user(username="learning-route", password="pw123456")
        self.client.force_authenticate(user=self.user)

    def test_old_task_delete_route_unavailable(self):
        """旧单数 task 删除路由不可达（tasks/<thread_id>/ 为唯一删除端点）→ 404。"""
        resp = self.client.delete(f"{LEARNING_URL}task/some-thread-id/")
        self.assertEqual(resp.status_code, 404, resp.content)

    def test_new_route_names_resolvable(self):
        """新路由 name 可 reverse 解析（路由已注册）。"""
        self.assertEqual(
            reverse("learning:tasks_delete", kwargs={"thread_id": "t1"}),
            f"{LEARNING_URL}tasks/t1/",
        )

    def test_tasks_list_route_resolvable(self):
        """tasks 列表路由保持可用（dj-16 延伸前已存在，防误删）。"""
        self.assertEqual(reverse("learning:tasks"), f"{LEARNING_URL}tasks/")

    def test_old_route_names_removed(self):
        """旧路由 name 已注销，reverse 抛 NoReverseMatch。"""
        with self.assertRaises(NoReverseMatch):
            reverse("learning:delete", kwargs={"thread_id": "t1"})
