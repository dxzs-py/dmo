"""P0-1 注入位置测试：MemoryInjectionMiddleware 把注入块合并进最后一条 user message。

背景（P0-1 prompt caching 修复）：旧实现把注入 SystemMessage 前插到消息列表
头部，注入内容随对话推进变化，导致 [静态 system + 历史] 缓存前缀永远在
第 2 条消息处断裂；新实现把注入块（带分隔标记）合并进最后一条 user message
的 content，注入块位于尾部易变区，前缀跨模型调用逐字节稳定。

覆盖点：
(a) 注入块位于最后一条 user message 之前（合并进其 content，注入在前原文紧随）；
(b) 字节稳定性：相同历史下连续两次 wrap，注入点之前的消息逐字节相同；
(c) 工具循环稳定性：同一轮连续 3 次 wrap（每次均从干净 state 消息出发，
    与 langchain override 不回写 state 的真实行为一致），注入块位置与内容不变；
(d) 空注入不注入（完全透传）；
(e) 无 user message 时注入块以 SystemMessage 追加到列表末尾（防御性兜底）。

运行：
    python -m pytest Django_xm/apps/context_manager/tests/test_memory_injection_position.py -q
"""

import asyncio
from unittest import mock

from django.test import SimpleTestCase
from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from Django_xm.apps.context_manager.middleware import MemoryInjectionMiddleware

_INJECTION_TEXT = "【跨会话上下文】\n用户此前决定使用 LangGraph 构建多智能体。"


def _make_middleware() -> MemoryInjectionMiddleware:
    return MemoryInjectionMiddleware(injection_text=_INJECTION_TEXT)


def _make_request(messages):
    return ModelRequest(
        model=mock.MagicMock(),
        messages=list(messages),
        system_message=None,
        tool_choice=None,
        tools=[],
        response_format=None,
        state=None,
        runtime=None,
    )


def _wrap(request) -> list:
    """执行同步 wrap 并返回 override 后送达模型的消息列表"""
    captured = {}

    def handler(req):
        captured["messages"] = req.messages
        return "ok"

    _make_middleware().wrap_model_call(request, handler)
    return list(captured["messages"])


def _merged_content(messages: list) -> str:
    """取注入合并后的最后一条 user message 的 content（str 形态）"""
    last_human = [m for m in messages if isinstance(m, HumanMessage)][-1]
    return last_human.content


