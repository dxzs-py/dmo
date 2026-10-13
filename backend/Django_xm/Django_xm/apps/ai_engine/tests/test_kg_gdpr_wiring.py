"""KG GDPR 出口接线测试（fix-review-round3 Task 4.7）。

覆盖：delete_user_all_data（用户注销清理链终点：Celery cleanup_checkpoints →
delete_user_all_data）接线 ContextKnowledgeGraph.clear_graph——
- Store 内 (user_id, "knowledge_graph") 命名空间由 delete_thread_store_data
  与 clear_graph 双重覆盖（幂等无害）；
- 4.6 引入的进程级图缓存经 clear_graph 内的 clear() 失效（直接 store.delete
  不触发缓存失效，TTL 内可能读到已删除图数据）；
- 跨会话记忆（(user_id, "session_contexts")）随 delete_thread_store_data 的
  命名空间清单清除（checkpointer_factory.delete_thread_store_data:688）。

本测试置于 ai_engine 侧：context_manager 禁止 import ai_engine（环 1 契约
.importlinter:context-manager-not-dependent-on-ai-engine），测试放置边同样
遵守。

运行：
    python manage.py test Django_xm.apps.ai_engine.tests.test_kg_gdpr_wiring
"""

import asyncio
import unittest
from contextlib import ExitStack
from unittest import mock


class DeleteUserAllDataClearGraphWiringTests(unittest.TestCase):
    def test_delete_user_all_data_invokes_clear_graph(self):
        from Django_xm.apps.ai_engine.services import checkpointer_factory
        from Django_xm.apps.context_manager.services.knowledge_graph import ContextKnowledgeGraph

        fake_model = mock.Mock()
        fake_model.objects.filter.return_value.values_list.return_value = []

        with ExitStack() as stack:
            stack.enter_context(mock.patch("django.apps.apps.get_model", return_value=fake_model))
            stack.enter_context(
                mock.patch.object(
                    checkpointer_factory,
                    "delete_thread_checkpoints",
                    new=mock.AsyncMock(return_value=True),
                )
            )
            mock_store_delete = stack.enter_context(
                mock.patch.object(
                    checkpointer_factory,
                    "delete_thread_store_data",
                    new=mock.AsyncMock(return_value=True),
                )
            )
            mock_clear = stack.enter_context(
                mock.patch.object(ContextKnowledgeGraph, "clear_graph", return_value=True)
            )

            result = asyncio.run(checkpointer_factory.delete_user_all_data(424242))

        self.assertTrue(result)
        mock_store_delete.assert_awaited_once_with(424242)
        mock_clear.assert_called_once_with(424242)

    def test_clear_graph_failure_does_not_break_cleanup(self):
        """clear_graph 异常吞没（warning 留痕），不阻塞用户注销清理主链路。"""
        from Django_xm.apps.ai_engine.services import checkpointer_factory
        from Django_xm.apps.context_manager.services.knowledge_graph import ContextKnowledgeGraph

        fake_model = mock.Mock()
        fake_model.objects.filter.return_value.values_list.return_value = []

        with ExitStack() as stack:
            stack.enter_context(mock.patch("django.apps.apps.get_model", return_value=fake_model))
            stack.enter_context(
                mock.patch.object(
                    checkpointer_factory,
                    "delete_thread_checkpoints",
                    new=mock.AsyncMock(return_value=True),
                )
            )
            stack.enter_context(
                mock.patch.object(
                    checkpointer_factory,
                    "delete_thread_store_data",
                    new=mock.AsyncMock(return_value=True),
                )
            )
            stack.enter_context(
                mock.patch.object(
                    ContextKnowledgeGraph,
                    "clear_graph",
                    side_effect=RuntimeError("store down"),
                )
            )

            result = asyncio.run(checkpointer_factory.delete_user_all_data(424242))

        self.assertTrue(result)


if __name__ == "__main__":
    unittest.main()
