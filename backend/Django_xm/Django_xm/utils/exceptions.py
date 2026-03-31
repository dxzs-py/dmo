from django.conf import settings
from rest_framework.views import exception_handler
from rest_framework.response import Response
from rest_framework import status
import logging

logger = logging.getLogger(__name__)

def custom_exception_handler(exc, context):
    response = exception_handler(exc, context)
    
    if response is not None:
        response.data['success'] = False
        response.data['error'] = response.data.get('detail', str(exc))
        if 'detail' in response.data:
            del response.data['detail']
    else:
        logger.error(f"未处理的异常: {str(exc)}", exc_info=True)
        response = Response(
            {
                'success': False,
                'error': '服务器内部错误',
                'message': str(exc) if settings.DEBUG else None
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
    
    return response
