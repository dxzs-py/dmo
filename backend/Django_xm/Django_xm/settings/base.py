"""
Django settings for Django_xm project - 基础共享配置

dev.py 和 prod.py 的公共配置基类，消除重复代码。
仅保留环境无关的框架设置，环境差异由子模块覆盖。

配置架构:
  - 项目级真相源: apps/core/config.py (Pydantic ProjectSettings, 读取 .env / 环境变量)
  - AI 真相源: apps/ai_engine/config.py (继承 ProjectSettings, 添加 AI 专属配置)
  - base.py: Django 框架公共设置
  - dev.py: 开发环境覆盖
  - prod.py: 生产环境覆盖
"""

import os
import sys
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from Django_xm.apps.ai_engine.config import settings as app_cfg
from Django_xm.apps.core.config import settings as project_cfg

if app_cfg.langsmith_tracing and app_cfg.langsmith_api_key:
    os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
    os.environ.setdefault("LANGCHAIN_API_KEY", app_cfg.langsmith_api_key)
    os.environ.setdefault("LANGCHAIN_PROJECT", app_cfg.langsmith_project)
    os.environ.setdefault("LANGCHAIN_ENDPOINT", app_cfg.langsmith_endpoint)

BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BASE_DIR.parent

SECRET_KEY = os.environ.get("SECRET_KEY", project_cfg.secret_key)
if not SECRET_KEY:
    raise ValueError("SECRET_KEY environment variable is not set!")

DEBUG = project_cfg.debug

ALLOWED_HOSTS = (
    [h.strip() for h in project_cfg.allowed_hosts.split(",") if h.strip()]
    if project_cfg.allowed_hosts
    else ["localhost", "127.0.0.1"]
)

INSTALLED_APPS = [
    "daphne",  # ASGI WebSocket 服务器（必须在 django 相关 app 之前）
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "drf_spectacular",
    "channels",  # Django Channels（WebSocket + 后台任务）
    "Django_xm.apps.cache_manager.apps.CacheManagerConfig",
    "Django_xm.apps.attachments.apps.AttachmentsConfig",
    "Django_xm.apps.users.apps.UsersConfig",
    "Django_xm.apps.core.apps.CoreConfig",
    "Django_xm.apps.ai_engine.apps.AiEngineConfig",
    "Django_xm.apps.context_manager.apps.ContextManagerConfig",
    "Django_xm.apps.tools.apps.ToolsConfig",
    "Django_xm.apps.approvals.apps.ApprovalsConfig",
    "Django_xm.apps.chat.apps.ChatConfig",
    "Django_xm.apps.knowledge.apps.KnowledgeConfig",
    "Django_xm.apps.learning.apps.LearningConfig",
    "Django_xm.apps.research.apps.ResearchConfig",
    "Django_xm.apps.realtime.apps.RealtimeConfig",
    "Django_xm.apps.analytics.apps.AnalyticsConfig",
    "Django_xm.apps.agent_hub",
]

ROOT_URLCONF = "Django_xm.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "Django_xm.wsgi.application"
# Django Channels ASGI 应用路径（WebSocket 支持）
ASGI_APPLICATION = "Django_xm.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": os.environ.get("DB_HOST", "127.0.0.1"),
        "PORT": int(os.environ.get("DB_PORT", "5432")),
        "USER": os.environ.get("DB_USER", "daixing"),
        "PASSWORD": os.environ.get("DB_PASSWORD", ""),
        "NAME": os.environ.get("DB_NAME", "langchain"),
        "CONN_MAX_AGE": 60,
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": {
            "connect_timeout": 10,
        },
    }
}

if not DATABASES["default"]["USER"]:
    raise ValueError("DB_USER environment variable is not set!")
if not DATABASES["default"]["PASSWORD"]:
    raise ValueError("DB_PASSWORD environment variable is not set!")
if not DATABASES["default"]["NAME"]:
    raise ValueError("DB_NAME environment variable is not set!")

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True

CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": os.environ.get("REDIS_URL", project_cfg.redis_url),
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
            "CONNECTION_POOL_KWARGS": {
                "max_connections": 50,
                "retry_on_timeout": True,
                "socket_timeout": 3,
                "socket_connect_timeout": 3,
            },
            "PASSWORD": os.environ.get("REDIS_PASSWORD", project_cfg.redis_password),
        },
        "TIMEOUT": project_cfg.redis_default_timeout,
        "KEY_PREFIX": "langchain_xm",
    },
    "chat_sessions": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": os.environ.get("REDIS_CHAT_URL", project_cfg.redis_chat_url),
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
            "CONNECTION_POOL_KWARGS": {
                "max_connections": 100,
                "retry_on_timeout": True,
                "socket_timeout": 3,
                "socket_connect_timeout": 3,
            },
            "PASSWORD": os.environ.get("REDIS_PASSWORD", project_cfg.redis_password),
        },
        "TIMEOUT": project_cfg.redis_chat_timeout,
        "KEY_PREFIX": "chat_session",
    },
}

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_USER_MODEL = "users.User"
AUTHENTICATION_BACKENDS = [
    "Django_xm.apps.users.backends.UsernameMobileAuthBackend",
    "django.contrib.auth.backends.ModelBackend",
]

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "Django_xm.apps.core.authentication.QueryParamTokenAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "EXCEPTION_HANDLER": "Django_xm.common.exceptions.custom_exception_handler",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_THROTTLE_CLASSES": [
        "Django_xm.apps.core.throttling.AnonymousRateThrottle",
        "Django_xm.apps.core.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anonymous": "100/min",
        "user": "200/min",
        "login": "5/min",
        "chat_stream": "30/min",
        "research": "5/min",
        "knowledge": "60/min",
        "sensitive": "10/min",
        "snapshot": "30/min",
        # 页面加载即请求的只读元数据接口（多浏览器并发时单页约 13 个请求），
        # 60/min 会被 4 浏览器同时打开瞬间打满，提升至 300/min 匹配设计意图
        "meta": "300/min",
    },
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    "SEARCH_PARAM": "search",
    "ORDERING_PARAM": "ordering",
    "NUM_PROXIES": 0,
    "DEFAULT_FILTER_BACKENDS": [
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "COERCE_DECIMAL_TO_STRING": False,
    "UPLOADED_FILES_USE_URL": True,
    "JSON_UNDERSCOREIZE": {
        "no_underscore_before_number": True,
    },
}

SIMPLE_JWT = {
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "ALGORITHM": "HS256",
    "SIGNING_KEY": SECRET_KEY,
    "TOKEN_OBTAIN_SERIALIZER": "Django_xm.apps.users.serializers.MyTokenObtainPairSerializer",
    "UPDATE_LAST_LOGIN": True,
}

SPECTACULAR_SETTINGS = {
    "TITLE": f"{project_cfg.app_name} API",
    "DESCRIPTION": f"{project_cfg.app_name} 智能学习 & 研究助手 API",
    "VERSION": project_cfg.app_version,
    "SERVE_INCLUDE_SCHEMA": False,
    "CONTACT": {"name": "API Support"},
    "LICENSE": {"name": "MIT License"},
}

CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
CORS_ALLOW_HEADERS = [
    "accept",
    "accept-encoding",
    "authorization",
    "content-type",
    "dnt",
    "origin",
    "user-agent",
    "x-csrftoken",
    "x-requested-with",
]
CORS_EXPOSE_HEADERS = ["content-disposition", "X-Captcha-Key", "X-Request-Duration"]

SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"

_upload_limit = project_cfg.upload_max_memory_size_mb * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = _upload_limit
FILE_UPLOAD_MAX_MEMORY_SIZE = _upload_limit
FILE_UPLOAD_PERMISSIONS = 0o644
FILE_UPLOAD_DIRECTORY_PERMISSIONS = 0o755

EMAIL_BACKEND = project_cfg.email_backend
DEFAULT_FROM_EMAIL = project_cfg.default_from_email
SERVER_EMAIL = project_cfg.default_from_email

LOCALE_PATHS = [BASE_DIR / "locale"]

