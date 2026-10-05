"""跨进程信令总线（Django Web ↔ FastAPI 执行服务）。

归位于 common 统一信令底层：发布方（Django apps 层）与订阅方（FastAPI 执行服务）
共用，禁止业务层各自实现信令通道。

信令类型（频道 `agent:{kind}:{thread_id}`，Redis Pub/Sub）：
- ``start``: 创建/续研会话（Django → 执行服务）
- ``approval``: 审批决策 确认/拒绝/超时（Django → 执行服务）
- ``stop``: 终止会话（Django → 执行服务）

``session_type``（信令 payload 字段）区分会话类型：
- ``chat``: 聊天 agent 会话（thread_id = chat session_id）
- ``research``: 深度研究会话（thread_id = research task_id）

设计说明：
- 审批等决策先落库（DB 为唯一真相源），信令仅用于即时唤醒挂起的执行协程；
  即使信令丢失，执行器挂起时周期性扫描 DB 批次决策与过期审批自愈，最终一致。
- Redis Pub/Sub 按 DB 隔离：发布/订阅须在同一 DB 才能互通；
  双方均经 get_signal_redis_url() 复用同一 Redis 连接串（含 DB 号），保证收发一致。
"""

import json
import logging

logger = logging.getLogger(__name__)

SIGNAL_PREFIX = "agent"
SIGNAL_START = "start"
SIGNAL_APPROVAL = "approval"
SIGNAL_STOP = "stop"

SESSION_TYPE_CHAT = "chat"
SESSION_TYPE_RESEARCH = "research"


def get_signal_redis_url() -> str:
    """信令 Redis URL：复用 Celery broker 的 Redis 连接串（保持原 API 供发布/订阅双方导入）。

    说明：保证发布/订阅双方使用同一 Redis DB（Pub/Sub 按 DB 隔离），
    仅复用配置值，深度研究执行服务不依赖 Celery 功能。
    解析逻辑（env > Pydantic 合并 > 兜底默认）收敛至
    ``Django_xm.common.connections.get_signal_redis_url`` 单一来源。
    """
    from Django_xm.common.connections import get_signal_redis_url as _resolve_signal_redis_url

    return _resolve_signal_redis_url()


def signal_channel(kind: str, thread_id: str) -> str:
    return f"{SIGNAL_PREFIX}:{kind}:{thread_id}"


def publish_signal(kind: str, thread_id: str, payload: dict) -> None:
    """发布跨进程信令（Django 侧同步调用，fire-and-forget）。"""
    import redis as redis_lib

    channel = signal_channel(kind, thread_id)
    try:
        client = redis_lib.Redis.from_url(get_signal_redis_url())
        client.publish(channel, json.dumps(payload, ensure_ascii=False))
        logger.info(f"[SignalBus] 已发布信令: {channel}")
    except Exception:
        logger.exception(f"[SignalBus] 发布信令失败: {channel}")
