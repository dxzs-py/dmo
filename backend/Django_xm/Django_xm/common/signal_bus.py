"""跨进程信令总线（Django Web ↔ FastAPI 执行服务）。

归位于 common 统一信令底层：发布方（Django apps 层）与订阅方（FastAPI 执行服务）
共用，禁止业务层各自实现信令通道。

信令类型（频道 `agent:{kind}:{thread_id}`，Redis Pub/Sub）：
- ``start``: 创建/续研会话（Django → 执行服务）
- ``approval``: 审批决策 确认/拒绝/超时（Django → 执行服务）
- ``stop``: 终止会话（Django → 执行服务）
- ``retry-subagent``: 单独重启失败子代理（Django → 执行服务）

``session_type``（信令 payload 字段）区分会话类型：
- ``chat``: 聊天 agent 会话（thread_id = chat session_id）
- ``research``: 深度研究会话（thread_id = research task_id）

设计说明：
- 审批等决策先落库（DB 为唯一真相源），信令仅用于即时唤醒挂起的执行协程；
  即使信令丢失，执行器挂起时周期性扫描 DB 批次决策与过期审批自愈，最终一致。
- Redis Pub/Sub 不区分 DB，发布/订阅可使用任意 Redis 连接。
"""

import json
import logging

logger = logging.getLogger(__name__)

SIGNAL_PREFIX = "agent"
SIGNAL_START = "start"
SIGNAL_APPROVAL = "approval"
SIGNAL_STOP = "stop"
SIGNAL_RETRY_SUBAGENT = "retry-subagent"

SESSION_TYPE_CHAT = "chat"
SESSION_TYPE_RESEARCH = "research"


def get_signal_redis_url() -> str:
    """信令 Redis URL：复用 Celery broker 的 Redis 连接串。

    说明：保证发布/订阅双方使用同一 Redis DB（Pub/Sub 按 DB 隔离），
    仅复用配置值，深度研究执行服务不依赖 Celery 功能。
    CELERY_BROKER_URL 在 settings/base.py 无条件定义，空值时回退默认
    （与 project_cfg.celery_broker_url 默认值一致，保持原行为）。
    """
    from django.conf import settings as django_settings

    return getattr(django_settings, "CELERY_BROKER_URL", "") or "redis://127.0.0.1:6379/3"


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


def publish_retry_subagent_signal(
    thread_id: str,
    *,
    agent_path: list[str],
    tool_call_id: str,
    agent_name: str = "",
    original_args: dict | None = None,
    user_id: int | None = None,
    chat_session_id: str | None = None,
) -> None:
    """发布单独重启失败子代理信令（Django → 执行服务）。

    执行服务收到后：会话运行中直接注入重试指令；会话已结束/服务重启恢复态
    从 checkpoint 恢复会话后注入。主 agent 收到指令后以原始入参重新调用
    目标子代理，新执行事件归属同一 agentPath 继续展示。

    Args:
        thread_id: 深度研究任务 ID（= 会话线程 ID）
        agent_path: 目标子代理完整调用链路（如 ["main", "web-researcher"]）
        tool_call_id: 失败子代理工具调用 ID
        agent_name: 目标子代理名称（agent_path 末位元素）
        original_args: 子代理原始委派入参（主 agent 重新委派时沿用）
        user_id: 任务归属用户 ID
        chat_session_id: 关联 chat 会话 ID（聊天深度研究场景）
    """
    publish_signal(
        SIGNAL_RETRY_SUBAGENT,
        thread_id,
        {
            "thread_id": thread_id,
            "session_type": SESSION_TYPE_RESEARCH,
            "agent_path": agent_path,
            "tool_call_id": tool_call_id,
            "agent_name": agent_name,
            "original_args": original_args or {},
            "user_id": user_id,
            "chat_session_id": chat_session_id,
        },
    )
