"""
多上下文切换器模块

支持 Agent 在多个用户会话间切换上下文，基于 LangGraph Store 实现持久化。
上下文数据以 namespace=(user_id, "mcp_contexts") 存储，key 为 context_id。

使用方式:
    from Django_xm.apps.ai_engine.services.checkpointer_factory import get_store
    from Django_xm.apps.tools.mcp.context_switcher import ContextSwitcher

    store = get_store()
    switcher = ContextSwitcher(store)
    await switcher.switch_to("session-123", user_id="user_1")
"""

from typing import Any

from Django_xm.apps.core.logging_utils import get_logger

logger = get_logger(__name__)


class ContextSwitcher:
    """多上下文切换器，支持 Agent 在多个用户会话间切换上下文"""

    def __init__(self, store: Any):
        self._store = store
        self._current_context: str | None = None
        self._context_cache: dict[str, dict] = {}

    @property
    def current_context_id(self) -> str | None:
        """当前活跃上下文 ID"""
        return self._current_context

    def _build_namespace(self, user_id: str) -> tuple:
        """构建 Store namespace"""
        return (str(user_id), "mcp_contexts")

    async def switch_to(self, context_id: str, user_id: str) -> dict:
        """切换到指定上下文

        1. 保存当前上下文状态到 Store
        2. 从 Store 加载目标上下文
        3. 更新 _current_context
        4. 返回目标上下文数据

        Args:
            context_id: 目标上下文 ID
            user_id: 用户 ID

        Returns:
            目标上下文数据字典
        """
        # 保存当前上下文（如果存在缓存中的数据）
        if self._current_context is not None and self._current_context in self._context_cache:
            await self.save_current(
                user_id=user_id,
                state=self._context_cache[self._current_context],
            )

        # 加载目标上下文
        context_data = await self.load_context(context_id, user_id)
        if context_data is None:
            # 新上下文，初始化空数据
            context_data = {
                "context_id": context_id,
                "user_id": user_id,
                "state": {},
            }

        # 更新当前上下文
        self._current_context = context_id
        self._context_cache[context_id] = context_data

        logger.info(f"上下文已切换: context_id={context_id}, user_id={user_id}")
        return context_data

    async def save_current(self, user_id: str, state: dict) -> None:
        """保存当前上下文状态

        Args:
            user_id: 用户 ID
            state: 上下文状态数据
        """
        if self._current_context is None:
            logger.warning("无活跃上下文，跳过保存")
            return

        context_data = {
            "context_id": self._current_context,
            "user_id": user_id,
            "state": state,
        }

        namespace = self._build_namespace(user_id)
        try:
            self._store.put(namespace, self._current_context, context_data)
            self._context_cache[self._current_context] = context_data
            logger.debug(f"上下文已保存: context_id={self._current_context}")
        except Exception:
            logger.exception(f"保存上下文失败: context_id={self._current_context}")

    async def load_context(self, context_id: str, user_id: str) -> dict | None:
        """加载指定上下文

        Args:
            context_id: 上下文 ID
            user_id: 用户 ID

        Returns:
            上下文数据字典，不存在则返回 None
        """
        # 优先从内存缓存读取
        if context_id in self._context_cache:
            return self._context_cache[context_id]

        namespace = self._build_namespace(user_id)
        try:
            item = self._store.get(namespace, context_id)
            if item is not None:
                context_data = item.value if hasattr(item, "value") else item
                self._context_cache[context_id] = context_data
                return context_data
            return None
        except Exception:
            logger.exception(f"加载上下文失败: context_id={context_id}")
            return None

    async def list_contexts(self, user_id: str) -> list[str]:
        """列出用户所有可用上下文

        Args:
            user_id: 用户 ID

        Returns:
            上下文 ID 列表
        """
        namespace = self._build_namespace(user_id)
        try:
            items = self._store.search(namespace)
            context_ids = []
            for item in items:
                # item.key 即为 context_id
                key = item.key if hasattr(item, "key") else str(item)
                context_ids.append(key)
            return context_ids
        except Exception:
            logger.exception(f"列出上下文失败: user_id={user_id}")
            return []

    async def delete_context(self, context_id: str, user_id: str) -> bool:
        """删除指定上下文

        Args:
            context_id: 上下文 ID
            user_id: 用户 ID

        Returns:
            是否删除成功
        """
        namespace = self._build_namespace(user_id)
        try:
            self._store.delete(namespace, context_id)
            self._context_cache.pop(context_id, None)
            if self._current_context == context_id:
                self._current_context = None
            logger.info(f"上下文已删除: context_id={context_id}")
            return True
        except Exception:
            logger.exception(f"删除上下文失败: context_id={context_id}")
            return False

    def clear_cache(self) -> None:
        """清空内存缓存（不删除 Store 中的数据）"""
        self._context_cache.clear()
        self._current_context = None


__all__ = ["ContextSwitcher"]
