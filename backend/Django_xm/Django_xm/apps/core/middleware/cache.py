"""
缓存控制中间件

为 API 响应添加缓存控制头
"""


class CacheControlMiddleware:
    """缓存控制中间件

    根据请求路径匹配不同的缓存策略，为 API 响应添加 Cache-Control 头。
    - 聊天/研究等实时性接口：no-store
    - 知识库接口：短时私有缓存
    - 其他 API 接口：默认私有缓存
    """

    CACHE_POLICIES = {
        '/api/chat/': 'no-store, no-cache, must-revalidate',
        '/api/research/': 'no-store, no-cache, must-revalidate',
        '/api/knowledge/': 'private, max-age=300',
        '/api/core/cache/': 'no-store, no-cache, must-revalidate',
    }

    DEFAULT_POLICY = 'private, max-age=60'

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        if not request.path.startswith('/api/'):
            return response

        if 'Cache-Control' in response:
            return response

        policy = self.DEFAULT_POLICY
        for path_prefix, cache_policy in self.CACHE_POLICIES.items():
            if request.path.startswith(path_prefix):
                policy = cache_policy
                break

        response['Cache-Control'] = policy
        response['X-Cache-Policy'] = 'middleware'
        return response
