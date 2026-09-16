import logging

from drf_spectacular.utils import extend_schema
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView

from Django_xm.apps.ai_engine.services.checkpointer_factory import get_store
from Django_xm.apps.context_manager.services.manager import create_context_manager
from Django_xm.apps.core.throttling import MetaRateThrottle
from Django_xm.common.error_codes import ErrorCode
from Django_xm.common.exceptions import BaseAppError
from Django_xm.common.responses import success_response
from Django_xm.common.serializers import EmptySerializer

from .serializers import (
    ContextCompressRequestSerializer,
    ContextCompressResponseSerializer,
    ContextStatsSerializer,
    KnowledgeGraphDetailRequestSerializer,
    KnowledgeGraphDetailResponseSerializer,
    TokenBudgetRequestSerializer,
    TokenBudgetResponseSerializer,
)

logger = logging.getLogger(__name__)


class ContextStatsView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: ContextStatsSerializer})
    def get(self, request):
        user_id = request.user.id
        session_id = request.query_params.get("session_id")
        store = get_store()
        ctx_mgr = create_context_manager(user_id=user_id, store=store, thread_id=session_id)
        stats = ctx_mgr.get_stats()
        serializer = ContextStatsSerializer(stats)
        return success_response(data=serializer.data)


class KnowledgeGraphView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: EmptySerializer})
    def delete(self, request):
        confirm = request.query_params.get("confirm", "").lower() in ("1", "true", "yes")
        if not confirm:
            raise BaseAppError(
                "需要确认才能清除知识图谱数据（请添加查询参数 confirm=true）",
                business_code=ErrorCode.VALIDATION_FAILED,
            )

        user_id = request.user.id
        session_id = request.query_params.get("session_id")
        store = get_store()
        ctx_mgr = create_context_manager(user_id=user_id, store=store, thread_id=session_id)
        if ctx_mgr._knowledge_graph:
            ctx_mgr._knowledge_graph.clear_user_graph(user_id)
            return success_response(message="知识图谱数据已清除")
        return success_response(message="知识图谱未启用，无需清除")


class TokenBudgetView(APIView):
    """获取 Token 预算使用情况"""

    permission_classes = [IsAuthenticated]

    @extend_schema(parameters=[TokenBudgetRequestSerializer], responses={200: TokenBudgetResponseSerializer})
    def get(self, request):
        req_serializer = TokenBudgetRequestSerializer(data=request.query_params)
        req_serializer.is_valid(raise_exception=True)

        user_id = request.user.id
        session_id = req_serializer.validated_data.get("session_id")
        store = get_store()
        ctx_mgr = create_context_manager(user_id=user_id, store=store, thread_id=session_id)
        ctx_mgr.get_budget_usage()

        total_budget = ctx_mgr._token_budget_manager.total_budget
        total_used = ctx_mgr._token_budget_manager.total_used
        remaining = max(0, total_budget - total_used)
        utilization_percent = round(total_used / total_budget * 100, 2) if total_budget > 0 else 0.0

        data = {
            "total_budget": total_budget,
            "used": total_used,
            "remaining": remaining,
            "utilization_percent": utilization_percent,
        }
        resp_serializer = TokenBudgetResponseSerializer(data)
        return success_response(data=resp_serializer.data)


class ContextCompressView(APIView):
    """手动触发上下文压缩"""

    permission_classes = [IsAuthenticated]

    @extend_schema(request=ContextCompressRequestSerializer, responses={200: ContextCompressResponseSerializer})
    def post(self, request):
        req_serializer = ContextCompressRequestSerializer(data=request.data)
        req_serializer.is_valid(raise_exception=True)

        session_id = req_serializer.validated_data["session_id"]

        user_id = request.user.id
        store = get_store()
        ctx_mgr = create_context_manager(user_id=user_id, store=store, thread_id=session_id)

        if not ctx_mgr._compression_engine:
            raise BaseAppError("压缩引擎未启用", business_code=ErrorCode.SERVICE_UNAVAILABLE)

        # 从 Store 加载会话消息
        namespace = (str(user_id), "sessions", session_id)
        messages = []
        try:
            item = store.get(namespace, "messages")
            if item and hasattr(item, "value"):
                messages = item.value if isinstance(item.value, list) else []
        except Exception:
            # Store 读取失败时回退到空列表，后续返回 404
            logger.debug("从 Store 加载会话消息失败，回退到空列表")

        if not messages:
            raise BaseAppError("未找到该会话的消息记录", business_code=ErrorCode.NOT_FOUND)

        original_count = len(messages)
        compressed_messages, comp_result = ctx_mgr._compression_engine.compress(messages)

        data = {
            "original_tokens": comp_result.original_token_estimate,
            "compressed_tokens": comp_result.compressed_token_estimate,
            "compression_ratio": round(comp_result.compression_ratio, 4),
            "messages_removed": original_count - len(compressed_messages),
        }
        resp_serializer = ContextCompressResponseSerializer(data)
        return success_response(data=resp_serializer.data, message="上下文压缩完成")


class KnowledgeGraphDetailView(APIView):
    """获取知识图谱实体和关系详情"""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        parameters=[KnowledgeGraphDetailRequestSerializer],
        responses={200: KnowledgeGraphDetailResponseSerializer},
    )
    def get(self, request):
        req_serializer = KnowledgeGraphDetailRequestSerializer(data=request.query_params)
        req_serializer.is_valid(raise_exception=True)

        user_id = request.user.id
        session_id = req_serializer.validated_data.get("session_id")
        store = get_store()
        ctx_mgr = create_context_manager(user_id=user_id, store=store, thread_id=session_id)

        if not ctx_mgr._knowledge_graph:
            raise BaseAppError("知识图谱未启用", business_code=ErrorCode.SERVICE_UNAVAILABLE)

        entities, relations = ctx_mgr._knowledge_graph.get_full_graph(user_id)

        data = {
            "entities": [e.to_dict() for e in entities],
            "relations": [r.to_dict() for r in relations],
            "entity_count": len(entities),
            "relation_count": len(relations),
        }
        resp_serializer = KnowledgeGraphDetailResponseSerializer(data)
        return success_response(data=resp_serializer.data)


@extend_schema(responses={200: EmptySerializer})
@api_view(["GET"])
@throttle_classes([MetaRateThrottle])
@permission_classes([AllowAny])
def capability_config_view(request):
    from Django_xm.apps.ai_engine.capabilities import registry

    agent_type = request.query_params.get("agent_type", "base")

    data = {
        "available_capabilities": registry.list_capabilities(),
        "default_capabilities": registry.get_default_capabilities(agent_type),
        "agent_type": agent_type,
    }

    return success_response(data=data)
