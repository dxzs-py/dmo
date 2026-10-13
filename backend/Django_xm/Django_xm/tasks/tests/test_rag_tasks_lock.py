"""RP-12：Celery 上传锁 task_id 级 → KB 级互斥。

原锁键 ``lock:rag_add_docs:{task_id}`` 只防同任务重发；同 KB 两个不同上传
任务并发 add_documents 无互斥，num_documents 读改写（index_service）存在
竞态面。修复：锁键升级 ``lock:rag_add_docs:kb:{user_id}:{kb_id}``（与
user_index_name 一一对应），锁值记录持有者 task_id——

- 同任务重复投递（holder == 自身）→ 既有跳过语义保留；
- 异任务占用同 KB → Celery 重试等待（不占 worker），重试耗尽后显式失败终态
  （文件已落盘，用户稍后重传即可，先删后加语义保证幂等）；
- 异 KB → 键不同，天然并行；
- finally 仅在确实持锁时释放，严禁误删他人持有的锁。

E5-05 契约共存：确定性错误（ValueError/FileNotFoundError）不重试分支不变。

运行（backend/Django_xm 目录）：
    python manage.py test Django_xm.tasks.tests.test_rag_tasks_lock --noinput
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
from Django_xm.tasks.rag_tasks import (
    _acquire_rag_add_docs_lock,
    _release_rag_add_docs_lock,
    upload_documents_task,
)

_RAG_TASKS = "Django_xm.tasks.rag_tasks"
_KB_SERVICE = "Django_xm.apps.knowledge.services.kb_service"


class _FakeRedis:
    """最小 Redis 替身：SET NX EX / GET / DELETE。"""

    def __init__(self):
        self.store = {}

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    def get(self, key):
        return self.store.get(key)

    def delete(self, key):
        self.store.pop(key, None)


class RagAddDocsLockPrimitiveTests(TestCase):
    """锁原语：KB 级键 + 持有者记录。"""

    def test_lock_key_is_kb_scoped(self):
        """同 KB 互斥（第二任务拿不到锁且读到持有者）、异 KB 并行。"""
        fake = _FakeRedis()
        with mock.patch("django_redis.get_redis_connection", return_value=fake):
            ok_a1, holder_a1 = _acquire_rag_add_docs_lock(1, "kbA", "task-1")
            ok_b2, holder_b2 = _acquire_rag_add_docs_lock(1, "kbB", "task-2")
            ok_a2, holder_a2 = _acquire_rag_add_docs_lock(1, "kbA", "task-2")

        self.assertEqual((ok_a1, holder_a1), (True, "task-1"))
        self.assertEqual((ok_b2, holder_b2), (True, "task-2"))
        self.assertEqual((ok_a2, holder_a2), (False, "task-1"))
        # 键为 KB 级且值为持有者 task_id
        self.assertEqual(fake.store["lock:rag_add_docs:kb:1:kbA"], "task-1")
        self.assertIn("lock:rag_add_docs:kb:1:kbB", fake.store)

    def test_same_task_holder_reported_on_redelivery(self):
        """同任务重复获锁：NX 失败但 holder 为自身（供任务层区分跳过/等待）。"""
        fake = _FakeRedis()
        with mock.patch("django_redis.get_redis_connection", return_value=fake):
            _acquire_rag_add_docs_lock(7, "kbX", "task-9")
            ok, holder = _acquire_rag_add_docs_lock(7, "kbX", "task-9")

        self.assertFalse(ok)
        self.assertEqual(holder, "task-9")

    def test_release_removes_only_own_key(self):
        fake = _FakeRedis()
        with mock.patch("django_redis.get_redis_connection", return_value=fake):
            _acquire_rag_add_docs_lock(1, "kbA", "task-1")
            _release_rag_add_docs_lock(1, "kbA")

        self.assertNotIn("lock:rag_add_docs:kb:1:kbA", fake.store)


class UploadDocumentsTaskKBLockTests(TestCase):
    """任务层：同任务跳过 / 异任务忙等重试 / 重试耗尽显式失败 / 持锁才释放。

    TrackedTask 的 DB 持久化打桩（原因见 test_rag_tasks_retry.py：CeleryTaskRecord
    经 SeparateDatabaseAndState 只登记状态不建表）。
    """

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="raglock", password="pw123456")
        self.task = upload_documents_task._get_current_object()
        for method in ("mark_started", "mark_success", "mark_failure", "update_progress"):
            patcher = mock.patch.object(TrackedTask, method)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _patch_terminal(self):
        patcher = mock.patch(f"{_RAG_TASKS}._publish_upload_terminal")
        terminal = patcher.start()
        self.addCleanup(patcher.stop)
        return terminal

    def _patch_release(self):
        patcher = mock.patch(f"{_RAG_TASKS}._release_rag_add_docs_lock")
        release = patcher.start()
        self.addCleanup(patcher.stop)
        return release

    def test_same_task_redelivery_skips_without_release(self):
        """同任务重复投递 → 跳过终态，且不释放（未持锁，严禁误删自身/他人锁）。"""
        self._patch_terminal()
        release = self._patch_release()

        def _fake_acquire(user_id, kb_id, task_id):
            return False, task_id  # holder == 自身 → 同任务重复投递

        with (
            mock.patch(f"{_RAG_TASKS}._acquire_rag_add_docs_lock", side_effect=_fake_acquire),
            mock.patch(f"{_KB_SERVICE}.process_uploaded_documents") as process,
        ):
            eager = self.task.apply(args=[self.user.id, "kb-x", ["a.pdf"], None])

        self.assertEqual(eager.get(), {"status": "skipped", "reason": "another_worker_holding_lock"})
        process.assert_not_called()
        release.assert_not_called()

    def test_another_task_busy_retries(self):
        """异任务占用同 KB → 触发 Celery 重试等待，不执行上传、不释放他人锁。"""
        self._patch_terminal()
        release = self._patch_release()

        with (
            mock.patch(
                f"{_RAG_TASKS}._acquire_rag_add_docs_lock",
                return_value=(False, "other-task-id"),
            ),
            mock.patch(f"{_KB_SERVICE}.process_uploaded_documents") as process,
            mock.patch.object(self.task, "retry", side_effect=Retry()) as retry_mock,
            self.assertRaises(Retry),
        ):
            self.task.apply(args=[self.user.id, "kb-x", ["a.pdf"], None])

        process.assert_not_called()
        retry_mock.assert_called_once()
        release.assert_not_called()

    def test_busy_exhausted_retries_fail_with_visible_message(self):
        """重试耗尽 → 显式失败终态（mark_failure + 终态推送 + error dict）。"""
        terminal = self._patch_terminal()
        release = self._patch_release()

        with (
            mock.patch(
                f"{_RAG_TASKS}._acquire_rag_add_docs_lock",
                return_value=(False, "other-task-id"),
            ),
            mock.patch(f"{_KB_SERVICE}.process_uploaded_documents") as process,
            mock.patch.object(self.task, "max_retries", 0),
        ):
            eager = self.task.apply(args=[self.user.id, "kb-x", ["a.pdf"], None])

        result = eager.get()
        self.assertEqual(result["status"], "error")
        self.assertIn("请稍后重试", result["error"])
        process.assert_not_called()
        terminal.assert_called_once()
        self.assertFalse(terminal.call_args.kwargs.get("success", True))
        release.assert_not_called()

    def test_success_path_releases_kb_lock(self):
        """成功路径：持锁执行后释放 KB 级锁（user_id, kb_id 入参透传）。"""
        self._patch_terminal()
        release = self._patch_release()

        with (
            mock.patch(f"{_RAG_TASKS}._acquire_rag_add_docs_lock", return_value=(True, "own")) as acquire,
            mock.patch(
                f"{_KB_SERVICE}.process_uploaded_documents",
                return_value={"chunks_created": 3},
            ),
        ):
            eager = self.task.apply(args=[self.user.id, "kb-x", ["a.pdf"], None])

        self.assertEqual(eager.get()["status"], "success")
        acquire.assert_called_once()
        self.assertEqual(acquire.call_args.args, (self.user.id, "kb-x", mock.ANY))
        release.assert_called_once_with(self.user.id, "kb-x")

    def test_deterministic_error_contract_coexists(self):
        """E5-05 契约共存：确定性错误仍秒级终态不重试（锁路径不改变重试策略）。"""
        self._patch_terminal()
        release = self._patch_release()

        with (
            mock.patch(f"{_RAG_TASKS}._acquire_rag_add_docs_lock", return_value=(True, "own")),
            mock.patch(f"{_KB_SERVICE}.process_uploaded_documents", side_effect=ValueError("无法解析 PDF")),
            mock.patch.object(
                self.task, "retry", side_effect=AssertionError("确定性错误不得触发 retry")
            ),
        ):
            eager = self.task.apply(args=[self.user.id, "kb-x", ["bad.pdf"], None])

        self.assertEqual(eager.get(), {"status": "error", "error": "无法解析 PDF"})
        release.assert_called_once_with(self.user.id, "kb-x")
