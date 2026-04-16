"""
Django settings for Django_xm project - 生产环境配置

重要: 本文件为生产环境专用，请勿在开发环境使用!
切换方式: 修改 settings/__init__.py 中的 DJANGO_ENV 环境变量

配置架构（迁移后）:
  - 真相源: apps/core/config.py (Pydantic Settings, 读取 .env / 环境变量)
  - 本文件: 仅保留 Django 框架原生设置 + 从 Pydantic 注入所需值

安全检查清单:
- DEBUG=False (必须)
- SECRET_KEY 从环境变量读取 (必须)
- ALLOWED_HOSTS 配置正确域名 (必须)
- 安全响应头已启用 (已通过中间件实现)
- CSRF/Clickjacking 保护已启用 (已通过中间件实现)
"""

import os
from pathlib import Path
from datetime import timedelta
from dotenv import load_dotenv

load_dotenv()

# ==================== 导入统一配置（Django 无关，可安全前置导入）====================
import sys
_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from Django_xm.apps.core.config import settings as app_cfg

BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BASE_DIR.parent

# ==================== 核心安全配置（从 Pydantic 注入）====================
SECRET_KEY = os.environ.get("SECRET_KEY", app_cfg.secret_key)
if not SECRET_KEY:
    raise ValueError("SECRET_KEY environment variable is not set! 生产环境必须设置SECRET_KEY")

DEBUG = False

ALLOWED_HOSTS = (
    [h.strip() for h in app_cfg.allowed_hosts.split(",") if h.strip()]
    if app_cfg.allowed_hosts
    else ["localhost", "127.0.0.1"]
)

# ==================== 应用注册 ====================
INSTALLED_APPS = [
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
    "Django_xm.apps.users",
    "Django_xm.apps.core",
    "Django_xm.apps.agents",
    "Django_xm.apps.chat",
    "Django_xm.apps.rag",
    "Django_xm.apps.workflows",
    "Django_xm.apps.deep_research",
]

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
    "Django_xm.apps.core.middleware.SecurityHeadersMiddleware",
]

CORS_ALLOWED_ORIGINS = (
    [o.strip() for o in app_cfg.cors_allowed_origins.split(",") if o.strip()]
    if app_cfg.cors_allowed_origins and app_cfg.cors_allowed_origins.strip()
    else []
)
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
CORS_ALLOW_HEADERS = [
    "accept", "accept-encoding", "authorization", "content-type",
    "dnt", "origin", "user-agent", "x-csrftoken", "x-requested-with",
]
CORS_EXPOSE_HEADERS = ["content-disposition", "X-Captcha-Key"]

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

# ==================== 数据库（生产环境不持久连接）====================
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.mysql",
        "HOST": os.environ.get("DB_HOST", app_cfg.db_host),
        "PORT": int(os.environ.get("DB_PORT", str(app_cfg.db_port))),
        "USER": os.environ.get("DB_USER"),
        "PASSWORD": os.environ.get("DB_PASSWORD"),
        "NAME": os.environ.get("DB_NAME", app_cfg.db_name),
        "OPTIONS": {
            "charset": "utf8mb4",
            "init_command": "SET sql_mode='STRICT_TRANS_TABLES'",
            "connect_timeout": 10,
            "read_timeout": 30,
            "write_timeout": 30,
        },
        "CONN_HEALTH_CHECKS": True,
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

# ==================== 生产安全头 ====================
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_SAMESITE = 'Lax'
CSRF_TRUSTED_ORIGINS = (
    [o.strip() for o in app_cfg.csrf_trusted_origins.split(",") if o.strip()]
    if app_cfg.csrf_trusted_origins and app_cfg.csrf_trusted_origins.strip()
    else []
)

LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True

