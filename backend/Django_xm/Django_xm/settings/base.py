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
import warnings
from datetime import timedelta
from pathlib import Path

from dotenv import dotenv_values, load_dotenv  # # 加载 .env 文件到环境变量

# 显式加载项目根 .env（绝对路径）：load_dotenv() 无参版本依赖调用栈/工作目录，
# 不同进程（runserver / run_fastapi / celery）若解析到不同 .env 或漏加载，
# 会得到不同的 SECRET_KEY（channel layer 曾用 SECRET_KEY 做对称加密，
# 密钥不一致会让 consumer 解密失败并踢掉整条 WebSocket 连接）。
_PROJECT_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(_PROJECT_ENV_FILE)

# 环境变量优先于 .env（默认 override=False）；若两者不一致，说明本进程与
# 其它按 .env 启动的进程会使用不同的 SECRET_KEY，必须显式告警便于排查。
_ENV_SECRET_KEY = (os.environ.get("SECRET_KEY") or "").strip()
_FILE_SECRET_KEY = (dotenv_values(_PROJECT_ENV_FILE).get("SECRET_KEY") or "").strip()
if _ENV_SECRET_KEY and _FILE_SECRET_KEY and _ENV_SECRET_KEY != _FILE_SECRET_KEY:
    warnings.warn(
        f"SECRET_KEY 与 {_PROJECT_ENV_FILE} 不一致：本进程使用的是环境变量中的值，"
        "与其它按 .env 启动的进程可能不同（会导致跨进程 JWT 校验失败）",
        RuntimeWarning,
        stacklevel=1,
    )

_project_root = Path(__file__).resolve().parent.parent.parent  # # 项目根路径加入 sys.path，确保模块可导入
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from Django_xm.apps.ai_engine.config import settings as app_cfg  # Pydantic 真相源
from Django_xm.apps.core.config import settings as project_cfg  # AI 配置真相源

# LangSmith 追踪接线已收敛至唯一入口 configure_langsmith()（ARCH-10）：
# 由 AiEngineConfig.ready() 在 Django 启动时统一调用
# （apps/ai_engine/services/langsmith_setup.py），此处不再设置 LANGCHAIN_* 环境变量。

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
    "Django_xm.apps.agent_hub.apps.AgentHubConfig",
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
        "USER": os.environ.get("DB_USER", ""),
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

# ==================== Redis 连接配置（单一出口，ARCH-09 收敛） ====================
# env > Pydantic 真相源 合并后的 Redis 连接参数，以 Django settings 属性为通道
# 供 common/connections.py 统一读取；各消费方禁止再自行 os.environ 直读。
REDIS_URL = os.environ.get("REDIS_URL", project_cfg.redis_url)
REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD", project_cfg.redis_password)
REDIS_CHAT_URL = os.environ.get("REDIS_CHAT_URL", project_cfg.redis_chat_url)

CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": REDIS_URL,
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
            "CONNECTION_POOL_KWARGS": {
                "max_connections": 50,
                "retry_on_timeout": True,
                "socket_timeout": 3,
                "socket_connect_timeout": 3,
            },
            "PASSWORD": REDIS_PASSWORD,
        },
        "TIMEOUT": project_cfg.redis_default_timeout,
        "KEY_PREFIX": "langchain_xm",
    },
    "chat_sessions": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": REDIS_CHAT_URL,
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
            "CONNECTION_POOL_KWARGS": {
                "max_connections": 100,
                "retry_on_timeout": True,
                "socket_timeout": 3,
                "socket_connect_timeout": 3,
            },
            "PASSWORD": REDIS_PASSWORD,
        },
        "TIMEOUT": project_cfg.redis_chat_timeout,
        "KEY_PREFIX": "chat_session",
    },
}

