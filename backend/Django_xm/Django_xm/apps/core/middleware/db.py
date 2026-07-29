"""
数据库连接管理中间件

每个请求结束后通过 DatabaseConnectionManager 统一清理过期连接，
并周期性检查连接使用率进行告警。

覆盖场景：
  - 普通 HTTP API 请求
  - SSE 长连接（StreamingHttpResponse）
  - 异常中断的请求

与 sse_utils.py 中的心跳清理互补：
  - 中间件：请求粒度（请求开始→结束）
  - 心跳：时间粒度（SSE 流期间每 N 秒）
"""

import logging
import threading
import time

from Django_xm.apps.core.services.db_connection_manager import DatabaseConnectionManager

logger = logging.getLogger(__name__)

# 告警检查间隔（秒），避免每个请求都查 pg_stat_activity
_ALERT_CHECK_INTERVAL = 30
_last_alert_check = 0.0
_alert_lock = threading.Lock()


class DatabaseConnectionMiddleware:
    """
    请求级数据库连接管理中间件。

    功能：
    1. 请求结束后清理过期/失效连接
    2. 周期性检查连接使用率并告警
    3. 异常请求仍确保连接清理
    4. 慢请求日志中附带连接统计

    中间件位于 MIDDLEWARE 列表末尾，响应阶段最先执行清理。
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request._db_conn_start = time.time()

        try:
            response = self.get_response(request)
        except Exception:
            # 请求异常中断时仍需清理连接
            DatabaseConnectionManager.cleanup(source="middleware_exception")
            raise

        # 正常请求结束后清理
        DatabaseConnectionManager.cleanup(source=f"middleware:{request.method}:{request.path}")

        # 周期性检查连接使用率
        self._periodic_alert_check()

        return response

    def _periodic_alert_check(self):
        """周期性检查连接使用率并告警，避免每个请求都查询 pg_stat_activity。"""
        global _last_alert_check
        now = time.time()
        with _alert_lock:
            if now - _last_alert_check >= _ALERT_CHECK_INTERVAL:
                _last_alert_check = now
                need_check = True
            else:
                need_check = False

        if need_check:
            DatabaseConnectionManager.check_and_alert()
