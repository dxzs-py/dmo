"""子代理业务等待超时清扫任务（subagent_wait_tasks）单元测试。

覆盖：
- ``_is_wait_expired``：deadline 过期/未过期/非法/缺失（updated_at 兜底）；
- ``_build_timeout_resume_payload``：payload 结构（超时标记 + deadline + 等待时长）；
- ``cleanup_expired_subagent_waits``：过期等待定位与 resume（mock runtime），
  未过期/终态记录不触碰。

数据库：django.test.TestCase（pytest-django 自动建 test_<DB_NAME> 临时库）。
"""

import os
import time
import unittest
from datetime import timedelta
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.test import TestCase, override_settings
from django.utils import timezone

from Django_xm.apps.agent_hub.subagent_tools.wait import SUBAGENT_WAIT_TIMEOUT_FLAG
from Django_xm.apps.ai_engine.models import SubAgentInstance, SubAgentStatus
from Django_xm.tasks.subagent_wait_tasks import (
    _build_timeout_resume_payload,
    _is_wait_expired,
    cleanup_expired_subagent_waits,
)

_RUNTIME_MODULE = "Django_xm.apps.ai_engine.subagent_runtime"

TIMEOUT_SECONDS = 7200


def _wait_pending(deadline=None):
    pending = {"interrupt_id": "wait-int-1", "_subagent_wait": True}
    if deadline is not None:
        pending["deadline"] = deadline
    return pending


def _make_instance(thread_id, *, status=SubAgentStatus.RUNNING, pending=None) -> SubAgentInstance:
    return SubAgentInstance.objects.create(
        thread_id=thread_id,
        parent_thread_id="root-1",
        status=status,
        pending_interrupt_info=pending,
        metadata={},
    )


class TestIsWaitExpired(unittest.TestCase):
    """_is_wait_expired：deadline 判定与 updated_at 兜底。"""

    def _updated_at(self, age_seconds: float):
        return timezone.now() - timedelta(seconds=age_seconds)

    def test_expired_by_deadline(self):
        now_ts = time.time()
        self.assertTrue(_is_wait_expired(_wait_pending(now_ts - 1), self._updated_at(0), now_ts, TIMEOUT_SECONDS))

    def test_not_expired_by_deadline(self):
        now_ts = time.time()
        self.assertFalse(
            _is_wait_expired(_wait_pending(now_ts + 3600), self._updated_at(0), now_ts, TIMEOUT_SECONDS)
        )

    def test_fallback_updated_at_when_no_deadline(self):
        now_ts = time.time()
        # 挂起时长超过配置超时 → 过期
        self.assertTrue(
            _is_wait_expired(
                _wait_pending(None), self._updated_at(TIMEOUT_SECONDS + 10), now_ts, TIMEOUT_SECONDS
            )
        )
        # 挂起时长未超 → 未过期
        self.assertFalse(
            _is_wait_expired(_wait_pending(None), self._updated_at(60), now_ts, TIMEOUT_SECONDS)
        )

    def test_invalid_deadline_never_expired(self):
        now_ts = time.time()
        self.assertFalse(
            _is_wait_expired(_wait_pending("not-a-number"), self._updated_at(10**9), now_ts, TIMEOUT_SECONDS)
        )

    def test_non_dict_pending_never_expired(self):
        self.assertFalse(_is_wait_expired(None, timezone.now(), time.time(), TIMEOUT_SECONDS))


class TestBuildTimeoutResumePayload(unittest.TestCase):
    def test_with_deadline(self):
        now_ts = time.time()
        deadline = now_ts - 100
        payload = _build_timeout_resume_payload(_wait_pending(deadline), timezone.now(), now_ts, TIMEOUT_SECONDS)
        self.assertTrue(payload[SUBAGENT_WAIT_TIMEOUT_FLAG])
        self.assertEqual(payload["deadline"], deadline)
        self.assertEqual(payload["waited_seconds"], TIMEOUT_SECONDS + 100)

    def test_without_deadline_uses_updated_at(self):
        now_ts = time.time()
        updated_at = timezone.now() - timedelta(seconds=300)
        payload = _build_timeout_resume_payload(_wait_pending(None), updated_at, now_ts, TIMEOUT_SECONDS)
        self.assertTrue(payload[SUBAGENT_WAIT_TIMEOUT_FLAG])
        self.assertIsNone(payload["deadline"])
        self.assertAlmostEqual(payload["waited_seconds"], 300, delta=5)


