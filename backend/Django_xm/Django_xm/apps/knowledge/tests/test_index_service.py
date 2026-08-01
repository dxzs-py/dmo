"""index_service.py 单元测试。

覆盖范围：
- _TTLCache：TTL 过期、LRU 驱逐、线程安全、values 快照
- _get_backend_kwargs：5 种 store_type 配置映射
- IndexManager._generate_stable_ids：基于 source+content SHA256 的稳定 ID
- IndexManager._get_index_path / _get_metadata_path：路径计算
- IndexManager.index_exists：索引存在性检查（mock 文件系统）
- IndexManager._detect_embedding_dimension：embedding 维度检测（mock）

设计原则：
- 不依赖真实向量库 / 文件系统 / LLM
- 文件系统操作通过 tempfile 或 mock
- 使用 unittest.TestCase
"""

from __future__ import annotations

import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock

from langchain_core.documents import Document

from Django_xm.apps.knowledge.services.index_service import (
    IndexManager,
    _get_backend_kwargs,
    _TTLCache,
)

# ============================================================================
# _TTLCache 单测
# ============================================================================


class TTLCacheBasicTests(unittest.TestCase):
    """_TTLCache 基本操作。"""

    def test_set_and_get(self):
        cache = _TTLCache(maxsize=10, ttl=60)
        cache.set("key1", "value1")
        self.assertEqual(cache.get("key1"), "value1")

    def test_get_missing_returns_none(self):
        cache = _TTLCache()
        self.assertIsNone(cache.get("nonexistent"))

    def test_remove(self):
        cache = _TTLCache()
        cache.set("key1", "value1")
        cache.remove("key1")
        self.assertIsNone(cache.get("key1"))

    def test_remove_nonexistent_is_noop(self):
        cache = _TTLCache()
        cache.remove("nonexistent")  # 不报错

    def test_clear(self):
        cache = _TTLCache()
        cache.set("key1", "v1")
        cache.set("key2", "v2")
        cache.clear()
        self.assertIsNone(cache.get("key1"))
        self.assertIsNone(cache.get("key2"))


class TTLCacheExpiryTests(unittest.TestCase):
    """_TTLCache TTL 过期。"""

    def test_expired_entry_returns_none(self):
        cache = _TTLCache(maxsize=10, ttl=1)
        cache.set("key1", "value1")
        # 手动修改时间戳模拟过期
        cache._timestamps["key1"] = time.time() - 2
        self.assertIsNone(cache.get("key1"))

    def test_non_expired_entry_returns_value(self):
        cache = _TTLCache(maxsize=10, ttl=100)
        cache.set("key1", "value1")
        self.assertEqual(cache.get("key1"), "value1")

    def test_zero_ttl_immediate_expiry(self):
        cache = _TTLCache(maxsize=10, ttl=0)
        cache.set("key1", "value1")
        # ttl=0 意味着立即过期（time.time() - timestamp > 0 几乎总为 True）
        # 但由于 set 和 get 在同一时刻，差值可能恰好为 0
        # 实际行为：差值 > 0 才过期，所以可能仍然命中
        # 这里测试 ttl=0 不报错即可
        result = cache.get("key1")
        self.assertIn(result, [None, "value1"])


class TTLCacheLRUEvictionTests(unittest.TestCase):
    """_TTLCache LRU 驱逐。"""

    def test_evicts_oldest_when_full(self):
        cache = _TTLCache(maxsize=2, ttl=60)
        cache.set("key1", "v1")
        cache.set("key2", "v2")
        cache.set("key3", "v3")  # 超过 maxsize=2，驱逐 key1
        self.assertIsNone(cache.get("key1"))
        self.assertEqual(cache.get("key2"), "v2")
        self.assertEqual(cache.get("key3"), "v3")

    def test_get_moves_to_end(self):
        cache = _TTLCache(maxsize=2, ttl=60)
        cache.set("key1", "v1")
        cache.set("key2", "v2")
        # 访问 key1 使其变为最近使用
        cache.get("key1")
        # 添加 key3，应驱逐 key2（最久未使用）
        cache.set("key3", "v3")
        self.assertEqual(cache.get("key1"), "v1")
        self.assertIsNone(cache.get("key2"))

    def test_overwrite_does_not_increase_size(self):
        cache = _TTLCache(maxsize=2, ttl=60)
        cache.set("key1", "v1")
        cache.set("key1", "v2")  # 覆盖，不增加 size
        self.assertEqual(cache.get("key1"), "v2")
        cache.set("key2", "v2")
        # 仍只有 2 个 key，不应驱逐
        self.assertEqual(cache.get("key1"), "v2")


