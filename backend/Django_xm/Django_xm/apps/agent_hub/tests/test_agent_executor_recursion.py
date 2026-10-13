"""AgentExecutor._handle_graph_recursion 递归降级语义测试（fix-review-round3 2.2）。

历史缺陷：递归上限触发且本轮正文为空时，反向取任意历史 AIMessage.content
当最终答案输出——中间轮正文可能是步骤性文本（"我先搜索一下..."），
冒充答案误导用户与下游落库。

修复后语义：
- 本轮正文已流式输出 → 仅追加"未完成"说明（不重复正文、不伪装成答案）；
- 本轮正文为空 → 输出结构化"任务未完成"说明 + 已执行步骤摘要
  （工具调用按名聚合次数 + 模型轮数 + 工具结果数），绝不反向取历史正文。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.agent_hub.tests.test_agent_executor_recursion
"""

import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock

from langchain_core.messages import AIMessage, ToolMessage

from Django_xm.apps.agent_hub.services.agent_executor import AgentExecutor


def _make_executor() -> AgentExecutor:
    """最小构造（_handle_graph_recursion 仅依赖 ctx 参数，其余依赖打桩）。"""
    return AgentExecutor(
        fallback_service=mock.MagicMock(),
        rebuild_agent_fn=mock.MagicMock(),
        tools=[],
        model_instance=mock.MagicMock(),
        data={},
        usage_tracker=mock.MagicMock(),
        token_detail_tracker=mock.MagicMock(),
    )


def _make_ctx(all_messages: list, current_content: str = ""):
    return SimpleNamespace(all_messages=all_messages, current_message_content=current_content)


class GraphRecursionDegradeTests(unittest.TestCase):
    """递归上限降级：不伪装答案 + 结构化未完成信息。"""

    def test_no_partial_content_emits_structured_unfinished_with_steps(self):
        """正文为空：输出"未完成"说明 + 已执行步骤摘要，不反向取历史正文。"""
        executor = _make_executor()
        ctx = _make_ctx(
            all_messages=[
                AIMessage(content="我先搜索一下相关资料。", tool_calls=[{"name": "web_search", "args": {}, "id": "c1"}]),
                ToolMessage(content="结果1", tool_call_id="c1"),
                AIMessage(content="再查一次。", tool_calls=[{"name": "web_search", "args": {}, "id": "c2"}]),
                ToolMessage(content="结果2", tool_call_id="c2"),
                AIMessage(content="补充检索。", tool_calls=[{"name": "fs_read_file", "args": {}, "id": "c3"}]),
                ToolMessage(content="内容", tool_call_id="c3"),
            ],
            current_content="",
        )

        events = asyncio.run(_collect(executor._handle_graph_recursion(ctx)))

        self.assertEqual(len(events), 1)
        content = events[0]["content"]
        self.assertIn("任务未完成", content)
        self.assertIn("已达到单次执行上限", content)
        # 已执行步骤摘要
        self.assertIn("web_search（2 次）", content)
        self.assertIn("fs_read_file（1 次）", content)
        self.assertIn("3 次工具结果返回", content)
        # 红线：不反向取历史 AIMessage 正文冒充答案
        self.assertNotIn("我先搜索一下相关资料", content)
        self.assertNotIn("补充检索。", content)
        # 不再回写 ctx（历史正文不当答案复用）
        self.assertEqual(ctx.current_message_content, "")

    def test_partial_content_appends_unfinished_notice_only(self):
        """正文已流式输出：仅追加未完成说明，不重复正文。"""
        executor = _make_executor()
        partial = "这是已流式输出的部分内容"
        ctx = _make_ctx(all_messages=[AIMessage(content=partial)], current_content=partial)

        events = asyncio.run(_collect(executor._handle_graph_recursion(ctx)))

        self.assertEqual(len(events), 1)
        content = events[0]["content"]
        self.assertIn("任务尚未完成", content)
        self.assertIn("以上为已生成的部分内容", content)
        self.assertNotIn(partial, content, "已输出正文不得重复输出")

    def test_no_tool_calls_omits_steps_section(self):
        """无工具调用历史：仅输出未完成说明，无步骤摘要段。"""
        executor = _make_executor()
        ctx = _make_ctx(all_messages=[AIMessage(content="思考中...")], current_content="")

        events = asyncio.run(_collect(executor._handle_graph_recursion(ctx)))

        content = events[0]["content"]
        self.assertIn("任务未完成", content)
        self.assertNotIn("已执行", content)


async def _collect(agen) -> list[dict]:
    return [event async for event in agen]


if __name__ == "__main__":
    unittest.main()