# ==================== 缓存（从 Pydantic 注入 Redis 配置）====================
CACHES = {
    'default': {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': os.environ.get('REDIS_URL', app_cfg.redis_url),
        'OPTIONS': {
            'CLIENT_CLASS': 'django_redis.client.DefaultClient',
            'CONNECTION_POOL_KWARGS': {
                'max_connections': 50,
                'retry_on_timeout': True,
            },
            'PASSWORD': os.environ.get('REDIS_PASSWORD', app_cfg.redis_password),
        },
        'TIMEOUT': app_cfg.redis_default_timeout,
        'KEY_PREFIX': 'langchain_xm',
    },
    'chat_sessions': {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': os.environ.get('REDIS_CHAT_URL', app_cfg.redis_chat_url),
        'OPTIONS': {
            'CLIENT_CLASS': 'django_redis.client.DefaultClient',
            'CONNECTION_POOL_KWARGS': {
                'max_connections': 100,
                'retry_on_timeout': True,
            },
            'PASSWORD': os.environ.get('REDIS_PASSWORD', app_cfg.redis_password),
        },
        'TIMEOUT': app_cfg.redis_chat_timeout,
        'KEY_PREFIX': 'chat_session',
    }
}

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_USER_MODEL = 'users.User'
AUTHENTICATION_BACKENDS = [
    'Django_xm.apps.users.backends.UsernameMobileAuthBackend',
    'django.contrib.auth.backends.ModelBackend',
]

# ==================== DRF 配置（生产：仅 JWT + JSON）====================
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    "EXCEPTION_HANDLER": "Django_xm.utils.exceptions.custom_exception_handler",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_THROTTLE_CLASSES": [
        "Django_xm.apps.core.throttling.AnonymousRateThrottle",
        "Django_xm.apps.core.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anonymous": "60/min",
        "user": "120/min",
        "login": "5/min",
        "chat_stream": "20/min",
        "sensitive": "5/min",
    },
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
}

# ==================== JWT（生产：短 Access Token）====================
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=app_cfg.jwt_access_token_lifetime_minutes),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=app_cfg.jwt_refresh_token_lifetime_days),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
    'ALGORITHM': 'HS256',
    'SIGNING_KEY': SECRET_KEY,
    "TOKEN_OBTAIN_SERIALIZER": "Django_xm.apps.users.serializers.MyTokenObtainPairSerializer",
    'UPDATE_LAST_LOGIN': True,
}

SPECTACULAR_SETTINGS = {
    "TITLE": f"{app_cfg.app_name} API",
    "DESCRIPTION": f"{app_cfg.app_name} 智能学习 & 研究助手 API",
    "VERSION": app_cfg.app_version,
    "SERVE_INCLUDE_SCHEMA": False,
    "CONTACT": {"name": "API Support"},
    "LICENSE": {"name": "MIT License"},
}

# ==================== 日志（生产：仅文件 + WARNING 级别）====================
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
            "filename": os.path.join(BASE_DIR.parent, "logs", "django_prod.log"),
            "maxBytes": 100 * 1024 * 1024,
            "backupCount": 14,
            "formatter": "verbose",
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

# ==================== Celery（从 Pydantic 注入）====================
CELERY_BROKER_URL = os.environ.get('CELERY_BROKER_URL', app_cfg.celery_broker_url)
CELERY_RESULT_BACKEND = os.environ.get('CELERY_RESULT_BACKEND', app_cfg.celery_result_backend)
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = app_cfg.celery_task_time_limit
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_WORKER_MAX_TASKS_PER_CHILD = app_cfg.celery_worker_max_tasks_per_child

# ==================== 文件上传限制（从 Pydantic 注入）====================
_upload_limit = app_cfg.upload_max_memory_size_mb * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = _upload_limit
FILE_UPLOAD_MAX_MEMORY_SIZE = _upload_limit
FILE_UPLOAD_PERMISSIONS = 0o644
FILE_UPLOAD_DIRECTORY_PERMISSIONS = 0o755

# ==================== 邮件（从 Pydantic 注入）====================
EMAIL_BACKEND = app_cfg.email_backend
DEFAULT_FROM_EMAIL = app_cfg.default_from_email
SERVER_EMAIL = app_cfg.default_from_email

# ==================== 向后兼容（从 Pydantic 注入）====================
APP_NAME = app_cfg.app_name
APP_VERSION = app_cfg.app_version

# ==================== 数据目录初始化（从 Pydantic 注入路径）====================
DATA_DIR = PROJECT_ROOT / app_cfg.data_dir
DOCUMENTS_DIR = PROJECT_ROOT / app_cfg.data_documents_path
INDEXES_DIR = PROJECT_ROOT / app_cfg.vector_store_path
UPLOADS_DIR = PROJECT_ROOT / app_cfg.data_uploads_path

for directory in [DATA_DIR, DOCUMENTS_DIR, INDEXES_DIR, UPLOADS_DIR]:
    directory.mkdir(parents=True, exist_ok=True)
