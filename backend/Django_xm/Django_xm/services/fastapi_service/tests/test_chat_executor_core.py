"""chat_executor_core 异常路径广播测试（整改 A1.1 复现 + A1.3 修复）。

缺口证明（修复前，审计 F1-1）：
- chat 会话执行抛任意异常时，except 分支仅记日志，finally 恒广播
  STREAM_COMPLETED {success: True}（假成功）→ 用户侧空消息 + 无任何错误提示。
- 流内 error 事件被 stream_broadcast 白名单丢弃（见 apps.chat test_stream_broadcast）。

修复后行为：
- 异常分支经 build_user_facing_error 分类（error_code + 用户可行动中文文案），
  广播 data.success=False + error（文案）+ error_code；
- 成功路径 data 与现状完全一致：{success: True, stopped, incomplete}。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.services.fastapi_service.tests.test_chat_executor_core --noinput
    （⚠️ 禁止 python -m unittest 直跑：绕过 Django 测试运行器会连接真实 dev 库）
"""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from unittest.mock import AsyncMock, MagicMock, patch

from django.test import SimpleTestCase

from Django_xm.async_utils import run_async
from Django_xm.services.fastapi_service import chat_executor_core

_MODULE = "Django_xm.services.fastapi_service.chat_executor_core"
_PUBLISH_EVENT = "Django_xm.common.realtime_events.publish_event"
# 函数内 import 的依赖 patch 在源模块命名空间
_CHAT_SERVICE = "Django_xm.apps.chat.services.chat_service.ChatService"
_CHECKPOINTER = "Django_xm.apps.ai_engine.services.checkpointer_factory.release_async_checkpointer"
_CHAT_MESSAGE_OBJECTS = "Django_xm.apps.chat.models.ChatMessage.objects"
# B4 配套：终态收尾遗留审批批次（函数内 import，patch 在源模块命名空间）
_ABANDON = "Django_xm.apps.approvals.services.approval_batch.abandon_session_pending_approvals"


def _make_executor():
    executor = MagicMock()
    executor.check_stop_requested = MagicMock(return_value=False)
    executor._suspended = False
    executor._handle_chat_wait_suspend = AsyncMock()
    return executor


def _run_session(exc: Exception | None):
    """执行 run_chat_session：exc 非 None 时让 run_agent_session 抛出该异常。

    返回 publish_event 的 AsyncMock，供断言广播内容。所有 DB/Redis/LLM 依赖
    全 mock（SimpleTestCase 无 DB），仅验证执行器编排与广播语义。
    """
    executor = _make_executor()
    with patch(
        f"{_MODULE}.persist_chat_tool_calls", new_callable=AsyncMock
    ), patch(_CHAT_SERVICE) as mock_chat_service, patch(
        _PUBLISH_EVENT, new_callable=AsyncMock
    ) as mock_publish, patch(
        _CHECKPOINTER, new_callable=AsyncMock
    ), patch(
        _CHAT_MESSAGE_OBJECTS
    ) as mock_objects, patch(
        _ABANDON, return_value=0
    ):
        mock_objects.filter.return_value.first.return_value = None
        if exc is not None:
            mock_chat_service.return_value.run_agent_session = AsyncMock(side_effect=exc)
        else:
            mock_chat_service.return_value.run_agent_session = AsyncMock(return_value=None)
        run_async(
            chat_executor_core.run_chat_session(
                executor,
                {"thread_id": "s1", "message_id": "42", "user_id": 1, "message": "你好"},
            )
        )
    return mock_publish


def _find_completed_payload(mock_publish):
    """从广播调用中取 STREAM_COMPLETED payload（无则断言失败）。"""
    calls = [c for c in mock_publish.await_args_list if c.args[0].value == "stream_completed"]
    if not calls:
        raise AssertionError(
            f"未广播 stream_completed，实际广播事件: "
            f"{[c.args[0].value for c in mock_publish.await_args_list]}"
        )
    return calls[-1].args[1]


class TestRunChatSessionFailureBroadcast(SimpleTestCase):
    """chat 会话执行异常 → stream_completed 必须携带失败终态（A1.3）。"""

    def test_exception_broadcasts_success_false_with_classified_error(self):
        """任意异常 → data.success=False + error 文案 + error_code（不再恒假成功）。"""
        mock_publish = _run_session(RuntimeError("主模型不可用且无可用 fallback 模型"))
        payload = _find_completed_payload(mock_publish)
        data = payload["data"]
        self.assertIs(data["success"], False)
        self.assertTrue(data.get("error"), "失败终态必须携带用户可读错误文案")
        self.assertTrue(data.get("error_code"), "失败终态必须携带分类错误码")

    def test_timeout_exception_maps_to_model_call_error(self):
        """TimeoutError → MODEL_CALL_ERROR + 超时可行动文案（分类映射生效）。"""
        mock_publish = _run_session(TimeoutError("request timed out after 120s"))
        data = _find_completed_payload(mock_publish)["data"]
        self.assertIs(data["success"], False)
        self.assertEqual(data["error_code"], "MODEL_CALL_ERROR")
        self.assertEqual(data["error"], "请求超时，请稍后重试")

    def test_raw_exception_text_not_leaked(self):
        """失败终态 error 字段不透出裸异常原文（str(exc) 不直达用户）。"""
        raw_detail = "POST https://api.example.com/v1/chat failed with internal dict {x: 1}"
        mock_publish = _run_session(RuntimeError(raw_detail))
        data = _find_completed_payload(mock_publish)["data"]
        self.assertNotIn(raw_detail, str(data.get("error", "")))

    def test_user_stop_keeps_success_true_with_stopped_flag(self):
        """用户停止（CancelledError）不算失败：success=True + stopped=True（现状保持）。"""
        import asyncio

        mock_publish = _run_session(asyncio.CancelledError())
        data = _find_completed_payload(mock_publish)["data"]
        self.assertIs(data["success"], True)
        self.assertIs(data["stopped"], True)


class TestRunChatSessionSuccessBroadcast(SimpleTestCase):
    """成功路径回归保护：data 结构与修复前完全一致。"""

    def test_normal_completion_broadcasts_success_true_without_error(self):
        mock_publish = _run_session(None)
        data = _find_completed_payload(mock_publish)["data"]
        self.assertIs(data["success"], True)
        self.assertNotIn("error", data)
        self.assertNotIn("error_code", data)
