"""ContextManager 注入分区来源测试（P0-1 前缀缓存裁决）。

覆盖：
- get_document_context：文档上下文作为独立易变分区来源（供 builder 链
  volatile_text / 注入文本组装方调用），降级语义（无 user / 提供者未注册 /
  provider 异常 / 空内容 → 空串不抛异常）；
- get_injection_context：只承载 KG/跨会话分区（职责边界），不含文档上下文
  （文档由 get_document_context 独立获取，组装方决定并入位置，避免源级重复）。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.context_manager.tests.test_injection_context_sources --noinput
"""

from unittest import mock

from django.test import SimpleTestCase

from Django_xm.apps.context_manager.services.manager import (
    ContextManagementConfig,
    ContextManager,
)

# manager.py 内 from-import 绑定，patch 打在 manager 命名空间
_DOC_PROVIDER = "Django_xm.apps.context_manager.services.manager.get_document_context_provider"

_DOC_TAILNOTE = "请在回答时优先参考用户已上传的文档内容。"


def _make_manager(user_id: int | None = 7) -> ContextManager:
    """构造关闭 KG/跨会话/压缩的最小 ContextManager（聚焦文档分区语义）。"""
    config = ContextManagementConfig(
        compression_enabled=False,
        knowledge_graph_enabled=False,
        cross_session_enabled=False,
    )
    return ContextManager(user_id=user_id, config=config)


class GetDocumentContextTests(SimpleTestCase):
    """get_document_context：易变分区来源 + 降级语义。"""

    def test_no_user_returns_empty(self):
        """无 user_id → 空串，provider 不被调用。"""
        manager = _make_manager(user_id=None)
        with mock.patch(_DOC_PROVIDER) as mock_provider:
            self.assertEqual(manager.get_document_context(), "")
        mock_provider.assert_not_called()

    def test_provider_missing_returns_empty(self):
        """提供者未注册（attachments apps.ready() 未接线/未启动）→ 空串。"""
        manager = _make_manager()
        with mock.patch(_DOC_PROVIDER, return_value=None):
            self.assertEqual(manager.get_document_context(), "")

    def test_provider_content_returns_with_tailnote(self):
        """provider 返回内容 → 拼接参考提示尾注（与原 build_prompt_context 等价）。"""
        manager = _make_manager()
        with mock.patch(_DOC_PROVIDER, return_value=lambda user_id, store: "用户最近上传了 report.pdf"):
            result = manager.get_document_context()
        self.assertEqual(result, f"用户最近上传了 report.pdf\n{_DOC_TAILNOTE}")

    def test_provider_empty_content_returns_empty(self):
        """provider 返回空内容 → 空串（不输出孤立尾注）。"""
        manager = _make_manager()
        with mock.patch(_DOC_PROVIDER, return_value=lambda user_id, store: ""):
            self.assertEqual(manager.get_document_context(), "")

    def test_provider_error_degrades_to_empty(self):
        """provider 异常 → 空串（吞没降级，不阻塞注入组装主链路）。"""
        manager = _make_manager()
        with mock.patch(_DOC_PROVIDER, side_effect=RuntimeError("store unavailable")):
            self.assertEqual(manager.get_document_context(), "")


class GetInjectionContextScopeTests(SimpleTestCase):
    """get_injection_context 职责边界：只含 KG/跨会话，不含文档分区。"""

    def test_kg_context_present_document_absent(self):
        """KG 命中时注入文本只含 KG 段（文档经 get_document_context 独立获取）。"""
        manager = _make_manager()
        manager.config.knowledge_graph_enabled = True
        manager._knowledge_graph = mock.MagicMock()
        manager._knowledge_graph.get_context_for_query.return_value = "【知识图谱上下文】\n- langchain"
        with mock.patch(_DOC_PROVIDER, return_value=lambda user_id, store: "不应出现"):
            result = manager.get_injection_context("查询问题")

        self.assertIn("【知识图谱上下文】", result)
        self.assertNotIn("不应出现", result)
        self.assertNotIn(_DOC_TAILNOTE, result)

    def test_no_sources_returns_empty(self):
        """KG 未启用/未命中且跨会话关闭 → 空串（组装方按无记忆处理）。"""
        manager = _make_manager()
        self.assertEqual(manager.get_injection_context("查询问题"), "")
