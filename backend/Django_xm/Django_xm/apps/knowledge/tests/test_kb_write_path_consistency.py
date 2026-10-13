"""W4 写路径一致性测试（E5-04 / C-5 / B-5，整改依据 rag_audit_summary §三/§五）。

三类写路径一致性缺口的回归锁定：
- E5-04：delete_document 向量删除失败 → 不得吞异常继续删物理文件/软删 DB 行
  （旧行为：孤儿向量 + 后续同名重传更新污染 + 假"文件已删除"成功响应）；
  新行为：向量删除失败即抛 KnowledgeBaseError（SERVICE_UNAVAILABLE），
  文件与 DB 行保持原状（三态一致，用户可重试）。
- C-5：上传链替换语义 —— 同名文件先删后加（防新旧版本并存可检索），
  旧 Document 行软删不重复累积，chunk_count 按文件归属统计（非 len(chunks)//len(files) 均值分摊）。
- B-5：Embedding 降级 → SystemConfig 同步逻辑收口到 embedding_service
  （collect_fallback_info 单源），kb_service 上传编排只透传 fallback_info。

运行（backend/Django_xm 目录）：
    python manage.py test Django_xm.apps.knowledge.tests.test_kb_write_path_consistency
"""

import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.contrib.auth import get_user_model
from django.test import TestCase
from langchain_core.documents import Document as LCDocument

from Django_xm.apps.knowledge.models import Document, IndexMetadata
from Django_xm.apps.knowledge.services import embedding_service, kb_service
from Django_xm.common.error_codes import ErrorCode

_KB_SERVICE = "Django_xm.apps.knowledge.services.kb_service"
_KNOWLEDGE_CONFIG = "Django_xm.apps.knowledge.config"


def _lcdoc(file_path: str, file_name: str, content: str = "body") -> LCDocument:
    return LCDocument(
        page_content=content,
        metadata={"source": file_path, "file_name": file_name},
    )


class _BaseKbWriteTest(TestCase):
    """公共夹具：临时上传目录 + IndexManager/缓存失效/get_embeddings/load/split 打桩。"""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="kbwrite", password="pw123456")
        self.user_index_name = f"user_{self.user.id}_kb-write"
        self.index_meta = IndexMetadata.objects.create(
            user=self.user, name=self.user_index_name
        )

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        upload_dir_patch = mock.patch.object(
            kb_service.app_cfg, "data_uploads_path", self._tmp.name
        )
        upload_dir_patch.start()
        self.addCleanup(upload_dir_patch.stop)

        manager_patcher = mock.patch(f"{_KB_SERVICE}.IndexManager")
        self.manager_cls = manager_patcher.start()
        self.manager = self.manager_cls.return_value
        self.manager.index_exists.return_value = True
        self.manager.load_metadata.return_value = {"embedding_dimension": None, "num_documents": 0}
        self.manager.add_documents.return_value = 0
        self.manager.remove_documents_by_filename.return_value = 1
        self.addCleanup(manager_patcher.stop)

        cache_patcher = mock.patch(f"{_KB_SERVICE}.invalidate_knowledge_cache")
        self.invalidate_cache = cache_patcher.start()
        self.addCleanup(cache_patcher.stop)

        emb_patcher = mock.patch(f"{_KB_SERVICE}.get_embeddings")
        self.get_embeddings = emb_patcher.start()
        self.embeddings_stub = SimpleNamespace(name="embeddings-stub")
        self.get_embeddings.return_value = self.embeddings_stub
        self.addCleanup(emb_patcher.stop)

        # 文档加载/分块打桩：load_document 每文件产出 1 个携带 file_name 的 doc；
        # split_documents 按文件名归属产出块数（a.txt→2、其余→1），供 chunk_count
        # 归属断言使用（旧实现均值分摊无法区分 2/1）。
        load_patch = mock.patch(
            f"{_KB_SERVICE}.load_document",
            side_effect=lambda p: [_lcdoc(p, os.path.basename(p), os.path.basename(p))],
        )
        load_patch.start()
        self.addCleanup(load_patch.stop)

        def fake_split(docs):
            out = []
            for d in docs:
                n = 2 if d.metadata.get("file_name") == "a.txt" else 1
                out.extend(
                    LCDocument(page_content=f"{d.page_content}-{i}", metadata=dict(d.metadata))
                    for i in range(n)
                )
            return out

        split_patch = mock.patch(f"{_KB_SERVICE}.split_documents", side_effect=fake_split)
        split_patch.start()
        self.addCleanup(split_patch.stop)

    def _make_file(self, name: str, content: bytes = b"data") -> str:
        path = Path(self._tmp.name) / self.user_index_name / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return str(path)


