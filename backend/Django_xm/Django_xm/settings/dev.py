"""
Django settings for Django_xm project - 开发环境配置

继承 base.py 公共配置，仅覆盖开发环境差异项。
"""

import sys

from .base import *

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

DEBUG = True

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

CORS_ALLOWED_ORIGINS = [o.strip() for o in app_cfg.cors_allowed_origins.split(",") if o.strip()] or [
    "http://localhost:3000",
    "http://localhost:8000",
]

CSRF_TRUSTED_ORIGINS = [o.strip() for o in app_cfg.csrf_trusted_origins.split(",") if o.strip()] or CORS_ALLOWED_ORIGINS

REST_FRAMEWORK["DEFAULT_AUTHENTICATION_CLASSES"] = [
    "rest_framework_simplejwt.authentication.JWTAuthentication",
    "rest_framework.authentication.SessionAuthentication",
    "rest_framework.authentication.BasicAuthentication",
]
REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"] = [
    "rest_framework.renderers.JSONRenderer",
    "rest_framework.renderers.BrowsableAPIRenderer",
]
REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"] = {
    "anonymous": "100/min",
    "user": "200/min",
    "login": "5/min",
    "chat_stream": "30/min",
    "research": "5/min",
    "knowledge": "60/min",
    "learning": "10/min",
    "sensitive": "10/min",
    "snapshot": "120/min",
    # 页面加载即请求的只读元数据接口（多浏览器并发时单页约 13 个请求），
    # 60/min 会被 4 浏览器同时打开瞬间打满，提升至 300/min 匹配设计意图
    "meta": "300/min",
}

SIMPLE_JWT["ACCESS_TOKEN_LIFETIME"] = timedelta(days=app_cfg.jwt_access_token_lifetime_days)
SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"] = timedelta(days=app_cfg.jwt_refresh_token_lifetime_days)

SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
SESSION_COOKIE_AGE = app_cfg.session_cookie_age

CORS_EXPOSE_HEADERS = ["content-disposition", "X-Captcha-Key", "X-Request-Duration"]

CELERY_BROKER_TRANSPORT_OPTIONS = {
    "max_connections": 10,
    "visibility_timeout": 43200,
}
CELERY_BROKER_CONNECTION_MAX_RETRIES = 10
CELERY_BROKER_POOL_LIMIT = 10
CELERY_REDIS_BACKEND_HEALTH_CHECK_INTERVAL = 60
# dev 环境：单 worker 运行，清空 CELERY_TASK_ROUTES 使所有任务统一走 celery 队列。
# CELERY_TASK_DEFAULT_QUEUE = "celery"（base.py 已设置），无需担心路由丢失。
CELERY_TASK_ROUTES = None

# MCP_SERVERS 配置已迁移到 apps/tools/mcp/config.py（Task 27.3），
# 由 discovery.py 直接 import get_system_mcp_servers() 调用，
# 不再走 Django settings 间接访问。
