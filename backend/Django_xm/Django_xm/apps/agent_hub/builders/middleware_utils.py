"""builder 中间件注入公共工具函数（base / deep 双链路统一）。

收敛 base_builder 与 deep_builder 中重复的「确保 ApprovalMiddleware 注入」逻辑，
避免"修一处漏一处"。审批机制是核心安全能力，对所有有工具的 agent 强制启用。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def ensure_approval_middleware(middleware_stack: list) -> bool:
    """确保 ApprovalMiddleware 已注入中间件栈（缺失则追加）。

    与 chat / deep_research / learning 三模块统一审批入口，保证实时同步行为一致。

    Args:
        middleware_stack: 中间件栈（原地修改）。

    Returns:
        True 表示本次新注入；False 表示栈中已存在。
    """
    from Django_xm.apps.agent_hub.approval.middleware import ApprovalMiddleware

    if any(isinstance(m, ApprovalMiddleware) for m in middleware_stack):
        return False
    middleware_stack.append(ApprovalMiddleware())
    logger.info("已注入 ApprovalMiddleware 到 agent 中间件栈")
    return True
