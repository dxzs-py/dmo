"""load_research_context 摘要旁路接线测试（ARCH-07 统一引擎，任务 4.1 迁移）。

自 ``apps.context_manager.tests.test_engine_unification.LoadResearchContextBypassTests``
原样迁入（spec eliminate-priority-debts 4.1）：被测对象是本 app 的
ContextService.load_research_context，测试放置应与被测 app 一致
（原放置使 context_manager tests → chat.context_service → ai_engine 构成
跨 app 传递依赖，违反 context-manager-not-dependent-on-ai-engine 契约）。

锁定行为：超长研究文件走统一引擎 ContextCompressionEngine.summarize_long_text
摘要；短内容直接返回原文不触发引擎。

运行（backend/Django_xm 目录）：
    python manage.py test Django_xm.apps.chat.tests.test_load_research_context
"""

import os
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from Django_xm.apps.context_manager.services.compression import ContextCompressionEngine


class LoadResearchContextBypassTests(unittest.TestCase):
    """load_research_context 摘要旁路接线：超长内容调用统一引擎摘要。"""

    def test_long_content_uses_unified_engine_summary(self) -> None:
        from Django_xm.apps.chat.services.context_service import ContextService

        fake_file = mock.MagicMock()
        fake_file.path = Path("research/report.md")
        fake_file.base_dir = Path(".")
        fake_manager = mock.MagicMock()
        fake_manager.list_task_files.return_value = [fake_file]
        fake_manager.read_file_content.return_value = "研究内容" * 4000  # 远超 8000 token

        task = {"query": "研究问题", "final_report": ""}
        with (
            mock.patch.object(ContextService, "CHECKPOINTER_ENABLED", False),
            mock.patch(
                "Django_xm.apps.research.services.cross_app.get_research_task_for_context",
                return_value=task,
            ),
            mock.patch("Django_xm.apps.core.services.file_manager.get_file_manager", return_value=fake_manager),
            mock.patch.object(
                ContextCompressionEngine,
                "summarize_long_text",
                return_value="引擎摘要结果",
            ) as summarize_mock,
        ):
            service = ContextService(user_id=None, thread_id=None)
            result = service.load_research_context("task-1")

        summarize_mock.assert_called_once()
        self.assertEqual(result, "引擎摘要结果")

    def test_short_content_returns_raw_without_engine(self) -> None:
        from Django_xm.apps.chat.services.context_service import ContextService

        fake_file = mock.MagicMock()
        fake_file.path = Path("research/report.md")
        fake_file.base_dir = Path(".")
        fake_manager = mock.MagicMock()
        fake_manager.list_task_files.return_value = [fake_file]
        fake_manager.read_file_content.return_value = "短研究内容"

        task = {"query": "研究问题", "final_report": ""}
        with (
            mock.patch.object(ContextService, "CHECKPOINTER_ENABLED", False),
            mock.patch(
                "Django_xm.apps.research.services.cross_app.get_research_task_for_context",
                return_value=task,
            ),
            mock.patch("Django_xm.apps.core.services.file_manager.get_file_manager", return_value=fake_manager),
            mock.patch.object(
                ContextCompressionEngine, "summarize_long_text", return_value="不应被调用"
            ) as summarize_mock,
        ):
            service = ContextService(user_id=None, thread_id=None)
            result = service.load_research_context("task-1")

        summarize_mock.assert_not_called()
        # 原文直接返回（带 "### 相对路径" 头）
        self.assertTrue(result.endswith("短研究内容"))
        self.assertIn("### research", result)


if __name__ == "__main__":
    unittest.main()
