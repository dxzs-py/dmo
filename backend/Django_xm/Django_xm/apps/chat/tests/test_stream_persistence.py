"""stream_persistence 工具调用合并策略单元测试。

覆盖 P3-R2「tool_calls 状态演进合并」：
- existing pending + new completed → 演进为 completed（approval 保留）
- existing completed + new pending → 保持 completed（终态不回退）
- existing 有 approval 字段 → 合并后 approval 原样保留
- 无匹配的 new 条目 → 追加
- ``_persist_to_db_sync`` 的变更检测：字段级演进（长度不变）必须触发 save

实现说明：
- ``_merge_tool_calls_incremental`` 为纯函数（模块顶层仅依赖 logging/typing），
  通过 ``importlib`` 按文件路径直接加载被测模块，规避
  ``services/__init__.py`` 的重型导入链（agent_service 等会触发
  langchain / Django models 导入），使单测无需 Django settings 与数据库。
- ``_persist_to_db_sync`` 的 ORM 懒导入通过 ``sys.modules`` 注入替身模块
  （ChatSession/ChatMessage/timezone），避免依赖 Django 配置。
"""

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

# 被测模块绝对路径：本文件位于 Django_xm/apps/chat/tests/，
# parents[1] 即 chat app 目录，被测模块位于 chat/services/ 下。
_STREAM_PERSISTENCE_PATH = Path(__file__).resolve().parents[1] / "services" / "stream_persistence.py"


