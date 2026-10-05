from rest_framework.permissions import BasePermission


class IsAuthenticatedOrQueryParam(BasePermission):
    """支持标准认证或查询参数 token 认证的权限类

    用于**无法设置 Authorization 头**的场景，当前只有 `<a href>` 文件下载
    （前端以 <a download> 触发，浏览器无法附加请求头）。

    SSE 不再需要它：前端 `fetchSSE` / `rawSSEFetch` 均为 fetch 实现且已注入
    Authorization 头，故 SSE 走标准认证即可。

    注意：本权限类**自行**实例化 QueryParamTokenAuthentication 完成认证，
    因此不依赖该认证类出现在 DEFAULT_AUTHENTICATION_CLASSES 中。
    """

    def has_permission(self, request, view):
        if request.user and request.user.is_authenticated:
            return True
        from .authentication import QueryParamTokenAuthentication

        authenticator = QueryParamTokenAuthentication()
        result = authenticator.authenticate(request)
        if result is not None:
            user, _ = result
            request.user = user
            return True
        return False
