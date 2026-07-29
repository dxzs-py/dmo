from django.db import models

from Django_xm.apps.core.config import get_logger

logger = get_logger(__name__)


class ContextLifecycleManager:
    """上下文生命周期管理

    管理上下文的创建、更新、过期、清理：
    - 会话级上下文：会话结束时自动保存摘要到 AutoMemory
    - 跨会话上下文：LRU 淘汰 + 相关性检索
    - 过期策略：按 last_accessed_at 清理超过 30 天未访问的上下文
    - 用户删除时级联清理
    """

    def on_session_start(self, user_id: int, session_id: str):
        """会话开始：预加载用户规则和自动记忆"""
        # 无需特别操作，HierarchicalMemory.load_context() 会按需加载

    def on_session_end(self, user_id: int, session_id: str, summary: str = ""):
        """会话结束：保存摘要到 AutoMemory

        如果 summary 非空，保存为 AutoMemory（source='pattern'）
        """
        if not summary or not summary.strip():
            return
        try:
            from Django_xm.apps.context_manager.models import AutoMemory

            AutoMemory.objects.create(
                user_id=user_id,
                content=summary[:5000],  # 限制长度
                source="pattern",
            )
        except Exception as e:
            logger.warning(f"ContextLifecycle: 保存会话摘要失败: {e}")

    def on_context_access(self, user_id: int, memory_ids: list):
        """访问时更新 last_accessed_at 和 access_count"""
        if not memory_ids:
            return
        try:
            from django.utils import timezone

            from Django_xm.apps.context_manager.models import AutoMemory

            AutoMemory.objects.filter(id__in=memory_ids, user_id=user_id).update(
                access_count=models.F("access_count") + 1,
                last_accessed_at=timezone.now(),
            )
        except Exception as e:
            logger.warning(f"ContextLifecycle: 更新访问计数失败: {e}")

    def cleanup_expired(self, user_id: int | None = None, days: int = 30):
        """清理超过 N 天未访问的上下文"""
        try:
            from datetime import timedelta

            from django.utils import timezone

            from Django_xm.apps.context_manager.models import AutoMemory

            cutoff = timezone.now() - timedelta(days=days)
            qs = AutoMemory.objects.filter(last_accessed_at__lt=cutoff)
            if user_id:
                qs = qs.filter(user_id=user_id)
            count = qs.count()
            if count > 0:
                qs.delete()
                logger.info(f"ContextLifecycle: 清理 {count} 条过期记忆")
        except Exception as e:
            logger.warning(f"ContextLifecycle: 清理过期记忆失败: {e}")

    def on_user_delete(self, user_id: int):
        """用户删除时级联清理（Django FK CASCADE 会自动处理，此方法作为兜底）"""
        try:
            from Django_xm.apps.context_manager.models import AutoMemory, ContextRule, PromptCache

            ContextRule.objects.filter(user_id=user_id).delete()
            AutoMemory.objects.filter(user_id=user_id).delete()
            PromptCache.objects.filter(user_id=user_id).delete()
            logger.info(f"ContextLifecycle: 清理用户 {user_id} 的所有上下文数据")
        except Exception as e:
            logger.warning(f"ContextLifecycle: 清理用户数据失败: {e}")

    def get_auto_memory_stats(self, user_id: int) -> dict:
        """获取自动记忆统计信息"""
        try:
            from Django_xm.apps.context_manager.models import AutoMemory

            qs = AutoMemory.objects.filter(user_id=user_id)
            return {
                "total_count": qs.count(),
                "total_size": sum(len(m.content) for m in qs.only("content")[:1000]),
                "by_source": {source: qs.filter(source=source).count() for source, _ in AutoMemory.SOURCE_CHOICES},
            }
        except Exception:
            return {"total_count": 0, "total_size": 0, "by_source": {}}
