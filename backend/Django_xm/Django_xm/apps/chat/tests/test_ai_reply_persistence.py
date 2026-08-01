"""AI 回复完整持久化单元测试。

覆盖 spec ``fix-tool-approval-and-cross-browser-sync-integrity`` Task 3/4：

1. ``persist_stream_result``：stream 产生多 chunk，最终 ``Message.content`` 含所有 chunk 拼接
2. content 覆盖策略：后端更长时覆盖，前端等长或更长时保留前端版本
3. 完整 content 含尾部段落（"✅ 全部完成！"段不丢失）

修复背景：原后端 stream 结束时仅更新 token 字段，不持久化 content/tool_calls，
依赖前端 ``syncLastMessageToBackend`` PATCH 同步。若前端只同步部分内容，
非触发浏览器刷新后拉取的 ``Message.content`` 不完整（缺少后半段）。
现 ``finalize_stream`` 末尾调用 ``persist_stream_result`` 持久化完整 content。

mock 策略:
- Django TestCase + 真实 SQLite DB
- ``publish_event`` 被 patch 为 AsyncMock，隔离 Redis/Channels 依赖

运行方式:
    cd backend/Django_xm
    conda activate langchain_xm
    python -m pytest Django_xm/apps/chat/tests/test_ai_reply_persistence.py -v
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, patch

# Django 环境初始化（兼容 pytest 和 unittest 直接运行）
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.dev")
import django
import django.apps

if not django.apps.apps.ready:
    django.setup()

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import TestCase

from Django_xm.apps.chat.models import ChatMessage, ChatSession
from Django_xm.apps.chat.services.stream_helpers import (
    persist_stream_result,
)

User = get_user_model()


# 模拟 stream 产生的多 chunk 内容（复现"✅ 全部完成！"段丢失场景）
EXPECTED_FINAL_CONTENT = """## 执行结果

已运行 `ollama list` 和 `ollama ps`，结果如下：

- qwen3:8b 模型已安装（4.9GB）
- 当前未运行任何模型

