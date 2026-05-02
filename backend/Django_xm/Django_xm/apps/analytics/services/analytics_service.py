import logging
from datetime import timedelta
from django.db.models import Count, Sum, Avg, Q
from django.db.models.functions import TruncDate
from django.utils import timezone

from Django_xm.apps.ai_engine.services.cache_service import CacheService, CacheTTL
from Django_xm.apps.analytics.models import UserEvent, DailyAggregation, EventCategory, EventType

logger = logging.getLogger(__name__)


def _import_models():
    from Django_xm.apps.chat.models import ChatSession, ChatMessage
    from Django_xm.apps.knowledge.models import Document, DocumentIndex
    from Django_xm.apps.learning.models import WorkflowSession
    from Django_xm.apps.research.models import ResearchTask
    return ChatSession, ChatMessage, Document, DocumentIndex, WorkflowSession, ResearchTask


class AnalyticsService:
    @classmethod
    def get_dashboard_stats(cls, user) -> dict:
        cache_key = f"analytics:dashboard:user_{user.id}"
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
            'overview': overview,
            'usage_trend': usage_trend,
            'category_distribution': category_distribution,
            'feature_usage': feature_usage,
            'model_distribution': model_distribution,
            'performance_metrics': performance_metrics,
            'recent_activities': recent_activities,
        }

        CacheService.set(cache_key, result, CacheTTL.QUERY_SHORT)
        return result

    @classmethod
    def _get_overview_stats(cls, user) -> dict:
        ChatSession, ChatMessage, Document, DocumentIndex, WorkflowSession, ResearchTask = _import_models()

        chat_sessions = ChatSession.objects.filter(user=user, is_deleted=False).count()
        chat_messages = ChatMessage.objects.filter(
            session__user=user, session__is_deleted=False
        )
        msg_agg = chat_messages.aggregate(
            total=Count('id'),
            tokens=Sum('token_count'),
            cost=Sum('cost'),
            avg_time=Avg('response_time'),
        )
        total_messages = msg_agg['total'] or 0
        total_tokens = msg_agg['tokens'] or 0
        total_cost = float(msg_agg['cost'] or 0)
        avg_response_time = round(float(msg_agg['avg_time'] or 0), 2)

        events = UserEvent.objects.filter(user=user, is_deleted=False)
        total_events = events.count()
        api_requests = events.filter(event_type=EventType.API_REQUEST).count()
        api_errors = events.filter(
            event_type=EventType.API_REQUEST, is_success=False
        ).count()

        total_documents = Document.objects.filter(
            index__user=user, is_deleted=False
        ).count()
        total_workflows = WorkflowSession.objects.filter(
            created_by=user, is_deleted=False
        ).count()
        total_research = ResearchTask.objects.filter(
            created_by=user, is_deleted=False
        ).count()

        return {
            'total_sessions': chat_sessions,
            'total_messages': total_messages,
            'total_tokens': total_tokens,
            'total_cost': total_cost,
            'avg_response_time': avg_response_time,
            'total_events': total_events,
            'api_requests': api_requests,
            'api_errors': api_errors,
            'total_documents': total_documents,
            'total_workflows': total_workflows,
            'total_research': total_research,
        }

    @classmethod
    def _get_usage_trend(cls, user, since) -> list:
        ChatSession, ChatMessage, _, _, _, _ = _import_models()

        now = timezone.now()
        chat_daily = ChatMessage.objects.filter(
            session__user=user,
            session__is_deleted=False,
            created_at__gte=since,
        ).annotate(
            day=TruncDate('created_at')
        ).values('day').annotate(
            msg_count=Count('id'),
            token_sum=Sum('token_count'),
        ).order_by('day')
        chat_map = {
            item['day']: {'messages': item['msg_count'], 'tokens': item['token_sum'] or 0}
            for item in chat_daily
        }

        session_daily = ChatSession.objects.filter(
            user=user, is_deleted=False, created_at__gte=since
        ).annotate(
            day=TruncDate('created_at')
        ).values('day').annotate(
            session_count=Count('id'),
        ).order_by('day')
        session_map = {item['day']: item['session_count'] for item in session_daily}

        result = []
        for i in range(7):
            day = (now - timedelta(days=6 - i)).date()
            chat_data = chat_map.get(day, {'messages': 0, 'tokens': 0})
            result.append({
                'date': day.strftime('%m/%d'),
                'events': chat_data['messages'] + session_map.get(day, 0),
                'tokens': chat_data['tokens'],
            })
        return result

    @classmethod
    def _get_category_distribution(cls, user) -> list:
        ChatSession, ChatMessage, Document, DocumentIndex, WorkflowSession, ResearchTask = _import_models()

        items = []
        chat_count = ChatSession.objects.filter(user=user, is_deleted=False).count()
        if chat_count > 0:
            items.append({'name': '智能聊天', 'count': chat_count})

        doc_count = Document.objects.filter(index__user=user, is_deleted=False).count()
        if doc_count > 0:
            items.append({'name': 'RAG检索', 'count': doc_count})

        workflow_count = WorkflowSession.objects.filter(created_by=user, is_deleted=False).count()
        if workflow_count > 0:
            items.append({'name': '学习工作流', 'count': workflow_count})

        research_count = ResearchTask.objects.filter(created_by=user, is_deleted=False).count()
        if research_count > 0:
            items.append({'name': '深度研究', 'count': research_count})

        event_dist = UserEvent.objects.filter(
            user=user, is_deleted=False
        ).exclude(event_category=EventCategory.API_CALL).values(
            'event_category'
        ).annotate(count=Count('id')).order_by('-count')

        category_labels = {c.value: c.label for c in EventCategory}
        event_category_map = {}
        for item in event_dist:
            label = category_labels.get(item['event_category'], item['event_category'])
            if label not in [i['name'] for i in items]:
                event_category_map[label] = item['count']
            else:
                for i in items:
                    if i['name'] == label:
                        i['count'] += item['count']
                        break

        for name, count in event_category_map.items():
            if count > 0:
                items.append({'name': name, 'count': count})

        total = sum(item['count'] for item in items) or 1
        for item in items:
            item['value'] = round(item['count'] / total * 100)

        items.sort(key=lambda x: x['count'], reverse=True)
        return items

    @classmethod
    def _get_feature_usage(cls, user) -> list:
        ChatSession, ChatMessage, Document, DocumentIndex, WorkflowSession, ResearchTask = _import_models()

        items = []

        msg_count = ChatMessage.objects.filter(
            session__user=user, session__is_deleted=False, role='user'
        ).count()
        if msg_count > 0:
            items.append({'name': '发送聊天消息', 'value': msg_count})

        session_count = ChatSession.objects.filter(user=user, is_deleted=False).count()
        if session_count > 0:
            items.append({'name': '创建会话', 'value': session_count})

        doc_count = Document.objects.filter(index__user=user, is_deleted=False).count()
        if doc_count > 0:
            items.append({'name': '上传文档', 'value': doc_count})

        index_count = DocumentIndex.objects.filter(user=user, is_deleted=False).count()
        if index_count > 0:
            items.append({'name': '创建知识库', 'value': index_count})

        workflow_count = WorkflowSession.objects.filter(created_by=user, is_deleted=False).count()
        if workflow_count > 0:
            items.append({'name': '启动工作流', 'value': workflow_count})

        research_count = ResearchTask.objects.filter(created_by=user, is_deleted=False).count()
        if research_count > 0:
            items.append({'name': '启动研究', 'value': research_count})

        assistant_count = ChatMessage.objects.filter(
            session__user=user, session__is_deleted=False, role='assistant'
        ).count()
        if assistant_count > 0:
            items.append({'name': '接收AI回复', 'value': assistant_count})

        event_dist = UserEvent.objects.filter(
            user=user, is_deleted=False
        ).exclude(event_type=EventType.API_REQUEST).values(
            'event_type'
        ).annotate(count=Count('id')).order_by('-count')[:10]

        type_labels = {t.value: t.label for t in EventType}
        existing_names = {i['name'] for i in items}
        for item in event_dist:
            label = type_labels.get(item['event_type'], item['event_type'])
            if label not in existing_names:
                items.append({'name': label, 'value': item['count']})
                existing_names.add(label)

        items.sort(key=lambda x: x['value'], reverse=True)
        return items[:10]

    @classmethod
    def _get_model_distribution(cls, user) -> list:
        ChatSession, ChatMessage, _, _, _, _ = _import_models()

        messages = ChatMessage.objects.filter(
            session__user=user,
            session__is_deleted=False,
            role='assistant',
        ).exclude(model='').exclude(model__isnull=True)

        dist = messages.values('model').annotate(
            count=Count('id')
        ).order_by('-count')[:5]

        if not dist.exists():
            mode_dist = ChatSession.objects.filter(
                user=user, is_deleted=False
            ).values('mode').annotate(
                count=Count('id')
            ).order_by('-count')[:5]
            from Django_xm.apps.chat.models import ChatMode
            mode_labels = {m.value: m.label for m in ChatMode}
            return [
                {'name': mode_labels.get(item['mode'], item['mode']), 'value': item['count']}
                for item in mode_dist
            ]

        return [
            {'name': item['model'], 'value': item['count']}
            for item in dist
        ]

    @classmethod
    def _get_performance_metrics(cls, user, since) -> dict:
        ChatSession, ChatMessage, _, _, _, _ = _import_models()

        msg_agg = ChatMessage.objects.filter(
            session__user=user,
            session__is_deleted=False,
            role='assistant',
            response_time__gt=0,
        ).aggregate(
            avg_time=Avg('response_time'),
            count=Count('id'),
        )

        api_events = UserEvent.objects.filter(
            user=user, is_deleted=False,
            event_type=EventType.API_REQUEST,
            created_at__gte=since,
        )
        api_agg = api_events.aggregate(
            avg_duration=Avg('duration_ms'),
            error_count=Count('id', filter=Q(is_success=False)),
            total_count=Count('id'),
        )

        avg_response_time_ms = 0
        if msg_agg['avg_time']:
            avg_response_time_ms = round(float(msg_agg['avg_time']) * 1000, 0)
        elif api_agg['avg_duration']:
            avg_response_time_ms = round(float(api_agg['avg_duration']), 0)

        error_rate = 0
        if api_agg['total_count'] and api_agg['total_count'] > 0:
            error_rate = round(
                api_agg['error_count'] / api_agg['total_count'] * 100, 2
            )

        tracked = UserEvent.objects.filter(
            user=user, is_deleted=False,
            duration_ms__isnull=False,
        ).count()

        return {
            'avg_response_time_ms': avg_response_time_ms,
            'error_rate': error_rate,
            'total_tracked_requests': tracked or (msg_agg['count'] or 0),
        }

    @classmethod
    def _get_recent_activities(cls, user, limit=20) -> list:
        events = UserEvent.objects.filter(
            user=user, is_deleted=False
        ).select_related('user').order_by('-created_at')[:limit]

        type_labels = {t.value: t.label for t in EventType}
        category_labels = {c.value: c.label for c in EventCategory}
        return [
            {
                'id': e.id,
                'event_type': e.event_type,
                'event_type_label': type_labels.get(e.event_type, e.event_type),
                'event_category': e.event_category,
                'event_category_label': category_labels.get(e.event_category, e.event_category),
                'resource_type': e.resource_type,
                'metadata': e.metadata,
                'is_success': e.is_success,
                'duration_ms': e.duration_ms,
                'created_at': e.created_at.isoformat(),
            }
            for e in events
        ]

    @classmethod
    def record_page_view(cls, user, page_path: str, page_title: str = '',
                         ip_address: str = None, user_agent: str = ''):
        try:
            UserEvent.objects.create(
                user=user,
                event_type=EventType.PAGE_VIEW,
                event_category=EventCategory.PAGE_VIEW,
                metadata={
                    'path': page_path,
                    'title': page_title,
                },
                ip_address=ip_address,
                user_agent=user_agent[:500] if user_agent else '',
            )
            cls.invalidate_user_cache(user.id)
        except Exception as e:
            logger.warning(f"记录页面浏览事件失败: {e}")

    @classmethod
    def record_feature_usage(cls, user, feature_name: str, metadata: dict = None,
                             ip_address: str = None, user_agent: str = ''):
        try:
            UserEvent.objects.create(
                user=user,
                event_type=EventType.FEATURE_USE,
                event_category=EventCategory.SYSTEM,
                metadata={
                    'feature': feature_name,
                    **(metadata or {}),
                },
                ip_address=ip_address,
                user_agent=user_agent[:500] if user_agent else '',
            )
            cls.invalidate_user_cache(user.id)
        except Exception as e:
            logger.warning(f"记录功能使用事件失败: {e}")

    @classmethod
    def invalidate_user_cache(cls, user_id: int):
        CacheService.delete(f"analytics:dashboard:user_{user_id}")
