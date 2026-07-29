"""
数据库连接管理模块单元测试

验证:
1. DatabaseConnectionManager.cleanup 正确调用 close_old_connections
2. DatabaseConnectionManager.cleanup 异常时不影响调用方
3. DatabaseConnectionManager.force_close_all 关闭所有连接
4. DatabaseConnectionManager.get_connection_count 正确查询和缓存
5. DatabaseConnectionManager.check_and_alert 按阈值告警
6. DatabaseConnectionManager.get_stats 返回完整统计
7. db_connection_guard 上下文管理器正常/异常时均清理
8. db_task 装饰器入口/出口/异常时均清理
9. db_task 装饰器支持有参和无参用法
10. DatabaseConnectionMiddleware 请求结束后清理

运行方式:
    cd d:\\programming\\langchain\\langchain_xm\\backend\\Django_xm
    conda activate langchain_xm
    python -m pytest Django_xm/apps/core/tests/test_db_connection_manager.py -v

    # 或使用 unittest
    set DJANGO_SETTINGS_MODULE=Django_xm.settings.dev
    python -m unittest Django_xm.apps.core.tests.test_db_connection_manager -v
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

# 确保 Django settings 可加载
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.dev")

import django

django.setup()

from Django_xm.apps.core.middleware.db import DatabaseConnectionMiddleware
from Django_xm.apps.core.services.db_connection_manager import (
    DatabaseConnectionManager,
    db_check_and_alert,
    db_cleanup,
    db_connection_guard,
    db_force_close_all,
    db_task,
)


class TestDatabaseConnectionManagerCleanup(unittest.TestCase):
    """cleanup 方法测试"""

    @patch("Django_xm.apps.core.services.db_connection_manager.close_old_connections")
    def test_cleanup_calls_close_old_connections(self, mock_close):
        """cleanup 应调用 close_old_connections"""
        DatabaseConnectionManager.cleanup(source="test")
        mock_close.assert_called_once()

    @patch(
        "Django_xm.apps.core.services.db_connection_manager.close_old_connections", side_effect=Exception("db error")
    )
    def test_cleanup_exception_does_not_propagate(self, mock_close):
        """cleanup 异常时不应抛出，仅记录日志"""
        DatabaseConnectionManager.cleanup(source="test_exception")

    @patch("Django_xm.apps.core.services.db_connection_manager.close_old_connections")
    def test_cleanup_increments_counter(self, mock_close):
        """cleanup 应递增内部计数器"""
        import Django_xm.apps.core.services.db_connection_manager as mod

        initial = mod._cleanup_call_count
        DatabaseConnectionManager.cleanup(source="test")
        self.assertEqual(mod._cleanup_call_count, initial + 1)

    @patch("Django_xm.apps.core.services.db_connection_manager.close_old_connections")
    def test_db_cleanup_convenience_function(self, mock_close):
        """db_cleanup 便捷函数应正确工作"""
        db_cleanup(source="convenience_test")
        mock_close.assert_called_once()


class TestDatabaseConnectionManagerForceClose(unittest.TestCase):
    """force_close_all 方法测试"""

    @patch("Django_xm.apps.core.services.db_connection_manager.connections")
    def test_force_close_all_closes_all_connections(self, mock_connections):
        """force_close_all 应关闭所有数据库连接"""
        mock_conn1 = MagicMock()
        mock_conn2 = MagicMock()
        mock_connections.all.return_value = [mock_conn1, mock_conn2]

        DatabaseConnectionManager.force_close_all(source="test_shutdown")

        mock_conn1.close.assert_called_once()
        mock_conn2.close.assert_called_once()

    @patch("Django_xm.apps.core.services.db_connection_manager.connections")
    def test_force_close_all_continues_on_single_conn_error(self, mock_connections):
        """单个连接关闭失败不应影响其他连接"""
        mock_conn1 = MagicMock()
        mock_conn1.close.side_effect = Exception("close error")
        mock_conn2 = MagicMock()
        mock_connections.all.return_value = [mock_conn1, mock_conn2]

        DatabaseConnectionManager.force_close_all(source="test")

        mock_conn2.close.assert_called_once()

    @patch("Django_xm.apps.core.services.db_connection_manager.connections")
    def test_db_force_close_all_convenience_function(self, mock_connections):
        """db_force_close_all 便捷函数应正确工作"""
        mock_connections.all.return_value = []
        db_force_close_all(source="convenience_test")
        mock_connections.all.assert_called_once()


class TestDatabaseConnectionManagerGetCount(unittest.TestCase):
    """get_connection_count 方法测试"""

    def setUp(self):
        """每个测试前重置缓存"""
        import Django_xm.apps.core.services.db_connection_manager as mod

        mod._last_count_check.update(time=0.0, active=0, max_conn=100)

    @patch("Django_xm.apps.core.services.db_connection_manager.connections")
    def test_get_connection_count_returns_stats(self, mock_connections):
        """get_connection_count 应返回连接统计信息"""
        mock_cursor = MagicMock()
        mock_cursor.fetchone.side_effect = [(50,), (100,)]
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)

        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_connections.__getitem__ = MagicMock(return_value=mock_conn)

        result = DatabaseConnectionManager.get_connection_count()

        self.assertEqual(result["active"], 50)
        self.assertEqual(result["max_connections"], 100)
        self.assertEqual(result["usage_pct"], 50.0)
        self.assertFalse(result["cached"])

    @patch("Django_xm.apps.core.services.db_connection_manager.connections")
    def test_get_connection_count_uses_cache(self, mock_connections):
        """缓存未过期时应返回缓存值"""
        import time

        import Django_xm.apps.core.services.db_connection_manager as mod

        mod._last_count_check.update(time=time.time(), active=30, max_conn=100)

        result = DatabaseConnectionManager.get_connection_count()

        self.assertTrue(result["cached"])
        self.assertEqual(result["active"], 30)
        mock_connections.__getitem__.assert_not_called()

    @patch("Django_xm.apps.core.services.db_connection_manager.connections")
    def test_get_connection_count_handles_db_error(self, mock_connections):
        """数据库查询失败时应返回缓存值"""
        import Django_xm.apps.core.services.db_connection_manager as mod

        mod._last_count_check["active"] = 20
        mod._last_count_check["max_conn"] = 100

        mock_connections.__getitem__ = MagicMock(side_effect=Exception("connection error"))

        result = DatabaseConnectionManager.get_connection_count()

        self.assertTrue(result["cached"])
        self.assertEqual(result["active"], 20)
        self.assertIn("error", result)


class TestDatabaseConnectionManagerCheckAlert(unittest.TestCase):
    """check_and_alert 方法测试"""

    @patch.object(DatabaseConnectionManager, "get_connection_count")
    def test_alert_normal(self, mock_count):
        """使用率低于 70% 返回 normal"""
        mock_count.return_value = {"active": 50, "max_connections": 100, "usage_pct": 50.0}
        result = DatabaseConnectionManager.check_and_alert()
        self.assertEqual(result, "normal")

    @patch.object(DatabaseConnectionManager, "get_connection_count")
    def test_alert_warning(self, mock_count):
        """使用率 70%-90% 返回 warning"""
        mock_count.return_value = {"active": 75, "max_connections": 100, "usage_pct": 75.0}
        result = DatabaseConnectionManager.check_and_alert()
        self.assertEqual(result, "warning")

    @patch.object(DatabaseConnectionManager, "get_connection_count")
    def test_alert_critical(self, mock_count):
        """使用率 >= 90% 返回 critical"""
        mock_count.return_value = {"active": 95, "max_connections": 100, "usage_pct": 95.0}
        result = DatabaseConnectionManager.check_and_alert()
        self.assertEqual(result, "critical")

    @patch.object(DatabaseConnectionManager, "get_connection_count")
    def test_db_check_and_alert_convenience(self, mock_count):
        """db_check_and_alert 便捷函数应正确工作"""
        mock_count.return_value = {"active": 50, "max_connections": 100, "usage_pct": 50.0}
        result = db_check_and_alert()
        self.assertEqual(result, "normal")


class TestDatabaseConnectionManagerGetStats(unittest.TestCase):
    """get_stats 方法测试"""

    @patch.object(DatabaseConnectionManager, "get_connection_count")
    def test_get_stats_includes_cleanup_count(self, mock_count):
        """get_stats 应包含清理调用次数"""
        mock_count.return_value = {"active": 10, "max_connections": 100, "usage_pct": 10.0}
        result = DatabaseConnectionManager.get_stats()
        self.assertIn("cleanup_call_count", result)
        self.assertIsInstance(result["cleanup_call_count"], int)


class TestDbConnectionGuard(unittest.TestCase):
    """db_connection_guard 上下文管理器测试"""

    @patch.object(DatabaseConnectionManager, "cleanup")
    def test_guard_cleanup_on_normal_exit(self, mock_cleanup):
        """with 块正常退出时应清理连接"""
        with db_connection_guard(source="test_guard"):
            pass
        mock_cleanup.assert_called_once_with("test_guard")

    @patch.object(DatabaseConnectionManager, "cleanup")
    def test_guard_cleanup_on_exception(self, mock_cleanup):
        """with 块异常退出时也应清理连接"""
        with self.assertRaises(ValueError), db_connection_guard(source="test_guard_exc"):
            raise ValueError("test error")
        mock_cleanup.assert_called_once_with("test_guard_exc")


class TestDbTaskDecorator(unittest.TestCase):
    """db_task 装饰器测试"""

    @patch.object(DatabaseConnectionManager, "check_and_alert", return_value="normal")
    @patch.object(DatabaseConnectionManager, "cleanup")
    def test_db_task_cleanup_on_start_and_end(self, mock_cleanup, mock_alert):
        """db_task 应在任务开始和结束时清理连接"""

        @db_task
        def my_task():
            return "done"

        result = my_task()

        self.assertEqual(result, "done")
        self.assertEqual(mock_cleanup.call_count, 2)
        mock_cleanup.assert_any_call(source="celery_start:my_task")
        mock_cleanup.assert_any_call(source="celery_end:my_task")

    @patch.object(DatabaseConnectionManager, "cleanup")
    def test_db_task_cleanup_on_exception(self, mock_cleanup):
        """db_task 在异常时也应清理连接"""

        @db_task
        def failing_task():
            raise RuntimeError("task failed")

        with self.assertRaises(RuntimeError):
            failing_task()

        self.assertEqual(mock_cleanup.call_count, 2)
        mock_cleanup.assert_any_call(source="celery_error:failing_task")

    @patch.object(DatabaseConnectionManager, "check_and_alert", return_value="normal")
    @patch.object(DatabaseConnectionManager, "cleanup")
    def test_db_task_with_params(self, mock_cleanup, mock_alert):
        """db_task(cleanup_on_start=False) 应跳过入口清理"""

        @db_task(cleanup_on_start=False)
        def my_task():
            return "done"

        result = my_task()

        self.assertEqual(result, "done")
        self.assertEqual(mock_cleanup.call_count, 1)
        mock_cleanup.assert_called_with(source="celery_end:my_task")

    @patch.object(DatabaseConnectionManager, "check_and_alert", return_value="normal")
    @patch.object(DatabaseConnectionManager, "cleanup")
    def test_db_task_preserves_function_name(self, mock_cleanup, mock_alert):
        """db_task 应保留原函数名"""

        @db_task
        def my_custom_task():
            pass

        self.assertEqual(my_custom_task.__name__, "my_custom_task")

    @patch.object(DatabaseConnectionManager, "check_and_alert", return_value="normal")
    @patch.object(DatabaseConnectionManager, "cleanup")
    def test_db_task_uses_celery_task_name(self, mock_cleanup, mock_alert):
        """db_task 优先使用 Celery task name"""

        @db_task
        def my_task():
            return "done"

        my_task.name = "custom.named.task"
        my_task()

        mock_cleanup.assert_any_call(source="celery_start:custom.named.task")

    @patch.object(DatabaseConnectionManager, "check_and_alert", return_value="normal")
    @patch.object(DatabaseConnectionManager, "cleanup")
    def test_db_task_no_cleanup_on_end(self, mock_cleanup, mock_alert):
        """db_task(cleanup_on_end=False) 应跳过出口清理"""

        @db_task(cleanup_on_end=False)
        def my_task():
            return "done"

        result = my_task()

        self.assertEqual(result, "done")
        # 只有 start 清理
        self.assertEqual(mock_cleanup.call_count, 1)
        mock_cleanup.assert_called_with(source="celery_start:my_task")


class TestDatabaseConnectionMiddleware(unittest.TestCase):
    """DatabaseConnectionMiddleware 测试"""

    @patch.object(DatabaseConnectionManager, "check_and_alert", return_value="normal")
    @patch.object(DatabaseConnectionManager, "cleanup")
    def test_middleware_cleanup_after_normal_request(self, mock_cleanup, mock_alert):
        """正常请求结束后应清理连接"""

        def get_response(request):
            return MagicMock()

        middleware = DatabaseConnectionMiddleware(get_response)
        mock_request = MagicMock()
        mock_request.method = "GET"
        mock_request.path = "/api/v1/test/"

        middleware(mock_request)

        mock_cleanup.assert_called_once()
        call_args = mock_cleanup.call_args
        source = call_args[1].get("source", call_args[0][0] if call_args[0] else "")
        self.assertIn("middleware", source)

    @patch.object(DatabaseConnectionManager, "cleanup")
    def test_middleware_cleanup_on_exception(self, mock_cleanup):
        """请求异常时也应清理连接"""

        def get_response(request):
            raise ValueError("view error")

        middleware = DatabaseConnectionMiddleware(get_response)
        mock_request = MagicMock()

        with self.assertRaises(ValueError):
            middleware(mock_request)

        mock_cleanup.assert_called_once_with(source="middleware_exception")

    @patch.object(DatabaseConnectionManager, "check_and_alert", return_value="normal")
    @patch.object(DatabaseConnectionManager, "cleanup")
    def test_middleware_periodic_alert_check(self, mock_cleanup, mock_alert):
        """中间件应周期性检查连接使用率"""
        import Django_xm.apps.core.middleware.db as db_middleware_mod

        db_middleware_mod._last_alert_check = 0.0

        def get_response(request):
            return MagicMock()

        middleware = DatabaseConnectionMiddleware(get_response)
        mock_request = MagicMock()
        mock_request.method = "GET"
        mock_request.path = "/api/v1/test/"

        middleware(mock_request)

        mock_alert.assert_called_once()


if __name__ == "__main__":
    unittest.main()
