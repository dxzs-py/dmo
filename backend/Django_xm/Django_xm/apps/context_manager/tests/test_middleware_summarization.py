"""ContextManagerMiddleware × 官方 SummarizationMiddleware 集成测试（Task1）。

锁定官方语义重构后的关键契约：
1. 压缩经 before_model 状态更新真实生效：[RemoveMessage(REMOVE_ALL_MESSAGES),
   摘要, *保留消息] 经 add_messages reducer 合并后旧消息从模型可见列表消失，
   消息数与 token 真实下降（重构前返回压缩列表会被 reducer 追加，压缩无效）；
2. 摘要 HumanMessage 位于保留消息之前（官方语义）；
3. memory_tier 分层：SystemMessage 与显式 long_term 标记永不压缩、原样保留；
4. AI/Tool 配对保护：cutoff 落在 ToolMessage 时回退到配对 AIMessage；
5. 摘要模型失败向上传播（不伪造摘要）；
6. 压缩禁用 / provider 未注册 → 不压缩（明确降级，不阻塞装配）；
7. 异步链路走官方原生 abefore_model。

隔离说明：测试直接注入以 FakeChatModel 构建的官方 SummarizationMiddleware
实例（小阈值触发），不触达真实模型与 runtime_providers 解析链。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.context_manager.tests.test_middleware_summarization
"""

import asyncio
import os
import unittest
from typing import Any
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from langchain.agents.middleware import SummarizationMiddleware
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, RemoveMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.graph.message import REMOVE_ALL_MESSAGES, add_messages

from Django_xm.apps.context_manager.config import context_settings
from Django_xm.apps.context_manager.middleware import ContextManagerMiddleware

_BIG_CONTENT = "hello world " * 40


class FakeChatModel(BaseChatModel):
    """固定回复的假模型（官方 with_retry/ainvoke 链路兼容）。"""

    reply: str = "压缩摘要内容"

    @property
    def _llm_type(self) -> str:
        return "fake-chat"

    def _generate(self, messages: list, stop: list | None = None, run_manager: Any = None, **kwargs: Any) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self.reply))])

    def bind(self, **kwargs: Any):
        return self


def _small_summarizer(reply: str = "压缩摘要内容", keep: int = 2) -> SummarizationMiddleware:
    """小阈值触发的官方中间件（任何非空输入都触发，保留最近 keep 条）。"""
    return SummarizationMiddleware(
        FakeChatModel(reply=reply),
        trigger=("tokens", 1),
        keep=("messages", keep),
        token_counter=lambda msgs: sum(len(str(getattr(m, "content", ""))) for m in msgs),
    )


def _make_middleware(summarizer: SummarizationMiddleware | None) -> ContextManagerMiddleware:
    mw = ContextManagerMiddleware(model_name="", thread_id="sess-test")
    mw._summarizer = summarizer
    return mw


def _token_len(msgs: list[BaseMessage]) -> int:
    """测试用 token 代理指标：字符长度计数。"""
    return sum(len(str(getattr(m, "content", ""))) for m in msgs)


