"""B4~B7 RAG 服务健壮性回归（Task 8）。

四项整改的回归锁定：
- B4：delete_knowledge_base 向量删除失败即整体失败（对齐 delete_document E5-04
  三态一致）——旧行为忽略 delete_index 的 fail-soft 返回值继续软删 DB 行并
  rmtree 上传目录，PG 故障时向量残留 + 同名库重建恒抛
  KnowledgeBaseAlreadyExistsError 且无自愈路径。向量本就不存在
  （backend.exists()=False）属幂等 no-op，放行删除（不制造"删不掉"死角）。
- B5：查询缓存键补模型/模式段（读写必须同键）；create/update_knowledge_base
  失效必须带 user_index_name——invalidate_knowledge_cache 只在传入时才执行
  QueryCacheService/VectorSearchCacheService.invalidate_index_queries，
  只传 user_id 是空转。
- B6：多文件上传逐文件容错（对齐 document_service.load_directory 口径）——
  坏文件跳过并记录失败原因，返回部分成功报告；全失败才整体失败。
- B7：retriever_tool._arun_comprehensive 检索经 asyncio.to_thread 离开事件循环
  （与 _arun precise 分支同构，守护测试防回退为事件循环内同步直调）。

运行（backend/Django_xm 目录）：
    python manage.py test Django_xm.apps.knowledge.tests.test_rag_service_robustness --settings=Django_xm.settings.test
"""

import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from langchain_core.documents import Document as LCDocument

from Django_xm.apps.cache_manager.services import cache_service as cache_service_module
from Django_xm.apps.cache_manager.services.cache_service import (
    QueryCacheService,
    generate_query_cache_key,
)
from Django_xm.apps.knowledge.models import Document, IndexMetadata
from Django_xm.apps.knowledge.services import kb_service
from Django_xm.apps.knowledge.services.retriever_tool import (
    KB_RESULT_INSTRUCTION,
    SyncSafeRetrieverTool,
)
from Django_xm.common.error_codes import ErrorCode

_KB_SERVICE = "Django_xm.apps.knowledge.services.kb_service"
_RETRIEVER_TOOL = "Django_xm.apps.knowledge.services.retriever_tool"
_RAG_RETRIEVAL = "Django_xm.apps.knowledge.services.rag_retrieval"
_CROSS_APP = "Django_xm.apps.chat.services.cross_app"


# ── B4：删库向量删除失败即整体失败 ──────────────────────────────────


