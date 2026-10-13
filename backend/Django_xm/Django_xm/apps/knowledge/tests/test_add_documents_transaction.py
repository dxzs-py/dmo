"""3.3 add_documents 事务化 + 探测收敛 + 失败补偿单测。

背景：add_documents 原先的向量写入与元数据两写（IndexMetadata 行 +
num_documents 计数）无事务包裹，且 store_type/index_exists/load_metadata
在同一批内重复触发多次 IndexMetadata 探测查询。修复后：
- 元数据两写包 transaction.atomic（行与计数同滚）；
- 向量库写入不可回滚 → 固定"先向量后元数据"顺序，向量成功而元数据
  失败时按稳定 ID 补偿删除本批向量（_compensate_added_vectors）；
- store_type 仅探测一次，经参数传给 index_exists / load_metadata。

运行（backend/Django_xm 目录，conda env langchain_xm，DB_NAME=langchain_verify）：
    python manage.py test Django_xm.apps.knowledge.tests.test_add_documents_transaction --noinput
"""

import contextlib
import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.test import TestCase

from Django_xm.apps.knowledge.services.index_service import IndexManager


class AddDocumentsTransactionTests(TestCase):
    """add_documents 元数据一致性窗口、向量补偿与探测收敛。"""

    def setUp(self):
        self.manager = IndexManager(base_path=tempfile.mkdtemp())
        self.vector_store = mock.MagicMock(name="vector_store")
        self.backend = mock.MagicMock(name="backend")
        self.backend.exists.return_value = True
        self.backend.create.return_value = self.vector_store

    def _enter_core_patches(self, stack, *, metadata, save_metadata=None) -> dict:
        """公共替身集：后端/加载/稳定 ID/元数据写入全部 mock（不触真实向量库与 DB）。

        Returns:
            关键 mock 引用表：save_metadata / update_doc_count（作用域外断言用）。
        """
        mocks: dict = {}
        stack.enter_context(mock.patch.object(IndexManager, "get_store_type", return_value="faiss"))
        stack.enter_context(mock.patch.object(IndexManager, "get_backend", return_value=self.backend))
        stack.enter_context(mock.patch.object(IndexManager, "index_exists", return_value=True))
        stack.enter_context(mock.patch.object(IndexManager, "load_metadata", return_value=metadata))
        stack.enter_context(mock.patch.object(IndexManager, "_generate_stable_ids", return_value=["id-1", "id-2"]))
        mocks["update_doc_count"] = mock.MagicMock(name="update_doc_count")
        stack.enter_context(
            mock.patch.object(IndexManager, "_update_metadata_doc_count", mocks["update_doc_count"])
        )
        stack.enter_context(mock.patch.object(IndexManager, "_cache", mock.MagicMock()))
        if not (metadata and metadata.get("num_documents", 0) == 0):
            # 非空索引路径：load_index + 携带稳定 ID 追加
            stack.enter_context(mock.patch.object(IndexManager, "load_index", return_value=self.vector_store))
            stack.enter_context(mock.patch.object(IndexManager, "_add_documents_with_stable_ids"))
        if save_metadata is not None:
            mocks["save_metadata"] = mock.MagicMock(name="save_metadata", side_effect=save_metadata)
        else:
            mocks["save_metadata"] = mock.MagicMock(name="save_metadata")
        stack.enter_context(mock.patch.object(IndexManager, "_save_metadata", mocks["save_metadata"]))
        return mocks

    def test_metadata_failure_compensates_vectors_nonempty_index(self):
        """非空索引：向量写入成功 + _save_metadata 失败 → 补偿删除本批向量并上抛。"""
        with contextlib.ExitStack() as stack:
            self._enter_core_patches(
                stack, metadata={"num_documents": 3, "store_type": "faiss"}, save_metadata=RuntimeError("db down")
            )
            with self.assertRaises(RuntimeError):
                self.manager.add_documents("user_1_kb", [mock.MagicMock()], mock.MagicMock())

        self.vector_store.delete.assert_called_once_with(["id-1", "id-2"])
        self.backend.save.assert_called_once()

    def test_metadata_failure_compensates_vectors_empty_index(self):
        """空索引路径：backend.create 成功 + _save_metadata 失败 → 同样补偿。"""
        with contextlib.ExitStack() as stack:
            self._enter_core_patches(
                stack, metadata={"num_documents": 0, "store_type": "faiss"}, save_metadata=RuntimeError("db down")
            )
            with self.assertRaises(RuntimeError):
                self.manager.add_documents("user_1_kb", [mock.MagicMock()], mock.MagicMock())

        self.backend.create.assert_called_once()
        self.vector_store.delete.assert_called_once_with(["id-1", "id-2"])

    def test_compensation_failure_does_not_mask_original_error(self):
        """补偿删除失败仅 error 留痕，不掩盖原始元数据错误。"""
        with contextlib.ExitStack() as stack:
            self._enter_core_patches(
                stack, metadata={"num_documents": 3, "store_type": "faiss"}, save_metadata=RuntimeError("db down")
            )
            self.vector_store.delete.side_effect = RuntimeError("delete failed")
            with self.assertRaisesRegex(RuntimeError, "db down"):
                self.manager.add_documents("user_1_kb", [mock.MagicMock()], mock.MagicMock())

    def test_success_path_writes_metadata_and_count_without_compensation(self):
        """成功路径：_save_metadata 与 _update_metadata_doc_count 各一次，无补偿删除。"""
        with contextlib.ExitStack() as stack:
            mocks = self._enter_core_patches(stack, metadata={"num_documents": 3, "store_type": "faiss"})
            result = self.manager.add_documents(
                "user_1_kb", [mock.MagicMock(), mock.MagicMock()], mock.MagicMock()
            )

            self.assertEqual(result, 2)
            self.backend.save.assert_called_once()
            mocks["save_metadata"].assert_called_once()
            mocks["update_doc_count"].assert_called_once()
            self.vector_store.delete.assert_not_called()

    def test_probe_convergence_store_type_detected_once(self):
        """探测收敛：store_type 只探测一次，index_exists/load_metadata 复用传参。"""
        get_store_type = mock.MagicMock(return_value="faiss")
        metadata = {"num_documents": 3, "store_type": "faiss"}
        with (
            mock.patch.object(IndexManager, "get_store_type", get_store_type),
            mock.patch.object(IndexManager, "get_backend", return_value=self.backend),
            mock.patch.object(IndexManager, "_scoped_metadata_qs", return_value=mock.MagicMock()),
            mock.patch.object(IndexManager, "load_metadata", return_value=metadata) as load_md,
            mock.patch.object(IndexManager, "load_index", return_value=self.vector_store),
            mock.patch.object(IndexManager, "_add_documents_with_stable_ids"),
            mock.patch.object(IndexManager, "_save_metadata"),
            mock.patch.object(IndexManager, "_update_metadata_doc_count"),
            mock.patch.object(IndexManager, "_generate_stable_ids", return_value=["id-1"]),
            mock.patch.object(IndexManager, "_detect_store_type") as detect,
            mock.patch.object(IndexManager, "_cache", mock.MagicMock()),
        ):
            self.manager.add_documents("user_1_kb", [mock.MagicMock()], mock.MagicMock())

        # store_type 探测恰好一次；_detect_store_type 未被再次触发（load_metadata 复用传参）
        self.assertEqual(get_store_type.call_count, 1)
        detect.assert_not_called()
        load_md.assert_called_once_with("user_1_kb", store_type="faiss")


if __name__ == "__main__":
    unittest.main()