APP_NAME = project_cfg.app_name
APP_VERSION = project_cfg.app_version

DATA_DIR = PROJECT_ROOT / project_cfg.data_dir
INDEXES_DIR = PROJECT_ROOT / app_cfg.vector_store_path
UPLOADS_DIR = PROJECT_ROOT / project_cfg.data_uploads_path

MEDIA_ROOT = DATA_DIR / "media"

TOOLS_DIR = DATA_DIR / "tools"
TOOLS_SKILLS_DIR = TOOLS_DIR / "skills"
TOOLS_LANGCHAIN_DIR = TOOLS_DIR / "langchain"
TOOLS_MCP_DIR = TOOLS_DIR / "mcp"

for directory in [DATA_DIR, UPLOADS_DIR, TOOLS_DIR, TOOLS_SKILLS_DIR, TOOLS_LANGCHAIN_DIR, TOOLS_MCP_DIR, MEDIA_ROOT]:
    directory.mkdir(parents=True, exist_ok=True)

CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = project_cfg.celery_task_time_limit
CELERY_TASK_SOFT_TIME_LIMIT = project_cfg.celery_task_time_limit - 60
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_WORKER_MAX_TASKS_PER_CHILD = project_cfg.celery_worker_max_tasks_per_child
CELERY_RESULT_EXPIRES = 86400
CELERY_BEAT_SCHEDULE_FILENAME = str(PROJECT_ROOT / "data" / "celerybeat" / "celerybeat-schedule")
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", project_cfg.celery_broker_url)
CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", project_cfg.celery_result_backend)
CELERY_WORKER_HIJACK_ROOT_LOGGER = False
CELERY_WORKER_MAX_MEMORY_PER_CHILD = 512000
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_TASK_DEFAULT_QUEUE = "celery"
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True

CELERY_TASK_ROUTES = {
    "rag.create_index": {"queue": "rag"},
    "rag.add_documents": {"queue": "rag"},
    "rag.delete_index": {"queue": "rag"},
    "rag.update_index": {"queue": "rag"},
    "research.run_research": {"queue": "research"},
    "workflow.execute": {"queue": "workflow"},
    "chat.cleanup_expired_attachments": {"queue": "chat"},
    "chat.index_old_attachments": {"queue": "chat"},
    "chat.check_storage_alerts": {"queue": "chat"},
    "chat.attachment_full_lifecycle": {"queue": "chat"},
    "chat.cleanup_checkpoints": {"queue": "chat"},
    "base.cleanup_old_task_records": {"queue": "celery"},
    "base.check_stale_tasks": {"queue": "celery"},
    "base.debug_task": {"queue": "celery"},
    "analytics.track_event": {"queue": "celery"},
    "approvals.cleanup_expired_approvals": {"queue": "celery"},
    "approvals.process_outbox": {"queue": "celery"},
    "approvals.resume_chat_after_timeout": {"queue": "celery"},
}

ATTACHMENT_DEFAULT_RETENTION_DAYS = int(os.environ.get("ATTACHMENT_DEFAULT_RETENTION_DAYS", 30))
ATTACHMENT_CLEANUP_HOUR = int(os.environ.get("ATTACHMENT_CLEANUP_HOUR", 3))
ATTACHMENT_ARCHIVE_ENABLED = os.environ.get("ATTACHMENT_ARCHIVE_ENABLED", "true").lower() == "true"
ATTACHMENT_ARCHIVE_DIR = PROJECT_ROOT / os.environ.get("ATTACHMENT_ARCHIVE_DIR", "data/archives")
ATTACHMENT_ARCHIVE_AFTER_DAYS = int(os.environ.get("ATTACHMENT_ARCHIVE_AFTER_DAYS", 60))
ATTACHMENT_STORAGE_WARNING_THRESHOLD = float(os.environ.get("ATTACHMENT_STORAGE_WARNING_THRESHOLD", 80))
ATTACHMENT_STORAGE_CRITICAL_THRESHOLD = float(os.environ.get("ATTACHMENT_STORAGE_CRITICAL_THRESHOLD", 95))
ATTACHMENT_DEDUP_ENABLED = os.environ.get("ATTACHMENT_DEDUP_ENABLED", "true").lower() == "true"
ATTACHMENT_MAX_TOTAL_SIZE_MB = int(os.environ.get("ATTACHMENT_MAX_TOTAL_SIZE_MB", 5120))

