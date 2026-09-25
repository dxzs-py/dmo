"""审批 Redis 持久化层。

按 interrupt_id 索引（替代旧的按 session_id 索引），支持：
- pending 审批持久化到 Redis List（按 source_id 分组）
- processed 审批最终状态持久化到 Redis Key（按 interrupt_id 索引）
"""

import json
import logging

from Django_xm.apps.approvals.services.approval_constants import (
    APPROVAL_PENDING_PREFIX,
    APPROVAL_PROCESSED_PREFIX,
    APPROVAL_TTL,
)
from Django_xm.common.redis_utils import get_redis_client

logger = logging.getLogger(__name__)


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
        redis_client = get_redis_client()
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
        redis_client = get_redis_client()
        key = f"{APPROVAL_PROCESSED_PREFIX}{interrupt_id}"
        redis_client.setex(key, APPROVAL_TTL, json.dumps(processed_data, ensure_ascii=False))
        logger.info(
            f"[ApprovalStore] 持久化 processed: interrupt_id={interrupt_id}, state={processed_data.get('state')}"
        )
    except Exception:
        logger.exception("[ApprovalStore] 持久化 processed 失败")


def persist_approval_state(interrupt_id, state, extra=None):
    """更新审批状态到 Redis（不依赖 complete，processing 状态也写入覆盖）。

    当审批状态变更（processing/approved/rejected/timeout）时立即写入最新状态。

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
