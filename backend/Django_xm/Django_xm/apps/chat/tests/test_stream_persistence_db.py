"""stream_persistence 拆分后字段组合并逻辑单元测试（C4/cq-03 Task 2）。

覆盖 _persist_to_db_sync / persist_stream_result 拆分出的模块级私有函数：
- ``_locate_session_and_message``：会话不存在 / message_id 精确命中 /
  非法 id 回退最新助手消息 / 无助手消息
- ``_merge_message_content``：前缀扩展 / 无重叠更长追加 / 更短保留 /
  尾部重叠去重 / 空内容与等值不变更
- ``_merge_message_tool_calls``：字段级演进（长度不变）变更检测 / 无变更
- ``_apply_reasoning_override`` / ``_apply_subagent_contents_override``：
  非空且不同覆盖 / 相同、空值、None 不变更
- ``_save_message_and_touch_session``：无变更不 save 但仍 touch session /
  有变更 save 且 update_fields 正确 / tool_calls 赋值时机
- ``_collect_tool_calls_with_subagents``：主条目白名单构建 +
  子代理条目按 id 去重合并
- ``persist_stream_result`` 编排：广播 payload / 广播失败不中断 /
  DB 异常返回 None / 空 session_id 短路

实现说明（沿用 test_stream_persistence.py 的模块加载模式）：
- 被测模块经 ``importlib`` 按文件路径直接加载，规避
  ``services/__init__.py`` 的重型导入链（agent_service 等会触发
  langchain / Django models 导入），使单测无需真实 DB。
- 整模块导入（chat.models / event_schema / realtime_events / asgiref.sync）
  经 ``sys.modules`` 注入替身隔离；``django.utils.timezone`` 因
  ``from django.utils import timezone`` 经父包属性取值，采用
  ``mock.patch`` 属性补丁；正文非前缀分支复用真实 ``sse_generator``
  权威实现（与现有测试同模式）。

运行命令（backend/Django_xm 目录）：
D:\\Anaconda_envs\\envs\\langchain_xm\\python.exe manage.py test \\
    Django_xm.apps.chat.tests.test_stream_persistence_db \\
    --settings=Django_xm.settings.test
"""

import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

_STREAM_PERSISTENCE_PATH = Path(__file__).resolve().parents[1] / "services" / "stream_persistence.py"