class DeleteKnowledgeBaseThreeStateTests(TestCase):
    """B4：向量删除失败 → 抛 KnowledgeBaseError，DB 行与上传目录原状（可重试）。"""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="kbdelb4", password="pw123456")
        self.user_index_name = f"user_{self.user.id}_kb-b4"

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        upload_dir_patch = mock.patch.object(kb_service.app_cfg, "data_uploads_path", self._tmp.name)
        upload_dir_patch.start()
        self.addCleanup(upload_dir_patch.stop)

        manager_patcher = mock.patch(f"{_KB_SERVICE}.IndexManager")
        self.manager_cls = manager_patcher.start()
        self.manager = self.manager_cls.return_value
        self.manager.get_store_type.return_value = "pgvector"
        self.backend = mock.Mock()
        self.manager.get_backend.return_value = self.backend
        self.addCleanup(manager_patcher.stop)

        cache_patcher = mock.patch(f"{_KB_SERVICE}.invalidate_knowledge_cache")
        self.invalidate_cache = cache_patcher.start()
        self.addCleanup(cache_patcher.stop)

        cross_app_patcher = mock.patch(f"{_CROSS_APP}.clear_knowledge_base_selection")
        cross_app_patcher.start()
        self.addCleanup(cross_app_patcher.stop)

        self.index_meta = IndexMetadata.objects.create(user=self.user, name=self.user_index_name)
        self.doc_row = Document.objects.create(
            index=self.index_meta, filename="x.md", file_path="/uploads/x.md", file_size=10, chunk_count=2
        )

        upload_dir = Path(self._tmp.name) / self.user_index_name
        upload_dir.mkdir(parents=True, exist_ok=True)
        self.upload_file = upload_dir / "x.md"
        self.upload_file.write_bytes(b"data")

    def test_vector_delete_failure_raises_and_keeps_three_states(self):
        """向量存在且 delete_index 返回 False → 抛错；DB 行/文件/缓存失效全不动。"""
        self.backend.exists.return_value = True
        self.manager.delete_index.return_value = False

        with self.assertRaises(kb_service.KnowledgeBaseError) as ctx:
            kb_service.delete_knowledge_base(self.user, "kb-b4")

        self.assertEqual(ctx.exception.business_code, ErrorCode.SERVICE_UNAVAILABLE)
        self.manager.delete_index.assert_called_once_with(self.user_index_name)

        # 三态一致：DB 实体行未软删、Document 行未软删、上传目录未清
        meta = IndexMetadata.all_objects.get(name=self.user_index_name)
        self.assertFalse(meta.is_deleted)
        self.assertIsNone(meta.deleted_at)
        self.doc_row.refresh_from_db()
        self.assertFalse(self.doc_row.is_deleted)
        self.assertTrue(self.upload_file.exists())
        self.invalidate_cache.assert_not_called()

    def test_probe_failure_raises_as_delete_failure(self):
        """存在性探测异常（PG 不可达）→ 同样按删除失败整体失败。"""
        self.backend.exists.side_effect = RuntimeError("pg down")

        with self.assertRaises(kb_service.KnowledgeBaseError) as ctx:
            kb_service.delete_knowledge_base(self.user, "kb-b4")

        self.assertEqual(ctx.exception.business_code, ErrorCode.SERVICE_UNAVAILABLE)
        self.manager.delete_index.assert_not_called()
        self.invalidate_cache.assert_not_called()

    def test_vector_absent_proceeds_idempotent_delete(self):
        """向量本就不存在（delete fail-soft 返回 False）→ 放行删除，不留"删不掉"死角。"""
        self.backend.exists.return_value = False
        self.manager.delete_index.return_value = False

        kb_service.delete_knowledge_base(self.user, "kb-b4")

        meta = IndexMetadata.all_objects.get(name=self.user_index_name)
        self.assertTrue(meta.is_deleted)
        self.assertIsNotNone(meta.deleted_at)
        self.assertTrue(Document.all_objects.get(filename="x.md").is_deleted)
        self.assertFalse(self.upload_file.exists())
        self.invalidate_cache.assert_called_once_with(user_id=self.user.id, user_index_name=self.user_index_name)


# ── B5：查询缓存键模型/模式段 + 失效真实生效 ────────────────────────


class QueryCacheKeySegmentsTests(SimpleTestCase):
    """B5：查询缓存键含模型/模式段，读写同键，换模型/模式后旧缓存不命中。"""

    def test_key_contains_model_and_mode_segments(self):
        key = generate_query_cache_key("q", "user_1_kb", 4, model_name="openai:gpt-4o", rag_mode="strict")
        self.assertTrue(key.startswith("rag_query:user_1_kb:"))
        self.assertIn(":k4:mopenai:gpt-4o:rstrict", key)

    def test_different_model_or_mode_yields_different_key(self):
        base = generate_query_cache_key("q", "user_1_kb", 4, model_name="openai:gpt-4o", rag_mode="strict")
        other_model = generate_query_cache_key("q", "user_1_kb", 4, model_name="other:model", rag_mode="strict")
        other_mode = generate_query_cache_key("q", "user_1_kb", 4, model_name="openai:gpt-4o", rag_mode="agent")
        self.assertEqual(len({base, other_model, other_mode}), 3)

    def test_read_and_write_use_same_key(self):
        """同参数读与写必须落到同一键（读写异键 = 写后永不命中的静默失效）。"""
        with (
            mock.patch.object(cache_service_module.CacheService, "get", return_value=None) as get_mock,
            mock.patch.object(cache_service_module.CacheService, "set", return_value=True) as set_mock,
        ):
            QueryCacheService.get_cached_query("q", "idx", 4, model_name="test-model", rag_mode="strict")
            QueryCacheService.cache_query_result("q", {}, "idx", 4, model_name="test-model", rag_mode="strict")

        self.assertEqual(get_mock.call_args.args[0], set_mock.call_args.args[0])
        self.assertIn(":mtest-model:rstrict", get_mock.call_args.args[0])

    def test_default_segments_placeholder(self):
        """取不到模型/模式时 "default" 占位（docstring 契约，键结构变化旧键自然失配）。"""
        key = generate_query_cache_key("q", "idx", 4)
        self.assertTrue(key.endswith(":k4:mdefault:rdefault"))


