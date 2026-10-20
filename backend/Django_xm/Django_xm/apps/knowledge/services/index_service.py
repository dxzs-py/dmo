"""
索引管理器模块 - Facade 模式

提供向量索引的统一管理接口，内部委托给：
- VectorStoreBackend: 向量存储操作（通过 VectorStoreRegistry 获取）
- IndexMetadata: 元数据数据库管理
- _TTLCache: 带 TTL 的 LRU 缓存

向后兼容：
- 保留 create_vector_store / load_vector_store / save_vector_store 模块级函数（已废弃）
- 保留 acreate_vector_store / aadd_documents_to_store 模块级函数
"""

import hashlib
import json
import shutil
import threading
import time
import uuid
from collections import OrderedDict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from django.db import transaction
from django.db.models import Q
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import VectorStore

from Django_xm.apps.core.logging_utils import get_logger
from Django_xm.apps.knowledge.config import settings
from Django_xm.apps.knowledge.vector_store.base import VectorStoreBackend
from Django_xm.apps.knowledge.vector_store.registry import VectorStoreRegistry

logger = get_logger(__name__)


# 稳定文档 ID 生成的固定 namespace（uuid5 保证相同输入产生相同 ID）
# 跨环境/跨进程一致，重试场景下相同 source+content 必然产生相同 ID
_DOC_ID_NAMESPACE = uuid.UUID("a3e5b8c1-2d4f-4e6b-9c8d-7a1b2c3d4e5f")


# ==================== TTL 缓存 ====================


class _TTLCache:
    """带 TTL 的 LRU 缓存

    Args:
        maxsize: 最大条目数（LRU 逐出）
        ttl: 过期秒数
        on_evict: 逐出回调 ``(key, value) -> None``——过期/LRU 逐出/remove 时
            在锁外调用（C3/KV-3：PGVector engine 连接池释放；dispose 耗时，
            不得在持锁状态执行）。**clear() 不触发回调**——快照由调用方经
            ``IndexManager.clear_cache()`` 负责释放，避免双重 dispose。
    """

    def __init__(self, maxsize=32, ttl=1800, on_evict=None):
        self._cache: OrderedDict = OrderedDict()
        self._timestamps: dict[str, float] = {}
        self._maxsize = maxsize
        self._ttl = ttl
        self._lock = threading.Lock()
        self._on_evict = on_evict

    def get(self, key: str):
        evicted = []
        with self._lock:
            if key in self._cache:
                if time.time() - self._timestamps[key] > self._ttl:
                    evicted.append((key, self._cache.pop(key)))
                    self._timestamps.pop(key, None)
                else:
                    self._cache.move_to_end(key)
                    return self._cache[key]
        self._run_evict(evicted)
        return None

    def set(self, key: str, value):
        evicted = []
        with self._lock:
            self._cache[key] = value
            self._timestamps[key] = time.time()
            self._cache.move_to_end(key)
            while len(self._cache) > self._maxsize:
                oldest = next(iter(self._cache))
                evicted.append((oldest, self._cache.pop(oldest)))
                self._timestamps.pop(oldest, None)
        self._run_evict(evicted)

    def remove(self, key: str):
        evicted = []
        with self._lock:
            value = self._cache.pop(key, None)
            self._timestamps.pop(key, None)
            if value is not None:
                evicted.append((key, value))
        self._run_evict(evicted)

    def clear(self):
        """清空全部条目。**不触发 on_evict**——快照释放由调用方负责
        （见 IndexManager.clear_cache 契约）。"""
        with self._lock:
            self._cache.clear()
            self._timestamps.clear()

    def values(self) -> list:
        """返回所有未过期缓存值的快照（线程安全）"""
        with self._lock:
            now = time.time()
            return [v for k, v in self._cache.items() if now - self._timestamps[k] <= self._ttl]

    def _run_evict(self, evicted: list) -> None:
        """锁外执行逐出回调（回调可能是耗时 IO，如 dispose 连接池）。"""
        if not evicted or self._on_evict is None:
            return
        for key, value in evicted:
            try:
                self._on_evict(key, value)
            except Exception:
                logger.exception(f"TTL 缓存逐出回调异常: key={key}")


# ==================== Backend 工厂辅助 ====================


def _get_backend_kwargs(store_type: str) -> dict[str, Any]:
    """根据 store_type 获取 Backend 构造参数"""
    if store_type == "pgvector":
        return {}
    elif store_type == "faiss":
        return {"base_path": settings.vector_store_path}
    elif store_type == "chroma":
        return {
            "persist_directory": getattr(settings, "chroma_persist_directory", "data/chroma_db"),
            "collection_name": getattr(settings, "chroma_collection_name", "langchain_xm"),
        }
    elif store_type == "milvus":
        return {"uri": getattr(settings, "milvus_uri", "milvus_demo.db")}
    elif store_type == "inmemory":
        return {}
    else:
        return {}


def _create_backend(store_type: str) -> VectorStoreBackend:
    """根据 store_type 创建 Backend 实例"""
    backend_cls = VectorStoreRegistry.get(store_type)
    kwargs = _get_backend_kwargs(store_type)
    return backend_cls(**kwargs)


# ==================== IndexManager (Facade) ====================


def _on_store_evict(key: str, value) -> None:
    """缓存逐出回调（C3/KV-3）：释放被逐出 VectorStore 的 engine 连接池。

    此前 TTL 过期/LRU 逐出直接丢弃 value，PGVector 的 SQLAlchemy engine
    连接池不释放 → 每次缓存轮换泄漏一池连接（上限约 480 连接/进程）。
    """
    from Django_xm.apps.knowledge.vector_store import dispose_vector_stores

    disposed = dispose_vector_stores([value])
    if disposed:
        logger.debug(f"已释放被逐出 VectorStore 的 engine 连接池: key={key}")


