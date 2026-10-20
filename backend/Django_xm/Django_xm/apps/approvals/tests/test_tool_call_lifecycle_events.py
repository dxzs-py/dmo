"""tool_call 生命周期事件发布函数测试（G2，phase6a 重复审计）。

覆盖 approval_service 4 个工具事件发布函数（waiting / running / timeout / rejected，
G2 合并后为 _publish_tool_call_lifecycle_event 单实现 + 4 薄壳委托）：
- 每个事件路径断言经 tool_call_lifecycle.service.register + transition 到达底层
  publish_tool_call_sync 的事件 type 与 payload 字段正确（tool_call_id / tool_name /
  module / module_id / message_id / parameters / risk_level）
- 异常语义锁定：PayloadValidationError 上抛（审批事件丢失必须让上层感知）、
  其余异常静默吞掉（非致命，不阻断审批流程）

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.approvals.tests.test_tool_call_lifecycle_events \
        --settings=Django_xm.settings.test --noinput
"""

import os
from typing import Any
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.test import TestCase

from Django_xm.apps.approvals.models import Approval
from Django_xm.apps.approvals.services import approval_service
from Django_xm.apps.approvals.services.approval_service import (
    _publish_tool_call_running_event,
    _publish_tool_call_waiting_event,
    publish_tool_call_rejected_event,
    publish_tool_call_timeout_event,
)
from Django_xm.common.event_schema import EventSource, EventType, PayloadValidationError

# transition 内部模块级导入 publish_tool_call_sync，patch 其源模块单例属性即可拦截
PUBLISH_TARGET = "Django_xm.common.tool_call_lifecycle.publish_tool_call_sync"


def _make_tool_approval(interrupt_id: str, **extra: Any) -> Approval:
    """构造带 tool 事件所需字段的审批记录（extra 携带路由/风险等级）。"""
    merged_extra: dict[str, Any] = {
        "tool_call_id": interrupt_id,
        "message_id": f"msg-{interrupt_id}",
        "risk_level": "high",
        **extra,
    }
    return Approval.objects.create(
        interrupt_id=interrupt_id,
        source="chat",
        source_id=f"session-{interrupt_id}",
        chat_session_id=f"cs-{interrupt_id}",
        tool_name="shell_exec",
        state=Approval.State.PENDING,
        action=Approval.ACTION_CONFIRM,
        parameters={"cmd": "ls"},
        extra=merged_extra,
    )


class ToolCallLifecycleEventPublishTests(TestCase):
    """4 个事件路径：发布的事件 type 与 payload 字段正确。"""

    def _assert_published(self, publish_fn, expected_event: EventType) -> None:
        tool_call_id = f"tc-{expected_event.value}"
        approval = _make_tool_approval(tool_call_id)

        with mock.patch(PUBLISH_TARGET) as publish_mock:
            publish_fn(approval)

        publish_mock.assert_called_once()
        kwargs = publish_mock.call_args.kwargs
        self.assertEqual(kwargs["event_type"], expected_event)
        self.assertEqual(kwargs["tool_call_id"], tool_call_id)
        self.assertEqual(kwargs["tool_name"], "shell_exec")
        self.assertEqual(kwargs["module"], EventSource.CHAT)
        self.assertEqual(kwargs["module_id"], f"session-{tool_call_id}")
        self.assertEqual(kwargs["message_id"], f"msg-{tool_call_id}")
        self.assertEqual(kwargs["parameters"], {"cmd": "ls"})
        self.assertEqual(kwargs["risk_level"], "high")

    def test_waiting_event_publishes_tool_call_waiting(self) -> None:
        self._assert_published(
            _publish_tool_call_waiting_event, EventType.TOOL_CALL_WAITING
        )

    def test_running_event_publishes_tool_call_running(self) -> None:
        self._assert_published(
            _publish_tool_call_running_event, EventType.TOOL_CALL_RUNNING
        )

    def test_timeout_event_publishes_tool_call_timeout(self) -> None:
        self._assert_published(
            publish_tool_call_timeout_event, EventType.TOOL_CALL_TIMEOUT
        )

    def test_rejected_event_publishes_tool_call_rejected(self) -> None:
        self._assert_published(
            publish_tool_call_rejected_event, EventType.TOOL_CALL_REJECTED
        )

    def test_shells_delegate_to_single_implementation(self) -> None:
        """薄壳契约：4 个旧函数名逐一委托单实现，传入正确 event_type。"""
        approval = _make_tool_approval("tc-delegate")
        cases = [
            (publish_tool_call_timeout_event, EventType.TOOL_CALL_TIMEOUT),
            (publish_tool_call_rejected_event, EventType.TOOL_CALL_REJECTED),
            (_publish_tool_call_waiting_event, EventType.TOOL_CALL_WAITING),
            (_publish_tool_call_running_event, EventType.TOOL_CALL_RUNNING),
        ]
        with mock.patch.object(
            approval_service, "_publish_tool_call_lifecycle_event"
        ) as core_mock:
            for publish_fn, _evt in cases:
                publish_fn(approval)

        self.assertEqual(
            core_mock.call_args_list,
            [mock.call(approval, evt) for _fn, evt in cases],
        )


class ToolCallLifecycleEventErrorHandlingTests(TestCase):
    """异常语义锁定（try 块内路径）：PayloadValidationError 上抛、其余异常吞掉。

    注：build_approval_payload 位于 try 块之外（原版行为即如此），其异常
    一律直接上抛，不属于本组锁定范围；故 mock 点选 service.register。
    """

    def test_payload_validation_error_reraises(self) -> None:
        """PayloadValidationError：捕获后重新抛出（审批事件丢失让上层感知）。"""
        approval = _make_tool_approval("tc-err-validation")
        with mock.patch.object(
            approval_service.service,
            "register",
            side_effect=PayloadValidationError("bad payload"),
        ), self.assertRaises(PayloadValidationError):
            publish_tool_call_timeout_event(approval)

    def test_unexpected_error_is_swallowed(self) -> None:
        """其他异常：静默吞掉不阻断审批流程（logger.warning 分支）。"""
        approval = _make_tool_approval("tc-err-generic")
        with mock.patch.object(
            approval_service.service,
            "register",
            side_effect=RuntimeError("boom"),
        ):
            publish_tool_call_rejected_event(approval)  # 不应抛出