class UpdateKnowledgeBaseCacheInvalidationTests(TestCase):
    """B5：update 描述后失效必须带 user_index_name（旧实现只传 user_id 空转）。"""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="kbupdb5", password="pw123456")
        cache_patcher = mock.patch(f"{_KB_SERVICE}.invalidate_knowledge_cache")
        self.invalidate_cache = cache_patcher.start()
        self.addCleanup(cache_patcher.stop)
        IndexMetadata.objects.create(user=self.user, name=f"user_{self.user.id}_kb-b5", description="旧描述")

    def test_update_invalidates_index_caches(self):
        result = kb_service.update_knowledge_base(self.user, "kb-b5", "新描述")

        self.assertEqual(result["description"], "新描述")
        self.invalidate_cache.assert_called_once_with(
            user_id=self.user.id, user_index_name=f"user_{self.user.id}_kb-b5"
        )


# ── B6：多文件上传逐文件容错、部分成功报告 ──────────────────────────


class UploadPartialSuccessTests(TestCase):
    """B6：单坏文件部分成功；文件缺失记失败不中断；全失败才整体失败。"""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="kbupb6", password="pw123456")
        self.user_index_name = f"user_{self.user.id}_kb-b6"
        self.index_meta = IndexMetadata.objects.create(user=self.user, name=self.user_index_name)

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        upload_dir_patch = mock.patch.object(kb_service.app_cfg, "data_uploads_path", self._tmp.name)
        upload_dir_patch.start()
        self.addCleanup(upload_dir_patch.stop)

        manager_patcher = mock.patch(f"{_KB_SERVICE}.IndexManager")
        self.manager_cls = manager_patcher.start()
        self.manager = self.manager_cls.return_value
        self.manager.index_exists.return_value = True
        self.manager.load_metadata.return_value = {"embedding_dimension": None, "num_documents": 0}
        self.manager.add_documents.return_value = 3
        self.manager.remove_documents_by_filename.return_value = 1
        self.addCleanup(manager_patcher.stop)

        cache_patcher = mock.patch(f"{_KB_SERVICE}.invalidate_knowledge_cache")
        self.invalidate_cache = cache_patcher.start()
        self.addCleanup(cache_patcher.stop)

        emb_patcher = mock.patch(f"{_KB_SERVICE}.get_embeddings", return_value=SimpleNamespace(name="emb-stub"))
        emb_patcher.start()
        self.addCleanup(emb_patcher.stop)

        split_patch = mock.patch(
            f"{_KB_SERVICE}.split_documents",
            side_effect=lambda docs: [
                LCDocument(page_content=d.page_content, metadata=dict(d.metadata)) for d in docs
            ],
        )
        split_patch.start()
        self.addCleanup(split_patch.stop)

        self.upload_dir = Path(self._tmp.name) / self.user_index_name
        self.upload_dir.mkdir(parents=True, exist_ok=True)

    def _make_file(self, name: str) -> None:
        (self.upload_dir / name).write_bytes(b"data")

    @staticmethod
    def _doc(name: str) -> LCDocument:
        return LCDocument(page_content=f"{name} body", metadata={"source": f"/x/{name}", "file_name": name})

    def test_single_bad_file_partial_success(self):
        """一好一坏：好文件正常入库，坏文件跳过并记录（不再整批拖垮）。"""
        self._make_file("good.txt")
        self._make_file("bad.txt")

        def fake_load(path: str):
            if Path(path).name == "bad.txt":
                raise ValueError("无法解析 PDF")
            return [self._doc(Path(path).name)]

        with mock.patch(f"{_KB_SERVICE}.load_document", side_effect=fake_load):
            result = kb_service.process_uploaded_documents(self.user, "kb-b6", ["good.txt", "bad.txt"])

        self.assertEqual(result["documents_uploaded"], 1)
        self.assertEqual(result["documents_failed"], 1)
        self.assertEqual(result["failed_files"], [{"name": "bad.txt", "reason": "无法解析 PDF"}])
        self.assertEqual([f["name"] for f in result["files"]], ["good.txt"])

        # 仅成功文件进入 Document 行与向量侧替换清理（坏文件旧向量保留）
        self.assertTrue(
            Document.objects.filter(index=self.index_meta, filename="good.txt", is_deleted=False).exists()
        )
        self.assertFalse(
            Document.objects.filter(index=self.index_meta, filename="bad.txt", is_deleted=False).exists()
        )
        cleaned = {c.args[2] for c in self.manager.remove_documents_by_filename.call_args_list}
        self.assertEqual(cleaned, {"good.txt"})

    def test_missing_file_recorded_as_failure_without_aborting_batch(self):
        """文件落盘缺失：记失败条目，不中断其余文件（旧实现直接 raise）。"""
        self._make_file("good.txt")

        with mock.patch(f"{_KB_SERVICE}.load_document", return_value=[self._doc("good.txt")]):
            result = kb_service.process_uploaded_documents(self.user, "kb-b6", ["good.txt", "ghost.txt"])

        self.assertEqual(result["documents_uploaded"], 1)
        self.assertEqual(result["documents_failed"], 1)
        self.assertEqual(result["failed_files"], [{"name": "ghost.txt", "reason": "文件不存在"}])

    def test_empty_extraction_recorded_as_failure(self):
        """load_document 成功但零内容 → 记失败（旧实现触发整批 raise）。"""
        self._make_file("good.txt")
        self._make_file("empty.txt")

        def fake_load(path: str):
            if Path(path).name == "empty.txt":
                return []
            return [self._doc(Path(path).name)]

        with mock.patch(f"{_KB_SERVICE}.load_document", side_effect=fake_load):
            result = kb_service.process_uploaded_documents(self.user, "kb-b6", ["good.txt", "empty.txt"])

        self.assertEqual(result["documents_uploaded"], 1)
        self.assertEqual(result["documents_failed"], 1)
        self.assertEqual(result["failed_files"][0]["name"], "empty.txt")

    def test_all_files_fail_raises_with_details(self):
        """全失败 → 整体失败（确定性错误），异常含各文件失败原因。"""
        self._make_file("bad1.txt")
        self._make_file("bad2.txt")

        def fake_load(path: str):
            raise ValueError(f"解析失败: {Path(path).name}")

        with mock.patch(f"{_KB_SERVICE}.load_document", side_effect=fake_load), self.assertRaises(ValueError) as ctx:
            kb_service.process_uploaded_documents(self.user, "kb-b6", ["bad1.txt", "bad2.txt"])

        self.assertIn("所有上传文件均处理失败", str(ctx.exception))
        self.assertIn("bad1.txt", str(ctx.exception))
        self.assertIn("bad2.txt", str(ctx.exception))