✅ 全部完成！报告已写入 /tmp/report.md"""


class PersistStreamResultContentTests(TestCase):
    """persist_stream_result：AI 回复完整 content 持久化。"""

    def setUp(self):
        self.user = User.objects.create_user(username="testuser2", password="pass")
        self.session = ChatSession.objects.create(
            session_id="test-session-ai-reply",
            user=self.user,
            title="测试AI回复持久化",
            mode="agent",
        )
        # 创建空的 assistant Message（模拟前端占位）
        self.assistant_msg = ChatMessage.objects.create(
            session=self.session,
            role="assistant",
            content="",
            tool_calls=[],
        )

    @patch("Django_xm.common.realtime_events.publish_event", new_callable=AsyncMock)
    def test_persists_complete_content_with_tail_segment(self, _mock_publish):
        """完整 content（含"✅ 全部完成！"尾部段）持久化到 Message.content。"""
        # 模拟前端只同步了不完整内容（缺少尾部段）
        self.assistant_msg.content = "## 执行结果\n\n已运行 ollama list 和 ollama ps"
        self.assistant_msg.save(update_fields=["content"])

        # 后端 stream 累积的完整 content（含尾部段）
        async_to_sync(persist_stream_result)(
            session_id=self.session.session_id,
            user_id=self.user.id,
            content=EXPECTED_FINAL_CONTENT,
            tool_calls_map={},
            message_id=str(self.assistant_msg.id),
        )

        self.assistant_msg.refresh_from_db()
        # 后端 content 更长 → 覆盖前端不完整内容
        self.assertEqual(self.assistant_msg.content, EXPECTED_FINAL_CONTENT)
        # 验证尾部段不丢失
        self.assertIn("✅ 全部完成！", self.assistant_msg.content)
        self.assertIn("报告已写入 /tmp/report.md", self.assistant_msg.content)

    @patch("Django_xm.common.realtime_events.publish_event", new_callable=AsyncMock)
    def test_does_not_overwrite_when_frontend_already_complete(self, _mock_publish):
        """前端已同步等长或更长内容时，后端不覆盖（避免覆盖前端更完整版本）。"""
        # 前端已同步完整内容（与后端等长）
        frontend_content = EXPECTED_FINAL_CONTENT
        self.assistant_msg.content = frontend_content
        self.assistant_msg.save(update_fields=["content"])

        # 后端 stream 累积的内容（等长，不应覆盖）
        async_to_sync(persist_stream_result)(
            session_id=self.session.session_id,
            user_id=self.user.id,
            content=EXPECTED_FINAL_CONTENT,
            tool_calls_map={},
            message_id=str(self.assistant_msg.id),
        )

        self.assistant_msg.refresh_from_db()
        # 等长，保留前端版本（不变）
        self.assertEqual(self.assistant_msg.content, frontend_content)

    @patch("Django_xm.common.realtime_events.publish_event", new_callable=AsyncMock)
    def test_does_not_overwrite_when_frontend_longer(self, _mock_publish):
        """前端内容比后端更长时，保留前端版本（后端不覆盖）。"""
        # 前端同步了超长内容（如含用户编辑的补充）
        frontend_content = EXPECTED_FINAL_CONTENT + "\n\n## 补充说明\n用户手动补充的内容"
        self.assistant_msg.content = frontend_content
        self.assistant_msg.save(update_fields=["content"])

        # 后端 stream 累积的内容更短
        async_to_sync(persist_stream_result)(
            session_id=self.session.session_id,
            user_id=self.user.id,
            content=EXPECTED_FINAL_CONTENT,
            tool_calls_map={},
            message_id=str(self.assistant_msg.id),
        )

        self.assistant_msg.refresh_from_db()
        # 前端更长，保留前端版本
        self.assertEqual(self.assistant_msg.content, frontend_content)
        self.assertIn("用户手动补充的内容", self.assistant_msg.content)

    @patch("Django_xm.common.realtime_events.publish_event", new_callable=AsyncMock)
    def test_persists_content_when_message_empty(self, _mock_publish):
        """Message.content 为空时，后端 content 直接写入。"""
        # 模拟前端未同步（content 为空）
        self.assistant_msg.content = ""
        self.assistant_msg.save(update_fields=["content"])

        async_to_sync(persist_stream_result)(
            session_id=self.session.session_id,
            user_id=self.user.id,
            content=EXPECTED_FINAL_CONTENT,
            tool_calls_map={},
            message_id=str(self.assistant_msg.id),
        )

        self.assistant_msg.refresh_from_db()
        self.assertEqual(self.assistant_msg.content, EXPECTED_FINAL_CONTENT)

    @patch("Django_xm.common.realtime_events.publish_event", new_callable=AsyncMock)
    def test_simulate_multi_chunk_stream_accumulation(self, _mock_publish):
        """模拟 stream 产生多 chunk，最终 content 含所有 chunk 拼接。

        复现今晨场景：触发浏览器 SSE 收到完整 AI 回复，
        非触发浏览器刷新后拉取应得到相同完整内容。
        """
        # 模拟 stream 多 chunk 累积（finalizer 中 ctx.current_message_content 的累积结果）
        chunks = [
            "## 执行结果\n\n",
            "已运行 `ollama list` 和 `ollama ps`，结果如下：\n\n",
            "- qwen3:8b 模型已安装（4.9GB）\n",
            "- 当前未运行任何模型\n\n",
            "✅ 全部完成！报告已写入 /tmp/report.md",
        ]
        # finalizer 累积的最终 content（模拟 ctx.current_message_content += chunk）
        accumulated_content = "".join(chunks)

        # 后端持久化累积的完整 content
        async_to_sync(persist_stream_result)(
            session_id=self.session.session_id,
            user_id=self.user.id,
            content=accumulated_content,
            tool_calls_map={},
            message_id=str(self.assistant_msg.id),
        )

        self.assistant_msg.refresh_from_db()
        # 断言：持久化的 content 含所有 chunk 拼接
        self.assertEqual(self.assistant_msg.content, accumulated_content)
        # 断言：尾部段不丢失（修复 3 核心断言）
        self.assertIn("✅ 全部完成！", self.assistant_msg.content)
        self.assertIn("报告已写入 /tmp/report.md", self.assistant_msg.content)
        # 断言：开头段也在
        self.assertIn("## 执行结果", self.assistant_msg.content)

    @patch("Django_xm.common.realtime_events.publish_event", new_callable=AsyncMock)
    def test_broadcasts_message_updated_event(self, mock_publish):
        """持久化后发布 MESSAGE_UPDATED 事件，通知非触发浏览器拉取完整数据。"""
        async_to_sync(persist_stream_result)(
            session_id=self.session.session_id,
            user_id=self.user.id,
            content=EXPECTED_FINAL_CONTENT,
            tool_calls_map={},
            message_id=str(self.assistant_msg.id),
        )

        # 断言：publish_event 被调用（通知非触发浏览器）
        self.assertGreater(mock_publish.call_count, 0)


if __name__ == "__main__":
    unittest.main()
