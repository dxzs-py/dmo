"""
请求工具函数

提取公共的请求处理逻辑，遵循 DRY 原则。
"""


def get_client_ip(request):
    if request is None:
        return None
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def get_user_agent(request, max_length=500):
    if request is None:
        return ''
    return request.META.get('HTTP_USER_AGENT', '')[:max_length]
