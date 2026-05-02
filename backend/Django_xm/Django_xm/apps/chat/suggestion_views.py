"""
Suggestion API Views

动态建议生成接口 - 根据当前查询和上下文生成建议的后续问题
"""
import logging

from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated

from Django_xm.apps.common.responses import success_response, error_response
from Django_xm.apps.common.error_codes import ErrorCode
from Django_xm.apps.ai_engine.services.suggestion_service import generate_suggestions
from Django_xm.apps.ai_engine.services.cache_service import CacheService, CacheTTL

logger = logging.getLogger(__name__)


class SuggestionsView(APIView):
    """
    动态建议生成接口
    
    POST /api/chat/suggestions/
    
    Request:
    {
        "query": "用户当前问题",
        "context": "可选的上下文信息"
    }
    
    Response:
    {
        "code": 0,
        "message": "success",
        "data": {
            "suggestions": ["建议1", "建议2", "建议3"]
        }
    }
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            query = request.data.get('query', '').strip()
            context = request.data.get('context', '')

            if not query:
                return error_response(
                    code=ErrorCode.INVALID_PARAMS,
                    message='查询内容不能为空',
                    http_status=400
                )

            cache_key = f"suggestions:{hash(query)}:{hash(context)}"
            cached = CacheService.get(cache_key)
            if cached is not None:
                logger.info("建议缓存命中")
                return success_response(data={'suggestions': cached})

            suggestions = generate_suggestions(query, context)

            CacheService.set(cache_key, suggestions, CacheTTL.QUERY_LONG)

            return success_response(data={'suggestions': suggestions})
            
        except Exception as e:
            logger.error(f"生成建议失败: {e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message=str(e)
            )