def _load_stream_persistence_module():
    """按文件路径加载被测模块（绕过 services/__init__.py 的重型导入链）。"""
    spec = importlib.util.spec_from_file_location(
        "stream_persistence_under_test",
        _STREAM_PERSISTENCE_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


MODULE = _load_stream_persistence_module()
_merge_tool_calls_incremental = MODULE._merge_tool_calls_incremental
_persist_to_db_sync = MODULE._persist_to_db_sync


class MergeContentOverlapTests(unittest.TestCase):
    """sse_generator._merge_content_with_overlap 尾部重叠去重测试。

    恢复轮 checkpoint 重生成场景：挂起前 content 尾部与恢复轮首个 chunk 前缀
    重叠时，直接 ``+=`` 会产生「三个三个」类重复，需去重拼接。
    """

    @classmethod
    def setUpClass(cls):
        sse_path = Path(__file__).resolve().parents[1] / "services" / "sse_generator.py"
        spec = importlib.util.spec_from_file_location("sse_generator_under_test", sse_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        assert spec.loader is not None
        spec.loader.exec_module(module)
        # staticmethod：避免经实例访问时被绑定为方法（多传 self）
        cls.merge = staticmethod(module._merge_content_with_overlap)

    def test_normal_append_no_overlap(self):
        self.assertEqual(self.merge("ABC", "DEF"), "ABCDEF")

    def test_tail_overlap_dedup(self):
        self.assertEqual(self.merge("汇总结果。三个", "三个子代理全部完成"), "汇总结果。三个子代理全部完成")

    def test_partial_overlap_dedup(self):
        self.assertEqual(self.merge("AABBCC", "BCCDEF"), "AABBCCDEF")

    def test_full_overlap_keeps_existing(self):
        self.assertEqual(self.merge("AB", "AB"), "AB")

    def test_empty_chunk_idempotent(self):
        self.assertEqual(self.merge("AB", ""), "AB")

    def test_empty_existing_returns_chunk(self):
        self.assertEqual(self.merge("", "CD"), "CD")


class MergeToolCallsIncrementalTests(unittest.TestCase):
    """_merge_tool_calls_incremental 字段级状态演进合并测试。"""

    def test_pending_to_completed_evolves_with_approval_preserved(self):
        """P3-R2 核心：existing pending + new completed → 演进为 completed，approval 原样保留。"""
        existing = [
            {
                "id": "call_1",
                "name": "shell_exec",
                "type": "tool-call-shell_exec",
                "state": "input-available",
                "status": "pending",
                "parameters": {"command": "ls"},
                "result": None,
                "error": None,
                "approval": {
                    "state": "approved",
                    "approval_id": "appr_1",
                    "tool_call_id": "call_1",
                    "interrupt_id": "int_1",
                },
            }
        ]
        new = [
            {
                "id": "call_1",
                "name": "shell_exec",
                "type": "tool-call-shell_exec",
                "state": "output-available",
                "status": "completed",
                "parameters": {"command": "ls"},
                "result": {"output": "file1\nfile2"},
                "error": None,
            }
        ]

        merged = _merge_tool_calls_incremental(existing, new)

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["status"], "completed")
        self.assertEqual(merged[0]["state"], "output-available")
        self.assertEqual(merged[0]["result"], {"output": "file1\nfile2"})
        # approval 原样保留
        self.assertEqual(merged[0]["approval"]["state"], "approved")
        self.assertEqual(merged[0]["approval"]["approval_id"], "appr_1")

    def test_terminal_status_never_regresses(self):
        """existing completed + new pending → 保持 completed（终态不回退）。"""
        existing = [{"id": "call_1", "name": "x", "status": "completed", "result": "done"}]
        new = [{"id": "call_1", "name": "x", "status": "pending", "result": None}]

        merged = _merge_tool_calls_incremental(existing, new)

        self.assertEqual(merged[0]["status"], "completed")
        self.assertEqual(merged[0]["result"], "done")

    def test_all_terminal_statuses_are_protected(self):
        """failed/timeout/rejected 终态同样不可被非终态覆盖。"""
        for terminal in ("failed", "timeout", "rejected"):
            with self.subTest(terminal=terminal):
                existing = [{"id": "c", "name": "x", "status": terminal}]
                new = [{"id": "c", "name": "x", "status": "pending"}]
                merged = _merge_tool_calls_incremental(existing, new)
                self.assertEqual(merged[0]["status"], terminal)

    def test_non_terminal_regression_rejected(self):
        """非终态回退（running→pending）被拒绝，保持 running。"""
        existing = [{"id": "call_1", "name": "x", "status": "running"}]
        new = [{"id": "call_1", "name": "x", "status": "pending"}]

        merged = _merge_tool_calls_incremental(existing, new)

        self.assertEqual(merged[0]["status"], "running")

    def test_non_terminal_forward_evolution_allowed(self):
        """非终态前进演进（pending→running）允许。"""
        existing = [{"id": "call_1", "name": "x", "status": "pending"}]
        new = [{"id": "call_1", "name": "x", "status": "running"}]

        merged = _merge_tool_calls_incremental(existing, new)

        self.assertEqual(merged[0]["status"], "running")

    def test_existing_approval_wins_over_new_approval(self):
        """existing 有 approval 字段 → 合并后 approval 原样保留（new 的 approval 不覆盖）。"""
        existing = [
            {
                "id": "call_1",
                "name": "x",
                "status": "waiting",
                "approval": {"state": "waiting", "approval_id": "a1"},
            }
        ]
        new = [
            {
                "id": "call_1",
                "name": "x",
                "status": "completed",
                "approval": {"state": "pending", "approval_id": "a2"},
            }
        ]

        merged = _merge_tool_calls_incremental(existing, new)

        self.assertEqual(merged[0]["status"], "completed")
        self.assertEqual(merged[0]["approval"], {"state": "waiting", "approval_id": "a1"})

    def test_approval_taken_from_new_when_existing_lacks_it(self):
        """existing 无 approval 而 new 有 → 以 new 为准。"""
        existing = [{"id": "call_1", "name": "x", "status": "pending"}]
        new = [
            {
                "id": "call_1",
                "name": "x",
                "status": "completed",
                "approval": {"state": "approved"},
            }
        ]

        merged = _merge_tool_calls_incremental(existing, new)

        self.assertEqual(merged[0]["approval"], {"state": "approved"})

    def test_unmatched_new_entry_appended(self):
        """无匹配的 new 条目 → 追加；existing 不匹配条目保留。"""
        existing = [
            {
                "id": "call_1",
                "name": "x",
                "status": "pending",
                "approval": {"state": "approved"},
            }
        ]
        new = [{"id": "call_2", "name": "y", "status": "completed", "result": "ok"}]

        merged = _merge_tool_calls_incremental(existing, new)

        self.assertEqual(len(merged), 2)
        self.assertEqual([tc["id"] for tc in merged], ["call_1", "call_2"])
        self.assertEqual(merged[1]["status"], "completed")

    def test_existing_only_entries_kept(self):
        """existing 中存在但 new 中不存在的条目 → 保留（不丢失历史）。"""
        existing = [{"id": "call_1", "name": "x", "status": "completed"}]
        new = [{"id": "call_2", "name": "y", "status": "pending"}]

        merged = _merge_tool_calls_incremental(existing, new)

        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0]["id"], "call_1")
        self.assertEqual(merged[1]["id"], "call_2")

    def test_name_fallback_match(self):
        """无 id 时按 name 降级匹配，演进并保留 existing 的 approval。"""
        existing = [{"name": "search_tool", "status": "pending", "approval": {"state": "waiting"}}]
        new = [{"name": "search_tool", "status": "completed", "result": "found"}]

        merged = _merge_tool_calls_incremental(existing, new)

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["status"], "completed")
        self.assertEqual(merged[0]["approval"], {"state": "waiting"})

    def test_empty_inputs(self):
        """existing 为空 → 直接返回 new；new 为空 → 直接返回 existing。"""
        new = [{"id": "c", "name": "x", "status": "pending"}]
        self.assertEqual(_merge_tool_calls_incremental(None, new), new)
        existing = [{"id": "c", "name": "x", "status": "pending"}]
        self.assertEqual(_merge_tool_calls_incremental(existing, []), existing)

    def test_inputs_not_mutated(self):
        """入参对象不被修改（演进返回新字典）。"""
        existing = [
            {
                "id": "call_1",
                "name": "x",
                "status": "pending",
                "approval": {"state": "approved"},
            }
        ]
        new = [{"id": "call_1", "name": "x", "status": "completed", "result": "ok"}]

        _merge_tool_calls_incremental(existing, new)

        self.assertEqual(existing[0]["status"], "pending")
        self.assertEqual(existing[0]["approval"]["state"], "approved")
        self.assertEqual(new[0]["status"], "completed")