# Django Channels 通道层配置（WebSocket 实时同步）
# dev 环境 Redis 为硬依赖（Celery broker / CACHES / channel layer 共用），
# 不做启动 ping 探测：连接由 channels_redis 连接池惰性建立并自动重连，
# Redis 不可用时显式报错（fail-fast），严禁静默降级 InMemoryChannelLayer——
# 降级会导致 WebSocket 跨进程失效，破坏多浏览器实时同步。
# Redis 连接池参数说明（本地偶发 "Connection closed by server" 的根治）：
# - 服务端 redis.windows-service.conf 已配置 timeout 0（不因空闲关闭连接），
#   断连一般来自 Redis 服务重启等偶发事件，客户端无法阻止，但可自愈。
# - health_check_interval=30：连接空闲超过 30 秒后，下次取出前发送 PING 校验，
#   发现死连接自动剔除并重建，避免复用已断开连接导致 ConnectionError。
# - socket_keepalive=True + socket_connect_timeout：TCP 层保活与连接建立超时。
#   注意：channels_redis 4.3.0 不支持 set_heartbeat 参数，勿传入（会 TypeError）。
# 加密说明（重要）：
# 不再配置 ``symmetric_encryption_keys``。该选项用各进程自己的 SECRET_KEY 做对称
# 加密/解密，一旦 Django / FastAPI / Celery 进程的 SECRET_KEY 不一致（例如某个进程
# 由 IDE/终端带入了旧环境变量），receive() 反序列化会抛
# ``cryptography.fernet.InvalidToken``；channels_redis 对此没有兜底（core.py
# receive_single 直接 deserialize），异常会打死整个 consumer 协程 → WebSocket 以
# 1011 关闭且不触发 disconnect()，表现为“生成期间连接每秒断一次、结束后才回放”。
# 事件历史（realtime_events）本就以明文 JSON 存 Redis，加密只保护临时队列，
# 收益有限却把“配置不一致”放大成“实时通道不可用”，故移除；
# 同时改用 ResilientRedisChannelLayer 对单条坏消息兜底（不打断连接）。
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "Django_xm.common.channels_layer.ResilientRedisChannelLayer",
        "CONFIG": {
            "hosts": [
                {
                    "address": REDIS_URL,
                    "socket_keepalive": True,
                    "health_check_interval": 30,
                    "socket_connect_timeout": 5,
                    "socket_timeout": 10,
                    "retry_on_timeout": True,
                }
            ],
        },
    }
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
        # 注：QueryParamTokenAuthentication（?token=<JWT>）**不再**作为默认认证类。
        # 此前挂在全局默认里，意味着**任意**端点都能用 URL 查询串认证，JWT 会进入
        # 浏览器历史 / Referer / 各类访问日志（OWASP 不建议敏感数据放查询串）。
        # 前端 SSE 已全部改走 Authorization 头（fetchSSE / rawSSEFetch 本就注入
        # Authorization），查询串传令牌只剩 `<a href>` 文件下载一种场景——由
        # IsAuthenticatedOrQueryParam 权限类在下载端点自行处理，不依赖默认认证类。
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
        "learning": "10/min",
        "sensitive": "10/min",
        "snapshot": "120/min",
        # 页面加载即请求的只读元数据接口（多浏览器并发时单页约 13 个请求），
        # 60/min 会被 4 浏览器同时打开瞬间打满，提升至 300/min 匹配设计意图
        "meta": "300/min",
    },
    "DEFAULT_PAGINATION_CLASS": "Django_xm.common.pagination.ProjectPagination",
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
    # JWT 独立签名钥：JWT_SIGNING_KEY 留空/未设置时沿用 SECRET_KEY（不破坏现有登录态）；
    # 生产环境可设置该变量与 SECRET_KEY 解耦，二者轮换互不影响
    "SIGNING_KEY": os.environ.get("JWT_SIGNING_KEY") or SECRET_KEY,
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

DATA_DIR = PROJECT_ROOT / project_cfg.data_dir
INDEXES_DIR = PROJECT_ROOT / app_cfg.vector_store_path
UPLOADS_DIR = PROJECT_ROOT / project_cfg.data_uploads_path

MEDIA_ROOT = DATA_DIR / "media"

