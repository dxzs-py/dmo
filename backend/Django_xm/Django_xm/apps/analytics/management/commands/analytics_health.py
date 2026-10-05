"""analytics 健康巡检：数据量、口径一致性与待清理量。

用法::

    python manage.py analytics_health            # 人工巡检
    python manage.py analytics_health --days 7   # 指定统计窗口

设计目的：把"静默失败"变成"可见异常"。采集收敛后最危险的失败模式是**指标断层**
（明细停了、聚合没写），本命令的「一致性」小节专门检出该情况。

注意：信号事件丢失率（`_safe_record_event` 静默降级）**无法**由本命令检出——
失败只落 logger。对它的告警需接入日志平台，对本命令输出的
「记录分析事件失败」关键字设速率阈值。
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Count, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from Django_xm.apps.analytics.models import ApiDailyStat, EventType, UserEvent


class Command(BaseCommand):
    help = "analytics 健康巡检：数据量 / 口径一致性 / 枚举外脏值 / 待清理量"

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=7, help="统计窗口天数（默认 7）")

    def handle(self, *args, **options):
        days = options["days"]
        out = self.stdout

        total = UserEvent.objects.count()
        out.write(self.style.MIGRATE_HEADING(f"[analytics] 健康巡检（窗口 {days} 天，{timezone.localdate()}）"))
        out.write(f"UserEvent 总行数: {total:,}")

        # ── 1. 按类型分布 ──
        out.write(self.style.MIGRATE_HEADING("\n[1] 事件类型分布（前 10）"))
        for row in UserEvent.objects.values("event_type").annotate(c=Count("id")).order_by("-c")[:10]:
            out.write(f"  {row['event_type']:<28} {row['c']:>9,}")

        # ── 2. 采集收敛效果 ──
        api_detail = UserEvent.objects.filter(event_type=EventType.API_REQUEST).count()
        out.write(self.style.MIGRATE_HEADING("\n[2] 采集收敛效果（api.request）"))
        out.write(f"  api.request 明细累计: {api_detail:,}（含收敛前的历史数据）")
        out.write("  说明：收敛后仅「非 GET 或失败」落明细；完整指标见 ApiDailyStat。")

        # ── 3. 一致性：同一时间窗内比对聚合与明细（检出指标断层）──
        # 注意必须在**相同时间窗**内比对：聚合表保留 365 天而明细只保留 90 天，
        # 清理后全时段聚合必然大于明细，若直接全量比对会产生长期假警报。
        from django.conf import settings

        event_cutoff = timezone.localdate() - timedelta(days=settings.ANALYTICS_EVENT_RETENTION_DAYS)
        out.write(self.style.MIGRATE_HEADING(f"\n[3] 口径一致性（聚合 vs 明细，同一窗口：{event_cutoff} 起）"))
        detail_by_user = {
            r["user_id"]: r["c"]
            for r in UserEvent.objects.filter(
                event_type=EventType.API_REQUEST, created_at__date__gte=event_cutoff
            )
            .values("user_id")
            .annotate(c=Count("id"))
        }
        stat_by_user = {
            r["user_id"]: r["c"]
            for r in ApiDailyStat.objects.filter(date__gte=event_cutoff)
            .values("user_id")
            .annotate(c=Sum("api_requests"))
        }
        if not stat_by_user:
            out.write(self.style.WARNING("  聚合表为空 —— 若已切换采集，仪表盘指标将断层！"))
        mismatched = []
        for user_id, stat_total in stat_by_user.items():
            detail_total = detail_by_user.get(user_id)
            if detail_total is not None and detail_total != stat_total:
                mismatched.append((user_id, detail_total, stat_total))
        for user_id, detail_total, stat_total in mismatched:
            out.write(self.style.WARNING(f"  user={user_id}: 明细 {detail_total} != 聚合 {stat_total}"))
        if not mismatched:
            out.write(self.style.SUCCESS("  OK：窗口内所有用户的聚合与明细口径一致"))

        # ── 4. 枚举外脏值 ──
        known = {c.value for c in EventType}
        dirty = (
            UserEvent.objects.exclude(event_type__in=known)
            .values("event_type")
            .annotate(c=Count("id"))
            .order_by("-c")
        )
        out.write(self.style.MIGRATE_HEADING("\n[4] 枚举外脏值（EventType 未登记）"))
        if not dirty:
            out.write("  无")
        for row in dirty:
            out.write(self.style.WARNING(f"  {row['event_type']:<28} {row['c']:>7,}"))

        # ── 5. path_stats 截断率（正常现象，仅供了解）──
        stat_rows = ApiDailyStat.objects.count()
        truncated = ApiDailyStat.objects.filter(path_stats_truncated=True).count()
        ratio = f"{truncated / stat_rows * 100:.1f}%" if stat_rows else "n/a"
        out.write(self.style.MIGRATE_HEADING("\n[5] path_stats 截断率"))
        out.write(f"  聚合行 {stat_rows:,}，已截断 {truncated:,}（{ratio}）")
        out.write("  路径内嵌资源 ID 故基数无界，截断属正常现象，非数据异常。")

        # ── 6. 写入趋势（日均，来自明细；注意收敛后明细量骤降属预期）──
        out.write(self.style.MIGRATE_HEADING(f"\n[6] 近 {days} 天明细写入趋势（日均）"))
        daily = list(
            UserEvent.objects.filter(created_at__gte=timezone.now() - timedelta(days=days))
            .annotate(d=TruncDate("created_at"))
            .values("d")
            .annotate(c=Count("id"))
            .order_by("d")
        )
        for row in daily:
            out.write(f"  {row['d']}  {row['c']:>7,}")
        window_total = sum(r["c"] for r in daily)
        out.write(f"  窗口合计 {window_total:,}，日均 {window_total // max(days, 1):,}")

        # ── 7. 待清理量（与 cleanup_analytics_events 的口径一致）──
        stat_cutoff = timezone.localdate() - timedelta(days=settings.ANALYTICS_API_STAT_RETENTION_DAYS)
        stale_detail = UserEvent.objects.filter(
            event_type=EventType.API_REQUEST, created_at__date__lt=event_cutoff
        ).count()
        stale_stat = ApiDailyStat.objects.filter(date__lt=stat_cutoff).count()
        out.write(self.style.MIGRATE_HEADING("\n[7] 待清理量（超保留期）"))
        out.write(f"  明细保留期 {settings.ANALYTICS_EVENT_RETENTION_DAYS} 天（截止 {event_cutoff}）")
        out.write(f"    待清理 api.request 明细: {stale_detail:,}")
        out.write(f"  聚合保留期 {settings.ANALYTICS_API_STAT_RETENTION_DAYS} 天（截止 {stat_cutoff}）")
        out.write(f"    待清理 ApiDailyStat: {stale_stat:,}")
        if stale_detail or stale_stat:
            out.write(
                self.style.WARNING(
                    "  提示：确认无回滚需求后执行 "
                    "cleanup_analytics_events.apply(kwargs={'dry_run': False})"
                )
            )
