"""ApprovalMetrics 审批可观测性指标采集器单元测试。

覆盖：
- on_created / on_approved / on_rejected / on_timeout 计数累加
- on_sandbox_result 沙箱指标
- record_high_risk_for_session / get_high_risk_window_count 滑动窗口
- snapshot 聚合查询（avg_approval_time / sandbox_success_rate 计算）
- 故障隔离：Redis 不可用时静默降级不抛异常
"""

import sys
import unittest
from unittest.mock import MagicMock, patch

# 注意：common/observability/__init__.py re-export 了 approval_metrics 单例（ApprovalMetrics 实例），
# 导致 `from ... import approval_metrics` 和 `import ...approval_metrics as x` 都会得到实例而非模块。
# 必须从 sys.modules 取真正的模块对象，否则 patch.object 找不到模块级函数 _get_redis_client。
import Django_xm.common.observability.approval_metrics  # noqa: F401 触发模块导入

metrics_module = sys.modules["Django_xm.common.observability.approval_metrics"]
from Django_xm.common.observability.approval_metrics import ApprovalMetrics
from Django_xm.common.risk_levels import RiskLevel


def _mock_redis_client():
    """创建 mock Redis client（所有方法返回合理默认值）。"""
    client = MagicMock()
    client.incr = MagicMock()
    client.decr = MagicMock()
    client.expire = MagicMock()
    client.get = MagicMock(return_value=b"0")
    client.zadd = MagicMock()
    client.zremrangebyscore = MagicMock()
    client.zcard = MagicMock(return_value=0)
    client.incrbyfloat = MagicMock()
    return client


class OnCreatedTests(unittest.TestCase):
    """on_created 计数累加测试。"""

    @patch.object(metrics_module, "_get_redis_client")
    def test_increments_total_created_and_pending(self, mock_get_client):
        client = _mock_redis_client()
        mock_get_client.return_value = client
        m = ApprovalMetrics()

        m.on_created(RiskLevel.CONTROLLED)

        # total:created 和 pending 都应 INCR
        incr_keys = [call.args[0] for call in client.incr.call_args_list]
        self.assertIn(metrics_module._TOTAL_CREATED, incr_keys)
        self.assertIn(metrics_module._PENDING_KEY, incr_keys)
        # risk:controlled 应累加
        self.assertIn(metrics_module._RISK_KEY[RiskLevel.CONTROLLED], incr_keys)

    @patch.object(metrics_module, "_get_redis_client")
    def test_auto_approved_increments_counter(self, mock_get_client):
        client = _mock_redis_client()
        mock_get_client.return_value = client
        m = ApprovalMetrics()

        m.on_created(RiskLevel.SAFE, auto_approved=True)

        incr_keys = [call.args[0] for call in client.incr.call_args_list]
        self.assertIn(metrics_module._TOTAL_AUTO_APPROVED, incr_keys)

    @patch.object(metrics_module, "_get_redis_client")
    def test_high_risk_increments_risk_high(self, mock_get_client):
        client = _mock_redis_client()
        mock_get_client.return_value = client
        m = ApprovalMetrics()

        m.on_created(RiskLevel.HIGH)

        incr_keys = [call.args[0] for call in client.incr.call_args_list]
        self.assertIn(metrics_module._RISK_KEY[RiskLevel.HIGH], incr_keys)
        self.assertNotIn(metrics_module._RISK_KEY[RiskLevel.SAFE], incr_keys)


class TerminalStateTests(unittest.TestCase):
    """on_approved / on_rejected / on_timeout 终态累加 + pending 递减测试。"""

    @patch.object(metrics_module, "_get_redis_client")
    def test_on_approved_increments_and_decrements_pending(self, mock_get_client):
        client = _mock_redis_client()
        client.get.return_value = b"5"  # pending 当前值 5
        mock_get_client.return_value = client
        m = ApprovalMetrics()

        m.on_approved()

        client.incr.assert_any_call(metrics_module._TOTAL_APPROVED)
        # pending > 0 才 DECR
        client.decr.assert_called_once_with(metrics_module._PENDING_KEY)

    @patch.object(metrics_module, "_get_redis_client")
    def test_on_rejected_increments_total_rejected(self, mock_get_client):
        client = _mock_redis_client()
        client.get.return_value = b"3"
        mock_get_client.return_value = client
        m = ApprovalMetrics()

        m.on_rejected()

        client.incr.assert_any_call(metrics_module._TOTAL_REJECTED)

    @patch.object(metrics_module, "_get_redis_client")
    def test_on_timeout_increments_total_timeout(self, mock_get_client):
        client = _mock_redis_client()
        client.get.return_value = b"1"
        mock_get_client.return_value = client
        m = ApprovalMetrics()

        m.on_timeout()

        client.incr.assert_any_call(metrics_module._TOTAL_TIMEOUT)

    @patch.object(metrics_module, "_get_redis_client")
    def test_pending_not_decremented_below_zero(self, mock_get_client):
        """pending=0 时 DECR 不调用（防止负数）。"""
        client = _mock_redis_client()
        client.get.return_value = b"0"  # pending 已为 0
        mock_get_client.return_value = client
        m = ApprovalMetrics()

        m.on_approved()

        client.decr.assert_not_called()

    @patch.object(metrics_module, "_get_redis_client")
    def test_latency_recorded_when_created_at_provided(self, mock_get_client):
        """created_at 提供时记录延迟（incrbyfloat + incr）。"""
        client = _mock_redis_client()
        mock_get_client.return_value = client
        m = ApprovalMetrics()

        import time as _time

        created_at = _time.time() - 10  # 10 秒前

        m.on_approved(created_at=created_at)

        client.incrbyfloat.assert_called_once_with(metrics_module._LATENCY_SUM, unittest.mock.ANY)
        client.incr.assert_any_call(metrics_module._LATENCY_COUNT)


