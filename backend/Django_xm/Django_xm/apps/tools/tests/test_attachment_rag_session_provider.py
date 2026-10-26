"""attachment_rag 会话校验 provider 倒置 + 类型化错误文案测试（阶段 7 票② / E5-10）。

倒置（豁免清零）：tools 不得直连 chat.models（.importlinter
research-chat-via-facade-only 传递性豁免台账），ChatSession 存在性校验实现
归位 chat/services/cross_app.is_chat_session_exists，经运行期 provider 注入
（组合根 chat AppConfig.ready()）。本文件锁定消费层契约：
- 未注册 / 校验 False / 校验抛异常 → "未找到会话 (session_id=...)"（含会话 ID，
  沿用 C-1 身份测试锁定的文案契约；不重试检索）；
- 校验 True → 进入附件查询（无 DB 测试环境命中查询异常 → 类型化错误文案）。

E5-10（裸 str(e) 不进 LLM/ToolCallCard）：异常文案类型化——固定分类文案 +
异常类型名（有限细节），异常消息文本只进日志（logger.exception 留全栈）。

ChatConfig.ready() 在 django.setup() 时已执行，真实接线态下 provider 非空；
用例内经 setter 换桩并在 cleanup 还原，不污染其他模块。

运行（backend/Django_xm 目录）：
D:\\Anaconda_envs\\envs\\langchain_xm\\python.exe manage.py test \\
    Django_xm.apps.tools.tests.test_attachment_rag_session_provider \\
    --settings=Django_xm.settings.test --noinput
"""

import os
import uuid
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.test import SimpleTestCase

from Django_xm.apps.attachments import models as attachments_models
from Django_xm.apps.tools.langchain import attachment_rag as attachment_rag_module
from Django_xm.apps.tools.langchain.attachment_rag import attachment_search


def _unique_session(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


class ChatSessionProviderTests(SimpleTestCase):
    """会话存在性校验 provider 消费契约（含组合根接线态断言）。"""

    def setUp(self):
        self._original = attachment_rag_module.get_chat_session_exists_provider()
        self.addCleanup(
            lambda: attachment_rag_module.set_chat_session_exists_provider(self._original)
        )

    def test_provider_wired_by_appconfig_ready(self):
        """组合根接线态：django.setup() 后 provider 已注册（chat AppConfig.ready）。"""
        self.assertIsNotNone(
            attachment_rag_module.get_chat_session_exists_provider(),
            "ChatConfig.ready() 应注册会话校验 provider（阶段 7 票②组合根接线）",
        )

    def test_unregistered_provider_reports_session_missing(self):
        """未注册：按既有降级路径返回未找到文案（含 session_id），跳过检索。"""
        attachment_rag_module.set_chat_session_exists_provider(None)
        session = _unique_session("sess-unreg")

        result = attachment_search.invoke(
            "检索测试", config={"configurable": {"thread_id": session}}
        )

        self.assertIn("未找到会话", result)
        self.assertIn(session, result)

    def test_resolver_false_reports_session_missing(self):
        """校验 False（会话不存在）：返回未找到文案，不进入附件查询。"""
        attachment_rag_module.set_chat_session_exists_provider(lambda _sid: False)
        session = _unique_session("sess-miss")

        result = attachment_search.invoke(
            "检索测试", config={"configurable": {"thread_id": session}}
        )

        self.assertIn("未找到会话", result)
        self.assertIn(session, result)

    def test_resolver_failure_reports_session_missing(self):
        """校验抛异常（DB 不可用等）：降级为未找到文案，异常不外溢。"""

        def _boom(_sid: str) -> bool:
            raise RuntimeError("db down")

        attachment_rag_module.set_chat_session_exists_provider(_boom)
        session = _unique_session("sess-err")

        result = attachment_search.invoke(
            "检索测试", config={"configurable": {"thread_id": session}}
        )

        self.assertIn("未找到会话", result)
        self.assertIn(session, result)
        self.assertNotIn("db down", result)

    def test_resolver_true_enters_attachment_query_with_typed_error(self):
        """校验 True：进入附件查询；查询异常返回类型化文案（E5-10，无异常消息文本）。"""
        attachment_rag_module.set_chat_session_exists_provider(lambda _sid: True)
        session = _unique_session("sess-hit")

        # SimpleTestCase 无 DB：附件查询抛 AssertionError（非 Exception 消息外溢路径）
        with mock.patch.object(
            attachment_rag_module.logger, "exception"
        ) as log_mock:
            result = attachment_search.invoke(
                "检索测试", config={"configurable": {"thread_id": session}}
            )

        self.assertRegex(result, r"错误：查询会话附件列表失败（\w+），请稍后重试。")
        # 有限细节=类型名（\w+ 占位）；异常消息文本不外溢
        self.assertNotIn("not allowed in this test", result)
        log_mock.assert_called_once()  # 全栈留档（logger.exception）


class TypedErrorCopyTests(SimpleTestCase):
    """E5-10：错误文案类型化契约（固定分类文案 + 异常类型名，无消息文本）。"""

    def setUp(self):
        self._original = attachment_rag_module.get_chat_session_exists_provider()
        self.addCleanup(
            lambda: attachment_rag_module.set_chat_session_exists_provider(self._original)
        )

    def test_error_copy_does_not_leak_exception_message(self):
        """附件查询异常消息含敏感样文本 → 不出现在工具输出。"""
        attachment_rag_module.set_chat_session_exists_provider(lambda _sid: True)
        session = _unique_session("sess-leak")

        fake_model = mock.MagicMock()
        fake_model.objects.filter.side_effect = RuntimeError("SECRET_SQL_FRAGMENT")

        with mock.patch.object(attachments_models, "ChatAttachment", fake_model):
            result = attachment_search.invoke(
                "检索测试", config={"configurable": {"thread_id": session}}
            )

        self.assertIn("错误：查询会话附件列表失败", result)
        self.assertIn("RuntimeError", result)
        self.assertNotIn("SECRET_SQL_FRAGMENT", result)