class IndexManager:
    """索引管理器 - Facade 模式

    委托给：
    - VectorStoreBackend: 向量存储 CRUD（通过 VectorStoreRegistry 获取）
    - IndexMetadata: 元数据数据库管理
    - _TTLCache: 带 TTL 的 LRU 缓存（逐出时自动释放 engine 连接池）
    """

    _cache = _TTLCache(maxsize=32, ttl=1800, on_evict=_on_store_evict)

    def __init__(self, base_path: str | None = None):
        self.base_path = Path(base_path or settings.vector_store_path)
        self.base_path.mkdir(parents=True, exist_ok=True)
        self._backends: dict[str, VectorStoreBackend] = {}
        logger.debug(f"索引管理器初始化: {self.base_path}")

    @classmethod
    def clear_cache(cls) -> list:
        """清空 VectorStore 实例缓存，返回清空前的实例快照（**公共 API**）。

        调用方需要在清空后释放底层资源，例如 `vector_store/pgvector_backend.py`
        在调整 embedding 列维度后，须对旧实例执行 `engine.dispose()` 释放连接池，
        否则会残留指向旧列定义的连接。

        此前该逻辑直接操作 `IndexManager._cache` 私有类属性（跨 2 个模块），
        已收口到此方法。修改缓存结构时只需改这一处。
        """
        snapshot = cls._cache.values()
        cls._cache.clear()
        return snapshot

    def get_backend(self, store_type: str) -> VectorStoreBackend:
        """获取指定存储类型的后端实例（**公共 API，跨模块契约**）。

        ⚠️ 被 **ai_engine**（`ai_engine/settings_views.py`）跨模块调用；
        修改前必须同步调用点。
        """
        """获取或创建 Backend 实例（带缓存）"""
        if store_type not in self._backends:
            self._backends[store_type] = _create_backend(store_type)
        return self._backends[store_type]

    def _get_index_path(self, name: str) -> Path:
        return self.base_path / name

    def _get_metadata_path(self, name: str) -> Path:
        return self._get_index_path(name) / "metadata.json"

    # ==================== 元数据管理 ====================

    @staticmethod
    def _user_scope_denied(name: str, user_id: int | None) -> bool:
        """user 归属守卫（3.2 权限过滤）。

        user_id 提供时，索引名必须符合 ``user_{user_id}_`` 前缀约定
        （单源规则 views_utils.build_user_index_name 的反向校验）——攻击者
        即使知道他人完整内部索引名（如 user_2_xxx），也无法经带 user_id 的
        调用链探测/读取该索引。

        user_id=None 为**系统内部可信路径**：调用方为 IndexManager 内部编排
        （load_index/add_documents/remove_* 等）或管理命令/管理员视图，索引名
        已在更外层经 build_user_index_name 用真实 user 构造，此处不再重复过滤。
        """
        return user_id is not None and not name.startswith(f"user_{user_id}_")

    @staticmethod
    def _scoped_metadata_qs(name: str, user_id: int | None):
        """IndexMetadata 查询集的 user 归属过滤（3.2 第二层防御）。

        user_id 提供时仅匹配"该用户拥有或历史遗留无主（user 为 NULL）"的行，
        防止命名前缀与 DB 归属不一致时（user_1_xxx 行实际归属 user_2）越权命中。
        """
        from Django_xm.apps.knowledge.models import IndexMetadata

        qs = IndexMetadata.objects.filter(name=name)
        if user_id is not None:
            qs = qs.filter(Q(user_id=user_id) | Q(user__isnull=True))
        return qs

    def _save_metadata(self, name: str, metadata: dict[str, Any], store_type: str | None = None) -> None:
        """保存元数据

        PGVector 模式：仅写数据库（IndexMetadata）
        FAISS/Chroma 模式：仅写文件系统（metadata.json）
        """
        effective_store_type = store_type or metadata.get("store_type", settings.vector_store_type)

        if effective_store_type == "pgvector":
            # PGVector: 仅写入数据库
            self._save_metadata_to_db(name, metadata)
        else:
            # FAISS/Chroma: 仅写入文件系统
            metadata_path = self._get_metadata_path(name)
            metadata_path.parent.mkdir(parents=True, exist_ok=True)
            with open(metadata_path, "w", encoding="utf-8") as f:
                json.dump(metadata, f, ensure_ascii=False, indent=2)

    def _save_metadata_to_db(self, name: str, metadata: dict[str, Any]) -> None:
        """将元数据写入 IndexMetadata 数据库模型"""
        try:
            from Django_xm.apps.knowledge.models import IndexMetadata

            _obj, created = IndexMetadata.objects.update_or_create(
                name=name,
                defaults={
                    "description": metadata.get("description", ""),
                    "store_type": metadata.get("store_type", "pgvector"),
                    "embedding_model": metadata.get("embedding_model", ""),
                    "embedding_dimension": metadata.get("embedding_dimension"),
                    "num_documents": metadata.get("num_documents", 0),
                },
            )
            logger.debug(f"元数据写入数据库: {name} (created={created})")
        except Exception as e:
            logger.warning(f"元数据写入数据库失败（不影响主流程）: {e}")

    def load_metadata(
        self,
        name: str,
        user_id: int | None = None,
        store_type: str | None = None,
    ) -> dict[str, Any] | None:
        """加载元数据（**公共 API，跨模块契约**）。

        PGVector 模式：仅从数据库读取（IndexMetadata）
        FAISS/Chroma 模式：仅从文件系统读取（metadata.json）

        权限过滤（3.2）：user_id 提供时执行归属校验——索引名不符合
        ``user_{user_id}_`` 前缀约定直接返回 None（越权视角等同不存在），
        DB 查询同时按 user 归属过滤（_scoped_metadata_qs）。
        user_id=None 为系统内部可信路径（见 _user_scope_denied）。

        store_type：调用方已探测过的存储类型（如 add_documents 链）可传参复用，
        跳过 _detect_store_type 的重复 DB/文件系统探测（3.3 N+1 收敛）。

        ⚠️ 本方法是被 **chat**（`chat/services/rag_chat_service.py`）与
        **ai_engine**（`ai_engine/settings_views.py`）跨模块调用的公共契约，
        同时被 knowledge 内 kb_service / views_rag / rebuild_index 使用。
        重命名或改签名前**必须**同步所有调用点，否则会静默破坏其他 app。
        """
        if self._user_scope_denied(name, user_id):
            logger.warning(f"load_metadata 拒绝非归属索引访问: user_id={user_id}, index={name}")
            return None

        # 判断 store_type（调用方已探测时传参复用，避免重复探测）
        store_type = store_type or self._detect_store_type(name, user_id=user_id)

        if store_type == "pgvector":
            # PGVector: 仅从数据库读取
            try:
                record = self._scoped_metadata_qs(name, user_id).first()
                if record:
                    return {
                        "name": record.name,
                        "description": record.description,
                        "created_at": record.created_at.isoformat() if record.created_at else "",
                        "updated_at": record.updated_at.isoformat() if record.updated_at else "",
                        "num_documents": record.num_documents,
                        "store_type": record.store_type,
                        "embedding_model": record.embedding_model,
                        "embedding_dimension": record.embedding_dimension,
                        "_status": record.status,
                        "_error_message": record.error_message,
                    }
            except Exception as e:
                logger.debug(f"从数据库读取元数据失败: {e}")
            return None
        else:
            # FAISS/Chroma: 仅从文件系统读取
            metadata_path = self._get_metadata_path(name)
            if not metadata_path.exists():
                return None
            try:
                with open(metadata_path, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                logger.exception("加载元数据失败")
                return None

    def _detect_store_type(self, name: str, user_id: int | None = None) -> str:
        """检测索引的存储类型：优先从数据库判断，回退到文件系统

        权限过滤（3.2）：user_id 提供且索引名不属于该用户时，不做任何探测
        （DB/文件系统存在性均为他人侧信息），直接返回默认存储类型。
        """
        if self._user_scope_denied(name, user_id):
            logger.warning(f"_detect_store_type 拒绝非归属索引探测: user_id={user_id}, index={name}")
            return settings.vector_store_type

        # 1. 尝试从数据库判断
        try:
            record = self._scoped_metadata_qs(name, user_id).first()
            if record:
                return record.store_type
        except Exception:
            # 数据库查询失败时回退到文件系统判断
            logger.debug("从数据库检测 store_type 失败，回退到文件系统")

        # 2. 尝试从文件系统元数据判断
        metadata_path = self._get_metadata_path(name)
        if metadata_path.exists():
            try:
                with open(metadata_path, encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("store_type"):
                    return data["store_type"]
            except Exception:
                # 文件系统元数据读取失败时回退到默认值
                logger.debug("从文件系统读取 store_type 元数据失败，回退到默认值")

        # 3. 默认
        return settings.vector_store_type

    @staticmethod
    def _detect_embedding_dimension(embeddings: Embeddings | None) -> int | None:
        """探测 Embedding 模型的输出维度"""
        if embeddings is None:
            return None
        try:
            test_vec = embeddings.embed_query("dimension_test")
            return len(test_vec)
        except Exception as e:
            logger.warning(f"探测 Embedding 维度失败: {e}")
            return None

    def get_store_type(self, name: str, user_id: int | None = None) -> str:
        """获取索引的存储类型（**公共 API，跨模块契约**）。

        ⚠️ 与 load_metadata 同为跨模块公共契约，被 ai_engine
        （`ai_engine/settings_views.py`）与 knowledge 内 rebuild_index 调用。
        修改前必须同步所有调用点。

        权限过滤（3.2）：user_id 语义同 load_metadata（None=系统内部可信路径）。
        """
        return self._detect_store_type(name, user_id=user_id)

    def get_score_semantics(self, name: str) -> str:
        """获取索引后端的原生分数语义（**公共 API**，C-2arch 收敛单源）。

        返回 "similarity"（越大越相关）或 "distance"（pgvector cosine 距离，
        越小越相关）。判定按后端 ``native_score_semantics`` 类属性声明收敛，
        调用方（kb_service.search_knowledge_base / views_rag.SearchAllView）
        不得再按 store_type 字符串自行硬判。
        """
        backend = self.get_backend(self.get_store_type(name))
        return getattr(backend, "native_score_semantics", "similarity")

    def _set_index_status(self, name: str, store_type: str, status: str, error_message: str = "") -> None:
        """更新 IndexMetadata 状态"""
        try:
            from Django_xm.apps.knowledge.models import IndexMetadata

            record, created = IndexMetadata.objects.get_or_create(
                name=name,
                defaults={
                    "store_type": store_type,
                    "status": status,
                    "error_message": error_message,
                },
            )
            if not created:
                if record.status == status:
                    # 同状态写入：状态机无自环转换，幂等跳过（避免误报非法转换）
                    pass
                else:
                    # 尝试使用状态机转换
                    try:
                        record.transition_to(status)
                    except ValueError:
                        # B15：非法转换不再静默直写——error 留痕（状态机违规通常意味着
                        # 前置流程状态未按机演进，如构建崩溃残留 building）。仍回退直写
                        # 保持本方法尽力而为契约（状态字段为诊断元数据，调用方不做控制流），
                        # 但违规必须可被日志追查
                        logger.exception(
                            f"IndexMetadata 非法状态转换（回退直写）: index={name}, "
                            f"{record.status} -> {status}，"
                            f"允许的转换见 IndexMetadata.transition_to"
                        )
                        record.status = status
                        record.save(update_fields=["status", "updated_at"])

            if error_message:
                record.error_message = error_message
                record.save(update_fields=["error_message", "updated_at"])
        except Exception as e:
            logger.debug(f"更新 IndexMetadata 状态失败（不影响主流程）: {e}")

    def _update_metadata_doc_count(self, name: str, store_type: str) -> None:
        """从 Backend 获取实际文档数并更新 IndexMetadata"""
        try:
            from Django_xm.apps.knowledge.models import IndexMetadata

            record = IndexMetadata.objects.filter(name=name).first()
            if record:
                backend = self.get_backend(store_type)
                stats = backend.get_stats(name)
                if stats.get("exists"):
                    record.num_documents = stats.get("num_documents", record.num_documents)
                    record.save(update_fields=["num_documents", "updated_at"])
        except Exception as e:
            logger.debug(f"更新 IndexMetadata 文档数失败（不影响主流程）: {e}")

    # ==================== 索引 CRUD ====================

    def create_index(
        self,
        name: str,
        documents: list[Document] | None = None,
        embeddings: Embeddings | None = None,
        description: str = "",
        store_type: str | None = None,
        overwrite: bool = False,
        **kwargs,
    ):
        """创建新索引，支持创建空索引和 overwrite

        overwrite 模式（带备份恢复）：
        1. 备份旧索引文档列表
        2. 将 IndexMetadata 状态设为 BUILDING
        3. 删旧 collection + 建新 collection
        4. 成功 → 状态设为 READY
        5. 失败 → 尝试用备份文档恢复旧索引，恢复失败则状态设为 ERROR
        """
        effective_store_type = store_type or settings.vector_store_type
        backend = self.get_backend(effective_store_type)

        # 备份旧索引文档（用于 overwrite 失败时恢复）
        backup_documents: list[Document] = []
        is_overwrite = False

        # 检查索引是否已存在
        if backend.exists(name):
            if not overwrite:
                raise ValueError(f"索引已存在: {name}。使用 overwrite=True 来覆盖。")

            is_overwrite = True

            # 备份旧索引的文档
            if embeddings:
                try:
                    backup_documents = self.read_all_documents(name, embeddings, effective_store_type)
                    logger.info(f"overwrite 模式：备份旧索引文档 {len(backup_documents)} 个: {name}")
                except Exception as e:
                    logger.warning(f"overwrite 模式：备份旧索引文档失败（旧索引可能已损坏）: {e}")

            # overwrite 模式：先标记状态为 BUILDING，再删旧建新
            self._set_index_status(name, effective_store_type, "building")
            try:
                backend.delete(name)
                logger.info(f"overwrite 模式：删除旧索引: {name}")
            except Exception as e:
                logger.warning(f"overwrite 模式：删除旧索引失败: {e}")

        logger.info(f"创建索引: {name}，文档数量: {len(documents) if documents else 0}")

        try:
            vector_store = None
            if documents and embeddings:
                vector_store = backend.create(documents, embeddings, collection_name=name, **kwargs)

                # C-1arch：无条件 save（pgvector/inmemory 的 save 为 no-op，
                # faiss/chroma/milvus 落盘持久化）——后端持久化能力由各自
                # save 实现声明，IndexManager 不再按 store_type 字符串硬判
                index_path = self._get_index_path(name)
                backend.save(vector_store, str(index_path))

            # 保存元数据
            active_model = (
                embeddings.get_active_provider_id()
                if embeddings is not None and hasattr(embeddings, "get_active_provider_id")
                else settings.embedding_model
            )
            metadata = {
                "name": name,
                "description": description,
                "created_at": datetime.now(UTC).isoformat(),
                "updated_at": datetime.now(UTC).isoformat(),
                "num_documents": len(documents) if documents else 0,
                "store_type": effective_store_type,
                "embedding_model": active_model,
                "embedding_dimension": self._detect_embedding_dimension(embeddings),
            }
            self._save_metadata(name, metadata, store_type=effective_store_type)

            # 更新 IndexMetadata 状态为 READY
            self._set_index_status(name, effective_store_type, "ready")

            logger.info(f"索引创建成功: {name}")
            self._cache.remove(name)
            return vector_store

        except Exception as e:
            logger.exception("创建索引失败")

            # overwrite 模式下尝试恢复旧索引
            if is_overwrite and backup_documents and embeddings:
                try:
                    backend.create(backup_documents, embeddings, collection_name=name)
                    logger.warning(f"索引重建失败，已恢复旧索引文档 {len(backup_documents)} 个: {name}")
                    # 恢复元数据
                    active_model = (
                        embeddings.get_active_provider_id()
                        if hasattr(embeddings, "get_active_provider_id")
                        else settings.embedding_model
                    )
                    backup_metadata = {
                        "name": name,
                        "description": description,
                        "created_at": datetime.now(UTC).isoformat(),
                        "updated_at": datetime.now(UTC).isoformat(),
                        "num_documents": len(backup_documents),
                        "store_type": effective_store_type,
                        "embedding_model": active_model,
                        "embedding_dimension": self._detect_embedding_dimension(embeddings),
                    }
                    self._save_metadata(name, backup_metadata, store_type=effective_store_type)
                    self._set_index_status(name, effective_store_type, "ready")
                except Exception as restore_err:
                    logger.exception(f"索引恢复失败: {name} -")
                    self._set_index_status(
                        name,
                        effective_store_type,
                        "error",
                        error_message=f"索引创建失败且恢复失败: {e}; 恢复错误: {restore_err}"[:500],
                    )
            else:
                # 更新 IndexMetadata 状态为 ERROR
                self._set_index_status(name, effective_store_type, "error", error_message=str(e)[:500])

            # 清理文件系统残留（pgvector/inmemory 无该路径，exists() 为 False 时 no-op）
            index_path = self._get_index_path(name)
            if index_path.exists():
                shutil.rmtree(index_path)
            raise

    def create_empty_index(
        self,
        name: str,
        description: str = "",
        store_type: str | None = None,
        overwrite: bool = False,
    ):
        """创建空索引（无需指定文档）"""
        return self.create_index(
            name=name,
            documents=None,
            embeddings=None,
            description=description,
            store_type=store_type,
            overwrite=overwrite,
        )

    def load_index(self, name: str, embeddings: Embeddings, **kwargs):
        """加载索引"""
        cached = self._cache.get(name)
        if cached is not None:
            logger.debug(f"索引缓存命中: {name}")
            return cached

        store_type = self.get_store_type(name)
        backend = self.get_backend(store_type)

        logger.info(f"加载索引: {name} (store_type={store_type})")

        try:
            metadata = self.load_metadata(name)
            if metadata:
                logger.info(f"描述: {metadata.get('description', 'N/A')}")
                logger.info(f"文档数: {metadata.get('num_documents', 'N/A')}")

            vector_store = backend.load(embeddings, collection_name=name, **kwargs)

            logger.info(f"索引加载成功: {name}")
            self._cache.set(name, vector_store)
            return vector_store

        except Exception:
            logger.exception("加载索引失败")
            raise

    def list_indexes(self) -> list[dict[str, Any]]:
        """列出所有索引"""
        indexes = []
        seen_names = set()

        # 1. 从 IndexMetadata 数据库加载（优先）
        try:
            from Django_xm.apps.knowledge.models import IndexMetadata

            for record in IndexMetadata.objects.only(
                "name", "description", "created_at", "updated_at",
                "num_documents", "store_type", "embedding_model", "embedding_dimension",
            ).all():
                indexes.append(
                    {
                        "name": record.name,
                        "description": record.description,
                        "created_at": record.created_at.isoformat() if record.created_at else "",
                        "updated_at": record.updated_at.isoformat() if record.updated_at else "",
                        "num_documents": record.num_documents,
                        "store_type": record.store_type,
                        "embedding_model": record.embedding_model,
                        "embedding_dimension": record.embedding_dimension,
                    }
                )
                seen_names.add(record.name)
        except Exception as e:
            logger.debug(f"从数据库列出索引失败，回退到文件系统: {e}")

        # 2. 从文件系统元数据补充
        if self.base_path.exists():
            for item in self.base_path.iterdir():
                if item.is_dir() and item.name not in seen_names:
                    metadata = self.load_metadata(item.name)
                    if metadata:
                        indexes.append(metadata)
                    else:
                        indexes.append(
                            {
                                "name": item.name,
                                "description": "",
                                "created_at": "",
                                "updated_at": "",
                                "num_documents": 0,
                                "store_type": "faiss",
                                "embedding_model": "",
                            }
                        )
                    seen_names.add(item.name)

        # 3. 从 Backend 补充（PGVector 等数据库类型可能不在文件系统中）
        default_store_type = settings.vector_store_type
        try:
            backend = self.get_backend(default_store_type)
            collections = backend.list_collections()
            for collection_name in collections:
                if collection_name not in seen_names:
                    indexes.append(
                        {
                            "name": collection_name,
                            "description": "",
                            "created_at": "",
                            "updated_at": "",
                            "num_documents": 0,
                            "store_type": default_store_type,
                            "embedding_model": "",
                        }
                    )
        except Exception as e:
            logger.warning(f"从 Backend 列出集合失败: {e}")

        return indexes

    def delete_index(self, name: str) -> bool:
        """删除向量索引与文件系统元数据。

        实体生命周期归 kb_service：IndexMetadata 行不在此删除
        （知识库删除走软删墓碑，全局索引管理删除实体由调用方负责）。
        """
        self._cache.remove(name)
        store_type = self.get_store_type(name)
        backend = self.get_backend(store_type)
        index_path = self._get_index_path(name)

        try:
            # 1. 删除向量存储
            deleted = backend.delete(name)

            # 2. 删除文件系统元数据
            if index_path.exists():
                shutil.rmtree(index_path)

            if deleted:
                logger.info(f"索引删除成功: {name}")
            return deleted

        except Exception:
            logger.exception("删除索引失败")
            return False

    def index_exists(
        self,
        name: str,
        user_id: int | None = None,
        store_type: str | None = None,
    ) -> bool:
        """检查索引是否存在

        权限过滤（3.2）：user_id 提供且索引名不属于该用户时直接返回 False
        （越权视角等同不存在，不泄露他人索引的存在性）；DB 检查同时按
        user 归属过滤。user_id=None 为系统内部可信路径（见 _user_scope_denied）。
        store_type：调用方已探测过的存储类型传参复用，跳过 Backend 段的
        重复 store_type 探测（3.3 N+1 收敛）。
        """
        if self._user_scope_denied(name, user_id):
            logger.warning(f"index_exists 拒绝非归属索引探测: user_id={user_id}, index={name}")
            return False

        # 1. 检查数据库（按 user 归属过滤）
        try:
            if self._scoped_metadata_qs(name, user_id).exists():
                return True
        except Exception as e:
            logger.warning(f"index_exists 数据库检查异常: name={name}, error={e}")

        # 2. 检查文件系统
        if self._get_index_path(name).exists():
            return True

        # 3. 检查 Backend
        store_type = store_type or self.get_store_type(name, user_id=user_id)
        try:
            backend = self.get_backend(store_type)
            result = backend.exists(name)
            if not result:
                logger.warning(f"index_exists 三级检查均未通过: name={name}, store_type={store_type}")
            return result
        except Exception as e:
            logger.warning(f"index_exists Backend 检查异常: name={name}, store_type={store_type}, error={e}")
            return False

    def get_index_stats(self, name: str, embeddings: Embeddings = None) -> dict[str, Any]:
        """获取索引统计信息"""
        store_type = self.get_store_type(name)

        # 检查索引是否存在
        if not self.index_exists(name):
            raise FileNotFoundError(f"索引不存在：{name}")

        metadata = self.load_metadata(name)

        stats = {
            "name": name,
            "exists": True,
            "num_documents": metadata.get("num_documents", 0) if metadata else 0,
            "description": metadata.get("description", "") if metadata else "",
            "created_at": metadata.get("created_at", "") if metadata else "",
            "updated_at": metadata.get("updated_at", "") if metadata else "",
            "store_type": metadata.get("store_type", store_type),
            "embedding_model": metadata.get("embedding_model", ""),
            "embedding_dimension": metadata.get("embedding_dimension"),
        }

        # 尝试从 Backend 获取更详细的统计
        try:
            backend = self.get_backend(store_type)
            backend_stats = backend.get_stats(name)
            if backend_stats.get("exists"):
                stats.update({k: v for k, v in backend_stats.items() if k not in ("name", "exists")})
        except Exception as e:
            logger.debug(f"从 Backend 获取统计失败: {e}")

        # 如果有 embeddings，尝试加载获取 FAISS 详细信息
        if embeddings and store_type == "faiss":
            try:
                vector_store = self.load_index(name, embeddings)
                if hasattr(vector_store, "index") and vector_store.index:
                    stats["dimension"] = vector_store.index.d
                    stats["total_vectors"] = vector_store.index.ntotal
            except Exception as e:
                logger.warning(f"获取详细统计失败：{e}")

        return stats

    def read_all_documents(
        self,
        name: str,
        embeddings: Embeddings | None = None,
        store_type: str | None = None,
    ) -> list[Document]:
        """从索引读取所有文档（PGVector 全量读取 / 其他后端回退 docstore 遍历）。

        供知识库降级检索与 overwrite 备份等场景加载全量 chunk。

        Args:
            name: 索引名称
            embeddings: Embeddings 实例（docstore 回退路径需要）
            store_type: 存储类型，缺省时自动检测

        Returns:
            Document 列表，读取失败或索引无文档时返回空列表
        """
        store_type = store_type or self._detect_store_type(name)
        backend = self.get_backend(store_type)

        # PGVector: 使用 backend 的 read_all_documents
        if hasattr(backend, "read_all_documents"):
            docs = backend.read_all_documents(name)
            if docs:
                return docs

        # 回退：加载向量库后遍历 docstore
        try:
            vector_store = self.load_index(name, embeddings)
            if hasattr(vector_store, "docstore") and hasattr(vector_store, "index_to_docstore_id"):
                documents = []
                for _idx, doc_id in vector_store.index_to_docstore_id.items():
                    doc = vector_store.docstore.search(doc_id)
                    if isinstance(doc, Document):
                        documents.append(doc)
                return documents
        except Exception as e:
            logger.warning(f"读取索引文档失败: {e}")

        return []

    # ==================== 文档操作 ====================

    @staticmethod
    def _generate_stable_ids(documents: list[Document]) -> list[str]:
        """为文档列表生成稳定 ID

        基于 metadata.source（文件路径）+ page_content SHA256 + 文档序号生成 uuid5。
        - 相同文件相同内容的 chunk 在重试场景下产生相同 ID（序号按批内顺序稳定），
          配合 PGVector ON CONFLICT DO NOTHING 实现幂等写入。
        - 加入序号是为了保证同一批内 ID 唯一：UnstructuredLoader 分割出的 chunk
          可能包含内容完全相同的片段，若只基于 source+content 生成 ID 会出现重复，
          导致批量 upsert 抛 "ON CONFLICT DO UPDATE command cannot affect row a second time"。

        Args:
            documents: 文档列表

        Returns:
            与 documents 等长的稳定 ID 列表
        """
        ids: list[str] = []
        for idx, doc in enumerate(documents):
            source = ""
            if isinstance(doc.metadata, dict):
                source = doc.metadata.get("source", "") or ""
            content_hash = hashlib.sha256(doc.page_content.encode("utf-8")).hexdigest()
            stable_id = str(uuid.uuid5(_DOC_ID_NAMESPACE, f"{source}:{content_hash}:{idx}"))
            ids.append(stable_id)
        return ids

    def _add_documents_with_stable_ids(
        self,
        vector_store: VectorStore,
        documents: list[Document],
        stable_ids: list[str],
        store_type: str,
    ) -> None:
        """携带稳定 ID 添加文档到向量库

        PGVector: 通过 ids 参数触发 ON CONFLICT DO NOTHING，主键冲突时跳过
        其他后端: 尝试携带 ids 添加，失败则回退到无 ID 模式（仅 dev/test 使用）

        Args:
            vector_store: 已加载的向量库实例
            documents: 待添加文档列表
            stable_ids: 与 documents 等长的稳定 ID 列表
            store_type: 向量库类型，用于决定冲突处理策略
        """
        try:
            vector_store.add_documents(documents, ids=stable_ids)
        except Exception as e:
            if store_type == "pgvector":
                # 生产后端不应失败，向上抛出便于排查
                raise
            # 非 PGVector 后端（faiss/chroma/milvus/inmemory）可能不支持 ids 或冲突报错
            # 回退到无 ID 模式，这些后端仅用于 dev/test，不保证严格幂等
            logger.warning(f"向量库 {store_type} 携带 ID 添加文档失败，回退到无 ID 模式（非幂等）: {e}")
            vector_store.add_documents(documents)

    def add_documents(
        self,
        name: str,
        documents: list[Document],
        embeddings: Embeddings,
    ) -> int:
        """向现有索引添加文档（3.3 事务化 + 探测收敛）

        幂等保障（方案 A：业务层 content hash 去重）：
        - 基于 metadata.source + page_content SHA256 生成稳定 ID（uuid5）
        - PGVector 通过 ON CONFLICT DO NOTHING 跳过已存在 ID
        - Celery 任务重试场景下相同文档不会产生重复向量记录

        一致性窗口（3.3 最小窗口方案）：
        - 向量库写入无法参与 DB 事务 → 固定顺序"先向量、后元数据"；
        - 元数据侧两写（IndexMetadata 行 + num_documents 计数）包
          transaction.atomic，同滚保证行与计数一致；
        - 向量已写入而元数据落库失败时，补偿删除本批向量（尽力而为，
          见 _compensate_added_vectors），补偿失败仅 error 留痕不掩盖原错误。

        探测收敛（3.3 N+1）：store_type 仅探测一次，经参数传给
        index_exists / load_metadata（此前同批重复触发 3-5 次 IndexMetadata 查询）。
        """
        store_type = self.get_store_type(name)
        backend = self.get_backend(store_type)
        self._cache.remove(name)

        if not self.index_exists(name, store_type=store_type):
            raise FileNotFoundError(f"索引不存在：{name}")

        if not documents:
            raise ValueError("文档列表不能为空")

        # 生成稳定 ID：基于 source + content SHA256，保证重试场景幂等
        stable_ids = self._generate_stable_ids(documents)

        logger.info(f"向索引 {name} 添加 {len(documents)} 个文档（携带稳定 ID）")

        vector_store = None
        try:
            metadata = self.load_metadata(name, store_type=store_type)
            is_empty_index = metadata and metadata.get("num_documents", 0) == 0

            if is_empty_index:
                # 空索引：创建新的向量库（携带稳定 ID）
                logger.info(f"索引 {name} 为空，创建新的向量库")
                vector_store = backend.create(
                    documents,
                    embeddings,
                    collection_name=name,
                    ids=stable_ids,
                )

                # C-1arch：无条件 save（pgvector/inmemory 为 no-op）
                index_path = self._get_index_path(name)
                backend.save(vector_store, str(index_path))
            else:
                # 加载现有向量库并添加文档（携带稳定 ID）
                vector_store = self.load_index(name, embeddings)
                self._add_documents_with_stable_ids(
                    vector_store,
                    documents,
                    stable_ids,
                    store_type,
                )

                # C-1arch：无条件 save（pgvector/inmemory 为 no-op）
                index_path = self._get_index_path(name)
                backend.save(vector_store, str(index_path))

            # 元数据一致性窗口：行写入与计数更新同事务（失败同滚）
            try:
                with transaction.atomic():
                    # 更新元数据
                    if metadata:
                        old_count = metadata.get("num_documents", 0)
                        metadata["num_documents"] = old_count + len(documents)
                        metadata["updated_at"] = datetime.now(UTC).isoformat()
                        # 记录实际激活的 embedding 模型（provider id），与向量数据真实来源一致
                        if embeddings is not None and hasattr(embeddings, "get_active_provider_id"):
                            active_model = embeddings.get_active_provider_id()
                            if active_model:
                                metadata["embedding_model"] = active_model
                        # 补充维度信息（旧索引可能没有此字段）
                        if not metadata.get("embedding_dimension") and embeddings:
                            metadata["embedding_dimension"] = self._detect_embedding_dimension(embeddings)
                        self._save_metadata(name, metadata, store_type=store_type)

                    # 更新 IndexMetadata 文档数
                    self._update_metadata_doc_count(name, store_type)
            except Exception:
                # 向量成功 + 元数据失败 → 补偿删除本批向量后上抛原始错误
                self._compensate_added_vectors(vector_store, stable_ids, name)
                raise

            logger.info(f"成功添加 {len(documents)} 个文档到索引 {name}")
            return len(documents)

        except Exception:
            logger.exception("添加文档失败：")
            raise

    @staticmethod
    def _compensate_added_vectors(vector_store, stable_ids: list[str], name: str) -> None:
        """向量写入成功而元数据落库失败时，尽力删除本批已写入向量（3.3）。

        向量库写入不可回滚（最小窗口方案：先向量后元数据），补偿按稳定 ID
        逐条删除；删除失败仅 error 留痕（残留向量可经重建索引收敛），不再
        向上抛出——补偿路径异常会掩盖原始元数据错误。非 pgvector 后端的
        无 ID 回退写入（_add_documents_with_stable_ids 降级分支）无法按 ID
        精确回收，删除失败同样走该留痕路径。
        """
        if vector_store is None:
            return
        try:
            vector_store.delete(stable_ids)
            logger.warning(f"add_documents 元数据失败，已补偿删除本批向量: index={name}, count={len(stable_ids)}")
        except Exception as comp_err:
            logger.exception(
                f"add_documents 向量补偿删除失败（残留向量需重建索引收敛）: "
                f"index={name}, count={len(stable_ids)}, error={comp_err}"  # noqa: TRY401 -- 异常摘要保留在文案中
            )

    def remove_documents(
        self,
        name: str,
        embeddings: Embeddings,
        document_ids: list[str] | None = None,
    ) -> int:
        """从索引中删除文档"""
        store_type = self.get_store_type(name)
        backend = self.get_backend(store_type)
        self._cache.remove(name)

        if not self.index_exists(name):
            raise FileNotFoundError(f"索引不存在：{name}")

        logger.info(f"从索引 {name} 删除文档")

        try:
            if not document_ids:
                logger.warning("未提供 document_ids")
                return 0

            # 3.3 能力矩阵：不支持文档级删除的后端（faiss，supports_remove=False）
            # 直接走通用回退，不再调用 backend（其默认实现抛 NotImplementedError）；
            # 支持的后端执行真实删除，返回 False（未命中任何行）才触发回退
            if backend.supports_remove:
                success = backend.remove_documents(name, document_ids)
            else:
                success = False

            if not success:
                # 回退：加载向量库后通过 VectorStore.delete 删除
                vector_store = self.load_index(name, embeddings)
                if hasattr(vector_store, "delete"):
                    vector_store.delete(document_ids)

                    # C-1arch：无条件 save（pgvector/inmemory 为 no-op）
                    index_path = self._get_index_path(name)
                    backend.save(vector_store, str(index_path))
                else:
                    logger.warning("向量库不支持删除操作")
                    return 0

            logger.info(f"成功删除文档 from 索引 {name}")
            return len(document_ids) if document_ids else 0

        except Exception:
            logger.exception("删除文档失败：")
            raise

    def remove_documents_by_filename(
        self,
        name: str,
        embeddings: Embeddings,
        filename: str,
    ) -> int:
        """根据文件名从索引中删除所有相关文档"""
        store_type = self.get_store_type(name)
        backend = self.get_backend(store_type)
        self._cache.remove(name)

        if not self.index_exists(name):
            raise FileNotFoundError(f"索引不存在：{name}")

        logger.info(f"从索引 {name} 按文件名删除文档: {filename}")

        try:
            if store_type == "pgvector":
                # PGVector: 先尝试 file_name 精确匹配
                deleted_count = backend.remove_documents_by_metadata(name, "file_name", filename)
                if deleted_count == 0:
                    # B15：回退到 source LIKE 匹配；文件名中的 LIKE 通配符（% _ \）
                    # 必须转义，否则 "a_b.pdf" 会误匹配 "axb.pdf" 造成跨文件误删
                    # （PostgreSQL LIKE 默认转义符为反斜杠，无需 ESCAPE 子句）
                    escaped_filename = (
                        filename.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                    )
                    deleted_count = backend.remove_documents_by_metadata_like(
                        name, "source", f"%{escaped_filename}"
                    )
            else:
                # FAISS/Chroma: 需要加载后遍历 docstore
                vector_store = self.load_index(name, embeddings)
                ids_to_delete = self._find_documents_by_filename(vector_store, filename)

                logger.info(f"找到 {len(ids_to_delete)} 个匹配的文档块")

                if not ids_to_delete:
                    logger.warning(f"未在索引 {name} 中找到文件 {filename} 的文档")
                    return 0

                if hasattr(vector_store, "delete"):
                    logger.info(f"调用 vector_store.delete 删除 {len(ids_to_delete)} 个文档")
                    vector_store.delete(ids_to_delete)
                else:
                    logger.warning("向量库不支持删除操作")
                    return 0

                # C-1arch：无条件 save（pgvector/inmemory 为 no-op）
                index_path = self._get_index_path(name)
                backend.save(vector_store, str(index_path))

                deleted_count = len(ids_to_delete)

            # 更新元数据
            metadata = self.load_metadata(name)
            if metadata:
                old_count = metadata.get("num_documents", 0)
                metadata["num_documents"] = max(0, old_count - deleted_count)
                metadata["updated_at"] = datetime.now(UTC).isoformat()
                self._save_metadata(name, metadata, store_type=store_type)

            # 更新 IndexMetadata 文档数
            self._update_metadata_doc_count(name, store_type)

            logger.info(f"成功从索引 {name} 删除文件 {filename} 的 {deleted_count} 个文档块")
            return deleted_count

        except Exception:
            logger.exception("按文件名删除文档失败：")
            raise

    def _find_documents_by_filename(
        self,
        vector_store: VectorStore,
        filename: str,
    ) -> list[str]:
        """在向量库中查找匹配文件名的文档 ID"""
        ids_to_delete = []

        if hasattr(vector_store, "docstore") and hasattr(vector_store, "index_to_docstore_id"):
            index_to_id = vector_store.index_to_docstore_id
            for idx, doc_id in index_to_id.items():
                try:
                    doc = vector_store.docstore.search(doc_id)
                    if doc is None or isinstance(doc, str):
                        continue
                    if isinstance(doc, Document):
                        doc_source = doc.metadata.get("source", "")
                        doc_file_name = doc.metadata.get("file_name", "")

                        if idx < 5:
                            logger.debug(f"文档 {doc_id}: file_name={doc_file_name}, source={doc_source}")

                        if doc_file_name == filename or doc_source.endswith(filename) or (filename in doc_source):
                            ids_to_delete.append(doc_id)
                except Exception as e:
                    logger.warning(f"查找文档 {doc_id} 失败: {e}")
                    continue

        return ids_to_delete