# 归档逻辑未实现，不自动创建空目录；实际实现归档功能时再恢复
# if ATTACHMENT_ARCHIVE_ENABLED:
#     ATTACHMENT_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

from celery.schedules import crontab

CELERY_BEAT_SCHEDULE = {
    "check-storage-alerts-every-hour": {
        "task": "chat.check_storage_alerts",
        "schedule": crontab(minute=10),
    },
    "index-old-attachments-daily": {
        "task": "chat.index_old_attachments",
        "schedule": crontab(hour=2, minute=0),
    },
    "cleanup-expired-attachments-daily": {
        "task": "chat.cleanup_expired_attachments",
        "schedule": crontab(hour=int(os.environ.get("ATTACHMENT_CLEANUP_HOUR", "3")), minute=0),
    },
    "cleanup-old-task-records-daily": {
        "task": "base.cleanup_old_task_records",
        "schedule": crontab(hour=4, minute=0),
    },
    "check-stale-tasks-hourly": {
        "task": "base.check_stale_tasks",
        "schedule": crontab(minute=30),
    },
    "cleanup-expired-approvals-every-minute": {
        "task": "approvals.cleanup_expired_approvals",
        "schedule": 60.0,
    },
    "process-approval-outbox-every-5s": {
        "task": "approvals.process_outbox",
        "schedule": 5.0,
    },
}

# AI Engine Settings
AI_RATE_LIMIT_MAX_MODEL_CALLS = 50

# Phase D: Docker 沙箱执行层配置
# HIGH 级工具调用在 Docker 容器内执行，防止不可逆变更影响宿主系统。
# 开发环境默认关闭（ENABLED=False），生产环境通过环境变量启用。
SANDBOX_CONFIG = {
    "ENABLED": os.environ.get("SANDBOX_ENABLED", "false").lower() == "true",
    "DOCKER_IMAGE": os.environ.get("SANDBOX_IMAGE", "python:3.11-slim"),
    "MEMORY_LIMIT": os.environ.get("SANDBOX_MEMORY", "512m"),
    "CPU_LIMIT": int(os.environ.get("SANDBOX_CPUS", "1")),
    "NETWORK_MODE": os.environ.get("SANDBOX_NETWORK", "none"),
    "SESSION_TIMEOUT": int(os.environ.get("SANDBOX_SESSION_TIMEOUT", "3600")),
    "WORK_DIR_MOUNT": str(PROJECT_ROOT),
}
AI_RATE_LIMIT_MAX_TOOL_CALLS = 30
AI_LLM_TIMEOUT = 120.0
AI_LLM_MAX_RETRIES = 3
AI_DEFAULT_PROVIDER = "deepseek"
AI_MODEL_CACHE_MAXSIZE = 32
AI_TOOL_PERMISSION_CACHE_TIMEOUT = 3600
AI_DEFAULT_MODEL_TOKEN_LIMIT = 128000

AI_HELPER_MODEL_PROVIDER = ""
AI_HELPER_MODEL_NAME = ""
AI_HELPER_MODEL_TEMPERATURE = 0.0
AI_HELPER_MODEL_MAX_TOKENS = 256

# ── Shell Exec 安全配置 ──────────────────────────────────────────
# 白名单命令：单一真相源在 Django_xm.apps.tools.langchain.shell.DEFAULT_WHITELIST_COMMANDS，
# 此处置为 None 使用代码级默认值（含平台过滤）。
# 如需部署级覆盖，设为列表将完全替换默认白名单（不会合并）。
SHELL_EXEC_WHITELIST = None
# 允许的工作目录（限制命令执行目录范围，None 表示不限制——生产环境必须配置）
SHELL_EXEC_ALLOWED_DIRS = [DATA_DIR, MEDIA_ROOT]
