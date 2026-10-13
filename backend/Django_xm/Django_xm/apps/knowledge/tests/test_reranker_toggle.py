"""B8/D1：reranker 配置开关接线测试。

修复前缺口：
- ``build_user_retriever``（precise/comprehensive 两分支）均不传 use_reranker，
  ``create_retriever`` 的 use_reranker 形参默认 False → 线上检索器重排序整体不生效，
  仅 rag_retrieval 降级路径与 evaluate_rag 消融策略在用。

修复：新增 ``settings.retriever_use_reranker``（默认 True，与 retriever_use_multi_query
同族命名），build_user_retriever 两分支读取并透传；chat 工具链路
（get_rag_retriever → build_user_retriever）接线后自动生效。

测试证明方式：
patch 模块内 load_user_vector_store / create_retriever（comprehensive 分支再 patch
create_multi_query_retriever 隔离 LLM 依赖），断言 create_retriever 收到的
use_reranker 与开关值一致；另以 model_fields 断言配置默认值为 True。

运行：
    python manage.py test Django_xm.apps.knowledge.tests.test_reranker_toggle
"""

from unittest import mock

from django.test import SimpleTestCase

from Django_xm.apps.ai_engine.config import Settings, settings
from Django_xm.apps.knowledge.services import retrieval_service

_FAKE_STORE = object()


class RerankerTogglePassthroughTests(SimpleTestCase):
    def setUp(self):
        self._original = settings.retriever_use_reranker
        self.addCleanup(setattr, settings, "retriever_use_reranker", self._original)
        # comprehensive 用例关闭 MultiQuery 叠加：避免测试触发 get_helper_model
        # （DB 回退 + LLM 实例构建副作用，与本测试断言无关）
        self._original_mq = settings.retriever_use_multi_query
        self.addCleanup(setattr, settings, "retriever_use_multi_query", self._original_mq)
        settings.retriever_use_multi_query = False

    def _run_build(self, mode):
        """patch 依赖后运行 build_user_retriever，返回 create_retriever 的调用 kwargs"""
        with mock.patch.object(
            retrieval_service, "load_user_vector_store", return_value=(_FAKE_STORE, {}, None, lambda: _FAKE_STORE)
        ), mock.patch.object(
            retrieval_service, "create_retriever", return_value=object()
        ) as fake_create, mock.patch.object(
            retrieval_service, "create_multi_query_retriever", return_value=object()
        ):
            retrieval_service.build_user_retriever(1, "kb-a", mode=mode)
        self.assertEqual(fake_create.call_count, 1)
        return fake_create.call_args

    def test_precise_branch_passes_toggle_true(self):
        settings.retriever_use_reranker = True
        call = self._run_build("precise")
        self.assertIs(call.kwargs["use_reranker"], True)

    def test_precise_branch_passes_toggle_false(self):
        settings.retriever_use_reranker = False
        call = self._run_build("precise")
        self.assertIs(call.kwargs["use_reranker"], False)

    def test_comprehensive_branch_passes_toggle_true(self):
        settings.retriever_use_reranker = True
        call = self._run_build("comprehensive")
        self.assertIs(call.kwargs["use_reranker"], True)

    def test_comprehensive_branch_passes_toggle_false(self):
        settings.retriever_use_reranker = False
        call = self._run_build("comprehensive")
        self.assertIs(call.kwargs["use_reranker"], False)

    def test_setting_defaults_to_true(self):
        """D1 决策：配置开关默认开启"""
        self.assertIs(Settings.model_fields["retriever_use_reranker"].default, True)
