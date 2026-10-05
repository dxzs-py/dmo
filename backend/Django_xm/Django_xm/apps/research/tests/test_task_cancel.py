"""研究任务取消端点与条件终态化测试（C1/P2-1 深研取消全链）。

覆盖：
- 端点：可取消状态发布 SIGNAL_STOP（session_type=research）；不可取消状态 409
  拒绝且不发信令；非创建者/不存在 404；未认证 401
- 条件终态化 cancel_task_if_active：活跃态原子写 CANCELLED；终态退让不覆盖；
  不存在/软删除返回 False
- 迁移完整性：CANCELLED 为模型权威成员（防 0005/0011 历史残留误判复发）

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.research.tests.test_task_cancel --noinput
"""

import os
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from Django_xm.apps.research.models import ResearchTask, ResearchTaskStatus
from Django_xm.apps.research.services.task_manager import (
    cancel_task_if_active,
    get_task_manager,
)

CANCEL_URL = "/api/v1/research/tasks/{task_id}/cancel/"
_SIGNAL_MODULE = "Django_xm.apps.research.views"


def _create_task(task_id="t-cancel-1", status=ResearchTaskStatus.RUNNING, owner=None, **kw):
    return ResearchTask.objects.create(
        task_id=task_id,
        query="研究主题",
        status=status,
        created_by=owner,
        **kw,
    )


class ResearchTaskCancelEndpointTests(TestCase):
    """POST /api/v1/research/tasks/<task_id>/cancel/：信令发布 + 状态/权限校验。"""

    def setUp(self):
        self.client = APIClient()
        self.user = get_user_model().objects.create_user(username="cancel-owner", password="pw123456")
        self.other = get_user_model().objects.create_user(username="cancel-other", password="pw123456")
        self.client.force_authenticate(user=self.user)

    def test_cancel_running_task_publishes_stop_signal(self):
        """RUNNING 任务取消 → 200 + 发布 SIGNAL_STOP（session_type=research），不落状态。"""
        task = _create_task(status=ResearchTaskStatus.RUNNING, owner=self.user)
        with mock.patch(f"{_SIGNAL_MODULE}.publish_signal") as publish:
            resp = self.client.post(CANCEL_URL.format(task_id=task.task_id))
        self.assertEqual(resp.status_code, 200, resp.content)
        publish.assert_called_once_with(
            "stop",
            task.task_id,
            {"session_type": "research"},
        )
        # 端点职责边界：仅发信令，终态化由执行服务完成（此处状态不变）
        task.refresh_from_db()
        self.assertEqual(task.status, ResearchTaskStatus.RUNNING)

    @mock.patch(f"{_SIGNAL_MODULE}.publish_signal")
    def test_cancel_cancellable_statuses(self, publish):
        """pending / awaiting_approval 同样可取消（活跃态全覆盖）。"""
        for status in (ResearchTaskStatus.PENDING, ResearchTaskStatus.AWAITING_APPROVAL):
            with self.subTest(status=status):
                task = _create_task(task_id=f"t-{status.value}", status=status, owner=self.user)
                resp = self.client.post(CANCEL_URL.format(task_id=task.task_id))
                self.assertEqual(resp.status_code, 200, resp.content)

    @mock.patch(f"{_SIGNAL_MODULE}.publish_signal")
    def test_cancel_terminal_status_rejected(self, publish):
        """completed/failed/cancelled 终态取消 → 409（TASK_NOT_CANCELLABLE），不发信令。"""
        for status in (
            ResearchTaskStatus.COMPLETED,
            ResearchTaskStatus.FAILED,
            ResearchTaskStatus.CANCELLED,
        ):
            with self.subTest(status=status):
                task = _create_task(task_id=f"t-{status.value}", status=status, owner=self.user)
                resp = self.client.post(CANCEL_URL.format(task_id=task.task_id))
                self.assertEqual(resp.status_code, 409, resp.content)
        publish.assert_not_called()

    def test_cancel_task_of_other_user_rejected(self):
        """非创建者取消 → 404（按 created_by 隔离，不泄露任务存在性）。"""
        task = _create_task(status=ResearchTaskStatus.RUNNING, owner=self.other)
        resp = self.client.post(CANCEL_URL.format(task_id=task.task_id))
        self.assertEqual(resp.status_code, 404, resp.content)

    def test_cancel_missing_task_rejected(self):
        """任务不存在 → 404。"""
        resp = self.client.post(CANCEL_URL.format(task_id="no-such-task"))
        self.assertEqual(resp.status_code, 404, resp.content)

    def test_cancel_requires_authentication(self):
        """未认证 → 401。"""
        self.client.force_authenticate(user=None)
        resp = self.client.post(CANCEL_URL.format(task_id="whatever"))
        self.assertEqual(resp.status_code, 401, resp.content)


class CancelTaskIfActiveTests(TestCase):
    """cancel_task_if_active 条件终态化：原子写 CANCELLED + 终态退让。"""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="cancel-mgr", password="pw123456")
        get_task_manager()._cache.clear()

    def test_active_task_cancelled(self):
        """活跃态任务 → True + 状态写 CANCELLED + error_message 落库。"""
        task = _create_task(status=ResearchTaskStatus.RUNNING, owner=self.user)
        self.assertTrue(cancel_task_if_active(task.task_id))
        task.refresh_from_db()
        self.assertEqual(task.status, ResearchTaskStatus.CANCELLED)
        self.assertEqual(task.error_message, "用户已取消该研究任务")

    def test_terminal_task_not_overwritten(self):
        """已终态（completed/failed/cancelled）→ False 且状态不被覆盖。"""
        for status in (
            ResearchTaskStatus.COMPLETED,
            ResearchTaskStatus.FAILED,
            ResearchTaskStatus.CANCELLED,
        ):
            with self.subTest(status=status):
                task = _create_task(task_id=f"tm-{status.value}", status=status, owner=self.user)
                self.assertFalse(cancel_task_if_active(task.task_id))
                task.refresh_from_db()
                self.assertEqual(task.status, status)

    def test_missing_or_deleted_task_returns_false(self):
        """不存在/软删除任务 → False（无写操作）。"""
        self.assertFalse(cancel_task_if_active("no-such-task"))
        task = _create_task(status=ResearchTaskStatus.RUNNING, owner=self.user)
        task.soft_delete()
        self.assertFalse(cancel_task_if_active(task.task_id))

    def test_error_message_truncated_to_model_limit(self):
        """error_message 超长截断到 5000 字符（模型校验限制，防 ValidationError）。"""
        task = _create_task(status=ResearchTaskStatus.RUNNING, owner=self.user)
        self.assertTrue(cancel_task_if_active(task.task_id, error_message="取" * 6000))
        task.refresh_from_db()
        self.assertEqual(len(task.error_message), 5000)


class CancelledStatusModelTests(TestCase):
    """CANCELLED 成员模型层完整性（防历史迁移残留回归）。"""

    def test_cancelled_is_authoritative_member(self):
        """CANCELLED 为当前模型权威成员，choices 含 cancelled 且可落库。"""
        self.assertEqual(ResearchTaskStatus.CANCELLED, "cancelled")
        self.assertIn(("cancelled", "已取消"), ResearchTaskStatus.choices)
        task = _create_task(status=ResearchTaskStatus.CANCELLED)
        task.full_clean(exclude=["error_message"])
        self.assertTrue(
            ResearchTask.objects.filter(task_id=task.task_id, status=ResearchTaskStatus.CANCELLED).exists()
        )