def _load_stream_persistence_module():
    """按文件路径加载被测模块（绕过 services/__init__.py 的重型导入链）。"""
    spec = importlib.util.spec_from_file_location(
        "stream_persistence_db_under_test",
        _STREAM_PERSISTENCE_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


MODULE = _load_stream_persistence_module()
_locate_session_and_message = MODULE._locate_session_and_message
_merge_message_content = MODULE._merge_message_content
_merge_message_tool_calls = MODULE._merge_message_tool_calls
_apply_reasoning_override = MODULE._apply_reasoning_override
_apply_subagent_contents_override = MODULE._apply_subagent_contents_override
_save_message_and_touch_session = MODULE._save_message_and_touch_session
_collect_tool_calls_with_subagents = MODULE._collect_tool_calls_with_subagents
persist_stream_result = MODULE.persist_stream_result


def _build_chat_models_fake(session_fake, message_fake, get_side_effect=None):
    """构造 ChatSession/ChatMessage 替身模块（复刻现有测试注入模式）。

    Args:
        session_fake: 会话替身（需含 pk 属性）
        message_fake: 消息替身（回退路径返回值）
        get_side_effect: ``ChatMessage.objects.get`` 的 side_effect
            （None 时返回 message_fake，模拟 message_id 精确命中）

    Returns:
        types.ModuleType: 可注入 sys.modules 的替身模块
    """
    chat_models = types.ModuleType("Django_xm.apps.chat.models")
    chat_models.ChatSession = type("ChatSession", (), {})
    chat_models.ChatMessage = type("ChatMessage", (), {})
    chat_models.ChatMessage.DoesNotExist = type("DoesNotExist", (Exception,), {})

    chat_models.ChatSession.objects = mock.MagicMock()
    chat_models.ChatSession.objects.filter.return_value.first.return_value = session_fake
    chat_models.ChatSession.objects.filter.return_value.update.return_value = None
    chat_models.ChatMessage.objects = mock.MagicMock()
    if get_side_effect is not None:
        chat_models.ChatMessage.objects.get.side_effect = get_side_effect
    else:
        chat_models.ChatMessage.objects.get.return_value = message_fake
    chat_models.ChatMessage.objects.filter.return_value.order_by.return_value.first.return_value = message_fake
    return chat_models


def _build_message_fake(existing_tool_calls=None, existing_content="", existing_reasoning=None,
                        existing_subagent=None):
    """构造内存消息替身（覆盖全部被持久化字段）。"""
    return SimpleNamespace(
        id=1,
        content=existing_content,
        tool_calls=list(existing_tool_calls or []),
        reasoning=existing_reasoning,
        subagent_contents=existing_subagent,
        save=mock.MagicMock(),
    )


class LocateSessionAndMessageTests(unittest.TestCase):
    """_locate_session_and_message 消息定位段测试。"""

    def test_session_not_found_returns_none(self):
        """会话不存在 → 返回 None。"""
        session_fake = None
        chat_models = _build_chat_models_fake(session_fake, _build_message_fake())
        chat_models.ChatSession.objects.filter.return_value.first.return_value = None

        with mock.patch.dict(sys.modules, {"Django_xm.apps.chat.models": chat_models}):
            result = _locate_session_and_message("sess-1", None)

        self.assertIsNone(result)

    def test_message_id_exact_hit(self):
        """message_id 有效 → 按主键精确取消息，不走回退查询。"""
        session_fake = SimpleNamespace(pk=1)
        message_fake = _build_message_fake()
        chat_models = _build_chat_models_fake(session_fake, message_fake)

        with mock.patch.dict(sys.modules, {"Django_xm.apps.chat.models": chat_models}):
            result = _locate_session_and_message("sess-1", "1")

        self.assertEqual(result, (session_fake, message_fake))
        chat_models.ChatMessage.objects.get.assert_called_once_with(id=1, session=session_fake, role="assistant")

    def test_invalid_message_id_falls_back_to_latest_assistant(self):
        """message_id 非法（int 转换 ValueError）→ 回退最新 assistant 消息。"""
        session_fake = SimpleNamespace(pk=1)
        message_fake = _build_message_fake()
        chat_models = _build_chat_models_fake(
            session_fake, message_fake, get_side_effect=ValueError("invalid id")
        )

        with mock.patch.dict(sys.modules, {"Django_xm.apps.chat.models": chat_models}):
            result = _locate_session_and_message("sess-1", "not-a-number")

        self.assertEqual(result, (session_fake, message_fake))
        chat_models.ChatMessage.objects.filter.return_value.order_by.assert_called_once_with("-created_at")

    def test_no_assistant_message_returns_none(self):
        """message_id 未命中（DoesNotExist）且会话无 assistant 消息 → 返回 None。"""
        session_fake = SimpleNamespace(pk=1)
        chat_models = _build_chat_models_fake(session_fake, None)
        # side_effect 异常类必须来自同一替身实例（except 按类精确匹配）
        chat_models.ChatMessage.objects.get.side_effect = chat_models.ChatMessage.DoesNotExist
        # 回退路径也无 assistant 消息
        chat_models.ChatMessage.objects.filter.return_value.order_by.return_value.first.return_value = None

        with mock.patch.dict(sys.modules, {"Django_xm.apps.chat.models": chat_models}):
            result = _locate_session_and_message("sess-1", "999")

        self.assertIsNone(result)


class MergeMessageContentTests(unittest.TestCase):
    """_merge_message_content 正文合并段测试（非前缀分支复用真实 sse_generator）。"""

    def _run(self, existing_content, content):
        message_fake = _build_message_fake(existing_content=existing_content)
        changed = _merge_message_content(message_fake, content)
        return changed, message_fake

    def test_prefix_content_extends(self):
        """新内容以已有内容为前缀 → 直接采用新内容。"""
        changed, message_fake = self._run("流式段A", "流式段A总结段B")
        self.assertTrue(changed)
        self.assertEqual(message_fake.content, "流式段A总结段B")

    def test_non_prefix_longer_content_appends(self):
        """新内容更长但无前缀/重叠 → 追加保留历史段。"""
        changed, message_fake = self._run("流式段A", "总结段B内容")
        self.assertTrue(changed)
        self.assertEqual(message_fake.content, "流式段A总结段B内容")

    def test_shorter_non_prefix_keeps_existing(self):
        """新内容更短且非前缀 → 保留已有版本。"""
        changed, message_fake = self._run("流式段A", "短")
        self.assertFalse(changed)
        self.assertEqual(message_fake.content, "流式段A")

    def test_overlap_tail_dedup(self):
        """尾部重叠（恢复轮 checkpoint 重生成）→ 去重拼接，不产生重复。"""
        changed, message_fake = self._run(
            "我来并行派发三个子代理分别执行任务，然后汇总结果。三个",
            "三个子代理全部完成！结果如下：",
        )
        self.assertTrue(changed)
        self.assertEqual(
            message_fake.content,
            "我来并行派发三个子代理分别执行任务，然后汇总结果。三个子代理全部完成！结果如下：",
        )

    def test_full_overlap_without_growth_keeps_existing(self):
        """新内容与已有内容完全重叠（无净增长）→ 保留已有版本。"""
        changed, message_fake = self._run("流式段A三个", "三个")
        self.assertFalse(changed)
        self.assertEqual(message_fake.content, "流式段A三个")

    def test_empty_content_no_change(self):
        """新内容为空串 → 不变更（content=False）。"""
        changed, message_fake = self._run("流式段A", "")
        self.assertFalse(changed)
        self.assertEqual(message_fake.content, "流式段A")

    def test_identical_content_no_change(self):
        """新内容与已有内容相同 → 不变更。"""
        changed, message_fake = self._run("流式段A", "流式段A")
        self.assertFalse(changed)
        self.assertEqual(message_fake.content, "流式段A")


class MergeMessageToolCallsTests(unittest.TestCase):
    """_merge_message_tool_calls 工具调用增量合并段测试。"""

    def test_field_evolution_detected_as_change(self):
        """字段级演进（长度不变，pending→completed）→ changed=True（深度比较）。"""
        existing = [
            {"id": "call_1", "name": "x", "status": "pending", "result": None},
        ]
        new = [
            {"id": "call_1", "name": "x", "status": "completed", "result": "ok"},
        ]
        message_fake = _build_message_fake(existing_tool_calls=existing)

        merged, changed = _merge_message_tool_calls(message_fake, new)

        self.assertTrue(changed)
        self.assertEqual(merged[0]["status"], "completed")
        self.assertEqual(merged[0]["result"], "ok")

    def test_no_change_detected_when_identical(self):
        """new 与 existing 完全一致 → changed=False。"""
        entry = {"id": "call_1", "name": "x", "status": "pending", "result": None}
        message_fake = _build_message_fake(existing_tool_calls=[dict(entry)])

        merged, changed = _merge_message_tool_calls(message_fake, [dict(entry)])

        self.assertFalse(changed)
        self.assertEqual(merged, [entry])

    def test_unmatched_entry_appended(self):
        """无匹配的新条目 → 追加且 changed=True。"""
        message_fake = _build_message_fake(
            existing_tool_calls=[{"id": "call_1", "name": "x", "status": "completed"}]
        )
        new = [{"id": "call_2", "name": "y", "status": "pending"}]

        merged, changed = _merge_message_tool_calls(message_fake, new)

        self.assertTrue(changed)
        self.assertEqual([tc["id"] for tc in merged], ["call_1", "call_2"])

    def test_existing_tool_calls_not_mutated(self):
        """assistant_msg.tool_calls 在合并段保持只读（赋值由保存段执行）。"""
        existing = [{"id": "call_1", "name": "x", "status": "pending"}]
        message_fake = _build_message_fake(existing_tool_calls=existing)
        new = [{"id": "call_1", "name": "x", "status": "completed"}]

        _merge_message_tool_calls(message_fake, new)

        self.assertEqual(message_fake.tool_calls, [{"id": "call_1", "name": "x", "status": "pending"}])


class ApplyReasoningOverrideTests(unittest.TestCase):
    """_apply_reasoning_override reasoning 覆盖段测试。"""

    def test_override_when_content_differs(self):
        """传入非空 reasoning 且 content 不同 → 覆盖并返回 True。"""
        message_fake = _build_message_fake(existing_reasoning={"content": "旧推理"})
        reasoning = {"content": "新推理"}

        changed = _apply_reasoning_override(message_fake, reasoning)

        self.assertTrue(changed)
        self.assertEqual(message_fake.reasoning, {"content": "新推理"})

    def test_no_override_when_content_identical(self):
        """传入 reasoning 的 content 与已有相同 → 不变更。"""
        message_fake = _build_message_fake(existing_reasoning={"content": "推理", "extra": 1})

        changed = _apply_reasoning_override(message_fake, {"content": "推理"})

        self.assertFalse(changed)
        self.assertEqual(message_fake.reasoning, {"content": "推理", "extra": 1})

    def test_no_override_when_reasoning_none(self):
        """reasoning=None → 不变更。"""
        message_fake = _build_message_fake(existing_reasoning={"content": "推理"})

        changed = _apply_reasoning_override(message_fake, None)

        self.assertFalse(changed)
        self.assertEqual(message_fake.reasoning, {"content": "推理"})

    def test_no_override_when_content_empty(self):
        """reasoning.content 为空串 → 不变更。"""
        message_fake = _build_message_fake(existing_reasoning={"content": "推理"})

        changed = _apply_reasoning_override(message_fake, {"content": ""})

        self.assertFalse(changed)
        self.assertEqual(message_fake.reasoning, {"content": "推理"})

    def test_override_when_existing_not_dict(self):
        """已有 reasoning 非 dict（如 None）→ 视为空 dict，正常覆盖。"""
        message_fake = _build_message_fake(existing_reasoning=None)

        changed = _apply_reasoning_override(message_fake, {"content": "推理"})

        self.assertTrue(changed)
        self.assertEqual(message_fake.reasoning, {"content": "推理"})


class ApplySubagentContentsOverrideTests(unittest.TestCase):
    """_apply_subagent_contents_override 子代理图层正文覆盖段测试。"""

    def test_override_when_differs(self):
        """传入非空 subagent_contents 且不同 → 覆盖并返回 True。"""
        message_fake = _build_message_fake(existing_subagent={"thread_1": {"content": "旧"}})

        changed = _apply_subagent_contents_override(message_fake, {"thread_1": {"content": "新"}})

        self.assertTrue(changed)
        self.assertEqual(message_fake.subagent_contents, {"thread_1": {"content": "新"}})

    def test_no_override_when_identical(self):
        """传入值与已有相同 → 不变更。"""
        existing = {"thread_1": {"content": "同"}}
        message_fake = _build_message_fake(existing_subagent=dict(existing))

        changed = _apply_subagent_contents_override(message_fake, dict(existing))

        self.assertFalse(changed)

    def test_no_override_when_none_or_empty(self):
        """None 与空 dict → 均不变更（避免空覆盖清除历史）。"""
        message_fake = _build_message_fake(existing_subagent={"thread_1": {"content": "旧"}})

        self.assertFalse(_apply_subagent_contents_override(message_fake, None))
        self.assertFalse(_apply_subagent_contents_override(message_fake, {}))
        self.assertEqual(message_fake.subagent_contents, {"thread_1": {"content": "旧"}})

    def test_override_when_existing_not_dict(self):
        """已有 subagent_contents 非 dict（如 None）→ 视为空 dict，正常覆盖。"""
        message_fake = _build_message_fake(existing_subagent=None)

        changed = _apply_subagent_contents_override(message_fake, {"thread_1": {"content": "新"}})

        self.assertTrue(changed)
        self.assertEqual(message_fake.subagent_contents, {"thread_1": {"content": "新"}})


class SaveMessageAndTouchSessionTests(unittest.TestCase):
    """_save_message_and_touch_session 变更检测与保存段测试。"""

    def _run(self, changed_flags, merged_tool_calls=None, existing_tool_calls=None):
        session_fake = SimpleNamespace(pk=7)
        message_fake = _build_message_fake(existing_tool_calls=existing_tool_calls or [])
        chat_models = _build_chat_models_fake(session_fake, message_fake)
        merged = merged_tool_calls if merged_tool_calls is not None else []

        # timezone 采用属性补丁：``from django.utils import timezone`` 经父包属性
        # 取值，sys.modules 叶子模块注入不生效（与 chat.models 的整模块导入不同）
        with mock.patch.dict(
            sys.modules,
            {"Django_xm.apps.chat.models": chat_models},
        ), mock.patch("django.utils.timezone.now", return_value="2026-01-01T00:00:00"):
            _save_message_and_touch_session(message_fake, session_fake, merged, *changed_flags)

        return message_fake, chat_models

    def test_no_change_skips_save_but_touches_session(self):
        """无任何变更 → 不 save，但仍无条件刷新会话 updated_at。"""
        message_fake, chat_models = self._run((False, False, False, False))

        message_fake.save.assert_not_called()
        chat_models.ChatSession.objects.filter.return_value.update.assert_called_once_with(
            updated_at="2026-01-01T00:00:00"
        )
        chat_models.ChatSession.objects.filter.assert_called_once_with(pk=7)

    def test_save_called_with_fixed_update_fields(self):
        """有变更 → save 一次且 update_fields 固定包含全部业务字段与 updated_at。"""
        merged = [{"id": "call_1", "name": "x", "status": "completed"}]
        message_fake, _ = self._run((True, False, False, False), merged_tool_calls=merged)

        message_fake.save.assert_called_once_with(
            update_fields=["content", "tool_calls", "reasoning", "subagent_contents", "updated_at"]
        )
        # tool_calls 在 save 前统一赋值为合并结果
        self.assertEqual(message_fake.tool_calls, merged)

    def test_each_flag_alone_triggers_save(self):
        """四个变更标记任一为 True → 均触发 save。"""
        for idx in range(4):
            with self.subTest(flag_index=idx):
                flags = [False] * 4
                flags[idx] = True
                message_fake, _ = self._run(tuple(flags))
                message_fake.save.assert_called_once()

    def test_tool_calls_assigned_only_when_saving(self):
        """无变更时不赋值 tool_calls（避免污染未保存的消息对象）。"""
        message_fake, _ = self._run((False, False, False, False), merged_tool_calls=[{"id": "new"}])

        self.assertEqual(message_fake.tool_calls, [])


class CollectToolCallsWithSubagentsTests(unittest.TestCase):
    """_collect_tool_calls_with_subagents tool_calls 构造阶段测试。"""

    def test_builds_whitelist_entries_from_map(self):
        """主代理条目按白名单构建（含内部字段过滤与默认值兜底）。"""
        tool_calls_map = {
            "k1": {
                "id": "call_1",
                "name": "shell_exec",
                "type": "tool-call-shell_exec",
                "state": "output-available",
                "status": "completed",
                "parameters": {"command": "ls"},
                "result": "ok",
                "error": None,
                "_index": 3,
                "seq": 5,
            },
        }

        collected = _collect_tool_calls_with_subagents(tool_calls_map, None)

        self.assertEqual(len(collected), 1)
        entry = collected[0]
        self.assertEqual(entry["id"], "call_1")
        self.assertEqual(entry["seq"], 5)
        self.assertNotIn("_index", entry)

    def test_subagent_entries_appended_with_layer_fields(self):
        """子代理条目（含图层字段）按 id 去重后追加到主条目之后。"""
        subagent_entries = {
            "sub_call_1": {
                "id": "sub_call_1",
                "name": "shell_exec",
                "status": "completed",
                "subagent_thread_id": "thread_1",
                "agent_name": "researcher",
                "depth": 1,
            },
        }

        collected = _collect_tool_calls_with_subagents({}, subagent_entries)

        self.assertEqual(len(collected), 1)
        self.assertEqual(collected[0]["id"], "sub_call_1")
        self.assertEqual(collected[0]["subagent_thread_id"], "thread_1")

    def test_subagent_entry_with_existing_id_skipped(self):
        """子代理条目 id 与主条目重复 → 跳过（按 id 天然去重）。"""
        tool_calls_map = {"k1": {"id": "call_1", "name": "main_tool", "status": "completed"}}
        subagent_entries = {"call_1": {"id": "call_1", "name": "main_tool", "status": "completed"}}

        collected = _collect_tool_calls_with_subagents(tool_calls_map, subagent_entries)

        self.assertEqual(len(collected), 1)
        self.assertEqual(collected[0]["name"], "main_tool")

    def test_subagent_entry_with_empty_id_skipped(self):
        """子代理条目 id 为空串/None key → 跳过。"""
        subagent_entries = {
            "": {"id": "", "name": "orphan", "status": "pending"},
            "call_2": {"id": "call_2", "name": "valid", "status": "pending"},
        }

        collected = _collect_tool_calls_with_subagents({}, subagent_entries)

        self.assertEqual([tc["id"] for tc in collected], ["call_2"])

    def test_subagent_entry_appended_as_copy(self):
        """追加的子代理条目为副本（修改收集结果不影响入参）。"""
        subagent_entries = {"call_1": {"id": "call_1", "name": "x", "status": "pending"}}

        collected = _collect_tool_calls_with_subagents({}, subagent_entries)
        collected[0]["status"] = "completed"

        self.assertEqual(subagent_entries["call_1"]["status"], "pending")

    def test_none_entries_returns_main_only(self):
        """subagent_tool_entries=None → 仅返回主条目构建结果。"""
        tool_calls_map = {"k1": {"id": "call_1", "name": "x", "status": "pending"}}

        collected = _collect_tool_calls_with_subagents(tool_calls_map, None)

        self.assertEqual(len(collected), 1)


class PersistStreamResultOrchestrationTests(unittest.TestCase):
    """persist_stream_result 编排测试（替身注入 asgiref/broadcast，内存对象断言）。"""

    @staticmethod
    def _build_broadcast_fakes(publish_side_effect=None):
        """构造 event_schema / realtime_events / asgiref.sync 替身。"""
        event_schema = types.ModuleType("Django_xm.common.event_schema")
        event_schema.EventType = SimpleNamespace(MESSAGE_UPDATED="message_updated")
        event_schema.EventSource = SimpleNamespace(CHAT=SimpleNamespace(value="chat"))

        realtime_events = types.ModuleType("Django_xm.common.realtime_events")
        publish_mock = mock.AsyncMock(side_effect=publish_side_effect)
        realtime_events.publish_event = publish_mock

        asgiref_sync = types.ModuleType("asgiref.sync")

        def _fake_sync_to_async(fn, thread_sensitive=False):
            async def _wrapper(*args, **kwargs):
                return fn(*args, **kwargs)

            return _wrapper

        asgiref_sync.sync_to_async = _fake_sync_to_async
        return event_schema, realtime_events, asgiref_sync, publish_mock

    def _run_persist(self, message_fake, publish_side_effect=None, **persist_kwargs):
        """注入全部替身后运行 persist_stream_result，返回 (结果, publish_mock)。"""
        session_fake = SimpleNamespace(pk=7)
        chat_models = _build_chat_models_fake(session_fake, message_fake)
        chat_models.ChatMessage.objects.get.side_effect = chat_models.ChatMessage.DoesNotExist
        event_schema, realtime_events, asgiref_sync, publish_mock = self._build_broadcast_fakes(
            publish_side_effect
        )

        with mock.patch.dict(
            sys.modules,
            {
                "Django_xm.apps.chat.models": chat_models,
                "Django_xm.common.event_schema": event_schema,
                "Django_xm.common.realtime_events": realtime_events,
                "asgiref.sync": asgiref_sync,
            },
        ):
            result = asyncio.run(
                persist_stream_result(
                    session_id=persist_kwargs.get("session_id", "sess-1"),
                    user_id=None,
                    content=persist_kwargs.get("content", ""),
                    tool_calls_map=persist_kwargs.get("tool_calls_map", {}),
                    message_id=persist_kwargs.get("message_id"),
                    reasoning=persist_kwargs.get("reasoning"),
                    subagent_contents=persist_kwargs.get("subagent_contents"),
                    subagent_tool_entries=persist_kwargs.get("subagent_tool_entries"),
                )
            )
        return result, publish_mock

    def test_success_broadcasts_message_updated_payload(self):
        """成功路径：返回 message_id 并广播 MESSAGE_UPDATED（payload 字段完整）。"""
        message_fake = _build_message_fake(existing_content="")

        result, publish_mock = self._run_persist(message_fake, content="最终内容")

        self.assertEqual(result, "1")
        publish_mock.assert_awaited_once()
        args, kwargs = publish_mock.call_args
        self.assertEqual(args[0], "message_updated")
        self.assertEqual(kwargs, {"session_id": "sess-1"})
        self.assertEqual(
            args[1],
            {
                "message_id": "1",
                "session_id": "sess-1",
                "source": "chat",
                "source_id": "sess-1",
                "content_len": len("最终内容"),
                "tool_calls_count": 0,
            },
        )

    def test_broadcast_failure_does_not_break_result(self):
        """广播抛异常 → 仅 warning，仍返回 message_id（不中断持久化结果）。"""
        message_fake = _build_message_fake()

        result, publish_mock = self._run_persist(
            message_fake, content="内容", publish_side_effect=RuntimeError("channel down")
        )

        self.assertEqual(result, "1")
        publish_mock.assert_awaited_once()

    def test_db_failure_returns_none(self):
        """DB 阶段抛异常 → 返回 None 且不广播。"""
        message_fake = _build_message_fake()
        session_fake = SimpleNamespace(pk=7)
        chat_models = _build_chat_models_fake(session_fake, message_fake)
        chat_models.ChatMessage.objects.get.side_effect = RuntimeError("db down")
        chat_models.ChatMessage.objects.filter.return_value.order_by.return_value.first.side_effect = RuntimeError(
            "db down"
        )
        chat_models.ChatSession.objects.filter.return_value.first.side_effect = RuntimeError("db down")
        event_schema, realtime_events, asgiref_sync, publish_mock = self._build_broadcast_fakes()

        with mock.patch.dict(
            sys.modules,
            {
                "Django_xm.apps.chat.models": chat_models,
                "Django_xm.common.event_schema": event_schema,
                "Django_xm.common.realtime_events": realtime_events,
                "asgiref.sync": asgiref_sync,
            },
        ):
            result = asyncio.run(
                persist_stream_result("sess-1", None, "内容", {}, message_id=None)
            )

        self.assertIsNone(result)
        publish_mock.assert_not_awaited()

    def test_empty_session_id_short_circuits(self):
        """session_id 为空串 → 直接返回 None，不触发 DB 与广播。"""
        result, publish_mock = self._run_persist(_build_message_fake(), session_id="", content="x")

        self.assertIsNone(result)
        publish_mock.assert_not_awaited()

    def test_subagent_entries_persisted_end_to_end(self):
        """子代理工具条目经编排端到端落库（图层字段保留、触发 save）。"""
        message_fake = _build_message_fake()
        subagent_entries = {
            "sub_call_1": {
                "id": "sub_call_1",
                "name": "shell_exec",
                "status": "completed",
                "subagent_thread_id": "thread_1",
                "agent_name": "researcher",
                "depth": 1,
            },
        }

        result, publish_mock = self._run_persist(
            message_fake, content="", subagent_tool_entries=subagent_entries
        )

        self.assertEqual(result, "1")
        self.assertEqual(len(message_fake.tool_calls), 1)
        self.assertEqual(message_fake.tool_calls[0]["subagent_thread_id"], "thread_1")
        message_fake.save.assert_called_once()
        args, kwargs = publish_mock.call_args
        self.assertEqual(args[1]["tool_calls_count"], 1)


if __name__ == "__main__":
    unittest.main()
