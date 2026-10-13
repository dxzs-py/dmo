"""finalize_chat_message_cancelled（研究取消的聊天侧收尾）单元测试。

覆盖「深研详情页取消 → 聊天模块不同步」缺陷修复的收尾单源：
- ChatMessage 终止语义落库：is_streaming=False + content 为空补「深度研究已取消。」
  （非空保留已抢救过程正文——取消=部分成果保留展示）+ versions[0].content 同步
- MESSAGE_UPDATED 广播（session + task 双频道，携带 research_task_id）
- STREAM_COMPLETED(stopped=true) 广播：前端据此将聊天消息/任务信息置为取消终态
  （区别于失败终态，不误显"深度研究执行失败"）
- task_status_changed（user 频道）status="cancelled"
- 静默降级：ResearchTask / ChatMessage 不存在时不广播不抛错

隔离策略：对齐 test_persist_research_progress 既有桩模式（mock ORM + apps.get_model +
publish_event_sync），桩实例仅使用真实模型存在字段（content/is_streaming/versions/id），
不塞幻影字段（2026-10-06 writeback 幻影字段教训）。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.research.tests.test_finalize_chat_message_cancelled --noinput
"""

import os
import unittest
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from Django_xm.apps.research.models import ResearchTask
from Django_xm.apps.research.services.writeback import finalize_chat_message_cancelled

_WB = "Django_xm.apps.research.services.writeback"
# writeback 内 ResearchTask 为函数内 import：patch 真实模型类属性（类导入不触 DB）
_RESEARCH_MODELS = "Django_xm.apps.research.models.ResearchTask"


def _make_research_task(session_id="s1", user_id=7):
    task = mock.Mock()
    task.session_id = session_id
    task.created_by_id = user_id
    return task


def _make_message(content="", is_streaming=True, message_id="m1", versions=None):
    """构造桩 ChatMessage：字段严格对齐真实模型（apps/chat/models.py ChatMessage）。"""
    msg = mock.Mock()
    msg.id = message_id
    msg.content = content
    msg.is_streaming = is_streaming
    msg.versions = versions if versions is not None else []
    return msg


