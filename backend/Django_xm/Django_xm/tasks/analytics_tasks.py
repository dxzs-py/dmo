"""
分析模块 Celery 任务
"""

import logging

from celery import shared_task
from django.db import IntegrityError, transaction
from django.db.models import F

logger = logging.getLogger(__name__)


@shared_task(
    name="analytics.track_event",
    bind=True,
    max_retries=1,
    soft_time_limit=60,
    autoretry_for=(ConnectionError, TimeoutError, OSError),
    retry_backoff=True,
    retry_backoff_max=60,
)
def track_event(self, event_data):
    """异步记录用户事件（业务事件明细专用）。

    注意：API 请求**不再**走本任务——`api.request` 的指标已由 `ApiDailyStat`
    承载，见 `track_api_stat`。本任务只负责业务事件（chat/rag/workflow/...）
    与前端显式埋点（page.view / feature.use / cross_kb_search）。
    """
    from Django_xm.apps.analytics.models import UserEvent

    try:
        UserEvent.objects.create(**event_data)
    except Exception as e:
        logger.exception("异步记录用户事件失败")
        raise self.retry(exc=e, countdown=5) from e


@shared_task(
    name="analytics.track_api_stat",
    bind=True,
    max_retries=1,
    soft_time_limit=60,
    autoretry_for=(ConnectionError, TimeoutError, OSError),
    retry_backoff=True,
    retry_backoff_max=60,
)
def track_api_stat(self, stat_data, detail_event=None):
    """累加 API 日聚合，并按需写入 `api.request` 明细。

    两段职责：
    1. **总是**累加 `ApiDailyStat`（仪表盘 api_requests / api_errors / 平均耗时 /
       错误率的唯一来源，不再依赖明细表）。
    2. **仅当** detail_event 非空时写 UserEvent 明细。中间件按"非 GET 方法 或
       请求失败"判定是否需要明细——成功的 GET 无分析价值（实测占 api.request
       的 82.9%），故不落明细。

    并发安全：四个数值指标用 F() 表达式原子累加（lock-free，不丢计数）；当日行
    不存在时先 insert，撞唯一约束则退回 update。path_stats 是分析级近似值，
    采用 read-modify-write 且只写该列，并发下可能丢失个别路径计数，但**不影响**
    上面四个精确指标。
    """
    from Django_xm.apps.analytics.models import API_STAT_MAX_PATHS_PER_DAY, ApiDailyStat, UserEvent

    user_id = stat_data["user_id"]
    day = stat_data["date"]
    duration_ms = stat_data.get("duration_ms") or 0
    is_error = 1 if stat_data.get("is_success") is False else 0

    def _bump():
        return ApiDailyStat.objects.filter(user_id=user_id, date=day).update(
            api_requests=F("api_requests") + 1,
            api_errors=F("api_errors") + is_error,
            duration_sum_ms=F("duration_sum_ms") + duration_ms,
            duration_count=F("duration_count") + 1,
        )

    try:
        if not _bump():
            try:
                with transaction.atomic():
                    ApiDailyStat.objects.create(
                        user_id=user_id,
                        date=day,
                        api_requests=1,
                        api_errors=is_error,
                        duration_sum_ms=duration_ms,
                        duration_count=1,
                    )
            except IntegrityError:
                _bump()  # 并发下另一协程刚建好该行，退回原子累加
    except Exception as e:
        logger.exception("API 日聚合写入失败")
        raise self.retry(exc=e, countdown=5) from e

    _merge_path_stat(
        ApiDailyStat,
        user_id=user_id,
        day=day,
        path=stat_data.get("path") or "(unknown)",
        is_error=is_error,
        duration_ms=duration_ms,
        max_paths=API_STAT_MAX_PATHS_PER_DAY,
    )

    if detail_event:
        try:
            UserEvent.objects.create(**detail_event)
        except Exception as e:
            logger.exception("API 请求明细写入失败")
            raise self.retry(exc=e, countdown=5) from e


