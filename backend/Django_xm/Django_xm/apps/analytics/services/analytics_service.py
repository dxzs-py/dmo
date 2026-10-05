import logging
from datetime import timedelta

from django.apps import apps
from django.db.models import Avg, Count, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from Django_xm.apps.analytics.models import ApiDailyStat, EventCategory, EventType, UserEvent
from Django_xm.apps.cache_manager.services.cache_service import (
    CACHE_PREFIX_DASHBOARD,
    CacheService,
    CacheTTL,
)

logger = logging.getLogger(__name__)


def _import_models():
    """延迟获取跨 app 模型类，避免模块级循环导入"""
    ChatSession = apps.get_model("chat", "ChatSession")
    ChatMessage = apps.get_model("chat", "ChatMessage")
    Document = apps.get_model("knowledge", "Document")
    IndexMetadata = apps.get_model("knowledge", "IndexMetadata")
    WorkflowSession = apps.get_model("learning", "WorkflowSession")
    ResearchTask = apps.get_model("research", "ResearchTask")
    return ChatSession, ChatMessage, Document, IndexMetadata, WorkflowSession, ResearchTask


class AnalyticsService:
    @classmethod
    def get_overview_stats(cls, user) -> dict:
        """公开入口：用户总览统计（会话/消息/Token/文档/工作流/研究/API 指标）。

        供跨模块调用（如 users 的用量统计接口）。内部实现仍在 `_get_overview_stats`，
        此处为薄封装，避免调用方依赖私有成员导致重构时静默破坏。
        """
        return cls._get_overview_stats(user)

    @classmethod
    def get_dashboard_stats(cls, user) -> dict:
        cache_key = f"{CACHE_PREFIX_DASHBOARD}:user_{user.id}"
        cached = CacheService.get(cache_key)
        if cached is not None:
            return cached

        now = timezone.now()
        seven_days_ago = now - timedelta(days=7)

        overview = cls._get_overview_stats(user)
        usage_trend = cls._get_usage_trend(user, seven_days_ago)
        category_distribution = cls._get_category_distribution(user)
        feature_usage = cls._get_feature_usage(user)
        model_distribution = cls._get_model_distribution(user)
        performance_metrics = cls._get_performance_metrics(user, seven_days_ago)
        recent_activities = cls._get_recent_activities(user, limit=20)

        result = {
            "overview": overview,
            "usage_trend": usage_trend,
            "category_distribution": category_distribution,
            "feature_usage": feature_usage,
            "model_distribution": model_distribution,
            "performance_metrics": performance_metrics,
            "recent_activities": recent_activities,
        }

        CacheService.set(cache_key, result, CacheTTL.QUERY_SHORT)
        return result

    @classmethod
    def _get_overview_stats(cls, user) -> dict:
        ChatSession, ChatMessage, Document, _IndexMetadata, WorkflowSession, ResearchTask = _import_models()

        chat_sessions = ChatSession.objects.filter(user=user, is_deleted=False).count()
        chat_messages = ChatMessage.objects.filter(session__user=user, session__is_deleted=False)
        msg_agg = chat_messages.aggregate(
            total=Count("id"),
            tokens=Sum("token_count"),
            avg_time=Avg("response_time"),
        )
        total_messages = msg_agg["total"] or 0
        total_tokens = msg_agg["tokens"] or 0

        research_agg = ResearchTask.objects.filter(created_by=user, is_deleted=False).aggregate(
            tokens=Sum("token_count"),
            avg_time=Avg("response_time"),
        )
        research_tokens = research_agg["tokens"] or 0
        research_avg_time = float(research_agg["avg_time"] or 0)

        workflow_agg = WorkflowSession.objects.filter(created_by=user, is_deleted=False).aggregate(
            tokens=Sum("token_count"),
            avg_time=Avg("response_time"),
        )
        workflow_tokens = workflow_agg["tokens"] or 0
        workflow_avg_time = float(workflow_agg["avg_time"] or 0)

        all_tokens = total_tokens + research_tokens + workflow_tokens

        all_times = []
        if msg_agg["avg_time"]:
            all_times.append(float(msg_agg["avg_time"]))
        if research_avg_time > 0:
            all_times.append(research_avg_time)
        if workflow_avg_time > 0:
            all_times.append(workflow_avg_time)
        combined_avg_time = round(sum(all_times) / len(all_times), 2) if all_times else 0

        events = UserEvent.objects.filter(user=user, is_deleted=False)
        # 注意：total_events 现在只统计**明细事件**。中间件收敛后成功的 API 请求
        # 不再落明细（实测原本占全表 97%），故该值语义从"所有事件"变为
        # "可追查的明细事件"，不应被误读为统计丢失。API 请求量见下方 api_requests。
        total_events = events.count()
        # api.request 的指标改由 ApiDailyStat 承载（明细已收敛，见 docs/other/
        # UserEvent数据量治理-议题与排期.md）。历史值已由迁移 0007 回填，此处按
        # user 汇总全时段；读取聚合表而非明细，指标不会因明细收敛而失真。
        api_stat = ApiDailyStat.objects.filter(user=user).aggregate(
            requests_total=Sum("api_requests"),
            errors_total=Sum("api_errors"),
        )
        api_requests = api_stat["requests_total"] or 0
        api_errors = api_stat["errors_total"] or 0

        total_documents = Document.objects.filter(index__user=user, is_deleted=False).count()
        total_workflows = WorkflowSession.objects.filter(created_by=user, is_deleted=False).count()
        total_research = ResearchTask.objects.filter(created_by=user, is_deleted=False).count()

        return {
            "total_sessions": chat_sessions,
            "total_messages": total_messages,
            "total_tokens": all_tokens,
            "avg_response_time": combined_avg_time,
            "total_events": total_events,
            "api_requests": api_requests,
            "api_errors": api_errors,
            "total_documents": total_documents,
            "total_workflows": total_workflows,
            "total_research": total_research,
            "token_breakdown": {
                "chat": total_tokens,
                "research": research_tokens,
                "workflow": workflow_tokens,
            },
        }

    @classmethod
    def _get_usage_trend(cls, user, since) -> list:
        ChatSession, ChatMessage, _, _, _, _ = _import_models()

        now = timezone.now()
        chat_daily = (
            ChatMessage.objects.filter(
                session__user=user,
                session__is_deleted=False,
                created_at__gte=since,
            )
            .annotate(day=TruncDate("created_at"))
            .values("day")
            .annotate(
                msg_count=Count("id"),
                token_sum=Sum("token_count"),
            )
            .order_by("day")
        )
        chat_map = {
            item["day"]: {"messages": item["msg_count"], "tokens": item["token_sum"] or 0} for item in chat_daily
        }

        session_daily = (
            ChatSession.objects.filter(user=user, is_deleted=False, created_at__gte=since)
            .annotate(day=TruncDate("created_at"))
            .values("day")
            .annotate(
                session_count=Count("id"),
            )
            .order_by("day")
        )
        session_map = {item["day"]: item["session_count"] for item in session_daily}

        result = []
        for i in range(7):
            day = (now - timedelta(days=6 - i)).date()
            chat_data = chat_map.get(day, {"messages": 0, "tokens": 0})
            result.append(
                {
                    "date": day.strftime("%m/%d"),
                    "events": chat_data["messages"] + session_map.get(day, 0),
                    "tokens": chat_data["tokens"],
                }
            )
        return result

    @classmethod
    def _get_category_distribution(cls, user) -> list:
        """分类分布 = 各业务分类的**使用事件次数**占比（单位统一为“次”，同类可比）。

        口径约定（改动本方法前先读，防止口径再次漂移）：

        1. **只统计行为事件，不含 api.request**。api.request 由中间件对每个 HTTP
           请求产生一条，实测占 UserEvent 全表 >97%；一旦计入，饼图会退化成
           “API 流量按业务域分布”，完全盖过真实功能使用。API 请求数/错误率见
           _get_overview_stats 的 api_requests / api_errors。
        2. **不混入实体存量计数**（会话/文档/工作流/研究的总数）。存量是“有多少个”，
           流量是“用了几次”，单位与语义都不同，混算无业务解释性（“100 个文档”与
           “500 次消息”求占比说明不了任何问题）。实体总数属存量指标，已由
           _get_overview_stats 的 total_sessions / total_documents /
           total_workflows / total_research 以数字卡片呈现。
        3. 本方法与 _get_feature_usage 是同一事件流的两种聚合粒度：本方法按
           event_category 汇总（粗），_get_feature_usage 按 event_type 汇总（细，
           且同样已排除 api.request）。
        """

        event_dist = (
            UserEvent.objects.filter(user=user, is_deleted=False)
            .exclude(event_type=EventType.API_REQUEST)
            .values("event_category")
            .annotate(count=Count("id"))
            .order_by("-count")
        )

        category_labels = {c.value: c.label for c in EventCategory}
        items = []
        for row in event_dist:
            count = row["count"]
            if count <= 0:
                continue
            items.append(
                {
                    "name": category_labels.get(row["event_category"], row["event_category"]),
                    "count": count,
                }
            )

        total = sum(item["count"] for item in items) or 1
        for item in items:
            item["value"] = round(item["count"] / total * 100)

        # 排序保持显式：event_dist 已 order_by("-count")，此处的 sort 是防御性的，
        # 避免将来有人调整查询顺序时静默破坏图表的降序展示。
        items.sort(key=lambda x: x["count"], reverse=True)
        return items

    @classmethod
    def _get_feature_usage(cls, user) -> list:
        """功能使用 = 各事件类型的**使用次数**排名（单位统一为“次”，同类可比）。

        口径与 _get_category_distribution 完全一致（改动前先读那边的口径注释）：

        1. **纯事件聚合，不混入实体存量计数**。原先"发送聊天消息/创建会话/上传文档/
           创建知识库/启动工作流/启动研究/接收AI回复"七项取的是 ChatMessage /
           ChatSession / Document / IndexMetadata / WorkflowSession / ResearchTask
           的**实体行数**，与事件次数不同量纲；同一张榜单里混排会让读者把"实体数"
           误读为"发生次数"（实测旧口径下"发送聊天消息"显示 5，而事件表里
           chat.message.send 实际有 1818 条）。实体总数已由 _get_overview_stats 的
           total_* 字段以数字卡片呈现。
        2. **排除 api.request**（中间件对每个 HTTP 请求产生一条，实测占全表 >97%）。
        3. 本方法按 event_type 汇总（细粒度），_get_category_distribution 按
           event_category 汇总（粗粒度），二者同源同口径、互为下钻关系。
        """
        event_dist = (
            UserEvent.objects.filter(user=user, is_deleted=False)
            .exclude(event_type=EventType.API_REQUEST)
            .values("event_type")
            .annotate(count=Count("id"))
            .order_by("-count")[:10]
        )

        type_labels = {t.value: t.label for t in EventType}
        items = [
            {
                "name": type_labels.get(row["event_type"], row["event_type"]),
                "value": row["count"],
            }
            for row in event_dist
            if row["count"] > 0
        ]

        # 排序保持显式：event_dist 已 order_by("-count")，此处为防御性排序，
        # 避免将来有人调整查询顺序时静默破坏榜单的降序展示。
        items.sort(key=lambda x: x["value"], reverse=True)
        return items[:10]

    @classmethod
    def _get_model_distribution(cls, user) -> list:
        ChatSession, ChatMessage, _, _, _, _ = _import_models()

        messages = (
            ChatMessage.objects.filter(
                session__user=user,
                session__is_deleted=False,
                role="assistant",
            )
            .exclude(model="")
            .exclude(model__isnull=True)
        )

        dist = (
            messages.values("model")
            .annotate(
                count=Count("id"),
                tokens=Sum("token_count"),
            )
            .order_by("-count")[:5]
        )

        if not dist.exists():
            mode_dist = (
                ChatSession.objects.filter(user=user, is_deleted=False)
                .values("mode")
                .annotate(count=Count("id"))
                .order_by("-count")[:5]
            )
            from Django_xm.apps.chat.models import ChatMode

            mode_labels = {m.value: m.label for m in ChatMode}
            return [
                {"name": mode_labels.get(item["mode"], item["mode"]), "value": item["count"], "tokens": 0}
                for item in mode_dist
            ]

        return [{"name": item["model"], "value": item["count"], "tokens": item["tokens"] or 0} for item in dist]

    @classmethod
    def _get_performance_metrics(cls, user, since) -> dict:
        _ChatSession, ChatMessage, _, _, _, _ = _import_models()

        msg_agg = ChatMessage.objects.filter(
            session__user=user,
            session__is_deleted=False,
            role="assistant",
            response_time__gt=0,
        ).aggregate(
            avg_time=Avg("response_time"),
            count=Count("id"),
        )

        # API 指标改读 ApiDailyStat（明细已收敛）。平均耗时不能对"和"直接取平均，
        # 必须用 sum/count 还原；错误率分母改用聚合表的请求总数。
        api_agg = ApiDailyStat.objects.filter(user=user, date__gte=since.date()).aggregate(
            duration_sum=Sum("duration_sum_ms"),
            duration_count=Sum("duration_count"),
            error_count=Sum("api_errors"),
            total_count=Sum("api_requests"),
        )
        avg_duration = None
        if api_agg["duration_count"]:
            avg_duration = api_agg["duration_sum"] / api_agg["duration_count"]

        avg_response_time_ms = 0
        if msg_agg["avg_time"]:
            avg_response_time_ms = round(float(msg_agg["avg_time"]) * 1000, 0)
        elif avg_duration:
            avg_response_time_ms = round(float(avg_duration), 0)

        error_rate = 0
        if api_agg["total_count"] and api_agg["total_count"] > 0:
            error_rate = round(api_agg["error_count"] / api_agg["total_count"] * 100, 2)

        # 仍读明细：业务事件（聊天消息等）也带 duration_ms，不走聚合表。
        # 语义随之收窄为"可追查明细中带耗时的条数"（成功的 API GET 已不再落明细）。
        tracked = UserEvent.objects.filter(
            user=user,
            is_deleted=False,
            duration_ms__isnull=False,
        ).count()

        return {
            "avg_response_time_ms": avg_response_time_ms,
            "error_rate": error_rate,
            "total_tracked_requests": tracked or (msg_agg["count"] or 0),
        }

    @classmethod
    def _get_recent_activities(cls, user, limit=20) -> list:
        events = (
            UserEvent.objects.filter(user=user, is_deleted=False).select_related("user").order_by("-created_at")[:limit]
        )

        type_labels = {t.value: t.label for t in EventType}
        category_labels = {c.value: c.label for c in EventCategory}
        return [
            {
                "id": e.id,
                "event_type": e.event_type,
                "event_type_label": type_labels.get(e.event_type, e.event_type),
                "event_category": e.event_category,
                "event_category_label": category_labels.get(e.event_category, e.event_category),
                "resource_type": e.resource_type,
                "metadata": e.metadata,
                "is_success": e.is_success,
                "duration_ms": e.duration_ms,
                "created_at": e.created_at.isoformat(),
            }
            for e in events
        ]

    @classmethod
    def record_page_view(
        cls, user, page_path: str, page_title: str = "", ip_address: str | None = None, user_agent: str = ""
    ):
        try:
            UserEvent.objects.create(
                user=user,
                event_type=EventType.PAGE_VIEW,
                event_category=EventCategory.PAGE_VIEW,
                metadata={
                    "path": page_path,
                    "title": page_title,
                },
                ip_address=ip_address,
                user_agent=user_agent[:500] if user_agent else "",
            )
            cls.invalidate_user_cache(user.id)
        except Exception as e:
            logger.warning(f"记录页面浏览事件失败: {e}")

    @classmethod
    def record_feature_usage(
        cls, user, feature_name: str, metadata: dict | None = None, ip_address: str | None = None, user_agent: str = ""
    ):
        try:
            UserEvent.objects.create(
                user=user,
                event_type=EventType.FEATURE_USE,
                event_category=EventCategory.SYSTEM,
                metadata={
                    "feature": feature_name,
                    **(metadata or {}),
                },
                ip_address=ip_address,
                user_agent=user_agent[:500] if user_agent else "",
            )
            cls.invalidate_user_cache(user.id)
        except Exception as e:
            logger.warning(f"记录功能使用事件失败: {e}")

    @classmethod
    def invalidate_user_cache(cls, user_id: int):
        CacheService.delete(f"{CACHE_PREFIX_DASHBOARD}:user_{user_id}")
