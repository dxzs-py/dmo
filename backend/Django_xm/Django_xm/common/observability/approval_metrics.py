"""审批可观测性指标采集器

基于 Redis 计数器的实时指标统计，供监控端点与告警系统使用。
设计原则：
    1. 写时累加 + 读时聚合：无后台轮询开销，指标在审批状态变更时实时累加
    2. 故障隔离：指标采集失败绝不影响审批主流程（全部 try/except 保护）
    3. 可降级：Redis 不可用时 snapshot 返回空指标而非抛异常
    4. 统一接入：approval_service 在状态变更点调用对应 increment 方法

指标分类：
    - 计数类：pending_count / total_created / total_approved / total_rejected /
              total_timeout / total_auto_approved / sandbox_success / sandbox_failure
    - 分布类：risk_distribution（safe/controlled/high 各级创建数）
    - 延迟类：avg_approval_time（从 pending 到终态的平均耗时，秒）
    - 熔断类：high_risk_window_count（滑动窗口内 HIGH 级操作数，供 F3 熔断判定）

Redis Key 规划（统一前缀 approval:metrics:）：
    approval:metrics:pending                 # 当前 pending 数（INCR/DECR）
    approval:metrics:total:created            # 累计创建数
    approval:metrics:total:approved           # 累计通过数
    approval:metrics:total:rejected           # 累计拒绝数
    approval:metrics:total:timeout            # 累计超时数
    approval:metrics:total:auto_approved      # SAFE 级自动通过数
    approval:metrics:risk:safe               # SAFE 级创建数
    approval:metrics:risk:controlled          # CONTROLLED 级创建数
    approval:metrics:risk:high               # HIGH 级创建数
    approval:metrics:sandbox:success          # 沙箱执行成功数
    approval:metrics:sandbox:failure          # 沙箱执行失败数
    approval:metrics:latency:sum             # 审批耗时总和（秒，用于求均值）
    approval:metrics:latency:count           # 已完成审批数（用于求均值）
    approval:metrics:high_risk:{source_id}   # 单会话 HIGH 级计数（滑动窗口）
"""

import logging
import time
from typing import Any

from django.core.cache import cache

from Django_xm.common.risk_levels import RiskLevel
from Django_xm.common.redis_utils import get_redis_client

logger = logging.getLogger(__name__)

# Redis Key 前缀
_METRICS_PREFIX = "approval:metrics:"
_PENDING_KEY = f"{_METRICS_PREFIX}pending"
_TOTAL_CREATED = f"{_METRICS_PREFIX}total:created"
_TOTAL_APPROVED = f"{_METRICS_PREFIX}total:approved"
_TOTAL_REJECTED = f"{_METRICS_PREFIX}total:rejected"
_TOTAL_TIMEOUT = f"{_METRICS_PREFIX}total:timeout"
_TOTAL_AUTO_APPROVED = f"{_METRICS_PREFIX}total:auto_approved"
_RISK_KEY = {
    RiskLevel.SAFE: f"{_METRICS_PREFIX}risk:safe",
    RiskLevel.CONTROLLED: f"{_METRICS_PREFIX}risk:controlled",
    RiskLevel.HIGH: f"{_METRICS_PREFIX}risk:high",
}
_SANDBOX_SUCCESS = f"{_METRICS_PREFIX}sandbox:success"
_SANDBOX_FAILURE = f"{_METRICS_PREFIX}sandbox:failure"
_LATENCY_SUM = f"{_METRICS_PREFIX}latency:sum"
_LATENCY_COUNT = f"{_METRICS_PREFIX}latency:count"

# 单会话 HIGH 级滑动窗口（供 F3 熔断判定）
_HIGH_RISK_WINDOW_PREFIX = f"{_METRICS_PREFIX}high_risk:"
HIGH_RISK_WINDOW_SECONDS = 300  # 5 分钟滑动窗口
HIGH_RISK_WINDOW_THRESHOLD = 10  # 单会话 5 分钟内 HIGH 级操作数阈值

# pending 计数器 TTL（防止泄漏，1 小时足够覆盖最长审批生命周期）
_PENDING_TTL = 3600
# 累计计数器 TTL（1 天，周期性重置便于观察趋势）
_COUNTER_TTL = 86400