@shared_task(name="analytics.cleanup_analytics_events", soft_time_limit=600)
def cleanup_analytics_events(dry_run=True):
    """按保留期清理 analytics 明细与聚合表。

    **默认 dry_run=True**：清理是不可逆的删除，必须先看统计再显式传 dry_run=False
    执行（Celery beat 调用时需显式传参，见 settings.CELERY_BEAT_SCHEDULE）。

    保留期（settings）：
    - ANALYTICS_EVENT_RETENTION_DAYS（默认 90）：只清 `api.request` 明细。
      业务事件（chat/rag/workflow/...）与前端埋点**不清理**——它们量级小
      （实测 6,301 行）且是行为审计的原始凭据。
    - ANALYTICS_API_STAT_RETENTION_DAYS（默认 365）：聚合表体量小，保留更久。

    分批删除以避免长事务锁表。
    """
    from datetime import timedelta

    from django.conf import settings
    from django.utils import timezone

    from Django_xm.apps.analytics.models import ApiDailyStat, UserEvent

    today = timezone.localdate()
    event_cutoff = today - timedelta(days=settings.ANALYTICS_EVENT_RETENTION_DAYS)
    stat_cutoff = today - timedelta(days=settings.ANALYTICS_API_STAT_RETENTION_DAYS)

    stale_events = UserEvent.objects.filter(event_type="api.request", created_at__date__lt=event_cutoff)
    stale_stats = ApiDailyStat.objects.filter(date__lt=stat_cutoff)

    report = {
        "dry_run": dry_run,
        "event_cutoff": str(event_cutoff),
        "stat_cutoff": str(stat_cutoff),
        "stale_api_detail": stale_events.count(),
        "stale_api_stat": stale_stats.count(),
        "deleted_api_detail": 0,
        "deleted_api_stat": 0,
    }

    if dry_run:
        logger.info("[analytics] 清理预演（未删除）: %s", report)
        return report

    # 分批删除，避免一次性长事务
    while True:
        ids = list(stale_events.values_list("id", flat=True)[:2000])
        if not ids:
            break
        deleted, _ = UserEvent.objects.filter(id__in=ids).delete()
        report["deleted_api_detail"] += deleted

    while True:
        ids = list(stale_stats.values_list("id", flat=True)[:2000])
        if not ids:
            break
        deleted, _ = ApiDailyStat.objects.filter(id__in=ids).delete()
        report["deleted_api_stat"] += deleted

    logger.info("[analytics] 清理完成: %s", report)
    return report


def _merge_path_stat(ApiDailyStat, *, user_id, day, path, is_error, duration_ms, max_paths):
    """把单次请求并入当日 path_stats（last-write-wins，仅影响该 JSON 列）。

    路径基数无界（路径内嵌资源 ID，实测单用户单日去重路径中位数 574、最大
    12,393），故只保留当日计数 Top-N，超出置 path_stats_truncated——这是正常
    现象而非数据异常，详见 models.API_STAT_MAX_PATHS_PER_DAY 的注释。
    """
    row = (
        ApiDailyStat.objects.filter(user_id=user_id, date=day)
        .values("path_stats", "path_stats_truncated")
        .first()
    )
    if row is None:
        return  # 聚合行必已由上面的累加创建；缺失属异常，放弃路径统计但不影响精确指标

    stats = dict(row["path_stats"] or {})
    truncated = bool(row["path_stats_truncated"])
    entry = stats.get(path)

    if entry is not None:
        stats[path] = {
            "count": entry.get("count", 0) + 1,
            "errors": entry.get("errors", 0) + is_error,
            "duration_sum": entry.get("duration_sum", 0) + duration_ms,
        }
    elif len(stats) < max_paths:
        stats[path] = {"count": 1, "errors": is_error, "duration_sum": duration_ms}
    else:
        truncated = True  # 已达上限，新路径只累加上面的数值指标，不再进 JSON

    ApiDailyStat.objects.filter(user_id=user_id, date=day).update(
        path_stats=stats,
        path_stats_truncated=truncated,
    )
