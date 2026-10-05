"""
Django settings for Django_xm project - 生产环境配置

继承 base.py 公共配置，仅覆盖生产环境差异项。
重要: 本文件为生产环境专用，请勿在开发环境使用!
"""

import copy
import sys

from .base import *  # noqa: F401,F403 — prod 继承 base 全量配置，仅覆盖差异项

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

DEBUG = False

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "Django_xm.apps.core.middleware.CurrentRequestMiddleware",
    "Django_xm.apps.core.middleware.SessionSecurityMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "Django_xm.apps.core.middleware.APIRequestMiddleware",
    "Django_xm.apps.core.middleware.CacheControlMiddleware",
    "Django_xm.apps.analytics.middleware.AnalyticsMiddleware",
    "Django_xm.apps.ai_engine.middleware.AIExceptionMiddleware",
    "Django_xm.apps.core.middleware.SecurityHeadersMiddleware",
]

CORS_ALLOWED_ORIGINS = (
    [o.strip() for o in app_cfg.cors_allowed_origins.split(",") if o.strip()]
    if app_cfg.cors_allowed_origins and app_cfg.cors_allowed_origins.strip()
    else []
)

CSRF_TRUSTED_ORIGINS = (
    [o.strip() for o in app_cfg.csrf_trusted_origins.split(",") if o.strip()]
    if app_cfg.csrf_trusted_origins and app_cfg.csrf_trusted_origins.strip()
    else []
)

CORS_EXPOSE_HEADERS = ["content-disposition", "X-Captcha-Key"]

REST_FRAMEWORK["DEFAULT_AUTHENTICATION_CLASSES"] = [
    "rest_framework_simplejwt.authentication.JWTAuthentication",
]
REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"] = [
    "rest_framework.renderers.JSONRenderer",
]
REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"] = {
    "anonymous": "60/min",
    "user": "120/min",
    "login": "5/min",
    "chat_stream": "20/min",
    "research": "5/min",
    "knowledge": "60/min",
    "learning": "10/min",
    "sensitive": "5/min",
    "snapshot": "120/min",
    "meta": "60/min",
}

SIMPLE_JWT["ACCESS_TOKEN_LIFETIME"] = timedelta(minutes=app_cfg.jwt_access_token_lifetime_minutes)
SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"] = timedelta(days=app_cfg.jwt_refresh_token_lifetime_days)

SECURE_SSL_REDIRECT = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# ---- 生产 LOGGING：以 base 为底增量覆盖 ----
# 修复（audit-supplement-remediate B1）：原实现整体替换 LOGGING，导致
# ①redact_sensitive 脱敏过滤器丢失（生产 4xx/5xx 的 ?token=<JWT> 不脱敏）；
# ②base 中全部 Django_xm.apps.* / services logger 丢失（应用日志仅剩
#   lastResort 输出 WARNING+ 到 stderr，django_prod.log 实际只收框架日志）。
# 现策略：深拷贝 base.LOGGING，仅覆盖 file handler 的生产参数（独立文件 +
# 生产轮转规格）。console 保留 require_debug_true（DEBUG=False 时自然静默）；
# file 级别取 base 的 INFO（应用 logger 多为 DEBUG 级，若钳到 WARNING 会
# 再次丢失应用日志）。文件名按 SERVICE_ROLE 角色区分，延续 base.py:495-498
# 的多进程分文件设计（避免 web 与 fastapi 服务竞争写同一文件）。
LOGGING = copy.deepcopy(LOGGING)
LOGGING["handlers"]["file"].update(
    {
        "filename": str(BASE_DIR.parent / "logs" / LOG_FILE_NAME.replace(".log", "_prod.log")),
        "maxBytes": 100 * 1024 * 1024,
        "backupCount": 14,
    }
)

CELERY_BROKER_TRANSPORT_OPTIONS = {
    "max_connections": 20,
    "visibility_timeout": 43200,
}
CELERY_BROKER_CONNECTION_MAX_RETRIES = 20
CELERY_BROKER_POOL_LIMIT = 20
CELERY_REDIS_BACKEND_HEALTH_CHECK_INTERVAL = 30