TOOLS_DIR = DATA_DIR / "tools"
TOOLS_SKILLS_DIR = TOOLS_DIR / "skills"
TOOLS_LANGCHAIN_DIR = TOOLS_DIR / "langchain"
TOOLS_MCP_DIR = TOOLS_DIR / "mcp"

# ==================== 验证码字体（跨平台渲染一致性） ====================
# 仓库内置 DejaVu Sans（Bitstream Vera 许可，允许再分发，见 apps/users/fonts/LICENSE_DEJAVU）。
# 不依赖系统字体的原因：Linux / WSL2 / 容器镜像默认不提供 arial.ttf，缺失时 PIL 会降级为
# 位图字体并丢弃 font_size，字符实际仅 6px 高（画布 40px），验证码将无法辨认。
# 需换字体时覆盖此路径即可（目标字体文件须存在于服务器，且覆盖全部候选字符的字形）。
CAPTCHA_FONT_PATH = BASE_DIR / "apps" / "users" / "fonts" / "DejaVuSans.ttf"

# LOGS_DIR / CELERYBEAT_DIR：RotatingFileHandler 与 Celery beat 均不会自动创建父目录，
# Git 又不跟踪空目录，故在此统一创建，保障克隆后可直接启动。
LOGS_DIR = BASE_DIR.parent / "logs"
CELERYBEAT_DIR = PROJECT_ROOT / "data" / "celerybeat"

for directory in [
    DATA_DIR,
    UPLOADS_DIR,
    TOOLS_DIR,
    TOOLS_SKILLS_DIR,
    TOOLS_LANGCHAIN_DIR,
    TOOLS_MCP_DIR,
    MEDIA_ROOT,
    LOGS_DIR,
    CELERYBEAT_DIR,
]:
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
    "rag.upload_documents": {"queue": "rag"},
    "chat.cleanup_expired_attachments": {"queue": "chat"},
    "chat.cleanup_attachment_indexes": {"queue": "chat"},
    "chat.index_old_attachments": {"queue": "chat"},
    "chat.check_storage_alerts": {"queue": "chat"},
    "chat.cleanup_checkpoints": {"queue": "chat"},
    "base.cleanup_old_task_records": {"queue": "celery"},
    "base.check_stale_tasks": {"queue": "celery"},
    "analytics.track_event": {"queue": "celery"},
    "approvals.cleanup_expired_approvals": {"queue": "celery"},
    "approvals.process_outbox": {"queue": "celery"},
    "subagent_wait.cleanup_expired_waits": {"queue": "celery"},
}

# 附件治理配置：真相源为 ProjectSettings（apps/core/config.py，env 键名一致映射），
# 此处仅做 Django settings 通道装配（ARCH-05，禁止 os.environ 直读旁路）。
ATTACHMENT_DEFAULT_RETENTION_DAYS = project_cfg.attachment_default_retention_days
ATTACHMENT_CLEANUP_HOUR = project_cfg.attachment_cleanup_hour
ATTACHMENT_STORAGE_WARNING_THRESHOLD = project_cfg.attachment_storage_warning_threshold
ATTACHMENT_STORAGE_CRITICAL_THRESHOLD = project_cfg.attachment_storage_critical_threshold
ATTACHMENT_DEDUP_ENABLED = project_cfg.attachment_dedup_enabled
ATTACHMENT_MAX_TOTAL_SIZE_MB = project_cfg.attachment_max_total_size_mb

