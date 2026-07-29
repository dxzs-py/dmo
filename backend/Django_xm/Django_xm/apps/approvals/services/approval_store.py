"""审批 Redis 持久化层。

按 interrupt_id 索引（替代旧的按 session_id 索引），支持：
- pending 审批持久化到 Redis List（按 source_id 分组）
- processed 审批最终状态持久化到 Redis Key（按 interrupt_id 索引）
- 历史审批合并读取（pending + processed 覆盖）
"""

import json
import logging

from django.core.cache import cache

logger = logging.getLogger(__name__)

# Redis key 前缀（按 source_id 索引 pending 列表）
APPROVAL_PENDING_PREFIX = "approval:pending:"
# Redis key 前缀（按 interrupt_id 索引 processed 最终状态）
APPROVAL_PROCESSED_PREFIX = "approval:processed:"
# TTL：30 分钟，与前端 APPROVAL_EXPIRY_MS 一致
APPROVAL_TTL = 1800


def _get_redis_client():
    return cache.client.get_client()


def persist_approval_pending(source_id, approval_data):
    """持久化 pending 审批到 Redis List（按 source_id 分组）。

    Args:
        source_id: 会话 ID 或任务 ID
        approval_data: 审批数据 dict，必须包含 interrupt_id
    """
    try:
        if not approval_data.get("interrupt_id"):
            logger.warning(f"[ApprovalStore] 持久化 pending 跳过: 缺少 interrupt_id, source_id={source_id}")
            return
        redis_client = _get_redis_client()
        key = f"{APPROVAL_PENDING_PREFIX}{source_id}"
        redis_client.rpush(key, json.dumps(approval_data, ensure_ascii=False))
        redis_client.expire(key, APPROVAL_TTL)
        logger.info(
            f"[ApprovalStore] 持久化 pending: source_id={source_id}, "
            f"interrupt_id={approval_data.get('interrupt_id')}, "
            f"tool={approval_data.get('tool_name')}"
        )
    except Exception:
        logger.exception("[ApprovalStore] 持久化 pending 失败")


def persist_approval_processed(interrupt_id, processed_data):
    """持久化已处理审批最终状态到 Redis Key（按 interrupt_id 索引）。

    Args:
        interrupt_id: 审批中断 ID
        processed_data: 已处理审批数据 dict，应包含 state 字段
    """
    try:
        if not interrupt_id:
            logger.warning("[ApprovalStore] 持久化 processed 跳过: 缺少 interrupt_id")
            return
        redis_client = _get_redis_client()
        key = f"{APPROVAL_PROCESSED_PREFIX}{interrupt_id}"
        redis_client.setex(key, APPROVAL_TTL, json.dumps(processed_data, ensure_ascii=False))
        logger.info(
            f"[ApprovalStore] 持久化 processed: interrupt_id={interrupt_id}, state={processed_data.get('state')}"
        )
    except Exception:
        logger.exception("[ApprovalStore] 持久化 processed 失败")


def persist_approval_state(interrupt_id, state, extra=None):
    """更新审批状态到 Redis（不依赖 complete，processing 状态也写入覆盖）。

    当审批状态变更（processing/approved/rejected/timeout）时立即调用，
    确保 get_approval_history 用最新状态覆盖 pending 中的旧数据。

    Args:
        interrupt_id: 审批中断 ID
        state: 审批状态
        extra: 附加数据 dict
    """
    try:
        if not interrupt_id:
            return
        processed_data = {"state": state}
        if extra:
            processed_data.update(extra)
        persist_approval_processed(interrupt_id, processed_data)
    except Exception:
        logger.exception("[ApprovalStore] persist_approval_state 失败")


def get_approval_processed(interrupt_id):
    """获取已处理审批最终状态。

    Args:
        interrupt_id: 审批中断 ID

    Returns:
        dict 或 None
    """
    try:
        redis_client = _get_redis_client()
        key = f"{APPROVAL_PROCESSED_PREFIX}{interrupt_id}"
        raw = redis_client.get(key)
        if not raw:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return json.loads(raw)
    except Exception:
        logger.exception("[ApprovalStore] 获取 processed 失败")
        return None


def get_approval_history(source_id):
    """获取审批历史（pending + processed 覆盖）。

    合并策略：
    1. 读取 pending List 中的所有审批（按时间顺序）
    2. 对每个 pending 审批，检查是否有对应的 processed Key
    3. 如果有 processed，用其数据覆盖 pending（保留最终状态）
    4. 返回合并后的审批列表

    Args:
        source_id: 会话 ID 或任务 ID

    Returns:
        list[dict]: 审批数据列表
    """
    try:
        redis_client = _get_redis_client()
        pending_key = f"{APPROVAL_PENDING_PREFIX}{source_id}"
        pending_items = redis_client.lrange(pending_key, 0, -1)

        result = []
        for item in pending_items:
            try:
                if isinstance(item, bytes):
                    item = item.decode("utf-8")
                approval_data = json.loads(item)
                interrupt_id = approval_data.get("interrupt_id")
                if interrupt_id:
                    processed_data = get_approval_processed(interrupt_id)
                    if processed_data:
                        approval_data = {**approval_data, **processed_data}
                result.append(approval_data)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue

        if result:
            logger.info(f"[ApprovalStore] 读取历史审批: source_id={source_id}, count={len(result)}")
        return result
    except Exception:
        logger.exception("[ApprovalStore] 获取审批历史失败")
        return []