class PersistToolCallsChangedTests(unittest.TestCase):
    """_persist_to_db_sync 变更检测测试（sys.modules 注入替身，避免 Django/DB 依赖）。"""

    @staticmethod
    def _install_fakes(existing_tool_calls, existing_content=""):
        """构造 ChatSession/ChatMessage/timezone 替身模块并返回。"""
        chat_models = types.ModuleType("Django_xm.apps.chat.models")
        chat_models.ChatSession = type("ChatSession", (), {})
        chat_models.ChatMessage = type("ChatMessage", (), {})
        chat_models.ChatMessage.DoesNotExist = type("DoesNotExist", (Exception,), {})

        session_fake = SimpleNamespace(pk=1)
        message_fake = SimpleNamespace(
            id=1,
            content=existing_content,
            tool_calls=list(existing_tool_calls),
            reasoning={},
            save=mock.MagicMock(),
        )
        chat_models.ChatSession.objects = mock.MagicMock()
        chat_models.ChatSession.objects.filter.return_value.first.return_value = session_fake
        chat_models.ChatSession.objects.filter.return_value.update.return_value = None
        chat_models.ChatMessage.objects = mock.MagicMock()
        chat_models.ChatMessage.objects.get.side_effect = chat_models.ChatMessage.DoesNotExist
        chat_models.ChatMessage.objects.filter.return_value.order_by.return_value.first.return_value = message_fake

        timezone_module = types.ModuleType("django.utils.timezone")
        timezone_module.now = mock.MagicMock(return_value="2026-01-01T00:00:00")

        return chat_models, timezone_module, message_fake

    def test_persist_saves_evolved_tool_calls(self):
        """P3-R2 端到端：pending 演进为 completed 后 tool_calls_changed=True 并触发 save。"""
        existing = [
            {
                "id": "call_1",
                "name": "shell_exec",
                "state": "input-available",
                "status": "pending",
                "parameters": {},
                "result": None,
                "error": None,
                "approval": {"state": "approved", "approval_id": "a1"},
            }
        ]
        new = [
            {
                "id": "call_1",
                "name": "shell_exec",
                "state": "output-available",
                "status": "completed",
                "parameters": {},
                "result": {"output": "ok"},
                "error": None,
            }
        ]
        chat_models, tz_module, message_fake = self._install_fakes(existing)

        with mock.patch.dict(
            sys.modules,
            {
                "Django_xm.apps.chat.models": chat_models,
                "django.utils.timezone": tz_module,
            },
        ):
            # content 传空串，确保 save 仅由 tool_calls 演进触发
            result = _persist_to_db_sync("sess-1", None, "", new)

        self.assertEqual(result["tool_calls_changed"], True)
        self.assertEqual(message_fake.tool_calls[0]["status"], "completed")
        self.assertEqual(message_fake.tool_calls[0]["result"], {"output": "ok"})
        self.assertEqual(
            message_fake.tool_calls[0]["approval"],
            {"state": "approved", "approval_id": "a1"},
        )
        message_fake.save.assert_called_once()

    def test_persist_skips_save_when_no_content_change(self):
        """内容无变化（等长且字段不变）→ tool_calls_changed=False 且不触发 save。"""
        entry = {
            "id": "call_1",
            "name": "x",
            "state": "input-available",
            "status": "pending",
            "parameters": {},
            "result": None,
            "error": None,
        }
        chat_models, tz_module, message_fake = self._install_fakes([dict(entry)])

        with mock.patch.dict(
            sys.modules,
            {
                "Django_xm.apps.chat.models": chat_models,
                "django.utils.timezone": tz_module,
            },
        ):
            result = _persist_to_db_sync("sess-1", None, "", [dict(entry)])

        self.assertEqual(result["tool_calls_changed"], False)
        message_fake.save.assert_not_called()

    def test_persist_appends_unmatched_new_entry(self):
        """无匹配的新条目 → 追加，长度变化同样触发 save。"""
        existing = [{"id": "call_1", "name": "x", "status": "completed"}]
        new = [{"id": "call_2", "name": "y", "status": "pending"}]
        chat_models, tz_module, message_fake = self._install_fakes(existing)

        with mock.patch.dict(
            sys.modules,
            {
                "Django_xm.apps.chat.models": chat_models,
                "django.utils.timezone": tz_module,
            },
        ):
            result = _persist_to_db_sync("sess-1", None, "", new)

        self.assertEqual(result["tool_calls_changed"], True)
        self.assertEqual(result["tool_calls_count"], 2)
        message_fake.save.assert_called_once()


