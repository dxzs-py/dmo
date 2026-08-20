"""ApprovalListView 真分页 + django-filter 测试（dj-15 回归：[:100] 硬截断）。

覆盖：
- 分页结构：默认 page_size=20 下 items/total/page/page_size/total_pages 逐字段断言
- page_size / page 查询参数：自定义每页条数与翻页
- django-filter 过滤：source / state / source_id 单独过滤与 source+state 组合过滤
- 用户隔离：仅返回 request.user 归属的审批（user 字段过滤）
- 超 100 条不截断：120 条数据 total=120（对比旧实现 [:100] 的静默丢失）

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.approvals.tests.test_views_list --settings=Django_xm.settings.test
"""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from Django_xm.apps.approvals.models import Approval

APPROVALS_URL = "/api/v1/approvals/"


def _make_approvals(
    user,
    count,
    *,
    source=Approval.Source.CHAT,
    state=Approval.State.PENDING,
    source_id="session-1",
    prefix="call",
):
    """批量构造 count 条审批记录（interrupt_id 以 prefix 区分保证唯一）。"""
    return Approval.objects.bulk_create(
        Approval(
            interrupt_id=f"{prefix}-{i:04d}",
            source=source,
            source_id=source_id,
            chat_session_id=f"cs-{source_id}",
            tool_name="shell_exec",
            state=state,
            user=user,
        )
        for i in range(count)
    )


class ApprovalListViewPaginationTests(TestCase):
    """dj-15：ApprovalListView 真分页 + django-filter 过滤。"""

    def setUp(self):
        self.client = APIClient()
        self.user = get_user_model().objects.create_user(username="approver", password="pw123456")
        self.client.force_authenticate(user=self.user)

    def _get_list(self, query=""):
        resp = self.client.get(f"{APPROVALS_URL}{query}")
        self.assertEqual(resp.status_code, 200, resp.content)
        return resp.json()["data"]

    def test_pagination_structure(self):
        """25 条数据：默认 page_size=20 → 首页 20 条、total_pages=2。"""
        _make_approvals(self.user, 25, prefix="pg")

        data = self._get_list()

        self.assertEqual(data["total"], 25)
        self.assertEqual(data["page"], 1)
        self.assertEqual(data["page_size"], 20)
        self.assertEqual(data["total_pages"], 2)
        self.assertEqual(len(data["items"]), 20)

    def test_page_size_param(self):
        """?page_size=10&page=2 → 返回第二页 10 条。"""
        _make_approvals(self.user, 25, prefix="psz")

        data = self._get_list("?page_size=10&page=2")

        self.assertEqual(data["page"], 2)
        self.assertEqual(data["page_size"], 10)
        self.assertEqual(data["total"], 25)
        self.assertEqual(data["total_pages"], 3)
        self.assertEqual(len(data["items"]), 10)

    def test_filter_by_source(self):
        """source 过滤：chat / deep_research 各造若干条，只返回对应来源。"""
        _make_approvals(self.user, 3, source=Approval.Source.CHAT, prefix="src-chat")
        _make_approvals(self.user, 2, source=Approval.Source.DEEP_RESEARCH, prefix="src-dr")

        chat_data = self._get_list("?source=chat")
        self.assertEqual(chat_data["total"], 3)
        self.assertTrue(all(item["source"] == "chat" for item in chat_data["items"]))

        dr_data = self._get_list("?source=deep_research")
        self.assertEqual(dr_data["total"], 2)
        self.assertTrue(all(item["source"] == "deep_research" for item in dr_data["items"]))

    def test_filter_by_state(self):
        """state 过滤：pending / approved 各造若干条，只返回对应状态。"""
        _make_approvals(self.user, 3, state=Approval.State.PENDING, prefix="st-pend")
        _make_approvals(self.user, 2, state=Approval.State.APPROVED, prefix="st-appr")

        data = self._get_list("?state=approved")

        self.assertEqual(data["total"], 2)
        self.assertTrue(all(item["state"] == "approved" for item in data["items"]))

    def test_filter_by_source_id(self):
        """source_id 过滤：只返回该来源 ID 的审批。"""
        _make_approvals(self.user, 3, source_id="session-a", prefix="sid-a")
        _make_approvals(self.user, 2, source_id="session-b", prefix="sid-b")

        data = self._get_list("?source_id=session-a")

        self.assertEqual(data["total"], 3)
        self.assertTrue(all(item["source_id"] == "session-a" for item in data["items"]))

    def test_filter_combined_source_and_state(self):
        """source + state 组合过滤：交集精确匹配。"""
        _make_approvals(
            self.user, 2, source=Approval.Source.CHAT, state=Approval.State.PENDING, prefix="cmb-1"
        )
        _make_approvals(
            self.user, 3, source=Approval.Source.CHAT, state=Approval.State.APPROVED, prefix="cmb-2"
        )
        _make_approvals(
            self.user,
            4,
            source=Approval.Source.DEEP_RESEARCH,
            state=Approval.State.PENDING,
            prefix="cmb-3",
        )

        data = self._get_list("?source=chat&state=pending")

        self.assertEqual(data["total"], 2)
        self.assertTrue(
            all(item["source"] == "chat" and item["state"] == "pending" for item in data["items"])
        )

    def test_user_isolation(self):
        """用户隔离：用户 B 查不到用户 A 的审批。"""
        _make_approvals(self.user, 3, prefix="iso-a")
        other_user = get_user_model().objects.create_user(username="intruder", password="pw123456")
        other_client = APIClient()
        other_client.force_authenticate(user=other_user)

        resp = other_client.get(APPROVALS_URL)

        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()["data"]
        self.assertEqual(data["total"], 0)
        self.assertEqual(data["items"], [])

    def test_over_100_not_truncated(self):
        """120 条数据：默认首页 20 条且 total=120（旧实现 [:100] 会静默截断）。"""
        _make_approvals(self.user, 120, prefix="big")

        data = self._get_list()

        self.assertEqual(len(data["items"]), 20)
        self.assertEqual(data["total"], 120)
        self.assertEqual(data["total_pages"], 6)
