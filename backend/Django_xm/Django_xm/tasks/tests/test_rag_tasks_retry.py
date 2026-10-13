"""E5-05：upload_documents_task 确定性错误不重试，秒级终态。

旧行为缺口：`except Exception` 通用分支对一切错误 retry（30s backoff × 3），
架空了 `autoretry_for=(ConnectionError, TimeoutError, OSError)` 的意图——
坏 PDF / 文件缺失（kb_service 抛 ValueError / FileNotFoundError）这类确定性
失败让用户白等 30s+ 才收到最终失败。

新行为：确定性错误直接 mark_failure + 终态推送 + 返回 error dict，不触发
self.retry；瞬时错误（ConnectionError 等）仍走既有重试通道。

测试方式：取 shared_task 代理的真实 Task 对象，patch ``self.retry`` 捕获重试
触发面；经 ``task.apply(...)``（本地 eager 执行，自带真实 request.id）驱动任务体。

运行（backend/Django_xm 目录）：
    python manage.py test Django_xm.tasks.tests.test_rag_tasks_retry
"""

import os
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from celery.exceptions import Retry
from django.contrib.auth import get_user_model
from django.test import TestCase

from Django_xm.tasks.base import TrackedTask
from Django_xm.tasks.rag_tasks import upload_documents_task

_KB_SERVICE = "Django_xm.apps.knowledge.services.kb_service"


class UploadDocumentsTaskRetryPolicyTests(TestCase):
    """确定性错误秒级终态 / 瞬时错误保留重试。

    注：TrackedTask 的 DB 持久化（CeleryTaskRecord）在本测试中打桩——
    该表经 core.0001_initial 的 SeparateDatabaseAndState 只登记状态、
    不建表（新测试库恒缺表），属既有基建缺口且不在本票范围；
    本票只锁重试策略语义。
    """

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="ragtask", password="pw123456")
        self.task = upload_documents_task._get_current_object()
        for method in ("mark_started", "mark_success", "mark_failure", "update_progress"):
            patcher = mock.patch.object(TrackedTask, method)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _apply(self, error: Exception, retry_side_effect):
        """以 eager 方式执行任务体：patch self.retry 捕获重试触发面。"""
        with (
            mock.patch.object(self.task, "retry", side_effect=retry_side_effect) as retry_mock,
            mock.patch(
                f"{_KB_SERVICE}.process_uploaded_documents", side_effect=error
            ),
        ):
            eager = self.task.apply(args=[self.user.id, "kb-retry", ["bad.pdf"], None])
        return eager, retry_mock

    def test_value_error_fails_fast_without_retry(self):
        """坏 PDF（ValueError）→ 直接终态返回 error dict，retry 零触发。"""
        eager, retry_mock = self._apply(
            ValueError("无法解析 PDF"), retry_side_effect=AssertionError("确定性错误不得触发 retry")
        )
        self.assertEqual(eager.get(), {"status": "error", "error": "无法解析 PDF"})
        retry_mock.assert_not_called()

    def test_file_not_found_fails_fast_without_retry(self):
        """文件缺失（FileNotFoundError）→ 同样确定性终态，不重试。"""
        eager, retry_mock = self._apply(
            FileNotFoundError("上传文件不存在: bad.pdf"),
            retry_side_effect=AssertionError("确定性错误不得触发 retry"),
        )
        self.assertEqual(
            eager.get(),
            {"status": "error", "error": "上传文件不存在: bad.pdf"},
        )
        retry_mock.assert_not_called()

    def test_transient_error_still_retries(self):
        """瞬时错误（ConnectionError）→ 仍走既有重试通道（守护重试能力不被误伤）。

        CELERY_TASK_EAGER_PROPAGATES=True 下 apply 会重抛 Retry，直接断言。
        """
        with (
            mock.patch.object(self.task, "retry", side_effect=Retry()) as retry_mock,
            mock.patch(
                f"{_KB_SERVICE}.process_uploaded_documents", side_effect=ConnectionError("网络抖动")
            ),self.assertRaises(Retry)
        ):
            self.task.apply(args=[self.user.id, "kb-retry", ["a.pdf"], None])
        retry_mock.assert_called_once()

    def test_autoretry_includes_embedding_unavailable(self):
        """3.4：EmbeddingUnavailableError 纳入 autoretry 契约（embedding 瞬时故障可重试）。"""
        from Django_xm.common.exceptions import EmbeddingUnavailableError

        self.assertIn(EmbeddingUnavailableError, self.task.autoretry_for)