class FinalizeChatMessageCancelledTests(unittest.TestCase):
    """finalize_chat_message_cancelled 收尾单源行为。"""

    def _patch_deps(self, research_task, msg):
        """打桩 ResearchTask.objects.get / apps.get_model(ChatMessage) / publish_event_sync。

        Returns:
            mock: publish_event_sync（广播断言用）
        """
        msg_cls = mock.Mock()
        msg_cls.objects.filter.return_value.first.return_value = msg
        only_qs = mock.Mock()
        only_qs.get.return_value = msg
        msg_cls.objects.only.return_value = only_qs
        chat_models = mock.Mock()
        chat_models.get_model.return_value = msg_cls

        publish = mock.patch(f"{_WB}.publish_event_sync").start()
        patcher = mock.patch(f"{_RESEARCH_MODELS}.objects")
        research_objects = patcher.start()
        research_objects.get.return_value = research_task
        mock.patch("django.apps.apps", chat_models).start()
        self.addCleanup(mock.patch.stopall)
        return publish

    def test_writes_stop_semantics_and_broadcasts_cancelled(self):
        """content 为空：补「深度研究已取消。」+ is_streaming=False + 双事件广播（stopped=true）。"""
        research_task = _make_research_task(session_id="s1", user_id=7)
        msg = _make_message(content="", is_streaming=True)
        publish = self._patch_deps(research_task, msg)

        result = finalize_chat_message_cancelled("t1")

        self.assertEqual(result, "m1")
        # 终止语义落库
        self.assertFalse(msg.is_streaming)
        self.assertEqual(msg.content, "深度研究已取消。")
        msg.save.assert_called_once()
        save_kwargs = msg.save.call_args.kwargs
        self.assertIn("content", save_kwargs["update_fields"])
        self.assertIn("is_streaming", save_kwargs["update_fields"])
        # MESSAGE_UPDATED（session + task 双频道）+ STREAM_COMPLETED
        broadcast_types = [c.args[0] for c in publish.call_args_list]
        self.assertIn("message_updated", broadcast_types)
        self.assertIn("stream_completed", broadcast_types)
        # 取消标记穿透到前端（区别于失败终态）
        completed_call = next(c for c in publish.call_args_list if c.args[0] == "stream_completed")
        self.assertTrue(completed_call.args[1]["stopped"])
        self.assertFalse(completed_call.args[1]["success"])
        # user 频道任务列表刷新：status=cancelled（非 completed/failed）
        status_call = next(c for c in publish.call_args_list if c.args[0] == "task_status_changed")
        self.assertEqual(status_call.args[1]["status"], "cancelled")

    def test_preserves_rescued_content(self):
        """content 非空（取消前已抢救过程正文）：不覆盖，仅置 is_streaming=False。"""
        research_task = _make_research_task()
        msg = _make_message(content="已抢救的过程正文", is_streaming=True)
        publish = self._patch_deps(research_task, msg)

        finalize_chat_message_cancelled("t1")

        self.assertEqual(msg.content, "已抢救的过程正文")
        self.assertFalse(msg.is_streaming)
        msg.save.assert_called_once()
        self.assertNotIn("content", msg.save.call_args.kwargs["update_fields"])
        completed_call = next(c for c in publish.call_args_list if c.args[0] == "stream_completed")
        self.assertTrue(completed_call.args[1]["stopped"])

    def test_syncs_versions_when_content_backfilled(self):
        """content 补写时同步 versions[0].content（对齐 writeback_to_chat_message B5 语义）。"""
        research_task = _make_research_task()
        msg = _make_message(content="", is_streaming=True, versions=[{"content": ""}])
        self._patch_deps(research_task, msg)

        finalize_chat_message_cancelled("t1")

        self.assertEqual(msg.versions[0]["content"], "深度研究已取消。")
        self.assertIn("versions", msg.save.call_args.kwargs["update_fields"])

    def test_skips_save_when_already_final(self):
        """已收尾（is_streaming=False 且 content 非空）：幂等，不重复 save，仍广播。"""
        research_task = _make_research_task()
        msg = _make_message(content="过程正文", is_streaming=False)
        publish = self._patch_deps(research_task, msg)

        finalize_chat_message_cancelled("t1")

        msg.save.assert_not_called()
        completed_call = next(c for c in publish.call_args_list if c.args[0] == "stream_completed")
        self.assertTrue(completed_call.args[1]["stopped"])

    def test_silent_without_chat_message(self):
        """无关联 ChatMessage（独立深研场景）：静默返回 None，不广播。"""
        research_task = _make_research_task()
        msg_cls = mock.Mock()
        msg_cls.objects.filter.return_value.first.return_value = None
        chat_models = mock.Mock()
        chat_models.get_model.return_value = msg_cls

        publish = mock.patch(f"{_WB}.publish_event_sync").start()
        research_objects = mock.patch(f"{_RESEARCH_MODELS}.objects").start()
        research_objects.get.return_value = research_task
        mock.patch("django.apps.apps", chat_models).start()
        self.addCleanup(mock.patch.stopall)

        result = finalize_chat_message_cancelled("t1")

        self.assertIsNone(result)
        publish.assert_not_called()

    def test_silent_without_research_task(self):
        """ResearchTask 不存在：静默返回 None，不广播（防御幂等）。"""
        publish = mock.patch(f"{_WB}.publish_event_sync").start()
        research_objects = mock.patch(f"{_RESEARCH_MODELS}.objects").start()
        research_objects.get.side_effect = ResearchTask.DoesNotExist
        chat_models = mock.Mock()
        mock.patch("django.apps.apps", chat_models).start()
        self.addCleanup(mock.patch.stopall)

        result = finalize_chat_message_cancelled("t-missing")

        self.assertIsNone(result)
        publish.assert_not_called()


if __name__ == "__main__":
    unittest.main()
