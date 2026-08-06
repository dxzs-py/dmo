"""Django settings for Django_xm project - 测试环境配置

继承 base.py 公共配置，覆盖测试环境差异项：
- SQLite in-memory 数据库：快速、无外部依赖（与现有测试 docstring 描述一致）
- LocMemCache：进程内字典缓存，避免 Redis 依赖（需真实 Redis 的测试用 redis_client fixture，db=15）
- InMemoryChannelLayer：Channels 内存传输层，避免 Redis channel layer 依赖
- Celery EAGER 模式：任务同步执行，便于测试断言
- MD5 密码哈希：加速测试（测试不关注密码哈希安全性）
- DEBUG=False：贴近生产行为

运行方式:
    cd backend/Django_xm
    conda activate langchain_xm
    python -m pytest                            # 全量测试
    python -m pytest -m unit                    # 仅单元测试
    python manage.py check --settings=Django_xm.settings.test
"""

from datetime import timedelta

from .base import *  # pylint: disable=wildcard-import,unused-wildcard-import

# ── DEBUG：测试环境关闭（贴近生产行为）──────────────────────────
DEBUG = False

# ── 邮件后端：locmem（进程内字典，避免 SMTP 连接超时）────────────
# 项目当前无邮件发送功能，但 DEBUG=False 下默认 SMTP backend 会在
# 意外触发邮件时尝试连接 localhost:25 导致测试卡住，locmem 是防御性配置。
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# ── ALLOWED_HOSTS：测试环境放行所有 Host（Django 测试客户端使用 testserver）──
ALLOWED_HOSTS = ["*"]

# ── 数据库：SQLite in-memory（快速测试，无外部依赖）──────────────
# base.py 的 PostgreSQL 配置被覆盖；base.py 中对 DB_USER/PASSWORD/NAME 的校验
# 已在 import 时通过 .env 完成，此处覆盖 DATABASES 不影响校验。
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
        # SQLite 默认不支持事务级外键约束，关闭以匹配测试期望
        "OPTIONS": {},
    }
}

# ── 缓存：LocMemCache（无 Redis 依赖，进程内字典）────────────────
# 需要真实 Redis 的测试使用 conftest.py 的 redis_client fixture（db=15）
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "test-default",
    },
    "chat_sessions": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "test-chat-sessions",
    },
}

# ── Channels：in-memory channel layer（无 Redis 依赖）────────────
# 测试中需要真实 Redis pub/sub 的场景用 redis_client fixture + mock channel layer
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    },
}

# ── Celery：EAGER 模式（同步执行，便于断言）──────────────────────
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_BROKER_URL = "memory://"
CELERY_RESULT_BACKEND = "cache+memory://"
# 注意：不覆盖 CELERY_BEAT_SCHEDULE——beat 调度条目仅为配置字典，
# 测试中无 beat worker 运行（CELERY_TASK_ALWAYS_EAGER=True）不会实际执行，
# 清空会导致 test_celery_idempotency 中审批清理调度断言失败。

# ── 密码哈希：MD5（加速测试，安全性非测试关注）──────────────────
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

# ── Middleware：最小集（保留认证/会话/CSRF，移除分析/缓存控制等非必要项）──
MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "Django_xm.apps.core.middleware.CurrentRequestMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# ── CORS / CSRF：测试环境最小配置 ────────────────────────────────
CORS_ALLOWED_ORIGINS = ["http://localhost:3000", "http://localhost:8000"]
CSRF_TRUSTED_ORIGINS = CORS_ALLOWED_ORIGINS

# ── JWT：测试环境 token 有效期（避免测试中 token 过期）──────────
SIMPLE_JWT["ACCESS_TOKEN_LIFETIME"] = timedelta(hours=1)
SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"] = timedelta(days=1)

# ── 安全设置：测试环境关闭（避免 HTTPS 重定向等干扰）────────────
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SESSION_EXPIRE_AT_BROWSER_CLOSE = False

# ── 限流：测试环境放大限额（避免限流干扰测试）──────────────────
REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"] = {
    "anonymous": "9999/min",
    "user": "9999/min",
    "login": "9999/min",
    "chat_stream": "9999/min",
    "research": "9999/min",
    "knowledge": "9999/min",
    "sensitive": "9999/min",
    "snapshot": "9999/min",
    "meta": "9999/min",
}

# ── 文件上传：测试环境限制（避免大文件测试 OOM）─────────────────
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024

# ── 日志：测试环境最小化（WARNING 级别输出到控制台）─────────────
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {"format": "%(levelname)s %(name)s %(message)s"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "simple",
        },
        "null": {
            "class": "logging.NullHandler",
        },
    },
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {
        "django": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "django.request": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        # 第三方库日志降噪
        "langchain": {"handlers": ["null"], "level": "CRITICAL", "propagate": False},
        "httpx": {"handlers": ["null"], "level": "CRITICAL", "propagate": False},
    },
}

# ── AI Engine：测试环境缩短超时、禁用重试（避免测试卡住）────────
AI_LLM_TIMEOUT = 5.0
AI_LLM_MAX_RETRIES = 0

# ── 沙箱：测试环境禁用（避免 Docker 依赖）──────────────────────
SANDBOX_CONFIG = {**SANDBOX_CONFIG, "ENABLED": False}

# ── 静态文件：测试环境使用临时目录（避免污染项目目录）──────────
STATIC_ROOT = BASE_DIR / "staticfiles_test"

# ── 测试专用标志位（供 conftest/fixtures 识别测试环境）──────────
IS_TEST_ENV = True
