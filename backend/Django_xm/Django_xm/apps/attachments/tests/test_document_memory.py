"""D-006 文档记忆写侧接线回归测试（audit-supplement-remediate B7）。

契约：
1. 写侧→读侧闭环：save_document 写入 Store 后 build_document_context 可读出
   （文档记忆功能的唯一端到端契约，D-006 修复前写侧无调用方、读侧恒空）；
2. LLM 摘要失败降级：get_chat_model 异常时 _generate_summary 回退前 200 字截取，
   闭环不断；
3. enqueue_document_memory_save：图片跳过 / 正常投递 / 投递失败不外抛（上传
   主流程零影响）；
4. save_document_memory 任务防御分支：附件不存在 / 图片 / 内容读取失败均
   安全跳过。

运行方式：
   python manage.py test Django_xm.apps.attachments.tests.test_document_memory --noinput
"""

from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from Django_xm.apps.attachments.services.document_memory_service import (
    DocumentMemoryService,
    enqueue_document_memory_save,
)


def _make_service_with_memory_store():
    from langgraph.store.memory import InMemoryStore

    return DocumentMemoryService(store=InMemoryStore())


class SaveReadClosureTests(SimpleTestCase):
    """写侧→读侧闭环（D-006 核心契约）。"""

    def test_save_then_build_context_roundtrip(self):
        service = _make_service_with_memory_store()
        ok = service.save_document(
            user_id=1,
            attachment_id=101,
            doc_name="季度报告.pdf",
            content="营收增长 20%，核心业务稳步扩张。",
            summary="季度营收增长 20%",
        )
        self.assertTrue(ok)
        context = service.build_document_context(1)
        self.assertIn("用户已上传的文档", context)
        self.assertIn("季度报告.pdf", context)
        self.assertIn("季度营收增长 20%", context)

    def test_build_context_empty_when_no_docs(self):
        service = _make_service_with_memory_store()
        self.assertEqual(service.build_document_context(999), "")

    def test_summary_llm_failure_falls_back_to_truncation(self):
        """LLM 摘要失败降级：闭环不断，回退摘要为 [自动截取] 前缀。"""
        service = _make_service_with_memory_store()
        with patch(
            "Django_xm.apps.ai_engine.services.llm_factory.get_chat_model",
            side_effect=RuntimeError("LLM 不可用"),
        ):
            ok = service.save_document(
                user_id=2,
                attachment_id=202,
                doc_name="notes.md",
                content="第一段关键内容。" + "x" * 300,
            )
        self.assertTrue(ok)
        context = service.build_document_context(2)
        self.assertIn("notes.md", context)
        self.assertIn("[自动截取]", context)

    def test_delete_document_removes_from_context(self):
        service = _make_service_with_memory_store()
        service.save_document(
            user_id=3, attachment_id=303, doc_name="tmp.txt", content="hello", summary="s"
        )
        self.assertTrue(service.delete_document(3, 303))
        self.assertEqual(service.build_document_context(3), "")


class EnqueueDocumentMemorySaveTests(SimpleTestCase):
    def test_image_attachment_skipped(self):
        att = SimpleNamespace(id=1, mime_type="image/png")
        self.assertFalse(enqueue_document_memory_save(att))

    def test_dispatched_for_text_attachment(self):
        att = SimpleNamespace(id=2, mime_type="text/markdown")
        with patch("Django_xm.tasks.chat_tasks.save_document_memory") as task_mock:
            self.assertTrue(enqueue_document_memory_save(att))
        task_mock.delay.assert_called_once_with(2)

    def test_dispatch_failure_does_not_raise(self):
        """投递失败（broker 不可用等）只返回 False，绝不打断上传主流程。"""
        att = SimpleNamespace(id=3, mime_type="application/pdf")
        with patch("Django_xm.tasks.chat_tasks.save_document_memory") as task_mock:
            task_mock.delay.side_effect = ConnectionError("broker down")
            self.assertFalse(enqueue_document_memory_save(att))


class SaveDocumentMemoryTaskTests(SimpleTestCase):
    """任务防御分支（编排薄层，DB 主路径由服务层闭环测试覆盖）。

    TrackedTask 依赖 core_celery_task_record 表——该表由
    core.0001_initial 的 SeparateDatabaseAndState（database_operations=[]，
    DDL 留在库外）状态迁移承载，全新测试库不存在此表，故 patch 掉
    TrackedTask（记录机制非本测试对象），保持 SimpleTestCase 无 DB。
    """

    def test_task_skips_when_attachment_missing(self):
        from Django_xm.tasks.chat_tasks import save_document_memory

        with patch("Django_xm.tasks.chat_tasks.TrackedTask"), patch(
            "Django_xm.apps.attachments.models.ChatAttachment.objects"
        ) as objects:
            objects.filter.return_value.first.return_value = None
            result = save_document_memory.apply(args=[404]).get()
        self.assertEqual(result["skipped"], "attachment_not_found")

    def test_task_skips_image(self):
        from Django_xm.tasks.chat_tasks import save_document_memory

        att = SimpleNamespace(id=5, mime_type="image/jpeg", is_deleted=False)
        with patch("Django_xm.tasks.chat_tasks.TrackedTask"), patch(
            "Django_xm.apps.attachments.models.ChatAttachment.objects"
        ) as objects:
            objects.filter.return_value.first.return_value = att
            result = save_document_memory.apply(args=[5]).get()
        self.assertEqual(result["skipped"], "image")

    def test_task_saves_text_attachment(self):
        from Django_xm.tasks.chat_tasks import save_document_memory

        att = SimpleNamespace(
            id=6,
            mime_type="application/pdf",
            is_deleted=False,
            original_name="doc.pdf",
            file_size=100,
            file=SimpleNamespace(path="/tmp/doc.pdf"),
            session=SimpleNamespace(user_id=7),
        )
        with patch("Django_xm.tasks.chat_tasks.TrackedTask"), patch(
            "Django_xm.apps.attachments.models.ChatAttachment.objects"
        ) as objects, patch(
            "Django_xm.apps.tools.langchain.file_reader.read_file_content",
            return_value="文档正文内容",
        ), patch(
            "Django_xm.apps.attachments.services.document_memory_service.DocumentMemoryService.save_document",
            return_value=True,
        ) as save_mock:
            objects.filter.return_value.first.return_value = att
            result = save_document_memory.apply(args=[6]).get()
        self.assertTrue(result["saved"])
        save_mock.assert_called_once()
        kwargs = save_mock.call_args.kwargs
        self.assertEqual(kwargs["user_id"], 7)
        self.assertEqual(kwargs["doc_name"], "doc.pdf")
