"""审批监控指标快照端点测试（Spec D3）。

覆盖 GET /api/v1/approvals/metrics/snapshot/：
- 管理员（IsAdminUser）：200，返回 approval_metrics.snapshot() 全量计数
  （pending_count / total 5 项 / risk_distribution 3 项 / sandbox / high_risk_windows /
  latency / thresholds）
- 普通用户：403
- 未认证：401

注：测试环境 Redis 不可用时指标采集器静默降级，snapshot 返回全 0 结构
（结构键完整，数值不敏感），断言以结构契约为准。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.approvals.tests.test_metrics_snapshot_view --noinput
"""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

METRICS_URL = "/api/v1/approvals/metrics/snapshot/"


class ApprovalMetricsSnapshotViewTests(TestCase):
    """GET /api/v1/approvals/metrics/snapshot/ 权限与结构契约。"""

    def setUp(self):
        self.client = APIClient()
        User = get_user_model()
        self.admin = User.objects.create_user(
            username="metrics-admin", password="pw123456", is_superuser=True, is_staff=True
        )
        self.normal_user = User.objects.create_user(username="metrics-user", password="pw123456")

    def test_unauthenticated_returns_401(self):
        """未认证：401。"""
        resp = self.client.get(METRICS_URL)
        self.assertEqual(resp.status_code, 401)

    def test_normal_user_returns_403(self):
        """普通用户（非管理员）：403。"""
        self.client.force_authenticate(self.normal_user)
        resp = self.client.get(METRICS_URL)
        self.assertEqual(resp.status_code, 403)

    def test_admin_returns_full_snapshot(self):
        """管理员：200，返回全量计数快照（9 类计数器结构完整）。"""
        self.client.force_authenticate(self.admin)
        resp = self.client.get(METRICS_URL)
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        # success_response 统一 {code, message, data}
        self.assertEqual(body["code"], 200)
        data = body["data"]

        # 计数类：pending + total 5 项
        self.assertIn("pending_count", data)
        self.assertIsInstance(data["pending_count"], int)
        total = data["total"]
        for key in ("created", "approved", "rejected", "timeout", "auto_approved"):
            self.assertIn(key, total, key)
            self.assertIsInstance(total[key], int)

        # 分布类：risk_distribution 3 项
        risk = data["risk_distribution"]
        for key in ("safe", "controlled", "high"):
            self.assertIn(key, risk, key)
            self.assertIsInstance(risk[key], int)

        # 沙箱结果：success / failure / success_rate
        sandbox = data["sandbox"]
        for key in ("success", "failure", "success_rate"):
            self.assertIn(key, sandbox, key)

        # 熔断窗口聚合 / 延迟 / 阈值
        self.assertIn("high_risk_windows", data)
        self.assertIn("latency", data)
        self.assertIn("avg_approval_time_seconds", data["latency"])
        self.assertIn("sample_count", data["latency"])
        self.assertIn("thresholds", data)
        self.assertIn("high_risk_window_seconds", data["thresholds"])
        self.assertIn("high_risk_window_threshold", data["thresholds"])

    def test_admin_response_matches_snapshot_call(self):
        """端点透传 approval_metrics.snapshot()，与直接调用输出一致。"""
        from Django_xm.common.observability import approval_metrics

        self.client.force_authenticate(self.admin)
        resp = self.client.get(METRICS_URL)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"], approval_metrics.snapshot())