# ── B7：comprehensive 异步检索离开事件循环（守护）──────────────────


class ComprehensiveAsyncThreadOffloadTests(IsolatedAsyncioTestCase):
    """B7：_arun_comprehensive 经 asyncio.to_thread 调度（与 precise 分支同构）。"""

    async def test_arun_comprehensive_offloads_via_to_thread(self):
        pipeline_result = SimpleNamespace(status="ok", text="KB内容", error=None)
        with (
            mock.patch(f"{_RAG_RETRIEVAL}.UnifiedRagPipeline") as pipeline_cls,
            mock.patch(
                f"{_RETRIEVER_TOOL}.asyncio.to_thread", new_callable=mock.AsyncMock, return_value=pipeline_result
            ) as to_thread_mock,
        ):
            pipeline_cls.return_value.search_with_retriever.return_value = pipeline_result
            tool = SyncSafeRetrieverTool(retriever=None)
            result = await tool._arun_comprehensive("总结知识库")

        to_thread_mock.assert_awaited_once()
        args, kwargs = to_thread_mock.await_args
        self.assertIs(args[0], pipeline_cls.return_value.search_with_retriever)
        self.assertEqual(kwargs["query"], "总结知识库")
        # ok 状态：KB 指令前缀 + 真实检索内容（_search_message 契约）
        self.assertIn(KB_RESULT_INSTRUCTION, result)
        self.assertIn("KB内容", result)
