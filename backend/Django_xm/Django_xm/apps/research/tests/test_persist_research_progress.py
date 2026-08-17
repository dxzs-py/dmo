"""persist_research_progress（挂起落库）单元测试。

覆盖「服务重启恢复场景子代理工具条目丢失」修复的前半环：
- ResearchTask.tool_calls 增量合并（字段级演进，重复调用幂等，不覆盖历史）
- ResearchTask.subagent_contents 落库（非空且不同才写）
- 关联 ChatMessage.tool_calls / subagent_contents 同步
- MESSAGE_UPDATED 广播（session + task 双频道）

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python -m unittest Django_xm.apps.research.tests.test_persist_research_progress
"""

import os
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from Django_xm.apps.research.services.writeback import persist_research_progress

_WB = "Django_xm.apps.research.services.writeback"


def _make_task(tool_calls=None, subagent_contents=None, chat_message_id=10):
    task = mock.Mock()
    task.task_id = "t1"
    task.tool_calls = list(tool_calls or [])
    task.subagent_contents = dict(subagent_contents or {})
    task.chat_message_id = chat_message_id
    return task


class PersistResearchProgressTests(unittest.TestCase):
    """persist_research_progress 合并落库逻辑。"""

    def _patch_deps(self, task, msg=None):
        """打桩 ResearchTask.objects / apps.get_model / publish_event_sync。

        Returns:
            dict: publish_event_sync 的 mock（用于广播断言）
        """
        qs = mock.Mock()
        qs.filter.return_value = qs
        qs.first.return_value = task

        msg_cls = mock.Mock()
        msg_cls.objects.filter.return_value.first.return_value = msg
        msg_cls.objects.only.return_value.get.return_value = msg
        chat_models = mock.Mock()
        chat_models.get_model.return_value = msg_cls

        new_mocks = mock.patch.multiple(_WB, publish_event_sync=mock.DEFAULT).start()
        mock.patch("Django_xm.apps.research.models.ResearchTask.objects", qs).start()
        mock.patch("django.apps.apps", chat_models).start()
        self.addCleanup(mock.patch.stopall)
        return new_mocks

    def test_merges_tool_calls_into_research_task(self):
        """子代理工具条目合并进 ResearchTask.tool_calls，既有条目演进而非覆盖。"""
        task = _make_task(
            tool_calls=[
                {
                    "id": "c1",
                    "name": "shell_exec",
                    "status": "pending",
                    "parameters": {"command": "ls"},
                    "approval": {"state": "approved"},
                }
            ]
        )
        entries = {
            "c1": {
                "id": "c1",
                "name": "shell_exec",
                "status": "completed",
                "parameters": {"command": "ls"},
                "result": {"output": "ok"},
                "subagent_thread_id": "sa1",
                "position": 3,
            }
        }
        self._patch_deps(task, msg=None)
        persist_research_progress("t1", subagent_tool_entries=entries)

        self.assertEqual(len(task.tool_calls), 1)
        merged = task.tool_calls[0]
        self.assertEqual(merged["status"], "completed")
        self.assertEqual(merged["result"], {"output": "ok"})
        # 演进保留既有 approval 且补全图层字段（审批重建路径丢失的字段经此补齐）
        self.assertEqual(merged["approval"], {"state": "approved"})
        self.assertEqual(merged["subagent_thread_id"], "sa1")
        self.assertEqual(merged["position"], 3)
        task.save.assert_called_once()

    def test_merge_is_idempotent(self):
        """重复调用幂等：二次落库无实际变更（merged == existing → 不触发 save）。"""
        task = _make_task()
        entries = {"c1": {"id": "c1", "name": "shell_exec", "subagent_thread_id": "sa1", "position": 0}}
        self._patch_deps(task, msg=None)
        persist_research_progress("t1", subagent_tool_entries=entries)
        task.save.assert_called_once()

        task.save.reset_mock()
        persist_research_progress("t1", subagent_tool_entries=entries)
        task.save.assert_not_called()

    def test_persists_subagent_contents(self):
        """subagent_contents 非空且与已有不同 → 落库。"""
        task = _make_task()
        self._patch_deps(task, msg=None)
        persist_research_progress("t1", subagent_contents={"sa1": {"content": "a"}})
        self.assertEqual(task.subagent_contents, {"sa1": {"content": "a"}})
        task.save.assert_called_once()

    def test_skips_when_no_data(self):
        """无子代理数据 → 直接返回，不触发任何保存。"""
        task = _make_task()
        self._patch_deps(task, msg=None)
        persist_research_progress("t1")
        task.save.assert_not_called()

    def test_syncs_chat_message_and_broadcasts(self):
        """关联 ChatMessage.tool_calls / subagent_contents 同步并广播 MESSAGE_UPDATED。"""
        task = _make_task(chat_message_id=10)
        msg = mock.Mock()
        msg.id = 10
        msg.tool_calls = []
        msg.subagent_contents = {}
        msg.session = SimpleNamespace(session_id="s1")
        entries = {"c1": {"id": "c1", "name": "shell_exec", "subagent_thread_id": "sa1", "position": 3}}
        new_mocks = self._patch_deps(task, msg=msg)
        persist_research_progress(
            "t1",
            subagent_contents={"sa1": {"content": "a"}},
            subagent_tool_entries=entries,
        )

        self.assertEqual(len(msg.tool_calls), 1)
        self.assertEqual(msg.tool_calls[0]["subagent_thread_id"], "sa1")
        self.assertEqual(msg.subagent_contents, {"sa1": {"content": "a"}})
        msg.save.assert_called_once()

        # 广播 MESSAGE_UPDATED 到 session + task 双频道
        publish = new_mocks["publish_event_sync"]
        publish.assert_called_once()
        event_type = publish.call_args.args[0]
        self.assertEqual(str(event_type), "message_updated")
        self.assertEqual(publish.call_args.kwargs["session_id"], "s1")
        self.assertEqual(publish.call_args.kwargs["task_id"], "t1")
        payload = publish.call_args.args[1]
        self.assertEqual(payload["message_id"], "10")
        self.assertEqual(payload["research_task_id"], "t1")


if __name__ == "__main__":
    unittest.main()