class InjectionPositionTests(SimpleTestCase):
    """(a) 注入块位于最后一条 user message 之前（合并进 content）"""

    def test_injection_merged_into_last_user_message(self):
        history = [
            SystemMessage(content="静态系统提示"),
            HumanMessage(content="第一轮问题"),
            AIMessage(content="第一轮回答"),
            HumanMessage(content="第二轮问题"),
        ]
        messages = _wrap(_make_request(history))

        # 消息条数不变（合并不增删消息），注入点之前的消息原样保留
        self.assertEqual(len(messages), len(history))
        for original, delivered in zip(history[:-1], messages[:-1], strict=True):
            self.assertEqual(delivered.content, original.content)

        last_human = messages[-1]
        self.assertIsInstance(last_human, HumanMessage)
        content = last_human.content
        # 注入块在前，用户原文紧随其后 → 注入块位于最后一条 user message 之前
        self.assertTrue(content.startswith(MemoryInjectionMiddleware._INJECTION_BEGIN_MARK))
        self.assertIn(MemoryInjectionMiddleware._INJECTION_END_MARK, content)
        self.assertLess(content.index(_INJECTION_TEXT), content.index("第二轮问题"))
        self.assertTrue(content.endswith("第二轮问题"))

    def test_injection_before_last_user_message_multimodal_content(self):
        """多模态（content 块列表）user message：注入块插入为首部独立 text 块"""
        original_blocks = [
            {"type": "text", "text": "看这张图"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,xxx"}},
        ]
        messages = _wrap(_make_request([HumanMessage(content=original_blocks)]))

        merged = messages[-1]
        self.assertIsInstance(merged.content, list)
        self.assertEqual(merged.content[0]["type"], "text")
        self.assertIn(_INJECTION_TEXT, merged.content[0]["text"])
        self.assertEqual(merged.content[1:], original_blocks)


class ByteStablePrefixTests(SimpleTestCase):
    """(b) 字节稳定性：相同历史下连续两次 wrap，注入点之前逐字节相同"""

    def test_repeated_wrap_prefix_byte_identical(self):
        history = [
            SystemMessage(content="静态系统提示"),
            HumanMessage(content="第一轮问题"),
            AIMessage(content="第一轮回答"),
            HumanMessage(content="第二轮问题"),
        ]

        first = _wrap(_make_request(history))
        second = _wrap(_make_request(history))

        # 注入点之前（最后一条 user message 之前的全部消息）逐字节相同
        first_prefix = [m.content for m in first[:-1]]
        second_prefix = [m.content for m in second[:-1]]
        self.assertEqual(first_prefix, second_prefix)
        # 且与原始历史（不含静态 system 由 factory 单独拼接的部分之外）完全一致
        self.assertEqual(first_prefix, [m.content for m in history[:-1]])
        # 注入块内容也完全确定（同一轮内注入文本不变）
        self.assertEqual(_merged_content(first), _merged_content(second))


class ToolLoopStabilityTests(SimpleTestCase):
    """(c) 工具循环稳定性：同一轮连续 3 次 wrap，注入块位置与内容不变"""

    def test_three_model_calls_in_one_turn_keep_injection_stable(self):
        # 工具循环真实行为：override 不回写 state，每次模型调用的
        # request.messages 均从干净 state 出发，逐次累积 AI/Tool 消息
        user_query = HumanMessage(content="查一下天气")
        call1_state = [user_query]
        call2_state = [
            user_query,
            AIMessage(content="", tool_calls=[{"name": "get_weather", "args": {"city": "北京"}, "id": "call_1"}]),
            ToolMessage(content="晴，25 度", tool_call_id="call_1"),
        ]
        call3_state = [
            user_query,
            AIMessage(content="", tool_calls=[{"name": "get_weather", "args": {"city": "北京"}, "id": "call_1"}]),
            ToolMessage(content="晴，25 度", tool_call_id="call_1"),
            AIMessage(content="", tool_calls=[{"name": "get_uv", "args": {"city": "北京"}, "id": "call_2"}]),
            ToolMessage(content="UV 指数 3", tool_call_id="call_2"),
        ]

        call1 = _wrap(_make_request(call1_state))
        call2 = _wrap(_make_request(call2_state))
        call3 = _wrap(_make_request(call3_state))

        # 注入块始终合并进同一条 user message（唯一 human 消息），内容三次完全一致
        content1 = _merged_content(call1)
        content2 = _merged_content(call2)
        content3 = _merged_content(call3)
        self.assertEqual(content1, content2)
        self.assertEqual(content2, content3)
        # 每次送达模型的列表中注入块只出现一次
        for content in (content1, content2, content3):
            self.assertEqual(content.count(MemoryInjectionMiddleware._INJECTION_BEGIN_MARK), 1)
        # 注入块位于最后一条 user message 内（尾部易变区），其之前的消息未被改写
        self.assertEqual([m.content for m in call2[1:]], [m.content for m in call2_state[1:]])


class EmptyInjectionPassthroughTests(SimpleTestCase):
    """(d) 空注入不注入：完全透传，handler 收到原请求对象"""

    def test_empty_injection_passthrough(self):
        for text in ("", "   "):
            request = _make_request([HumanMessage(content="hi")])
            captured = {}

            def handler(req):
                captured["request"] = req  # noqa: B023 -- captured 每轮重建且当轮同步消费，无 late-binding 风险
                return "ok"

            result = MemoryInjectionMiddleware(injection_text=text).wrap_model_call(request, handler)

            self.assertEqual(result, "ok")
            self.assertIs(captured["request"], request)


class NoUserMessageFallbackTests(SimpleTestCase):
    """(e) 无 user message：注入块以 SystemMessage 追加到列表末尾（防御性兜底）"""

    def test_no_user_message_appends_system_message_at_end(self):
        history = [
            AIMessage(content="仅有的助手消息"),
            ToolMessage(content="工具结果", tool_call_id="call_x"),
        ]
        messages = _wrap(_make_request(history))

        self.assertEqual(len(messages), len(history) + 1)
        # 既有消息时序不动，注入块追加在末尾
        for original, delivered in zip(history, messages[:-1], strict=True):
            self.assertIs(delivered, original)
        appended = messages[-1]
        self.assertIsInstance(appended, SystemMessage)
        self.assertIn(_INJECTION_TEXT, appended.content)


class AsyncMirrorTests(SimpleTestCase):
    """awrap_model_call 与 wrap_model_call 语义一致（合并注入）"""

    def test_awrap_matches_wrap_layout(self):
        history = [
            HumanMessage(content="第一轮问题"),
            AIMessage(content="第一轮回答"),
            HumanMessage(content="第二轮问题"),
        ]
        captured = {}

        async def handler(req):
            captured["messages"] = req.messages
            return "ok-async"

        asyncio.run(_make_middleware().awrap_model_call(_make_request(history), handler))
        async_messages = list(captured["messages"])

        sync_messages = _wrap(_make_request(history))
        self.assertEqual(
            [m.content for m in async_messages], [m.content for m in sync_messages]
        )
        self.assertIsInstance(async_messages[-1], HumanMessage)
        self.assertIn(_INJECTION_TEXT, async_messages[-1].content)
