"""deep_chat_service 导入完整性单测。

防止 finalize_tool_calls UnboundLocalError 回归：
该函数必须在模块顶层导入，不能仅在条件分支内延迟导入。
"""

import os
import unittest


class TestDeepChatServiceImports(unittest.TestCase):
    """验证 deep_chat_service 模块的导入完整性。"""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.dev")
        import django

        django.setup()

    def test_finalize_tool_calls_imported_at_module_level(self):
        """finalize_tool_calls 必须在模块顶层可访问。

        根因：原实现仅在 if interrupt_info is not None 分支内延迟导入，
        导致第 357 行（分支外）调用时 UnboundLocalError。
        """
        from Django_xm.apps.chat.services import deep_chat_service

        self.assertTrue(
            hasattr(deep_chat_service, "finalize_tool_calls"),
            "finalize_tool_calls 必须在 deep_chat_service 模块顶层可访问，防止 UnboundLocalError 回归",
        )

    def test_stream_helpers_symbols_imported_at_module_level(self):
        """验证所有在分支外使用的 stream_helpers 符号已在顶层导入。"""
        from Django_xm.apps.chat.services import deep_chat_service

        # finalize_tool_calls 在 if 分支内外均被使用，必须在顶层
        self.assertTrue(hasattr(deep_chat_service, "finalize_tool_calls"))
        # process_stream_chunk 和 update_usage_and_tokens 已在顶层
        self.assertTrue(hasattr(deep_chat_service, "process_stream_chunk"))
        self.assertTrue(hasattr(deep_chat_service, "update_usage_and_tokens"))


if __name__ == "__main__":
    unittest.main()