@override_settings(AGENT_SUBAGENT_WAIT_TIMEOUT=TIMEOUT_SECONDS)
class CleanupExpiredSubagentWaitsTests(TestCase):
    """beat 扫描任务：过期等待定位与 resume（runtime mock）。"""

    def _run_task(self):
        runtime = mock.Mock()
        runtime.resume = mock.AsyncMock()
        with mock.patch(f"{_RUNTIME_MODULE}.get_subagent_runtime", return_value=runtime):
            result = cleanup_expired_subagent_waits()
        return result, runtime

    def test_expired_wait_is_resumed_with_timeout_payload(self):
        now_ts = time.time()
        _make_instance("sub_expired", pending=_wait_pending(now_ts - 100))
        result, runtime = self._run_task()

        self.assertEqual(result["expired_count"], 1)
        self.assertEqual(result["resumed"], 1)
        self.assertEqual(result["failed"], 0)
        runtime.resume.assert_awaited_once_with("sub_expired", mock.ANY)
        payload = runtime.resume.await_args.args[1]
        self.assertTrue(payload[SUBAGENT_WAIT_TIMEOUT_FLAG])
        self.assertEqual(payload["deadline"], now_ts - 100)

    def test_not_expired_wait_untouched(self):
        _make_instance("sub_fresh", pending=_wait_pending(time.time() + 3600))
        result, runtime = self._run_task()

        self.assertEqual(result["expired_count"], 0)
        runtime.resume.assert_not_awaited()

    def test_terminal_instance_untouched(self):
        _make_instance(
            "sub_done",
            status=SubAgentStatus.COMPLETED,
            pending=_wait_pending(time.time() - 100),
        )
        result, runtime = self._run_task()

        self.assertEqual(result["expired_count"], 0)
        runtime.resume.assert_not_awaited()

    def test_legacy_wait_without_deadline_expired_by_updated_at(self):
        """历史挂起无 deadline：以挂起写入时间（updated_at，queryset.update 伪造）
        + 配置超时兜底判定过期。"""
        instance = _make_instance("sub_legacy", pending=_wait_pending(None))
        stale = timezone.now() - timedelta(seconds=TIMEOUT_SECONDS + 100)
        SubAgentInstance.objects.filter(pk=instance.pk).update(updated_at=stale)
        result, runtime = self._run_task()

        self.assertEqual(result["expired_count"], 1)
        self.assertEqual(result["resumed"], 1)
        payload = runtime.resume.await_args.args[1]
        self.assertTrue(payload[SUBAGENT_WAIT_TIMEOUT_FLAG])
        self.assertIsNone(payload["deadline"])
        # waited_seconds 基于伪造的挂起时刻
        self.assertGreaterEqual(payload["waited_seconds"], TIMEOUT_SECONDS)

    def test_resume_failure_isolated(self):
        """单条 resume 失败不影响其余（故障隔离），按 failed 计数。"""
        now_ts = time.time()
        _make_instance("sub_a", pending=_wait_pending(now_ts - 100))
        _make_instance("sub_b", pending=_wait_pending(now_ts - 100))
        runtime = mock.Mock()
        runtime.resume = mock.AsyncMock(
            side_effect=[None, RuntimeError("resume boom")]
        )
        with mock.patch(f"{_RUNTIME_MODULE}.get_subagent_runtime", return_value=runtime):
            result = cleanup_expired_subagent_waits()

        self.assertEqual(result["expired_count"], 2)
        self.assertEqual(result["resumed"], 1)
        self.assertEqual(result["failed"], 1)

    def test_no_pending_rows(self):
        result, runtime = self._run_task()
        self.assertEqual(result["expired_count"], 0)
        runtime.resume.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
