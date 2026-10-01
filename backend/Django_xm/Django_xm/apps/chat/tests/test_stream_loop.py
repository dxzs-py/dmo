"""stream.loop 知识库结果摘要单元测试。

锁定 chat 侧唯一实现的 KB 结果截断策略（恢复重构前
``_handle_tool_message_chunk`` 行为）：knowledge_base_* 工具结果超阈值时
截断为 ``前 200 字符 + "..."``，避免海量原文进入 WS/DB。

运行：
    python manage.py test Django_xm.apps.chat.tests.test_stream_loop
"""

from django.test import SimpleTestCase

from Django_xm.apps.chat.services.stream.loop import _summarize_kb_result


class SummarizeKbResultTests(SimpleTestCase):
    def test_long_kb_result_truncated_to_preview(self):
        """超过阈值（500）的知识库结果 → 前 200 字符 + "..."。"""
        content = "知" * 600
        out = _summarize_kb_result("knowledge_base_search", content)
        self.assertEqual(out, content[:200].rstrip() + "...")
        self.assertEqual(len(out), 203)

    def test_threshold_boundary_not_truncated_at_500(self):
        """恰好等于阈值（500，非 > 500）→ 原样返回。"""
        content = "x" * 500
        self.assertEqual(_summarize_kb_result("knowledge_base_search", content), content)

    def test_just_over_threshold_truncated(self):
        """501 字符 → 截断。"""
        content = "x" * 501
        out = _summarize_kb_result("knowledge_base_search", content)
        self.assertEqual(out, content[:200].rstrip() + "...")

    def test_non_kb_tool_result_unchanged(self):
        """非知识库工具即使超长也不截断。"""
        content = "y" * 600
        self.assertEqual(_summarize_kb_result("web_search", content), content)

    def test_non_string_result_unchanged(self):
        """非 str 结果（list/None）原样返回，不破坏 result 类型。"""
        payload = [1, 2, 3]
        self.assertIs(_summarize_kb_result("knowledge_base_search", payload), payload)
        self.assertIsNone(_summarize_kb_result("knowledge_base_search", None))

    def test_short_kb_result_unchanged(self):
        """短知识库结果原样返回。"""
        content = "短结果"
        self.assertEqual(_summarize_kb_result("knowledge_base_search", content), content)