class SandboxResultTests(unittest.TestCase):
    """on_sandbox_result 沙箱执行指标测试。"""

    @patch.object(metrics_module, "_get_redis_client")
    def test_success_increments_success_counter(self, mock_get_client):
        client = _mock_redis_client()
        mock_get_client.return_value = client
        m = ApprovalMetrics()

        m.on_sandbox_result(success=True)

        client.incr.assert_called_once_with(metrics_module._SANDBOX_SUCCESS)

    @patch.object(metrics_module, "_get_redis_client")
    def test_failure_increments_failure_counter(self, mock_get_client):
        client = _mock_redis_client()
        mock_get_client.return_value = client
        m = ApprovalMetrics()

        m.on_sandbox_result(success=False)

        client.incr.assert_called_once_with(metrics_module._SANDBOX_FAILURE)


class HighRiskWindowTests(unittest.TestCase):
    """record_high_risk_for_session / get_high_risk_window_count 滑动窗口测试。"""

    @patch.object(metrics_module, "_get_redis_client")
    def test_record_returns_window_count(self, mock_get_client):
        client = _mock_redis_client()
        client.zcard.return_value = 3
        mock_get_client.return_value = client
        m = ApprovalMetrics()

        count = m.record_high_risk_for_session("session-1")

        self.assertEqual(count, 3)
        client.zadd.assert_called_once()
        client.zremrangebyscore.assert_called_once()  # 清理过期
        client.expire.assert_called_once()
        client.zcard.assert_called_once()

    @patch.object(metrics_module, "_get_redis_client")
    def test_record_empty_source_id_returns_zero(self, mock_get_client):
        m = ApprovalMetrics()
        self.assertEqual(m.record_high_risk_for_session(""), 0)
        self.assertEqual(m.record_high_risk_for_session(None), 0)

    @patch.object(metrics_module, "_get_redis_client")
    def test_get_count_returns_zcard_result(self, mock_get_client):
        client = _mock_redis_client()
        client.zcard.return_value = 7
        mock_get_client.return_value = client
        m = ApprovalMetrics()

        count = m.get_high_risk_window_count("session-1")

        self.assertEqual(count, 7)
        client.zremrangebyscore.assert_called_once()  # 查询前清理过期

    @patch.object(metrics_module, "_get_redis_client")
    def test_get_count_empty_source_id_returns_zero(self, mock_get_client):
        m = ApprovalMetrics()
        self.assertEqual(m.get_high_risk_window_count(""), 0)


