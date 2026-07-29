"""
跨工具数据管道模块

允许 MCP 工具间通过共享 Store namespace 传递中间结果。
数据以 namespace=(user_id, "tool_data_pipe", tool_name) 存储，key 为数据键名。

使用方式:
    from Django_xm.apps.ai_engine.services.checkpointer_factory import get_store
    from Django_xm.apps.tools.mcp.tool_data_pipe import ToolDataPipe

    store = get_store()
    pipe = ToolDataPipe(store, user_id="user_1")

    # 工具 A 写入中间结果
    await pipe.write("search_tool", "results", ["doc1", "doc2"])

    # 工具 B 读取中间结果
    results = await pipe.read("search_tool", "results")
"""

import json
from typing import Any

from Django_xm.apps.core.config import get_logger

logger = get_logger(__name__)


class ToolDataPipe:
    """跨工具数据管道，允许 MCP 工具间通过共享 Store namespace 传递中间结果"""

    def __init__(self, store: Any, user_id: str):
        self._store = store
        self._user_id = str(user_id)
        self._namespace_prefix = (self._user_id, "tool_data_pipe")

    def _build_namespace(self, tool_name: str) -> tuple:
        """构建工具专属 namespace"""
        return (self._user_id, "tool_data_pipe", tool_name)

    async def write(self, tool_name: str, key: str, data: Any) -> None:
        """工具写入中间结果

        Args:
            tool_name: 工具名称
            key: 数据键名
            data: 要写入的数据（需可序列化）
        """
        namespace = self._build_namespace(tool_name)
        try:
            # 确保数据可序列化存储
            serializable_data = self._ensure_serializable(data)
            self._store.put(namespace, key, serializable_data)
            logger.debug(f"工具数据已写入: tool={tool_name}, key={key}")
        except Exception:
            logger.exception(f"工具数据写入失败: tool={tool_name}, key={key}")

    async def read(self, tool_name: str, key: str) -> Any | None:
        """工具读取中间结果

        Args:
            tool_name: 工具名称
            key: 数据键名

        Returns:
            数据值，不存在则返回 None
        """
        namespace = self._build_namespace(tool_name)
        try:
            item = self._store.get(namespace, key)
            if item is not None:
                return item.value if hasattr(item, "value") else item
            return None
        except Exception:
            logger.exception(f"工具数据读取失败: tool={tool_name}, key={key}")
            return None

    async def list_keys(self, tool_name: str) -> list[str]:
        """列出工具的所有数据键

        Args:
            tool_name: 工具名称

        Returns:
            数据键名列表
        """
        namespace = self._build_namespace(tool_name)
        try:
            items = self._store.search(namespace)
            return [item.key for item in items if hasattr(item, "key")]
        except Exception:
            logger.exception(f"列出工具数据键失败: tool={tool_name}")
            return []

    async def clear(self, tool_name: str | None = None) -> None:
        """清除工具数据

        Args:
            tool_name: 工具名称，不指定则清除全部工具数据
        """
        if tool_name is not None:
            # 清除指定工具的数据
            namespace = self._build_namespace(tool_name)
            try:
                items = self._store.search(namespace)
                for item in items:
                    if hasattr(item, "key"):
                        self._store.delete(namespace, item.key)
                logger.info(f"工具数据已清除: tool={tool_name}")
            except Exception:
                logger.exception(f"清除工具数据失败: tool={tool_name}")
        else:
            # 清除全部工具数据：遍历所有可能的 namespace
            try:
                items = self._store.search(self._namespace_prefix)
                for item in items:
                    if hasattr(item, "key"):
                        self._store.delete(self._namespace_prefix, item.key)
                logger.info("全部工具数据已清除")
            except Exception:
                logger.exception("清除全部工具数据失败:")

    @staticmethod
    def _ensure_serializable(data: Any) -> Any:
        """确保数据可序列化存储

        对于不可直接序列化的对象，转为 JSON 字符串存储。
        """
        if data is None:
            return None
        if isinstance(data, (str, int, float, bool, list, dict)):
            return data
        try:
            json.dumps(data)
            return data
        except (TypeError, ValueError):
            return json.dumps(str(data), ensure_ascii=False)


__all__ = ["ToolDataPipe"]
