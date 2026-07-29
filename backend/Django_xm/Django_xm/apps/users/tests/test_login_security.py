"""LoginSecurityService 单元测试（Task 11.5）。

覆盖 spec `fix-backend-audit-findings` SubTask 11.4：
按 username 失败锁定（Redis 计数 + TTL，5 次/h 失败锁定 1 小时）。

运行方式:
    cd d:\\programming\\langchain\\langchain_xm\\backend\\Django_xm
    conda activate langchain_xm
    python manage.py test Django_xm.apps.users.tests.test_login_security --verbosity=2
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

# Django 环境初始化（兼容 unittest 直接运行）
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.dev")
import django
import django.apps

if not django.apps.apps.ready:
    django.setup()

from Django_xm.apps.users.services.login_security import LoginSecurityService


class _FakeCache:
    """模拟 django-redis 的内存 cache（用于隔离 Redis 依赖）。"""

    def __init__(self):
        # store: key -> (value, ttl_seconds)
        self._store: dict[str, tuple] = {}

    def get(self, key, default=None):
        if key in self._store:
            return self._store[key][0]
        return default

    def set(self, key, value, timeout=None):
        self._store[key] = (value, timeout)

    def incr(self, key, delta=1):
        if key not in self._store:
            raise ValueError(f"Key '{key}' not found")
        value, ttl = self._store[key]
        new_value = value + delta
        # incr 不重置 TTL（与 django-redis 行为一致）
        self._store[key] = (new_value, ttl)
        return new_value

    def delete(self, key):
        self._store.pop(key, None)

    def ttl(self, key):
        if key not in self._store:
            return None
        return self._store[key][1] or -1

    def clear(self):
        self._store.clear()


class _FakeRequest:
    """模拟带 username 的请求。"""

    def __init__(self, username):
        self.data = {"username": username} if username else {}


@patch("Django_xm.apps.users.services.login_security.cache")
class LoginSecurityServiceTest(unittest.TestCase):
    """LoginSecurityService 行为测试。"""

    def setUp(self):
        # 每个测试用例使用独立的 FakeCache
        self.fake_cache = _FakeCache()
        # patch 的 mock_cache 已通过类装饰器注入，setUp 中替换其方法
        # 由于类装饰器给每个测试方法注入 mock_cache 参数，setUp 无法直接访问
        # 改用 setUp 中重新 patch 的方式

    def _install_fake_cache(self, mock_cache):
        """把 FakeCache 的方法绑定到 mock_cache。"""
        mock_cache.get.side_effect = self.fake_cache.get
        mock_cache.set.side_effect = self.fake_cache.set
        mock_cache.incr.side_effect = self.fake_cache.incr
        mock_cache.delete.side_effect = self.fake_cache.delete
        mock_cache.ttl.side_effect = self.fake_cache.ttl

    def test_first_failure_count_is_one_not_locked(self, mock_cache):
        """首次失败：count=1，未锁定。"""
        self._install_fake_cache(mock_cache)

        count = LoginSecurityService.record_failure("alice")

        self.assertEqual(count, 1)
        self.assertFalse(LoginSecurityService.is_locked("alice"))

    def test_fourth_failure_not_locked(self, mock_cache):
        """第 4 次失败：count=4，仍未锁定。"""
        self._install_fake_cache(mock_cache)

        for _i in range(4):
            count = LoginSecurityService.record_failure("bob")

        self.assertEqual(count, 4)
        self.assertFalse(LoginSecurityService.is_locked("bob"))

    def test_fifth_failure_triggers_lock(self, mock_cache):
        """第 5 次失败：触发锁定，is_locked=True。"""
        self._install_fake_cache(mock_cache)

        for _i in range(5):
            count = LoginSecurityService.record_failure("carol")

        self.assertEqual(count, 5)
        self.assertTrue(LoginSecurityService.is_locked("carol"))

    def test_sixth_failure_still_locked(self, mock_cache):
        """第 6 次失败：仍锁定（lock key 已存在，不重置 TTL）。"""
        self._install_fake_cache(mock_cache)

        for _i in range(6):
            count = LoginSecurityService.record_failure("dave")

        self.assertEqual(count, 6)
        self.assertTrue(LoginSecurityService.is_locked("dave"))

    def test_record_success_clears_fail_count(self, mock_cache):
        """登录成功后清除失败计数（避免历史失败影响）。"""
        self._install_fake_cache(mock_cache)

        LoginSecurityService.record_failure("eve")
        LoginSecurityService.record_failure("eve")
        self.assertEqual(
            self.fake_cache.get(LoginSecurityService._fail_key("eve")),
            2,
        )

        LoginSecurityService.record_success("eve")

        self.assertIsNone(self.fake_cache.get(LoginSecurityService._fail_key("eve")))

    def test_record_success_does_not_clear_lock(self, mock_cache):
        """登录成功只清除失败计数，不清除锁定标记。

        实际场景中锁定状态下不会到达成功分支（视图层在 is_locked 检查时已拒绝）。
        本测试验证 record_success 的语义边界：仅清 fail count，不动 lock。
        """
        self._install_fake_cache(mock_cache)

        for _ in range(5):
            LoginSecurityService.record_failure("frank")
        self.assertTrue(LoginSecurityService.is_locked("frank"))

        LoginSecurityService.record_success("frank")

        # lock 仍存在（视图层应保证锁定时不调用 record_success）
        self.assertTrue(LoginSecurityService.is_locked("frank"))

    def test_is_locked_empty_username_returns_false(self, mock_cache):
        """空 username 时 is_locked 返回 False。"""
        self._install_fake_cache(mock_cache)

        self.assertFalse(LoginSecurityService.is_locked(""))
        self.assertFalse(LoginSecurityService.is_locked(None))

    def test_record_failure_empty_username_returns_zero(self, mock_cache):
        """空 username 时不计数，返回 0。"""
        self._install_fake_cache(mock_cache)

        count = LoginSecurityService.record_failure("")

        self.assertEqual(count, 0)
        # 未写入任何 fail key
        self.assertEqual(len(self.fake_cache._store), 0)

    def test_record_success_empty_username_no_op(self, mock_cache):
        """空 username 时 record_success 不操作。"""
        self._install_fake_cache(mock_cache)

        LoginSecurityService.record_success("")

        self.assertEqual(len(self.fake_cache._store), 0)

    def test_get_remaining_lock_seconds_returns_ttl(self, mock_cache):
        """get_remaining_lock_seconds 返回锁定 key 的 TTL。"""
        self._install_fake_cache(mock_cache)

        # 触发锁定
        for _ in range(5):
            LoginSecurityService.record_failure("grace")

        remaining = LoginSecurityService.get_remaining_lock_seconds("grace")
        self.assertEqual(remaining, LoginSecurityService.LOCK_TTL)

    def test_get_remaining_lock_seconds_no_lock_returns_zero(self, mock_cache):
        """未锁定时 get_remaining_lock_seconds 返回 0。"""
        self._install_fake_cache(mock_cache)

        remaining = LoginSecurityService.get_remaining_lock_seconds("henry")
        self.assertEqual(remaining, 0)

    def test_different_users_independent(self, mock_cache):
        """不同用户的失败计数相互独立。"""
        self._install_fake_cache(mock_cache)

        for _ in range(5):
            LoginSecurityService.record_failure("ivan")
        # ivan 已锁定
        self.assertTrue(LoginSecurityService.is_locked("ivan"))

        # judy 未失败，未锁定
        self.assertFalse(LoginSecurityService.is_locked("judy"))
        self.assertEqual(LoginSecurityService.record_failure("judy"), 1)

    def test_fail_count_ttl_set_on_first_failure(self, mock_cache):
        """首次失败时设置 TTL（1 小时窗口从首次失败开始计时）。"""
        self._install_fake_cache(mock_cache)

        LoginSecurityService.record_failure("kate")

        fail_key = LoginSecurityService._fail_key("kate")
        self.assertEqual(
            self.fake_cache.ttl(fail_key),
            LoginSecurityService.FAIL_COUNT_TTL,
        )

    def test_incr_does_not_reset_ttl(self, mock_cache):
        """后续失败 incr 不重置 TTL（保持首次失败时刻的窗口）。"""
        self._install_fake_cache(mock_cache)

        LoginSecurityService.record_failure("leo")
        first_ttl = self.fake_cache.ttl(LoginSecurityService._fail_key("leo"))

        LoginSecurityService.record_failure("leo")
        LoginSecurityService.record_failure("leo")
        current_ttl = self.fake_cache.ttl(LoginSecurityService._fail_key("leo"))

        # TTL 保持首次设置的值（incr 不重置）
        self.assertEqual(first_ttl, current_ttl)
        # 计数正确递增
        self.assertEqual(
            self.fake_cache.get(LoginSecurityService._fail_key("leo")),
            3,
        )

    def test_lock_key_uses_distinct_prefix(self, mock_cache):
        """lock key 与 fail key 使用不同前缀，互不干扰。"""
        self._install_fake_cache(mock_cache)

        for _ in range(5):
            LoginSecurityService.record_failure("mona")

        fail_key = LoginSecurityService._fail_key("mona")
        lock_key = LoginSecurityService._lock_key("mona")

        self.assertNotEqual(fail_key, lock_key)
        self.assertTrue(fail_key.startswith("login_fail:"))
        self.assertTrue(lock_key.startswith("login_lock:"))


class LoginSecurityServiceKeyFormatTest(unittest.TestCase):
    """key 格式与常量校验（不依赖 cache，无需 patch）。"""

    def test_max_fail_count_is_five(self):
        """阈值固定为 5 次。"""
        self.assertEqual(LoginSecurityService.MAX_FAIL_COUNT, 5)

    def test_ttl_is_one_hour(self):
        """TTL 固定为 1 小时（3600 秒）。"""
        self.assertEqual(LoginSecurityService.FAIL_COUNT_TTL, 3600)
        self.assertEqual(LoginSecurityService.LOCK_TTL, 3600)

    def test_fail_key_format(self):
        """fail key 格式为 login_fail:{username}。"""
        key = LoginSecurityService._fail_key("alice")
        self.assertEqual(key, "login_fail:alice")

    def test_lock_key_format(self):
        """lock key 格式为 login_lock:{username}。"""
        key = LoginSecurityService._lock_key("alice")
        self.assertEqual(key, "login_lock:alice")


if __name__ == "__main__":
    unittest.main()
