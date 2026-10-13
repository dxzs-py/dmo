"""BaseMessage→dict 转换归一测试（phase6a G3，Task1 后收敛为双实现）。

归一结果（单源 = compression.messages_to_dicts）：
- compression.messages_to_dicts：权威实现（含 id / tool_calls / memory_tier /
  tool_call_id；非 BaseMessage 条目原样透传）；
- manager.ContextManager._messages_to_dicts：薄委托单源。
- middleware 侧委托已随官方 SummarizationMiddleware 替换删除（Task1），
  dict→BaseMessage 反向适配（dicts_to_messages）无生产消费方，已删除。

行为修复说明（G3 漂移）：manager 原实现缺 id / tool_call_id 字段（消息身份
丢失），归一后与单源对齐属行为修复——消费方（_serialize_messages /
mark_long_term / ContextPruner）仅读 role/content/name/pinned/memory_tier
等固定键，新增字段无副作用。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.context_manager.tests.test_message_adapter_normalization
"""

import os
import unittest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from Django_xm.apps.context_manager.services.compression import messages_to_dicts
from Django_xm.apps.context_manager.services.manager import ContextManager

_TOOL_CALLS = [
    {"name": "shell_exec", "args": {"cmd": "ls"}, "id": "call-1", "type": "tool_call"}
]


def _message_group() -> list:
    """同组 BaseMessage：AI 消息带 id + tool_calls，Tool 消息带 id + tool_call_id。"""
    system = SystemMessage(content="系统提示")
    system.additional_kwargs["memory_tier"] = "long_term"
    return [
        system,
        HumanMessage(content="问题", id="u-1"),
        AIMessage(content="调用工具", id="ai-1", tool_calls=list(_TOOL_CALLS)),
        ToolMessage(content="结果", tool_call_id="call-1", id="tool-1"),
    ]


# 完整版（单源权威语义）：保留消息身份字段
_EXPECTED_FULL = [
    {"role": "system", "content": "系统提示", "memory_tier": "long_term"},
    {"role": "user", "content": "问题", "id": "u-1"},
    {"role": "assistant", "content": "调用工具", "id": "ai-1", "tool_calls": _TOOL_CALLS},
    {"role": "tool", "content": "结果", "tool_call_id": "call-1", "id": "tool-1"},
]


class UnifiedAdapterTests(unittest.TestCase):
    """两处实现喂同一组 BaseMessage，输出逐字段一致（完整版语义）。"""

    def test_compression_single_source_is_complete(self) -> None:
        self.assertEqual(messages_to_dicts(_message_group()), _EXPECTED_FULL)

    def test_manager_delegates_to_single_source(self) -> None:
        self.assertEqual(ContextManager._messages_to_dicts(_message_group()), _EXPECTED_FULL)

    def test_both_implementations_produce_identical_output(self) -> None:
        """G3 归一核心断言：两处输出完全一致（漂移已消除）。"""
        via_compression = messages_to_dicts(_message_group())
        via_manager = ContextManager._messages_to_dicts(_message_group())
        self.assertEqual(via_compression, via_manager)

    def test_passthrough_non_base_message_all_implementations(self) -> None:
        """宽输入语义（原 manager 特有分支并入单源）：非 BaseMessage 原样透传。"""
        raw = {"role": "user", "content": "已构造的 dict"}
        message_group = [raw, *_message_group()]
        expected = [raw, *_EXPECTED_FULL]
        self.assertEqual(messages_to_dicts(message_group), expected)
        self.assertEqual(ContextManager._messages_to_dicts(message_group), expected)


if __name__ == "__main__":
    unittest.main()
