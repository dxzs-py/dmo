"""聊天 Serializer 校验完整性测试（Task 13.4）。

覆盖 spec `fix-backend-audit-findings` SubTask 13.4：
- ChatRequestSerializer.selected_mcp_servers / selected_tools 每个元素 max_length=50

运行方式：
    cd d:\\programming\\langchain\\langchain_xm\\backend\\Django_xm
    D:\\Anaconda_envs\\envs\\langchain_xm\\python.exe -m pytest \
        Django_xm/apps/chat/tests/test_serializer_validation.py -v --tb=short
"""

from __future__ import annotations

import os
import unittest

# Django 环境初始化（兼容 pytest 和 unittest 直接运行）
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.dev")
import django
import django.apps

if not django.apps.apps.ready:
    django.setup()

from Django_xm.apps.chat.serializers import ChatRequestSerializer


class ChatRequestSerializerMcpServersValidationTest(unittest.TestCase):
    """ChatRequestSerializer.selected_mcp_servers 校验测试。"""

    def test_valid_short_names_passes(self) -> None:
        """合法短名称列表通过校验。"""
        s = ChatRequestSerializer(
            data={
                "message": "hi",
                "selected_mcp_servers": ["server-a", "server-b"],
            }
        )
        self.assertTrue(s.is_valid(), msg=str(s.errors))

    def test_name_at_max_length_passes(self) -> None:
        """正好 50 字符的名称通过校验。"""
        name = "s" * 50
        s = ChatRequestSerializer(
            data={
                "message": "hi",
                "selected_mcp_servers": [name],
            }
        )
        self.assertTrue(s.is_valid(), msg=str(s.errors))

    def test_name_above_max_length_rejected(self) -> None:
        """超过 50 字符的名称被拒绝。"""
        long_name = "s" * 51
        s = ChatRequestSerializer(
            data={
                "message": "hi",
                "selected_mcp_servers": [long_name],
            }
        )
        self.assertFalse(s.is_valid())
        self.assertIn("selected_mcp_servers", s.errors)

    def test_one_of_many_too_long_rejected(self) -> None:
        """列表中只要有一个超长名称就被拒绝。"""
        s = ChatRequestSerializer(
            data={
                "message": "hi",
                "selected_mcp_servers": ["ok-name", "x" * 51, "also-ok"],
            }
        )
        self.assertFalse(s.is_valid())
        self.assertIn("selected_mcp_servers", s.errors)

    def test_empty_list_passes(self) -> None:
        """空列表通过校验。"""
        s = ChatRequestSerializer(
            data={
                "message": "hi",
                "selected_mcp_servers": [],
            }
        )
        self.assertTrue(s.is_valid(), msg=str(s.errors))


class ChatRequestSerializerToolsValidationTest(unittest.TestCase):
    """ChatRequestSerializer.selected_tools 校验测试。"""

    def test_valid_short_names_passes(self) -> None:
        """合法短名称列表通过校验。"""
        s = ChatRequestSerializer(
            data={
                "message": "hi",
                "selected_tools": ["web_search", "calculator"],
            }
        )
        self.assertTrue(s.is_valid(), msg=str(s.errors))

    def test_name_at_max_length_passes(self) -> None:
        """正好 50 字符的名称通过校验。"""
        name = "t" * 50
        s = ChatRequestSerializer(
            data={
                "message": "hi",
                "selected_tools": [name],
            }
        )
        self.assertTrue(s.is_valid(), msg=str(s.errors))

    def test_name_above_max_length_rejected(self) -> None:
        """超过 50 字符的名称被拒绝。"""
        long_name = "t" * 51
        s = ChatRequestSerializer(
            data={
                "message": "hi",
                "selected_tools": [long_name],
            }
        )
        self.assertFalse(s.is_valid())
        self.assertIn("selected_tools", s.errors)

    def test_one_of_many_too_long_rejected(self) -> None:
        """列表中只要有一个超长名称就被拒绝。"""
        s = ChatRequestSerializer(
            data={
                "message": "hi",
                "selected_tools": ["ok", "y" * 51],
            }
        )
        self.assertFalse(s.is_valid())
        self.assertIn("selected_tools", s.errors)


class ChatRequestSerializerMessageValidationTest(unittest.TestCase):
    """ChatRequestSerializer.message 基础校验测试（验证整体加载无回归）。"""

    def test_valid_message_passes(self) -> None:
        """合法 message 通过校验。"""
        s = ChatRequestSerializer(data={"message": "hello"})
        self.assertTrue(s.is_valid(), msg=str(s.errors))

    def test_empty_message_rejected(self) -> None:
        """空 message 被拒绝。"""
        s = ChatRequestSerializer(data={"message": ""})
        self.assertFalse(s.is_valid())
        self.assertIn("message", s.errors)

    def test_too_long_message_rejected(self) -> None:
        """超长 message 被拒绝。"""
        s = ChatRequestSerializer(data={"message": "a" * 10001})
        self.assertFalse(s.is_valid())
        self.assertIn("message", s.errors)


if __name__ == "__main__":
    unittest.main()