class DeleteDocumentThreeStateTests(_BaseKbWriteTest):
    """E5-04：删除三态一致 —— 向量删除失败不得假成功。"""

    def setUp(self):
        super().setUp()
        self.filename = "doc.txt"
        self.file_path = self._make_file(self.filename)
        self.doc_row = Document.objects.create(
            index=self.index_meta,
            filename=self.filename,
            file_path=self.file_path,
            file_size=4,
            chunk_count=2,
        )

    def test_vector_delete_failure_raises_and_keeps_three_states(self):
        """向量删除失败 → 抛 KnowledgeBaseError；文件与 DB 行原状（可重试）。"""
        self.manager.remove_documents_by_filename.side_effect = RuntimeError("pg down")

        with self.assertRaises(kb_service.KnowledgeBaseError) as ctx:
            kb_service.delete_document(self.user, "kb-write", self.filename)

        self.assertEqual(ctx.exception.business_code, ErrorCode.SERVICE_UNAVAILABLE)
        # 三态一致：物理文件未删
        self.assertTrue(os.path.exists(self.file_path))
        # DB 行未软删
        self.doc_row.refresh_from_db()
        self.assertFalse(self.doc_row.is_deleted)
        self.assertIsNone(self.doc_row.deleted_at)
        # 失败路径不触发缓存失效（无事发生）
        self.invalidate_cache.assert_not_called()

    def test_vector_delete_success_removes_all_three_states(self):
        """向量删除成功 → 文件删除 + DB 行软删 + 成功响应（既有行为守护）。"""
        result = kb_service.delete_document(self.user, "kb-write", self.filename)

        self.assertIn("文件已删除", result["message"])
        self.assertFalse(os.path.exists(self.file_path))
        self.doc_row.refresh_from_db()
        self.assertTrue(self.doc_row.is_deleted)
        self.manager.remove_documents_by_filename.assert_called_once_with(
            self.user_index_name, self.embeddings_stub, self.filename
        )


class UploadReplacementSemanticsTests(_BaseKbWriteTest):
    """C-5：同名重传先删后加 + Document 行不重复 + chunk_count 按文件归属。"""

    def setUp(self):
        super().setUp()
        self.file_a = self._make_file("a.txt")
        self.file_b = self._make_file("b.txt")
        # 上一轮上传遗留的活跃同名行（重传场景的污染源）
        self.old_row_a = Document.objects.create(
            index=self.index_meta,
            filename="a.txt",
            file_path=self.file_a,
            file_size=4,
            chunk_count=99,
        )

    def test_reupload_removes_old_chunks_and_soft_deletes_old_rows(self):
        """同名重传：先对每个文件执行向量删除；旧 Document 行软删，不重复累积。"""
        result = kb_service.process_uploaded_documents(
            self.user, "kb-write", ["a.txt", "b.txt"]
        )

        self.assertEqual(result["documents_uploaded"], 2)
        # C-5：每个上传文件都先删后加（新文件为无害 no-op，替换语义统一执行）
        self.assertEqual(
            self.manager.remove_documents_by_filename.call_count, 2,
            "每个上传文件都必须先执行向量侧同名清理（先删后加）",
        )
        called_names = {c.args[2] for c in self.manager.remove_documents_by_filename.call_args_list}
        self.assertEqual(called_names, {"a.txt", "b.txt"})
        for call in self.manager.remove_documents_by_filename.call_args_list:
            self.assertEqual(call.args[0], self.user_index_name)
            self.assertIs(call.args[1], self.embeddings_stub)

        # 旧行软删（不重复累积）
        self.old_row_a.refresh_from_db()
        self.assertTrue(self.old_row_a.is_deleted)

        # 每个文件名只剩一条活跃行
        active_a = Document.objects.filter(index=self.index_meta, filename="a.txt", is_deleted=False)
        active_b = Document.objects.filter(index=self.index_meta, filename="b.txt", is_deleted=False)
        self.assertEqual(active_a.count(), 1)
        self.assertEqual(active_b.count(), 1)

    def test_chunk_count_attributed_per_file_not_mean_averaged(self):
        """chunk_count 按文件归属：a.txt=2 / b.txt=1（旧实现均值 3//2=1 恒错）。"""
        kb_service.process_uploaded_documents(self.user, "kb-write", ["a.txt", "b.txt"])

        row_a = Document.objects.get(index=self.index_meta, filename="a.txt", is_deleted=False)
        row_b = Document.objects.get(index=self.index_meta, filename="b.txt", is_deleted=False)
        self.assertEqual(row_a.chunk_count, 2)
        self.assertEqual(row_b.chunk_count, 1)