# 分析数据量治理配置：真相源同为 ProjectSettings（apps/core/config.py）。
# ANALYTICS_EVENT_RETENTION_DAYS 约束 UserEvent 明细保留期（api.request 明细已收敛为
# 「非 GET 或失败」，量级从 21.7 万降至约 4.3 万）；ANALYTICS_API_STAT_RETENTION_DAYS
# 约束 ApiDailyStat 聚合表（体量小，保留更久以便回溯）。消费方：tasks/analytics_tasks.py。
ANALYTICS_EVENT_RETENTION_DAYS = project_cfg.analytics_event_retention_days
ANALYTICS_API_STAT_RETENTION_DAYS = project_cfg.analytics_api_stat_retention_days

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
        "schedule": crontab(hour=ATTACHMENT_CLEANUP_HOUR, minute=0),
    },
    # 会话附件检索索引（PG collection attach_*）TTL 回收：与上面的“附件实体清理”
    # 不同——这里只回收孤立的向量 collection/IndexMetadata，TTL 取设置页
    # rag_attachment_ttl_days（默认 7 天），错开 20 分钟执行避免与实体清理抢 IO。
    "cleanup-attachment-indexes-daily": {
        "task": "chat.cleanup_attachment_indexes",
        "schedule": crontab(hour=ATTACHMENT_CLEANUP_HOUR, minute=20),
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
    # 子代理业务等待（wait_for_subagent）超时清扫：仅覆盖父=子代理的挂起
    # （主图挂起由 FastAPI 进程内 watchdog 兜底，见 tasks/subagent_wait_tasks.py）。
    "cleanup-expired-subagent-waits-every-minute": {
        "task": "subagent_wait.cleanup_expired_waits",
        "schedule": 60.0,
    },
    "process-approval-outbox-every-5s": {
        "task": "approvals.process_outbox",
        "schedule": 5.0,
    },
    # analytics 明细/聚合保留期清理。任务默认 dry_run=True，beat 调用必须显式传
    # dry_run=False 才会真正删除（详见 tasks/analytics_tasks.py 注释）。
    "cleanup-analytics-events-daily": {
        "task": "analytics.cleanup_analytics_events",
        "schedule": crontab(hour=5, minute=30),
        "kwargs": {"dry_run": False},
    },
}

# MCP SSRF 豁免开关（env→settings 桥，ARCH-05）：common/net/guards.py 经
# SSRF_EXEMPT_SETTING 读取本属性。默认 False；仅隔离调试环境临时豁免，
# 生产环境严禁开启（豁免内网校验引入 SSRF 风险）。
MCP_SSRF_ALLOW_PRIVATE_ADDRESSES = project_cfg.mcp_ssrf_allow_private_addresses

# Phase D: Docker 沙箱执行层配置
# HIGH 级工具调用在 Docker 容器内执行，防止不可逆变更影响宿主系统。
# 开发环境默认关闭（ENABLED=False），生产环境通过环境变量启用。
# 真相源为 ProjectSettings 的 sandbox_* 字段（SANDBOX_* env 键一致映射，ARCH-05），
# common/sandbox/executor.py 经 settings.SANDBOX_CONFIG 统一读取（结构键名保持不变）。
SANDBOX_CONFIG = {
    "ENABLED": project_cfg.sandbox_enabled,
    "DOCKER_IMAGE": project_cfg.sandbox_image,
    "MEMORY_LIMIT": project_cfg.sandbox_memory,
    "CPU_LIMIT": project_cfg.sandbox_cpus,
    "NETWORK_MODE": project_cfg.sandbox_network,
    "SESSION_TIMEOUT": project_cfg.sandbox_session_timeout,
    # 挂载最小化（沙箱加固）：仅挂载 DATA_DIR 到容器 /workspace，
    # 不再挂载整个项目根（容器内破坏性操作不触及挂载区外的宿主文件）
    "WORK_DIR_MOUNT": str(DATA_DIR),
}

# ── 持久化后端装配（fix-review-round2 1.1，ARCH-05 Django settings 通道）──
# 真相源为 ai_engine pydantic Settings（store_backend/checkpointer_backend 字段，
# env 键 STORE_BACKEND/CHECKPOINTER_BACKEND 一致映射）；消费方
# ai_engine/services/checkpointer_factory 直接读 ai_engine 单例
# （getattr(settings, "store_backend", ...)/getattr(settings, "checkpointer_backend", ...)）。
# 此处装配为 Django settings 通道属性（对齐 ATTACHMENT_* 装配模式），
# 一致性由 test_store_backend_wiring 防漂移断言钉住。生产默认 postgres：
# Store 承载跨会话记忆/知识图谱等长期数据；InMemoryStore 仅作真降级路径
# （langgraph-store-postgres 缺失或连接失败时触发 _log_memory_store_fallback error 告警）。
STORE_BACKEND = app_cfg.store_backend
CHECKPOINTER_BACKEND = app_cfg.checkpointer_backend

