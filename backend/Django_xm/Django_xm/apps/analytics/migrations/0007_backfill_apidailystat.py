"""回填 ApiDailyStat：用历史 api.request 明细聚合出每日 API 指标。

背景：仪表盘的 api_requests / api_errors / 平均耗时 / 错误率原先直接数 UserEvent
明细。中间件收敛后成功的 GET 不再落明细，这些指标改由 ApiDailyStat 承载，因此
必须先把历史明细聚合回填，否则全时段指标会归零。

本迁移是**纯加法**（只 INSERT，不 DELETE 任何 UserEvent），可独立执行与验证；
对账标准：回填后 ApiDailyStat.api_requests 总和 == 参与回填的明细行数（误差须为 0）。
"""

from django.db import migrations
from django.db.models import Count, F, Q, Sum
from django.db.models.functions import TruncDate

# 单行 path_stats 保留的路径数上限（按当日计数降序）。超出则置 path_stats_truncated，
# 防止单个用户单日命中大量路径导致单行 JSON 体积失控。
#
# 为什么需要截断（实测结论，勿轻易调大）：本项目路径内嵌资源 ID，路径基数因此无界。
# 实测 2026-08-01 某用户单日去重路径达 12,393 个，中位数 574，形态如
#   /api/v1/chat/sessions/<uuid>/、/api/v1/realtime/snapshot/<uuid>/、
#   /api/v1/approvals/<id>/resume/
# 这些"一次性资源详情路径"构成长尾噪声，故只保留 Top-N（当前 50）即可覆盖
# 全部有分析价值的列表/配置类端点：实测 Top-50 对当日请求量的覆盖率为
# 均值 87.6% / 最低 67.35%（2026-08-01），缺失部分即 UUID 长尾。
# 因此 path_stats_truncated 被置位是**正常现象**而非数据异常（约 52% 的天会置位）。
#
# 另注：路径基数无界这一事实，也是 T2 采集面收敛采用"按 HTTP 方法 + 成败"
# 而非"按路径黑名单"的根本原因——后者在 UUID 路径下不可维护。
MAX_PATHS_PER_DAY = 50


def _build_path_stats(qs):
    """把 (user_id, date, path) 三元组聚合成 {user_id: {date: (totals, path_stats, truncated)}}。

    分两次查询而非一次，是为了让"总量"与"路径明细"各自走最优聚合路径：
    总量只需 2 列分组，路径明细需 3 列分组。
    """
    totals = {}
    for row in (
        qs.annotate(d=TruncDate("created_at"))
        .order_by()  # 清掉 UserEvent.Meta 的 ordering=["-created_at"]，避免干扰分组
        .values("user_id", "d")
        .annotate(
            c=Count("id"),
            e=Count("id", filter=Q(is_success=False)),
            ds=Sum("duration_ms"),
            dc=Count("duration_ms"),
        )
        .iterator()
    ):
        totals[(row["user_id"], row["d"])] = {
            "api_requests": row["c"],
            "api_errors": row["e"],
            "duration_sum_ms": row["ds"] or 0,
            "duration_count": row["dc"],
        }

    # 路径明细：先按 (user, date, path) 聚合，再在 Python 内按当日计数降序取 Top-N
    per_day_paths = {}
    path_rows = (
        qs.annotate(d=TruncDate("created_at"), path=F("metadata__path"))
        .order_by()
        .values("user_id", "d", "path")
        .annotate(
            c=Count("id"),
            e=Count("id", filter=Q(is_success=False)),
            ds=Sum("duration_ms"),
        )
        .iterator()
    )
    for row in path_rows:
        key = (row["user_id"], row["d"])
        if key not in totals:  # 理论上不会出现：路径集合是总量集合的子集
            continue
        per_day_paths.setdefault(key, []).append(
            {
                "path": row["path"] or "(unknown)",
                "count": row["c"],
                "errors": row["e"],
                "duration_sum": row["ds"] or 0,
            }
        )

    for key, paths in per_day_paths.items():
        paths.sort(key=lambda x: x["count"], reverse=True)
        truncated = len(paths) > MAX_PATHS_PER_DAY
        kept = paths[:MAX_PATHS_PER_DAY]
        totals[key]["path_stats"] = {
            p["path"]: {
                "count": p["count"],
                "errors": p["errors"],
                "duration_sum": p["duration_sum"],
            }
            for p in kept
        }
        totals[key]["path_stats_truncated"] = truncated

    return totals


def forwards(apps, schema_editor):
    UserEvent = apps.get_model("analytics", "UserEvent")
    ApiDailyStat = apps.get_model("analytics", "ApiDailyStat")

    # 仅取能归属到用户的行：ApiDailyStat.user 非空，user_id 为空的明细无法聚合。
    # 正常情况下 middleware 对未认证请求会提前 return，故此集合应为空或极小。
    qs = UserEvent.objects.filter(event_type="api.request", user__isnull=False)
    source_rows = qs.count()

    totals = _build_path_stats(qs)

    ApiDailyStat.objects.bulk_create(
        [
            ApiDailyStat(
                user_id=user_id,
                date=date,
                api_requests=agg["api_requests"],
                api_errors=agg["api_errors"],
                duration_sum_ms=agg["duration_sum_ms"],
                duration_count=agg["duration_count"],
                path_stats=agg.get("path_stats", {}),
                path_stats_truncated=agg.get("path_stats_truncated", False),
            )
            for (user_id, date), agg in totals.items()
        ],
        batch_size=500,
        ignore_conflicts=True,
    )

    backfilled = ApiDailyStat.objects.aggregate(s=Sum("api_requests"))["s"] or 0
    print(
        f"[backfill] 源明细 {source_rows} 行 -> 聚合 {len(totals)} 行，"
        f"api_requests 合计 {backfilled}，差额 {source_rows - backfilled}"
    )
    if backfilled != source_rows:
        raise RuntimeError(
            f"回填对账失败：源明细 {source_rows} 行 != api_requests 合计 {backfilled}，"
            "已中止，请勿继续切换读写路径。"
        )


def backwards(apps, schema_editor):
    """回填是纯加法，逆向即清空本表。

    仅在"尚未切换读写路径"时回滚才安全（此时读取仍走 UserEvent 明细，清空聚合表
    无影响）。若已切换，则应改用代码回滚而非迁移回滚。
    """
    ApiDailyStat = apps.get_model("analytics", "ApiDailyStat")
    ApiDailyStat.objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("analytics", "0006_create_apidailystat"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