class FallbackInfoCollectionTests(_BaseKbWriteTest):
    """B-5：SystemConfig 同步收口 embedding_service.collect_fallback_info。"""

    def _fallback_embeddings(self, provider: str = "prov-b"):
        return SimpleNamespace(
            get_fallback_events=lambda: [{"event": "provider_fallback"}],
            get_active_provider_id=lambda: provider,
        )

    def test_collect_fallback_info_syncs_system_config(self):
        """有降级事件 → SystemConfig.embedding_provider 对齐实际 provider 并返回 info。"""
        with mock.patch(
            f"{_KNOWLEDGE_CONFIG}.SystemConfig.get_value", return_value={"provider_id": "prov-a"}
        ) as get_value, mock.patch(f"{_KNOWLEDGE_CONFIG}.SystemConfig.set_value") as set_value:
            info = embedding_service.collect_fallback_info(self._fallback_embeddings())

        self.assertIsNotNone(info)
        self.assertEqual(info["actual_provider"], "prov-b")
        self.assertEqual(info["events"], [{"event": "provider_fallback"}])
        get_value.assert_called_once_with("embedding_provider", {})
        set_value.assert_called_once_with("embedding_provider", {"provider_id": "prov-b"})

    def test_collect_fallback_info_no_events_returns_none(self):
        """无降级事件 → None 且不触碰 SystemConfig。"""
        plain = SimpleNamespace(
            get_fallback_events=list,
            get_active_provider_id=lambda: "prov-b",
        )
        with mock.patch(f"{_KNOWLEDGE_CONFIG}.SystemConfig.set_value") as set_value:
            info = embedding_service.collect_fallback_info(plain)
        self.assertIsNone(info)
        set_value.assert_not_called()

    def test_collect_fallback_info_ignores_plain_embeddings(self):
        """裸 Embeddings（无事件接口）→ None，不报错。"""
        self.assertIsNone(embedding_service.collect_fallback_info(SimpleNamespace()))

    def test_upload_passes_through_fallback_info_with_config_sync(self):
        """上传链透传 fallback_info；SystemConfig 写入随 collect_fallback_info 仍生效。"""
        self._make_file("a.txt")
        self.get_embeddings.return_value = self._fallback_embeddings()
        with mock.patch(
            f"{_KNOWLEDGE_CONFIG}.SystemConfig.get_value", return_value={"provider_id": "prov-a"}
        ), mock.patch(f"{_KNOWLEDGE_CONFIG}.SystemConfig.set_value") as set_value:
            result = kb_service.process_uploaded_documents(self.user, "kb-write", ["a.txt"])

        self.assertEqual(result["fallback_info"]["actual_provider"], "prov-b")
        set_value.assert_called_once_with("embedding_provider", {"provider_id": "prov-b"})
