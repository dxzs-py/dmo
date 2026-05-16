"""
Chat service 单元测试

测试聊天服务层的纯数据处理逻辑。
"""
import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings")
django.setup()

from unittest import TestCase
from unittest.mock import MagicMock, patch
from langchain_core.messages import AIMessage, ToolMessage


class LcpLenTest(TestCase):
    """测试 _lcp_len 最长公共前缀长度"""

    def test_empty_strings(self):
        from Django_xm.apps.chat.utils import _lcp_len
        self.assertEqual(_lcp_len("", ""), 0)

    def test_identical_strings(self):
        from Django_xm.apps.chat.utils import _lcp_len
        self.assertEqual(_lcp_len("hello", "hello"), 5)

    def test_partial_match(self):
        from Django_xm.apps.chat.utils import _lcp_len
        self.assertEqual(_lcp_len("hello world", "hello there"), 6)

    def test_no_match(self):
        from Django_xm.apps.chat.utils import _lcp_len
        self.assertEqual(_lcp_len("abc", "xyz"), 0)

    def test_first_shorter(self):
        from Django_xm.apps.chat.utils import _lcp_len
        self.assertEqual(_lcp_len("ab", "abcde"), 2)

    def test_second_shorter(self):
        from Django_xm.apps.chat.utils import _lcp_len
        self.assertEqual(_lcp_len("abcde", "ab"), 2)

    def test_with_chinese(self):
        from Django_xm.apps.chat.utils import _lcp_len
        self.assertEqual(_lcp_len("你好世界", "你好"), 2)


class NeedsCompletionTest(TestCase):
    """测试 _needs_completion 文本完整性判断"""

    def test_empty_text(self):
        from Django_xm.apps.chat.utils import _needs_completion
        self.assertTrue(_needs_completion(""))

    def test_short_text(self):
        from Django_xm.apps.chat.utils import _needs_completion
        self.assertTrue(_needs_completion("你好"))

    def test_unfinished_sentence(self):
        from Django_xm.apps.chat.utils import _needs_completion
        self.assertTrue(_needs_completion("这是一个很长的句子但是没有句号这是一个很长的句子但是没有句号"))

    def test_finished_with_period(self):
        from Django_xm.apps.chat.utils import _needs_completion
        self.assertFalse(_needs_completion("这是一个超过三十个字的完整句子用来测试句号判断逻辑的完整性。"))

    def test_finished_with_exclamation(self):
        from Django_xm.apps.chat.utils import _needs_completion
        self.assertFalse(_needs_completion("真棒这个测试字符串已经超过三十个字了用来测试感叹号是否有效！"))

    def test_finished_with_question_mark(self):
        from Django_xm.apps.chat.utils import _needs_completion
        self.assertFalse(_needs_completion("你还好吗今天天气真不错我们去公园散步怎么样这样才够三十字吧？"))


class ProcessStreamChunkTest(TestCase):
    """测试 _process_stream_chunk 流式块处理"""

    def test_ai_message_with_content(self):
        from Django_xm.apps.chat.services.chat_service import ChatService
        chunk = AIMessage(content="Hello world")
        events = ChatService._process_stream_chunk(chunk, {}, "")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "chunk")
        self.assertEqual(events[0]["content"], "Hello world")

    def test_ai_message_with_tool_calls(self):
        from Django_xm.apps.chat.services.chat_service import ChatService
        chunk = AIMessage(
            content="",
            tool_calls=[{"id": "t1", "name": "search", "args": {"q": "test"}}],
        )
        events = ChatService._process_stream_chunk(chunk, {}, "")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "tool")
        self.assertEqual(events[0]["data"]["name"], "search")
        self.assertEqual(events[0]["data"]["id"], "t1")

    def test_tool_message_updates_tool_call(self):
        from Django_xm.apps.chat.services.chat_service import ChatService
        tool_calls_map = {"t1": {"id": "t1", "name": "search", "state": "input-available"}}
        chunk = ToolMessage(content="result data", tool_call_id="t1")
        events = ChatService._process_stream_chunk(chunk, tool_calls_map, "")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "tool_result")
        self.assertEqual(events[0]["data"]["state"], "output-available")
        self.assertEqual(events[0]["data"]["result"], "result data")

    def test_tool_message_with_error(self):
        from Django_xm.apps.chat.services.chat_service import ChatService
        tool_calls_map = {"t1": {"id": "t1", "name": "search", "state": "input-available"}}
        chunk = ToolMessage(content="error msg", tool_call_id="t1", status="error")
        events = ChatService._process_stream_chunk(chunk, tool_calls_map, "")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["data"]["state"], "output-error")
        self.assertEqual(events[0]["data"]["error"], "error msg")

    def test_content_dedup_via_lcp(self):
        from Django_xm.apps.chat.services.chat_service import ChatService
        # 第一次收到部分内容
        chunk1 = AIMessage(content="Hello world")
        events1 = ChatService._process_stream_chunk(chunk1, {}, "")
        self.assertEqual(len(events1), 1)
        self.assertEqual(events1[0]["content"], "Hello world")

        # 第二次收到完整内容，应该只返回增量
        prev_content = "Hello world"
        chunk2 = AIMessage(content="Hello world and more")
        events2 = ChatService._process_stream_chunk(chunk2, {}, prev_content)
        self.assertEqual(len(events2), 1)
        self.assertEqual(events2[0]["content"], " and more")