def _safe_incr(key: str, ttl: int = _COUNTER_TTL) -> None:
    """安全 INCR + EXPIRE，失败时仅记日志不抛异常。"""
    try:
        client = get_redis_client()
        if client is None:
            return
        client.incr(key)
        client.expire(key, ttl)
    except Exception as e:
        logger.debug(f"[ApprovalMetrics] INCR 失败 key={key}: {e}")


def _safe_decr(key: str) -> None:
    """安全 DECR，失败时仅记日志不抛异常。"""
    try:
        client = get_redis_client()
        if client is None:
            return
        # 防止递减到负数
        val = int(client.get(key) or 0)
        if val > 0:
            client.decr(key)
    except Exception as e:
        logger.debug(f"[ApprovalMetrics] DECR 失败 key={key}: {e}")


def _safe_get(key: str) -> int:
    """安全 GET 整数值，失败返回 0。"""
    try:
        client = get_redis_client()
        if client is None:
            return 0
        return int(client.get(key) or 0)
    except Exception as e:
        logger.debug(f"[ApprovalMetrics] GET 失败 key={key}: {e}")
        return 0


class ApprovalMetrics:
    """审批可观测性指标采集器（模块级单例 approval_metrics）。

    所有方法均为故障隔离：Redis 不可用时静默降级，不影响审批主流程。
    集成点：approval_service._persist_and_broadcast 中按 state 调用对应方法。
    """

    # ================================================================
    # 写入接口：审批状态变更时调用
    # ================================================================

    def on_created(self, risk_level: RiskLevel | str, *, auto_approved: bool = False) -> None:
        """审批创建时累加指标。

        Args:
            risk_level: 风险等级（SAFE/CONTROLLED/HIGH）
            auto_approved: 是否为 SAFE 级自动通过
        """
        _safe_incr(_TOTAL_CREATED)
        _safe_incr(_PENDING_KEY, ttl=_PENDING_TTL)
        if auto_approved:
            _safe_incr(_TOTAL_AUTO_APPROVED)
        # 风险分布
        rl = risk_level if isinstance(risk_level, RiskLevel) else RiskLevel(risk_level)
        risk_key = _RISK_KEY.get(rl)
        if risk_key:
            _safe_incr(risk_key)
        # HIGH 级单会话滑动窗口（由调用方传入 source_id 时额外记录）
        # 此处不传 source_id，仅记录全局分布；会话级窗口由 record_high_risk_for_session 记录

    def on_approved(self, created_at: float | None = None) -> None:
        """审批通过时累加指标。"""
        _safe_incr(_TOTAL_APPROVED)
        _safe_decr(_PENDING_KEY)
        if created_at is not None:
            self._record_latency(created_at)

    def on_rejected(self, created_at: float | None = None) -> None:
        """审批拒绝时累加指标。"""
        _safe_incr(_TOTAL_REJECTED)
        _safe_decr(_PENDING_KEY)
        if created_at is not None:
            self._record_latency(created_at)

    def on_timeout(self, created_at: float | None = None) -> None:
        """审批超时时累加指标。"""
        _safe_incr(_TOTAL_TIMEOUT)
        _safe_decr(_PENDING_KEY)
        if created_at is not None:
            self._record_latency(created_at)

    def on_sandbox_result(self, success: bool) -> None:
        """沙箱执行结果累加（Phase D 集成点）。"""
        if success:
            _safe_incr(_SANDBOX_SUCCESS)
        else:
            _safe_incr(_SANDBOX_FAILURE)

    def record_high_risk_for_session(self, source_id: str) -> int:
        """记录单会话 HIGH 级操作数（滑动窗口，供 F3 熔断判定）。

        使用 Redis sorted set + 时间戳作为 score，查询时清理过期成员。

        Args:
            source_id: 会话/任务 ID

        Returns:
            当前窗口内 HIGH 级操作数
        """
        if not source_id:
            return 0
        try:
            client = get_redis_client()
            if client is None:
                return 0
            key = f"{_HIGH_RISK_WINDOW_PREFIX}{source_id}"
            now = time.time()
            # 写入当前时间戳作为 score
            member = f"{now}"
            client.zadd(key, {member: now})
            # 清理窗口外的过期成员
            client.zremrangebyscore(key, 0, now - HIGH_RISK_WINDOW_SECONDS)
            # 设置 key TTL（窗口时长 + 缓冲）
            client.expire(key, HIGH_RISK_WINDOW_SECONDS + 60)
            # 返回窗口内成员数
            return client.zcard(key)
        except Exception as e:
            logger.debug(f"[ApprovalMetrics] 记录会话 HIGH 级窗口失败 source_id={source_id}: {e}")
            return 0

    def get_high_risk_window_count(self, source_id: str) -> int:
        """查询单会话滑动窗口内 HIGH 级操作数（不写入，仅查询）。"""
        if not source_id:
            return 0
        try:
            client = get_redis_client()
            if client is None:
                return 0
            key = f"{_HIGH_RISK_WINDOW_PREFIX}{source_id}"
            now = time.time()
            # 清理过期成员后计数
            client.zremrangebyscore(key, 0, now - HIGH_RISK_WINDOW_SECONDS)
            return client.zcard(key)
        except Exception as e:
            logger.debug(f"[ApprovalMetrics] 查询会话 HIGH 级窗口失败 source_id={source_id}: {e}")
            return 0

    def _record_latency(self, created_at: float) -> None:
        """记录审批延迟（从创建到终态的耗时）。"""
        try:
            latency = max(0.0, time.time() - created_at)
            client = get_redis_client()
            if client is None:
                return
            client.incrbyfloat(_LATENCY_SUM, latency)
            client.expire(_LATENCY_SUM, _COUNTER_TTL)
            client.incr(_LATENCY_COUNT)
            client.expire(_LATENCY_COUNT, _COUNTER_TTL)
        except Exception as e:
            logger.debug(f"[ApprovalMetrics] 记录延迟失败: {e}")

    # ================================================================
    # 读取接口：监控端点聚合查询
    # ================================================================

    def snapshot(self) -> dict[str, Any]:
        """聚合所有指标，返回完整快照供监控端点使用。

        Returns:
            dict: 包含计数类、分布类、延迟类指标的完整快照
        """
        total_created = _safe_get(_TOTAL_CREATED)
        total_approved = _safe_get(_TOTAL_APPROVED)
        total_rejected = _safe_get(_TOTAL_REJECTED)
        total_timeout = _safe_get(_TOTAL_TIMEOUT)
        total_auto_approved = _safe_get(_TOTAL_AUTO_APPROVED)
        sandbox_success = _safe_get(_SANDBOX_SUCCESS)
        sandbox_failure = _safe_get(_SANDBOX_FAILURE)

        risk_safe = _safe_get(_RISK_KEY[RiskLevel.SAFE])
        risk_controlled = _safe_get(_RISK_KEY[RiskLevel.CONTROLLED])
        risk_high = _safe_get(_RISK_KEY[RiskLevel.HIGH])

        latency_count = _safe_get(_LATENCY_COUNT)
        avg_approval_time = 0.0
        if latency_count > 0:
            try:
                client = get_redis_client()
                if client is not None:
                    latency_sum = float(client.get(_LATENCY_SUM) or 0)
                    avg_approval_time = round(latency_sum / latency_count, 2)
            except Exception as e:
                logger.debug(f"[ApprovalMetrics] 读取延迟指标失败: {e}")

        sandbox_total = sandbox_success + sandbox_failure
        sandbox_success_rate = round(sandbox_success / sandbox_total, 4) if sandbox_total > 0 else None

        return {
            "pending_count": _safe_get(_PENDING_KEY),
            "total": {
                "created": total_created,
                "approved": total_approved,
                "rejected": total_rejected,
                "timeout": total_timeout,
                "auto_approved": total_auto_approved,
            },
            "risk_distribution": {
                "safe": risk_safe,
                "controlled": risk_controlled,
                "high": risk_high,
            },
            "sandbox": {
                "success": sandbox_success,
                "failure": sandbox_failure,
                "success_rate": sandbox_success_rate,
            },
            "latency": {
                "avg_approval_time_seconds": avg_approval_time,
                "sample_count": latency_count,
            },
            "thresholds": {
                "high_risk_window_seconds": HIGH_RISK_WINDOW_SECONDS,
                "high_risk_window_threshold": HIGH_RISK_WINDOW_THRESHOLD,
            },
        }


# 模块级单例
approval_metrics = ApprovalMetrics()