class MiddlewareSummarizationTests(unittest.TestCase):
    """before_model 压缩集成（经 add_messages reducer 验证真实缩减）。"""

    def test_compression_really_shrinks_message_list(self) -> None:
        """压缩后消息列表真实缩减：旧消息消失、摘要位于最近消息之前、token 下降。"""
        mw = _make_middleware(_small_summarizer(keep=2))
        before = [HumanMessage(content=_BIG_CONTENT) for _ in range(6)]
        state = {"messages": list(before)}

        update = mw.before_model(state, runtime=mock.MagicMock())

        self.assertIsNotNone(update)
        returned = update["messages"]
        # 状态更新结构：RemoveMessage(REMOVE_ALL) + 摘要 + 保留消息
        self.assertEqual(returned[0], RemoveMessage(id=REMOVE_ALL_MESSAGES))
        summary_msg = returned[1]
        self.assertIsInstance(summary_msg, HumanMessage)
        self.assertIn("Here is a summary of the conversation to date:", summary_msg.content)

        # 经 add_messages reducer 合并后的真实状态（checkpointer 保存的即此结果）
        after = add_messages(before, returned)
        self.assertIsInstance(after, list)
        self.assertLess(len(after), len(before))
        # 被摘要范围（前 4 条）消失，仅保留最近 keep=2 条 + 摘要
        self.assertEqual(len(after), 3)
        self.assertEqual(sum(1 for m in after if m.content == _BIG_CONTENT), 2)
        # 摘要在保留消息之前
        self.assertIn("Here is a summary", after[0].content)
        # token（代理指标）真实下降
        self.assertLess(_token_len(after), _token_len(before))

    def test_long_term_messages_never_compressed(self) -> None:
        """memory_tier 分层：system 与显式 long_term 消息原样保留在摘要之前。"""
        mw = _make_middleware(_small_summarizer(keep=2))
        system_msg = SystemMessage(content="系统提示，必须保留")
        marked = HumanMessage(content="用户偏好：偏好深色主题", id="pref-1")
        marked.additional_kwargs["memory_tier"] = "long_term"
        before = [system_msg, marked] + [HumanMessage(content=_BIG_CONTENT) for _ in range(5)]
        state = {"messages": list(before)}

        update = mw.before_model(state, runtime=mock.MagicMock())
        self.assertIsNotNone(update)

        after = add_messages(before, update["messages"])
        # 长期记忆原样保留（同一消息对象语义：内容与标记不变）
        self.assertIn(system_msg, after)
        self.assertIn(marked, after)
        # 短期旧消息被摘要替换，仅最近 2 条短期消息存活
        self.assertEqual(sum(1 for m in after if m.content == _BIG_CONTENT), 2)
        # 排序：长期记忆在摘要之前
        summary_idx = next(i for i, m in enumerate(after) if "Here is a summary" in m.content)
        self.assertLess(after.index(system_msg), summary_idx)
        self.assertLess(after.index(marked), summary_idx)

    def test_tool_call_pair_not_split(self) -> None:
        """AI/Tool 配对保护：cutoff 落在 ToolMessage 时回退到配对 AIMessage。"""
        mw = _make_middleware(_small_summarizer(keep=2))
        # keep=2 时 target cutoff=5 落在 T2 上，官方应回退到 AI2（index 4）
        before = [
            HumanMessage(content="问题一"),
            AIMessage(content="", tool_calls=[{"name": "search", "args": {"q": "a"}, "id": "call-1"}]),
            ToolMessage(content="结果一", tool_call_id="call-1"),
            HumanMessage(content="问题二"),
            AIMessage(content="", tool_calls=[{"name": "search", "args": {"q": "b"}, "id": "call-2"}]),
            ToolMessage(content="结果二", tool_call_id="call-2"),
            HumanMessage(content="问题三"),
        ]
        state = {"messages": list(before)}

        update = mw.before_model(state, runtime=mock.MagicMock())
        self.assertIsNotNone(update)

        after = add_messages(before, update["messages"])
        # 保留集中 AI2 与 T2 必须成对存活（不产生孤立 ToolMessage）
        tool_ids = {m.tool_call_id for m in after if isinstance(m, ToolMessage)}
        ai_call_ids = {tc["id"] for m in after if isinstance(m, AIMessage) for tc in (m.tool_calls or [])}
        self.assertEqual(tool_ids, ai_call_ids)
        self.assertIn("call-2", tool_ids)

    def test_below_trigger_returns_none(self) -> None:
        """未超阈值：不压缩，返回 None。"""
        summarizer = SummarizationMiddleware(
            FakeChatModel(),
            trigger=("tokens", 100000),
            keep=("messages", 2),
            token_counter=lambda msgs: sum(len(str(getattr(m, "content", ""))) for m in msgs),
        )
        mw = _make_middleware(summarizer)
        state = {"messages": [HumanMessage(content="hi"), AIMessage(content="hello there")]}
        self.assertIsNone(mw.before_model(state, runtime=mock.MagicMock()))

    def test_summary_failure_propagates(self) -> None:
        """摘要模型失败：异常向上传播（不伪造摘要、不静默跳过）。"""
        mw = _make_middleware(_small_summarizer())
        assert mw._summarizer is not None
        broken = mock.MagicMock()
        broken.invoke.side_effect = RuntimeError("摘要模型不可用")
        mw._summarizer._summary_model = broken

        state = {"messages": [HumanMessage(content=_BIG_CONTENT) for _ in range(6)]}
        with self.assertRaises(RuntimeError):
            mw.before_model(state, runtime=mock.MagicMock())

    def test_disabled_compression_returns_none(self) -> None:
        """compression_enabled=False：不构建 summarizer，before_model 直通。"""
        mw = ContextManagerMiddleware(model_name="", thread_id="sess-test")
        with mock.patch.object(context_settings, "compression_enabled", False):
            mw_disabled = ContextManagerMiddleware(model_name="", thread_id="sess-test")
        self.assertIsNone(mw_disabled._summarizer)
        self.assertIsNotNone(mw._summarizer)  # 启用态构建不受影响

        state = {"messages": [HumanMessage(content=_BIG_CONTENT) for _ in range(6)]}
        self.assertIsNone(mw_disabled.before_model(state, runtime=mock.MagicMock()))

    def test_missing_provider_disables_compression(self) -> None:
        """压缩模型 provider 未注册：明确降级（不压缩），不抛异常。"""
        from Django_xm.apps.context_manager.services.runtime_providers import (
            get_compression_model_provider,
            set_compression_model_provider,
        )

        original = get_compression_model_provider()
        try:
            set_compression_model_provider(None)
            mw = ContextManagerMiddleware(model_name="", thread_id="sess-test")
            self.assertIsNone(mw._summarizer)
        finally:
            set_compression_model_provider(original)

    def test_async_before_model_delegates_to_official(self) -> None:
        """异步链路：abefore_model 走官方原生异步摘要，结果与同步语义一致。"""
        mw = _make_middleware(_small_summarizer(keep=2))
        before = [HumanMessage(content=_BIG_CONTENT) for _ in range(6)]
        state = {"messages": list(before)}

        update = asyncio.run(mw.abefore_model(state, runtime=mock.MagicMock()))

        self.assertIsNotNone(update)
        after = add_messages(before, update["messages"])
        self.assertEqual(len(after), 3)
        self.assertEqual(sum(1 for m in after if m.content == _BIG_CONTENT), 2)
        self.assertIn("Here is a summary", after[0].content)
        self.assertLess(_token_len(after), _token_len(before))


class SanitizeOverrideTests(unittest.TestCase):
    """wrap_model_call 配对清理经 request.override 送达模型（原有契约保持）。"""

    def test_orphan_tool_message_stripped_from_model_request(self) -> None:
        mw = _make_middleware(None)
        seen: dict[str, Any] = {}

        def handler(request):
            seen["messages"] = request.messages
            return "ok"

        orphan_tool = ToolMessage(content="孤儿结果", tool_call_id="ghost")
        request = mock.MagicMock()
        request.messages = [HumanMessage(content="q"), orphan_tool]
        request.override.side_effect = lambda messages: mock.MagicMock(messages=messages)

        result = mw.wrap_model_call(request, handler)

        self.assertEqual(result, "ok")
        self.assertEqual(seen["messages"], [HumanMessage(content="q")])


if __name__ == "__main__":
    unittest.main()
