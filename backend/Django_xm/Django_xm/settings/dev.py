"""
Django settings for Django_xm project - 开发环境配置

继承 base.py 公共配置，仅覆盖开发环境差异项。
"""

from .base import *  # noqa: F401,F403

import sys

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

DEBUG = True

try:
    import redis
    r = redis.from_url(os.environ.get('REDIS_URL', app_cfg.redis_url), socket_connect_timeout=3)
    r.ping()
    REDIS_AVAILABLE = True
except Exception as e:
    REDIS_AVAILABLE = False
    import logging
    logging.getLogger(__name__).warning(f"Redis 启动检测失败: {e} (django_redis 将在请求时自动重连)")

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
    "Django_xm.apps.core.middleware.AIExceptionMiddleware",
    "Django_xm.apps.core.middleware.SecurityHeadersMiddleware",
]

CORS_ALLOWED_ORIGINS = [
    o.strip() for o in app_cfg.cors_allowed_origins.split(",") if o.strip()
] or ["http://localhost:3000", "http://localhost:8000"]

CSRF_TRUSTED_ORIGINS = [
    o.strip() for o in app_cfg.csrf_trusted_origins.split(",") if o.strip()
] or CORS_ALLOWED_ORIGINS

REST_FRAMEWORK["DEFAULT_AUTHENTICATION_CLASSES"] = [  # noqa: F405
    'rest_framework_simplejwt.authentication.JWTAuthentication',
    'rest_framework.authentication.SessionAuthentication',
    'rest_framework.authentication.BasicAuthentication',
]
REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"] = [  # noqa: F405
    "rest_framework.renderers.JSONRenderer",
    "rest_framework.renderers.BrowsableAPIRenderer",
]
REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"] = {  # noqa: F405
    "anonymous": "100/min",
    "user": "200/min",
    "login": "5/min",
    "chat_stream": "30/min",
    "research": "5/min",
    "knowledge": "60/min",
    "sensitive": "10/min",
}

SIMPLE_JWT["ACCESS_TOKEN_LIFETIME"] = timedelta(days=app_cfg.jwt_access_token_lifetime_days)  # noqa: F405
SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"] = timedelta(days=app_cfg.jwt_refresh_token_lifetime_days)  # noqa: F405

SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
SESSION_COOKIE_AGE = app_cfg.session_cookie_age

CORS_EXPOSE_HEADERS = ["content-disposition", "X-Captcha-Key", "X-Request-Duration"]

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
    "filters": {
        "require_debug_true": {
            "()": "django.utils.log.RequireDebugTrue",
        },
    },
    "handlers": {
        "console": {
            "level": "DEBUG",
            "filters": ["require_debug_true"],
            "class": "logging.StreamHandler",
            "formatter": "simple",
            "stream": "ext://sys.stdout",
        },
        "file": {
            "level": "INFO",
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(BASE_DIR.parent / "logs" / "django.log"),  # noqa: F405
            "maxBytes": 300 * 1024 * 1024,
            "backupCount": 10,
            "formatter": "verbose",
            "encoding": "utf-8",
        },
    },
    "loggers": {
        "django": {
            "handlers": ["console", "file"],
            "propagate": True,
        },
        "langchain": {
            "handlers": ["console", "file"],
            "level": "INFO",
            "propagate": False,
        },
        "Django_xm.apps.chat": {
            "handlers": ["console", "file"],
            "level": "DEBUG",
            "propagate": False,
        },
        "Django_xm.apps.ai_engine": {
            "handlers": ["console", "file"],
            "level": "DEBUG",
            "propagate": False,
        },
        "Django_xm.apps.tools": {
            "handlers": ["console", "file"],
            "level": "DEBUG",
            "propagate": False,
        },
    },
}

CELERY_BROKER_TRANSPORT_OPTIONS = {
    'max_connections': 10,
    'visibility_timeout': 43200,
}
CELERY_BROKER_CONNECTION_MAX_RETRIES = 10
CELERY_BROKER_POOL_LIMIT = 10
CELERY_REDIS_BACKEND_HEALTH_CHECK_INTERVAL = 60
CELERY_TASK_ROUTES = {}

import os as _os
_npx_path = None
_node_dir = None
for _p in [r'D:\Front-end\nvm\v20.19.5\npx.cmd', r'D:\Front-end\npm\npx.cmd', 'npx']:
    if _os.path.exists(_p) or _p == 'npx':
        _npx_path = _p
        break
if _npx_path and _os.path.exists(r'D:\Front-end\nvm\v20.19.5\node.exe'):
    _node_dir = r'D:\Front-end\nvm\v20.19.5'

MCP_SERVERS = [
    {
        "name": "sequential-thinking",
        "transport": "stdio",
        "command": _npx_path,
        "args": ["-y", "@modelcontextprotocol/server-sequential-thinking"],
        "env": {"PATH": f"{_node_dir};{_os.environ.get('PATH', '')}"} if _node_dir else None,
        "description": "结构化渐进式思维工具 - 将复杂问题分解为可管理的步骤，支持修正和分支推理",
        "enabled": True,
    },
    {
        "name": "context7",
        "url": "https://mcp.context7.com/mcp",
        "transport": "http",
        "description": "实时库文档查询 - 获取最新的、版本特定的库文档和代码示例",
        "enabled": True,
    },
]

MCP_LOCAL_TOOLS_ENABLED = True