class PersistContentAppendTests(unittest.TestCase):
    """_persist_to_db_sync content 追加保留语义测试（修复流式累积段覆盖）。"""

    def _run(self, existing_content, content):
        chat_models, tz_module, message_fake = PersistToolCallsChangedTests._install_fakes(
            [], existing_content
        )
        with mock.patch.dict(
            sys.modules,
            {
                "Django_xm.apps.chat.models": chat_models,
                "django.utils.timezone": tz_module,
            },
        ):
            result = _persist_to_db_sync("sess-1", None, content, [])
        return result, message_fake

    def test_prefix_content_extends(self):
        """新内容以已有内容为前缀 → 直接采用新内容（正常流式累积 / 基线到位）。"""
        result, message_fake = self._run("流式段A", "流式段A总结段B")
        self.assertEqual(message_fake.content, "流式段A总结段B")
        self.assertTrue(result["content_changed"])

    def test_non_prefix_longer_content_appends(self):
        """新内容更长但不含已有前缀（恢复轮从空重建）→ 追加保留历史段。"""
        result, message_fake = self._run("流式段A", "总结段B内容")
        self.assertEqual(message_fake.content, "流式段A总结段B内容")
        self.assertTrue(result["content_changed"])

    def test_shorter_non_prefix_keeps_existing(self):
        """新内容更短且非前缀 → 保留已有版本（避免覆盖更长版本）。"""
        result, message_fake = self._run("流式段A", "短")
        self.assertEqual(message_fake.content, "流式段A")
        self.assertFalse(result["content_changed"])
        message_fake.save.assert_not_called()

    def test_overlap_tail_dedup(self):
        """新内容前缀与已有内容后缀重叠（恢复轮 checkpoint 重生成）→ 去重拼接。"""
        result, message_fake = self._run(
            "我来并行派发三个子代理分别执行任务，然后汇总结果。三个",
            "三个子代理全部完成！结果如下：",
        )
        self.assertEqual(
            message_fake.content,
            "我来并行派发三个子代理分别执行任务，然后汇总结果。三个子代理全部完成！结果如下：",
        )
        self.assertTrue(result["content_changed"])

    def test_overlap_full_content_keeps_existing(self):
        """新内容与已有内容完全重叠（仅尾部字符重放）→ 不产生净增长，保留已有。"""
        result, message_fake = self._run("流式段A三个", "三个")
        self.assertEqual(message_fake.content, "流式段A三个")
        self.assertFalse(result["content_changed"])
        message_fake.save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
