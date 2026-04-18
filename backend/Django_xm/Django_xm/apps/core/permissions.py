from rest_framework.permissions import BasePermission
from rest_framework.authentication import TokenAuthentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.tokens import AccessToken


class QueryParamTokenAuthentication(TokenAuthentication):
    """
    支持查询参数传递token的认证类
    优先从Authorization header获取token，如果没有则从查询参数获取
    """
    keyword = 'Bearer'

    def authenticate(self, request):
        auth = self.get_authorization_header(request).split()

        if not auth or auth[0].lower() != self.keyword.lower().encode():
            token = request.GET.get('token')
            if token:
                return self.authenticate_token(token)
            return None

        if len(auth) == 1:
            msg = 'Invalid token header. No credentials provided.'
            raise AuthenticationFailed(msg)
        elif len(auth) > 2:
            msg = 'Invalid token header. Token string should not contain spaces.'
            raise AuthenticationFailed(msg)

        return self.authenticate_token(auth[1].decode())

    def authenticate_token(self, token):
        try:
            access_token = AccessToken(token)
            user_id = access_token['user_id']
            from Django_xm.apps.users.models import User
            user = User.objects.get(id=user_id)
            return (user, token)
        except Exception:
            raise AuthenticationFailed('Invalid token')


class IsAuthenticatedOrQueryParam(BasePermission):
    """
    允许通过查询参数token进行认证的权限类
    """
    def has_permission(self, request, view):
        from rest_framework_simplejwt.authentication import JWTAuthentication

        auth = JWTAuthentication()
        try:
            user_auth_tuple = auth.authenticate(request)
            if user_auth_tuple is not None:
                request.user, _ = user_auth_tuple
                return True
        except Exception:
            pass

        token = request.GET.get('token')
        if token:
            try:
                access_token = AccessToken(token)
                user_id = access_token['user_id']
                from Django_xm.apps.users.models import User
                user = User.objects.get(id=user_id)
                request.user = user
                return True
            except Exception:
                pass

        return False
