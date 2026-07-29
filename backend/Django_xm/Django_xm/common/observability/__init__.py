"""可观测性模块：审批与工具调用指标采集。

提供基于 Redis 计数器的实时指标统计，供监控端点与告警系统使用。
所有指标采用"写时累加 + 读时聚合"模式，无后台轮询开销。
"""

from Django_xm.common.observability.approval_metrics import approval_metrics

__all__ = ["approval_metrics"]