# AI Engine Settings
AI_LLM_TIMEOUT = 120.0
AI_LLM_MAX_RETRIES = 3
AI_DEFAULT_PROVIDER = "deepseek"
AI_DEFAULT_MODEL_TOKEN_LIMIT = 128000

AI_HELPER_MODEL_PROVIDER = ""
AI_HELPER_MODEL_NAME = ""
AI_HELPER_MODEL_TEMPERATURE = 0.0
# 1024：摘要 prompt 要求"不超过400字"（≈600-800 token），256 会截断残句（A4）
AI_HELPER_MODEL_MAX_TOKENS = 1024

# 子代理最大嵌套深度（0=主 agent，1=子 agent，2=孙 agent，3=曾孙 agent）。
# spawn_sub_agent 派生链路与 deepagents inline 子代理共用；环境变量 AGENT_MAX_DEPTH 可覆盖。
AGENT_MAX_DEPTH = int(os.environ.get("AGENT_MAX_DEPTH", "3"))

# ── Agent 全局执行超时（B6/P2-13 不对齐项①）────────────────────────
# soft=警告阈值（触发一次 timeout_warning 事件，不终止）；hard=强制终止阈值
# （chat→FALLBACK / research→终止信号 / learning→TimeoutError）。None=不限制。
# 取值依据：深度研究单个子代理可跑 10-30min、挂起会话 watchdog 7200s；
# soft 覆盖正常长任务上界，hard 为其 2 倍兜底，均小于 watchdog；
# 审批等待已由 ExecutionTimeoutManager.pause/resume 排除计时，不受影响。
# 代码级兜底同值：ai_engine/services/agent_resilience.py DEFAULT_SOFT/HARD_TIMEOUT。
AGENT_SOFT_TIMEOUT = 1800.0
AGENT_HARD_TIMEOUT = 3600.0

# 通用工具级缺省执行超时（round3 2.4 双层职责，秒；None=不限制）。
# 外层兜底值 = 最慢内层工具超时（shell_exec MAX_TIMEOUT=300）+ 10s 余量：
# 内层负责优雅终止并返回自身错误结果，外层仅约束无自带超时的工具——
# 外层必须略大于最慢内层，否则内层优雅收尾（进程组 shutdown/输出回收）
# 会被外层先行打断。远小于 Celery rag soft_time_limit=1500s；超时返回
# error ToolMessage 不中断执行流（文案注明工具可能仍在后台运行——wait_for
# 无法强杀 executor 线程，框架边界）。
# 代码级兜底：ai_engine/services/tool_timeout.py DEFAULT_TOOL_EXECUTION_TIMEOUT。
AGENT_TOOL_EXECUTION_TIMEOUT = 310.0

# 子代理并发执行上限（D3/P2-9，进程级；无"禁用"语义，<1 按 1 处理）。
# 超限排队等待而非失败（threading.BoundedSemaphore 收敛于
# langgraph_adapter._run_in_thread，spawn/resume 全部执行线程唯一入口）。
# 取值依据（审计 PF-03 建议 4-8，取上界 8）：
# - 单个执行中子代理占用 1 线程 + 1 独立事件循环 + 1 个 per-loop PG
#   checkpointer 连接池（≥1 连接），8 槽 ≈ 8-16 PG 连接，处于 PG 默认
#   max_connections=100 安全余量内；
# - SessionManager 并发会话槽 DEFAULT_MAX_CONCURRENCY=20（chat 会话多数
#   0-2 个子代理、research fan-out 每波 2-5 个），8 槽覆盖 1-2 个完整波次；
# - 槽位生命周期 ≤ 单个子代理图执行时长（recursion_limit=200 + 工具超时
#   300s 约束），排队延迟分钟级 ≪ 挂起 watchdog 7200s，等待父会话不被误杀；
# - Celery 全局 hard 1800s 属独立 worker 进程域，仅作量级参照。
# 代码级兜底同值：ai_engine/subagent_runtime/concurrency.py
# DEFAULT_SUBAGENT_CONCURRENCY。
AGENT_SUBAGENT_CONCURRENCY = 8