class SnapshotTests(unittest.TestCase):
    """snapshot 聚合查询测试。"""

    @patch.object(metrics_module, "_get_redis_client")
    def test_snapshot_aggregates_all_metrics(self, mock_get_client):
        """snapshot 读取所有计数器并聚合。"""
        client = _mock_redis_client()

        # 模拟各计数器值：按 get 调用顺序返回
        values = {
            metrics_module._TOTAL_CREATED: b"100",
            metrics_module._TOTAL_APPROVED: b"80",
            metrics_module._TOTAL_REJECTED: b"15",
            metrics_module._TOTAL_TIMEOUT: b"5",
            metrics_module._TOTAL_AUTO_APPROVED: b"20",
            metrics_module._SANDBOX_SUCCESS: b"18",
            metrics_module._SANDBOX_FAILURE: b"2",
            metrics_module._RISK_KEY[RiskLevel.SAFE]: b"50",
            metrics_module._RISK_KEY[RiskLevel.CONTROLLED]: b"30",
            metrics_module._RISK_KEY[RiskLevel.HIGH]: b"20",
            metrics_module._PENDING_KEY: b"10",
            metrics_module._LATENCY_COUNT: b"90",
        }

        def get_side_effect(key, *args, **kwargs):
            return values.get(key, b"0")

        client.get.side_effect = get_side_effect
        client.incrbyfloat.return_value = None
        # 延迟总和 450 秒 / 90 = 5 秒平均
        # snapshot 中 latency_sum 通过 client.get(_LATENCY_SUM) 读取
        # 需要在 side_effect 中加入 _LATENCY_SUM
        values[metrics_module._LATENCY_SUM] = b"450.0"
        mock_get_client.return_value = client
        m = ApprovalMetrics()

        snap = m.snapshot()

        self.assertEqual(snap["pending_count"], 10)
        self.assertEqual(snap["total"]["created"], 100)
        self.assertEqual(snap["total"]["approved"], 80)
        self.assertEqual(snap["total"]["rejected"], 15)
        self.assertEqual(snap["total"]["timeout"], 5)
        self.assertEqual(snap["total"]["auto_approved"], 20)
        self.assertEqual(snap["risk_distribution"]["safe"], 50)
        self.assertEqual(snap["risk_distribution"]["controlled"], 30)
        self.assertEqual(snap["risk_distribution"]["high"], 20)
        self.assertEqual(snap["sandbox"]["success"], 18)
        self.assertEqual(snap["sandbox"]["failure"], 2)
        # 成功率 = 18 / 20 = 0.9
        self.assertAlmostEqual(snap["sandbox"]["success_rate"], 0.9, places=4)
        # 平均耗时 = 450 / 90 = 5.0
        self.assertEqual(snap["latency"]["avg_approval_time_seconds"], 5.0)
        self.assertEqual(snap["latency"]["sample_count"], 90)
        # 阈值信息
        self.assertEqual(snap["thresholds"]["high_risk_window_seconds"], 300)
        self.assertEqual(snap["thresholds"]["high_risk_window_threshold"], 10)

    @patch.object(metrics_module, "_get_redis_client")
    def test_snapshot_sandbox_success_rate_none_when_no_data(self, mock_get_client):
        """无沙箱数据时 success_rate 为 None。"""
        client = _mock_redis_client()
        client.get.return_value = b"0"  # 所有计数器为 0
        mock_get_client.return_value = client
        m = ApprovalMetrics()

        snap = m.snapshot()
        self.assertIsNone(snap["sandbox"]["success_rate"])

    @patch.object(metrics_module, "_get_redis_client")
    def test_snapshot_avg_zero_when_no_samples(self, mock_get_client):
        """无延迟样本时 avg_approval_time 为 0。"""
        client = _mock_redis_client()
        client.get.return_value = b"0"
        mock_get_client.return_value = client
        m = ApprovalMetrics()

        snap = m.snapshot()
        self.assertEqual(snap["latency"]["avg_approval_time_seconds"], 0.0)


class FaultIsolationTests(unittest.TestCase):
    """故障隔离：Redis 不可用时静默降级，不抛异常。"""

    def test_on_created_silent_when_redis_unavailable(self):
        """Redis client 为 None 时 on_created 不抛异常。"""
        with patch.object(metrics_module, "_get_redis_client", return_value=None):
            m = ApprovalMetrics()
            # 不应抛异常
            m.on_created(RiskLevel.HIGH)

    def test_on_approved_silent_when_redis_unavailable(self):
        """Redis client 为 None 时 on_approved 不抛异常。"""
        with patch.object(metrics_module, "_get_redis_client", return_value=None):
            m = ApprovalMetrics()
            m.on_approved()

    def test_record_high_risk_silent_when_redis_raises(self):
        """Redis 操作抛异常时 record_high_risk_for_session 返回 0。"""
        client = MagicMock()
        client.zadd.side_effect = Exception("Redis down")
        with patch.object(metrics_module, "_get_redis_client", return_value=client):
            m = ApprovalMetrics()
            count = m.record_high_risk_for_session("s1")
            self.assertEqual(count, 0)

    def test_snapshot_silent_when_redis_raises(self):
        """Redis get 抛异常时 _safe_get 内部捕获返回 0，snapshot 返回零值快照不崩。"""
        client = MagicMock()
        client.get.side_effect = Exception("Redis down")
        with patch.object(metrics_module, "_get_redis_client", return_value=client):
            m = ApprovalMetrics()
            snap = m.snapshot()
            # _safe_get 内部 try/except 捕获异常返回 0，snapshot 不抛异常
            self.assertEqual(snap["total"]["created"], 0)
            self.assertEqual(snap["pending_count"], 0)
            self.assertEqual(snap["risk_distribution"]["safe"], 0)

    def test_get_count_silent_when_redis_raises(self):
        """Redis 操作抛异常时 get_high_risk_window_count 返回 0。"""
        client = MagicMock()
        client.zcard.side_effect = Exception("Redis down")
        with patch.object(metrics_module, "_get_redis_client", return_value=client):
            m = ApprovalMetrics()
            count = m.get_high_risk_window_count("s1")
            self.assertEqual(count, 0)
