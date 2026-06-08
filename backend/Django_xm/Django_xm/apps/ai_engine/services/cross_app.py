"""
跨应用服务调用桥接模块

提供 ai_engine 对外暴露的同步调用接口，
内部通过 Celery 异步任务执行，避免跨应用硬依赖。
"""

import logging

logger = logging.getLogger(__name__)


def schedule_ai_data_cleanup(user_id=None, session_id=None):
    """
    调度 AI 数据清理任务（checkpoint / Store）

    通过 Celery 异步执行，调用方无需关心 ai_engine 内部实现。
    适用于用户注销、会话删除等场景。

    Args:
        user_id: 用户 ID（int, 可选）
        session_id: 会话 ID（str, 可选）
    """
    try:
        from Django_xm.tasks.chat_tasks import cleanup_checkpoints
        cleanup_checkpoints.delay(user_id=user_id, session_id=session_id)
        logger.info(f"已调度 AI 数据清理任务: user_id={user_id}, session_id={session_id}")
    except Exception as e:
        logger.error(f"调度 AI 数据清理任务失败: user_id={user_id}, session_id={session_id}, error={e}")