# 子代理并发槽排队等待超时（round3 2.5，秒；<=0/None = 无限等待）。
# 排队超过该时长仍未获得槽位 → 实例置 FAILED + 生命周期回调（父会话
# fan-in 等待者立即收到失败，不悬挂到 watchdog 7200s）。
# 代码级兜底同值：concurrency.py DEFAULT_SUBAGENT_ACQUIRE_TIMEOUT。
AGENT_SUBAGENT_ACQUIRE_TIMEOUT = 30.0

# wait_for_subagent 显式超时（秒）。默认 7200 对齐 SessionExecutor 挂起
# watchdog（B6 超时矩阵）：父图挂起在 _subagent_wait 时 interrupt payload 与
# SubAgentInstance.pending_interrupt_info 携带 deadline（epoch 秒），超时由
# Celery beat 清扫任务（subagent_wait_tasks.cleanup_expired_subagent_waits）
# 经 SubAgentRuntime.resume 唤醒父图走失败路径。覆盖范围边界：beat 仅清扫
# 父=子代理的挂起（可持久化、可跨进程 resume）；主图（chat/research 会话）
# 的等待不在 SubAgentInstance 表内，仍由 FastAPI 进程内 watchdog
# （_finalize_suspended_timeout）兜底。
# 代码级兜底同值：agent_hub/subagent_tools/wait.py DEFAULT_WAIT_TIMEOUT_SECONDS。
AGENT_SUBAGENT_WAIT_TIMEOUT = 7200

# wait_for_subagent fan-in 汇总预算（字符）。
# - 单条上限 5000：对齐 result_preview 提取上限（langgraph_adapter._extract_result
#   截 5000 字符），正常路径单条结果天然在限内，兜底防御未来上限调整；
# - 总预算 48000 的换算依据：按 128k token 父上下文、目标汇总 ≤ 25%
#   （≈32k token）、中文约 1.5-2 char/token 折算 → 48k-64k 字符，取保守值
#   48000（≈24k-32k token）。8 路并发满载（8×5000=40000 字符）在预算内，
#   超载由总预算按优先级收敛（failed 优先保留，completed 依次压缩）。
# 消费方：wait.py format_subagent_results_text（超预算时在汇总尾部标注
# "已截断，完整结果见子代理详情"）。
# 代码级兜底同值：wait.py DEFAULT_RESULT_MAX_CHARS / DEFAULT_TOTAL_BUDGET_CHARS。
AGENT_SUBAGENT_RESULT_MAX_CHARS = 5000
AGENT_SUBAGENT_RESULTS_TOTAL_BUDGET = 48000

# ── Shell Exec 安全配置 ──────────────────────────────────────────
# 白名单命令：单一真相源在 Django_xm.apps.tools.langchain.shell.DEFAULT_WHITELIST_COMMANDS，
# 此处置为 None 使用代码级默认值（含平台过滤）。
# 如需部署级覆盖，设为列表将完全替换默认白名单（不会合并）。
SHELL_EXEC_WHITELIST = None
# 允许的工作目录（限制命令执行目录范围，None 表示不限制——生产环境必须配置）
SHELL_EXEC_ALLOWED_DIRS = [DATA_DIR, MEDIA_ROOT]

