"""Redis / PostgreSQL 连接配置单一来源（ARCH-09 收敛）。

历史多路独立解析（env 直读、各自回退）收敛至本模块，消费方禁止自行解析：

- ``apps/ai_engine/services/redis_async_client.py``（Checkpointer Redis 热缓存）
- ``apps/cache_manager/services/cache_service.py::RedisDirectClient``（缓存运维直连）
- ``apps/ai_engine/services/checkpointer_factory.py``（Checkpointer/Store 的 PG 连接串）
- ``common/signal_bus.py``（跨进程信令 Pub/Sub，复用 Celery broker 连接串）

分层边界（.importlinter：common 禁止依赖 apps）：
env 变量与 Pydantic 真相源（``apps/core/config.py``）的合并在
``settings/base.py`` 完成，以 Django settings 属性（``REDIS_URL`` /
``REDIS_PASSWORD`` / ``REDIS_CHAT_URL`` / ``CELERY_BROKER_URL``）为通道，
本模块经 ``django.conf`` 惰性读取，不直接导入 apps 层。

URL 优先级语义（取各历史消费方现状中最严格者，行为保持）：

1. 环境变量 > Pydantic 配置（base.py 合并后经 Django settings 透出）；
2. Django settings 属性缺失/为空时（未配置 ``DJANGO_SETTINGS_MODULE``
   的独立进程），直接读环境变量；
3. 兜底默认值与 Pydantic 真相源字段默认值严格一致。

注意：不同通道（default cache / chat cache / Celery broker）历史上就是
三条独立连接串（含各自 DB 号），本模块按通道分别提供 getter，
不按 host:port + db 号合成 URL，避免行为漂移。

密码语义：Django cache（django_redis）接受空字符串密码；直连 redis-py
的客户端需将空密码归一化为 ``None``，由 :func:`get_redis_config` 统一
处理（与两个历史直连消费方行为一致）。
"""

from __future__ import annotations

import os
from typing import Any

from django.core.exceptions import ImproperlyConfigured

# 兜底默认值：与 apps/core/config.py Pydantic 字段默认值严格一致（行为保持）
DEFAULT_REDIS_URL = "redis://127.0.0.1:6379/1"
DEFAULT_REDIS_CHAT_URL = "redis://127.0.0.1:6379/2"
DEFAULT_SIGNAL_REDIS_URL = "redis://127.0.0.1:6379/3"
DEFAULT_POSTGRES_HOST = "127.0.0.1"
DEFAULT_POSTGRES_PORT = "5432"
DEFAULT_POSTGRES_DB = "langchain_xm"
DEFAULT_POSTGRES_USER = "postgres"


def _resolve_setting(attr_name: str, fallback: str) -> str:
    """解析单项连接配置。

    优先级：Django settings 属性（base.py 已完成 env > Pydantic 合并）
    > 同名环境变量 > 兜底默认值。
    """
    try:
        from django.conf import settings as django_settings

        value = getattr(django_settings, attr_name, "")
    except ImproperlyConfigured:
        value = ""  # Django 未配置的独立进程：回退环境变量
    if value:
        return value
    return os.environ.get(attr_name) or fallback


def get_redis_url() -> str:
    """业务/缓存 Redis URL（default cache 与 channel layer 共用）。"""
    return _resolve_setting("REDIS_URL", DEFAULT_REDIS_URL)


def get_redis_chat_url() -> str:
    """Chat 会话缓存 Redis URL（chat_sessions cache 专用）。"""
    return _resolve_setting("REDIS_CHAT_URL", DEFAULT_REDIS_CHAT_URL)


def get_redis_password() -> str:
    """Redis 密码（原始值，空字符串表示无密码，供 django_redis 使用）。"""
    return _resolve_setting("REDIS_PASSWORD", "")


def get_redis_config() -> dict[str, Any]:
    """Redis 连接配置（供直连 redis-py 的客户端使用）。

    password 与历史直连消费方（redis_async_client / RedisDirectClient）
    语义一致：空字符串归一化为 ``None``（redis-py 不接受空字符串密码）。

    Returns:
        ``{"url": str, "password": str | None}``
    """
    return {
        "url": get_redis_url(),
        "password": get_redis_password() or None,
    }


def get_signal_redis_url() -> str:
    """信令 Redis URL：复用 Celery broker 的 Redis 连接串。

    Pub/Sub 按 DB 隔离，发布/订阅双方必须使用同一连接串（含 DB 号）；
    本函数只复用配置值，深度研究执行服务不依赖 Celery 功能。
    CELERY_BROKER_URL 在 settings/base.py 无条件定义，空值时回退默认
    （测试环境 settings/test.py 覆写为 memory://，与收敛前行为一致）。
    """
    return _resolve_setting("CELERY_BROKER_URL", DEFAULT_SIGNAL_REDIS_URL)


def get_postgres_connection_string(uri_override: str | None = None) -> str:
    """构建 Checkpointer / Store 的 PostgreSQL 连接字符串。

    优先级（与 checkpointer_factory._build_connection_string 历史行为一致）：

    1. 环境变量 ``CHECKPOINTER_POSTGRES_URI``（完整连接串，避免密码进代码）；
    2. ``uri_override``（调用方透传 ``settings.checkpointer_postgres_uri``，
       当前 AISettings 未定义该字段、恒为 None，优先级保持在 env 之后）；
    3. env/settings 的 ``DB_HOST``/``DB_PORT``/``DB_NAME`` +
       ``DB_USER``/``DB_PASSWORD`` 拼接。

    安全措施：调用方日志必须经 checkpointer_factory._mask_connection_string
    遮蔽密码，禁止明文输出连接串。

    Args:
        uri_override: 可选的完整连接串覆盖值（来自项目配置层）。

    Returns:
        形如 ``postgresql://user:pass@host:port/dbname`` 的连接串。
    """
    connection_string = os.environ.get("CHECKPOINTER_POSTGRES_URI")
    if connection_string:
        return connection_string

    if uri_override:
        return uri_override

    db_host = os.environ.get("DB_HOST", DEFAULT_POSTGRES_HOST)
    db_port = os.environ.get("DB_PORT", DEFAULT_POSTGRES_PORT)
    db_name = os.environ.get("DB_NAME", DEFAULT_POSTGRES_DB)
    db_user = os.environ.get("DB_USER", DEFAULT_POSTGRES_USER)
    db_pass = os.environ.get("DB_PASSWORD", "")

    return f"postgresql://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}"