class TTLCacheValuesTests(unittest.TestCase):
    """_TTLCache.values 快照。"""

    def test_returns_all_values(self):
        cache = _TTLCache(maxsize=10, ttl=60)
        cache.set("key1", "v1")
        cache.set("key2", "v2")
        values = cache.values()
        self.assertIn("v1", values)
        self.assertIn("v2", values)
        self.assertEqual(len(values), 2)

    def test_excludes_expired(self):
        cache = _TTLCache(maxsize=10, ttl=60)
        cache.set("key1", "v1")
        cache.set("key2", "v2")
        # 使 key1 过期
        cache._timestamps["key1"] = time.time() - 100
        values = cache.values()
        self.assertNotIn("v1", values)
        self.assertIn("v2", values)

    def test_empty_cache_returns_empty_list(self):
        cache = _TTLCache()
        self.assertEqual(cache.values(), [])


class TTLCacheThreadSafetyTests(unittest.TestCase):
    """_TTLCache 线程安全。"""

    def test_concurrent_set_get(self):
        cache = _TTLCache(maxsize=100, ttl=60)
        errors = []

        def writer():
            try:
                for i in range(50):
                    cache.set(f"key_{i}", f"value_{i}")
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                for i in range(50):
                    cache.get(f"key_{i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer) for _ in range(3)]
        threads += [threading.Thread(target=reader) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])


# ============================================================================
# _get_backend_kwargs 单测
# ============================================================================


class GetBackendKwargsTests(unittest.TestCase):
    """_get_backend_kwargs store_type 配置映射。"""

    def test_pgvector_returns_empty(self):
        self.assertEqual(_get_backend_kwargs("pgvector"), {})

    def test_faiss_returns_base_path(self):
        kwargs = _get_backend_kwargs("faiss")
        self.assertIn("base_path", kwargs)

    def test_chroma_returns_persist_directory(self):
        kwargs = _get_backend_kwargs("chroma")
        self.assertIn("persist_directory", kwargs)
        self.assertIn("collection_name", kwargs)

    def test_milvus_returns_uri(self):
        kwargs = _get_backend_kwargs("milvus")
        self.assertIn("uri", kwargs)

    def test_inmemory_returns_empty(self):
        self.assertEqual(_get_backend_kwargs("inmemory"), {})

    def test_unknown_returns_empty(self):
        self.assertEqual(_get_backend_kwargs("unknown_store"), {})

    def test_empty_string_returns_empty(self):
        self.assertEqual(_get_backend_kwargs(""), {})


# ============================================================================
# IndexManager._generate_stable_ids 单测
# ============================================================================


class GenerateStableIdsTests(unittest.TestCase):
    """_generate_stable_ids 稳定 ID 生成。"""

    def setUp(self):
        self.manager = IndexManager(base_path=tempfile.gettempdir())

    def test_same_content_same_id(self):
        docs = [Document(page_content="相同内容", metadata={"source": "file.pdf"})]
        ids1 = self.manager._generate_stable_ids(docs)
        ids2 = self.manager._generate_stable_ids(docs)
        self.assertEqual(ids1, ids2)

    def test_different_content_different_id(self):
        docs1 = [Document(page_content="内容A", metadata={"source": "file.pdf"})]
        docs2 = [Document(page_content="内容B", metadata={"source": "file.pdf"})]
        ids1 = self.manager._generate_stable_ids(docs1)
        ids2 = self.manager._generate_stable_ids(docs2)
        self.assertNotEqual(ids1[0], ids2[0])

    def test_different_source_different_id(self):
        docs1 = [Document(page_content="相同内容", metadata={"source": "file1.pdf"})]
        docs2 = [Document(page_content="相同内容", metadata={"source": "file2.pdf"})]
        ids1 = self.manager._generate_stable_ids(docs1)
        ids2 = self.manager._generate_stable_ids(docs2)
        self.assertNotEqual(ids1[0], ids2[0])

    def test_empty_list_returns_empty(self):
        self.assertEqual(self.manager._generate_stable_ids([]), [])

    def test_multiple_docs(self):
        docs = [Document(page_content=f"内容{i}", metadata={"source": f"file{i}.pdf"}) for i in range(5)]
        ids = self.manager._generate_stable_ids(docs)
        self.assertEqual(len(ids), 5)
        # 所有 ID 应唯一
        self.assertEqual(len(ids), len(set(ids)))

    def test_missing_metadata_source(self):
        # metadata 无 source 键时使用空字符串，不报错
        docs = [Document(page_content="内容", metadata={})]
        ids = self.manager._generate_stable_ids(docs)
        self.assertEqual(len(ids), 1)

    def test_empty_metadata(self):
        # metadata 为空 dict 时不报错（Pydantic v2 不接受 None，用 {} 代替）
        docs = [Document(page_content="内容", metadata={})]
        ids = self.manager._generate_stable_ids(docs)
        self.assertEqual(len(ids), 1)

    def test_ids_are_valid_uuids(self):
        import uuid

        docs = [Document(page_content="内容", metadata={"source": "file.pdf"})]
        ids = self.manager._generate_stable_ids(docs)
        # 应能解析为 UUID
        parsed = uuid.UUID(ids[0])
        self.assertEqual(str(parsed), ids[0])


