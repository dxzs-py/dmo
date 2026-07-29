"""向量存储后端注册表

提供策略模式 + 注册表架构，支持动态注册和获取向量存储后端。
"""

import logging
from typing import ClassVar

from .base import VectorStoreBackend

logger = logging.getLogger(__name__)


class VectorStoreRegistry:
    """向量存储后端注册表

    使用策略模式，将存储类型名称映射到对应的后端实现类。
    支持动态注册新的后端，以及查询已注册的后端列表。
    """

    _backends: ClassVar[dict[str, type[VectorStoreBackend]]] = {}

    @classmethod
    def register(cls, name: str, backend_class: type[VectorStoreBackend]) -> None:
        """注册向量存储后端

        Args:
            name: 存储类型标识符
            backend_class: 后端实现类
        """
        if name in cls._backends:
            logger.warning(f"覆盖已注册的向量存储后端: {name}")
        cls._backends[name] = backend_class
        logger.debug(f"注册向量存储后端: {name} -> {backend_class.__name__}")

    @classmethod
    def get(cls, name: str) -> type[VectorStoreBackend]:
        """获取已注册的向量存储后端类

        Args:
            name: 存储类型标识符

        Returns:
            对应的后端实现类

        Raises:
            ValueError: 未注册的后端名称
        """
        if name not in cls._backends:
            raise ValueError(f"未注册的向量存储后端: {name}，可用: {list(cls._backends.keys())}")
        return cls._backends[name]

    @classmethod
    def list_backends(cls) -> list[str]:
        """列出所有已注册的后端名称"""
        return list(cls._backends.keys())

    @classmethod
    def unregister(cls, name: str) -> None:
        """取消注册向量存储后端"""
        if name in cls._backends:
            del cls._backends[name]
            logger.debug(f"取消注册向量存储后端: {name}")


# 自动注册内置后端
def _register_builtin_backends() -> None:
    from .chroma_backend import ChromaBackend
    from .faiss_backend import FAISSBackend
    from .inmemory_backend import InMemoryBackend
    from .milvus_backend import MilvusBackend
    from .pgvector_backend import PGVectorBackend

    VectorStoreRegistry.register("pgvector", PGVectorBackend)
    VectorStoreRegistry.register("faiss", FAISSBackend)
    VectorStoreRegistry.register("chroma", ChromaBackend)
    VectorStoreRegistry.register("milvus", MilvusBackend)
    VectorStoreRegistry.register("inmemory", InMemoryBackend)


_register_builtin_backends()
