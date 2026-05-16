"""
Django settings for Django_xm project - 生产环境配置

继承 base.py 公共配置，仅覆盖生产环境差异项。
重要: 本文件为生产环境专用，请勿在开发环境使用!
"""

from .base import *  # noqa: F401,F403

import sys

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
    "Django_xm.apps.core.middleware.SessionSecurityMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "Django_xm.apps.core.middleware.RequestTimeoutMiddleware",
    "Django_xm.apps.analytics.middleware.AnalyticsMiddleware",
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

REST_FRAMEWORK["DEFAULT_AUTHENTICATION_CLASSES"] = [  # noqa: F405
    'rest_framework_simplejwt.authentication.JWTAuthentication',
]
REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"] = [  # noqa: F405
    "rest_framework.renderers.JSONRenderer",
]
REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"] = {  # noqa: F405
    "anonymous": "60/min",
    "user": "120/min",
    "login": "5/min",
    "chat_stream": "20/min",
    "research": "5/min",
    "knowledge": "60/min",
    "sensitive": "5/min",
}

SIMPLE_JWT["ACCESS_TOKEN_LIFETIME"] = timedelta(minutes=app_cfg.jwt_access_token_lifetime_minutes)  # noqa: F405
SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"] = timedelta(days=app_cfg.jwt_refresh_token_lifetime_days)  # noqa: F405

SECURE_SSL_REDIRECT = True
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "%(levelname)s %(asctime)s %(module)s %(lineno)d %(message)s"
        },
        "simple": {
            "format": "%(levelname)s %(module)s %(lineno)d %(message)s"
        },
    },
    "handlers": {
        "file": {
            "level": "WARNING",
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(BASE_DIR.parent / "logs" / "django_prod.log"),  # noqa: F405
            "maxBytes": 100 * 1024 * 1024,
            "backupCount": 14,
            "formatter": "verbose",
            "encoding": "utf-8",
        },
    },
    "loggers": {
        "django": {
            "handlers": ["file"],
            "level": "INFO",
            "propagate": True,
        },
        "django.security": {
            "handlers": ["file"],
            "level": "WARNING",
            "propagate": False,
        },
        "langchain": {
            "handlers": ["file"],
            "level": "WARNING",
            "propagate": False,
        },
    },
}

CELERY_BROKER_TRANSPORT_OPTIONS = {
    'max_connections': 20,
    'visibility_timeout': 43200,
}
CELERY_BROKER_CONNECTION_MAX_RETRIES = 20
CELERY_BROKER_POOL_LIMIT = 20
CELERY_REDIS_BACKEND_HEALTH_CHECK_INTERVAL = 30