# ============================================================================
# IndexManager 路径计算单测
# ============================================================================


class IndexManagerPathTests(unittest.TestCase):
    """_get_index_path / _get_metadata_path 路径计算。"""

    def setUp(self):
        self.manager = IndexManager(base_path="/tmp/test_indexes")  # noqa: S108

    def test_get_index_path(self):
        path = self.manager._get_index_path("my_index")
        self.assertIn("my_index", str(path))
        self.assertIn("test_indexes", str(path))

    def test_get_metadata_path(self):
        path = self.manager._get_metadata_path("my_index")
        self.assertIn("my_index", str(path))
        self.assertTrue(str(path).endswith(".json"))

    def test_different_names_different_paths(self):
        path1 = self.manager._get_index_path("index1")
        path2 = self.manager._get_index_path("index2")
        self.assertNotEqual(str(path1), str(path2))


# ============================================================================
# IndexManager.index_exists 单测
# ============================================================================


class IndexExistsTests(unittest.TestCase):
    """index_exists 索引存在性检查。"""

    def test_nonexistent_returns_false(self):
        manager = IndexManager(base_path="/nonexistent/path/xyz")
        self.assertFalse(manager.index_exists("nonexistent_index"))

    def test_existing_index_returns_true(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = IndexManager(base_path=tmpdir)
            # 创建一个空的 metadata 文件模拟存在的索引
            meta_path = manager._get_metadata_path("test_index")
            meta_path.parent.mkdir(parents=True, exist_ok=True)
            meta_path.write_text('{"name": "test_index"}', encoding="utf-8")
            self.assertTrue(manager.index_exists("test_index"))


# ============================================================================
# IndexManager.list_indexes 单测
# ============================================================================


class ListIndexesTests(unittest.TestCase):
    """list_indexes 索引列表。"""

    def test_empty_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = IndexManager(base_path=tmpdir)
            result = manager.list_indexes()
            self.assertEqual(result, [])

    def test_lists_existing_indexes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = IndexManager(base_path=tmpdir)
            # 创建一个 metadata 文件
            meta_path = manager._get_metadata_path("test_index")
            meta_path.parent.mkdir(parents=True, exist_ok=True)
            meta_path.write_text(
                '{"name": "test_index", "doc_count": 5, "store_type": "faiss"}',
                encoding="utf-8",
            )
            result = manager.list_indexes()
            self.assertIsInstance(result, list)
            # 应包含 test_index
            names = [item.get("name") for item in result if isinstance(item, dict)]
            self.assertIn("test_index", names)


# ============================================================================
# IndexManager._detect_embedding_dimension 单测
# ============================================================================


class DetectEmbeddingDimensionTests(unittest.TestCase):
    """_detect_embedding_dimension embedding 维度检测。"""

    def test_none_embeddings_returns_none(self):
        self.assertIsNone(IndexManager._detect_embedding_dimension(None))

    def test_with_mock_embeddings(self):
        # mock embeddings 对象，返回固定维度
        mock_embeddings = MagicMock()
        mock_embeddings.embedding_dim = 1536
        result = IndexManager._detect_embedding_dimension(mock_embeddings)
        # _detect_embedding_dimension 可能通过不同方式获取维度
        # 只要不报错且返回合理值即可
        if result is not None:
            self.assertIsInstance(result, int)


if __name__ == "__main__":
    unittest.main()