# ─── 日志配置（公共） ───────────────────────────────────────────────
# 按服务角色隔离日志文件，避免多进程竞争写同一文件、日志混杂无法按服务区分：
# - Web/Django 进程（runserver/gunicorn/celery）：django.log（历史默认，SERVICE_ROLE 未设置）
# - FastAPI 执行服务（8001）：fastapi.log（services/fastapi_service/main.py 设置 SERVICE_ROLE=fastapi）
# 注意：prod.py 以本 LOGGING 为底做增量覆盖（保留过滤器与 app loggers，仅替换
# file handler 的生产文件名与轮转参数，*_prod.log），不得整体替换本配置。
SERVICE_ROLE = os.environ.get("SERVICE_ROLE", "web")
LOG_FILE_NAME = "fastapi.log" if SERVICE_ROLE == "fastapi" else "django.log"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {"format": "%(levelname)s %(asctime)s %(module)s %(lineno)d %(message)s"},
        "simple": {"format": "%(levelname)s %(module)s %(lineno)d %(message)s"},
    },
    "filters": {
        "require_debug_true": {
            "()": "django.utils.log.RequireDebugTrue",
        },
        # 敏感值脱敏：QueryParamTokenAuthentication 允许 ?token=<JWT>，而
        # django.request 在 4xx/5xx 会记录含查询串的完整路径。挂在 handler 上
        # 兜底抹除 token/access_token/refresh_token 与 Bearer 值。
        "redact_sensitive": {
            "()": "Django_xm.common.logging_filters.RedactSensitiveFilter",
        },
    },
    "handlers": {
        "console": {
            "level": "DEBUG",
            "filters": ["require_debug_true", "redact_sensitive"],
            "class": "logging.StreamHandler",
            "formatter": "simple",
            "stream": "ext://sys.stdout",
        },
        "file": {
            "level": "INFO",
            "filters": ["redact_sensitive"],
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(BASE_DIR.parent / "logs" / LOG_FILE_NAME),
            "maxBytes": 10 * 1024 * 1024,
            "backupCount": 5,
            "formatter": "verbose",
            "encoding": "utf-8",
        },
    },
    "loggers": {
        # "Django_xm" 兜底 logger（B2）：接住未显式枚举的项目命名空间
        # （knowledge/learning/users/attachments/analytics/cache_manager/core/
        #  tasks 等），使 core/logging_utils.get_logger 归流后这些模块的日志
        # 进入 file+console 管线并吃 redact_sensitive 脱敏；显式枚举的子
        # logger 均 propagate=False，不会在本层重复输出。
        "Django_xm": {
            "handlers": ["console", "file"],
            "level": "DEBUG",
            "propagate": False,
        },
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
        "Django_xm.apps.approvals": {
            "handlers": ["console", "file"],
            "level": "DEBUG",
            "propagate": False,
        },
        "Django_xm.apps.agent_hub": {
            "handlers": ["console", "file"],
            "level": "DEBUG",
            "propagate": False,
        },
        "Django_xm.common": {
            "handlers": ["console", "file"],
            "level": "DEBUG",
            "propagate": False,
        },
        "Django_xm.apps.context_manager": {
            "handlers": ["console", "file"],
            "level": "DEBUG",
            "propagate": False,
        },
        "Django_xm.apps.tools": {
            "handlers": ["console", "file"],
            "level": "DEBUG",
            "propagate": False,
        },
        "Django_xm.apps.research": {
            "handlers": ["console", "file"],
            "level": "DEBUG",
            "propagate": False,
        },
        # ASGI / 通道层：daphne 的 "Exception inside application"、channels 的
        # consumer 异常、channels_redis 的解密/连接告警必须进文件日志——
        # 否则 WebSocket 被服务端异常关闭时只留在控制台，日志里“看起来一切正常”。
        "daphne": {
            "handlers": ["console", "file"],
            "level": "INFO",
            "propagate": False,
        },
        "channels": {
            "handlers": ["console", "file"],
            "level": "INFO",
            "propagate": False,
        },
        "channels_redis": {
            "handlers": ["console", "file"],
            "level": "INFO",
            "propagate": False,
        },
        # FastAPI 执行服务：DEBUG 完整记录（8001 进程写入 fastapi.log；
        # Web 进程中该 logger 不产生日志，配置无副作用）
        "Django_xm.services.fastapi_service": {
            "handlers": ["console", "file"],
            "level": "DEBUG",
            "propagate": False,
        },
    },
}
