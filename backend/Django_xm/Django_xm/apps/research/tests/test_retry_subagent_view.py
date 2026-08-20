"""单独重启失败子代理 REST 端点测试（Task 3.5）。

覆盖 DeepResearchRetrySubagentView 的编排逻辑：
1. 非法参数（agent_path 为空/元素为空、tool_call_id 缺失）→ 400
2. 任务不存在或无权访问 → 404
3. 任务已完成 → 400（无法重试）
4. 目标子代理非失败态 → 400
5. 校验通过 → 发布重试信令 + 200 retry_scheduled

隔离策略：mock ResearchTask.objects / _validate_failed_subagent /
publish_retry_subagent_signal，不触碰真实 DB/Redis。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python -m unittest Django_xm.apps.research.tests.test_retry_subagent_view
"""

import os
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from rest_framework.test import APIRequestFactory, force_authenticate

from Django_xm.apps.research.views import DeepResearchRetrySubagentView

_VIEW_MODULE = "Django_xm.apps.research.views"


def _make_user(user_id=1):
    return SimpleNamespace(id=user_id, pk=user_id, is_authenticated=True)


def _make_task(status="failed", session_id=None):
    return SimpleNamespace(task_id="t1", status=status, session_id=session_id)


def _post(payload: dict, task_id="t1"):
    """构造已认证的 POST 请求并调用视图，返回 DRF Response。"""
    factory = APIRequestFactory()
    request = factory.post(f"/api/v1/research/tasks/{task_id}/retry-subagent/", data=payload, format="json")
    force_authenticate(request, user=_make_user())
    return DeepResearchRetrySubagentView.as_view()(request, task_id=task_id)


class RetrySubagentViewTests(unittest.TestCase):
    """视图编排：参数校验 → 任务归属 → 失败态校验 → 发布信令。"""

    def test_rejects_invalid_payload(self):
        """非法参数：agent_path 为空列表 / 元素为空 / tool_call_id 缺失 → 400。"""
        for payload in [
            {"agent_path": [], "tool_call_id": "c1"},
            {"agent_path": [""], "tool_call_id": "c1"},
            {"agent_path": ["main", "web-researcher"]},
            {},
        ]:
            with self.subTest(payload=payload):
                response = _post(payload)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.data["code"], 40002)  # VALIDATION_FAILED

    def test_rejects_missing_task(self):
        """任务不存在或无权访问 → 404。"""
        from Django_xm.apps.research.views import ResearchTask

        with mock.patch(
            f"{_VIEW_MODULE}.ResearchTask.objects.get",
            side_effect=ResearchTask.DoesNotExist,
        ):
            response = _post({"agent_path": ["main", "w"], "tool_call_id": "c1"})
        self.assertEqual(response.status_code, 404)

    def test_rejects_completed_task(self):
        """任务已完成：无法重试子代理 → 400。"""
        with mock.patch(f"{_VIEW_MODULE}.ResearchTask.objects.get", return_value=_make_task(status="completed")):
            response = _post({"agent_path": ["main", "w"], "tool_call_id": "c1"})
        self.assertEqual(response.status_code, 400)

    def test_rejects_non_failed_subagent(self):
        """目标子代理不存在或非失败态 → 400，且不发布信令。"""
        with mock.patch(
            f"{_VIEW_MODULE}.ResearchTask.objects.get", return_value=_make_task(status="failed")
        ), mock.patch.object(
            DeepResearchRetrySubagentView, "_validate_failed_subagent", return_value=None
        ) as validate, mock.patch(f"{_VIEW_MODULE}.publish_retry_subagent_signal") as publish:
            response = _post({"agent_path": ["main", "w"], "tool_call_id": "c1"})
        self.assertEqual(response.status_code, 400)
        validate.assert_called_once()
        publish.assert_not_called()

    def test_publishes_signal_on_success(self):
        """校验通过：发布重试信令（携带原始入参）并返回 200 retry_scheduled。"""
        task = _make_task(status="failed", session_id="s1")
        info = {"original_args": {"query": "原始子任务"}, "failed_tool_name": "task"}
        with mock.patch(f"{_VIEW_MODULE}.ResearchTask.objects.get", return_value=task), mock.patch.object(
            DeepResearchRetrySubagentView,
            "_validate_failed_subagent",
            return_value=info,
        ) as validate, mock.patch(f"{_VIEW_MODULE}.publish_retry_subagent_signal") as publish:
            response = _post({"agent_path": ["main", "web-researcher"], "tool_call_id": "c1"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["status"], "retry_scheduled")
        validate.assert_called_once()
        publish.assert_called_once()
        _, kwargs = publish.call_args
        self.assertEqual(kwargs["agent_path"], ["main", "web-researcher"])
        self.assertEqual(kwargs["tool_call_id"], "c1")
        self.assertEqual(kwargs["original_args"], {"query": "原始子任务"})
        self.assertEqual(kwargs["chat_session_id"], "s1")


class RetrySubagentValidationTests(unittest.TestCase):
    """_validate_failed_subagent 三级数据源判定（mock，不触碰 Redis/DB）。"""

    def test_lifecycle_context_is_authoritative(self):
        """lifecycle Redis 上下文匹配（module_id + agent_path + 失败终态）→ 返回原始入参。"""
        ctx = {
            "module_id": "t1",
            "agent_path": ["main", "web-researcher"],
            "last_event_type": "tool_call_failed",
            "tool_name": "task",
            "parent_tool_call_id": "p1",
            "parameters": {"fallback": True},
        }
        parent_ctx = {"parameters": {"query": "原始委派入参"}}
        lifecycle = mock.Mock()
        lifecycle.get_context.side_effect = lambda cid: parent_ctx if cid == "p1" else ctx
        with mock.patch("Django_xm.common.tool_call_lifecycle.service", lifecycle):
            info = DeepResearchRetrySubagentView._validate_failed_subagent(
                _make_task(), ["main", "web-researcher"], "c1"
            )
        self.assertEqual(info["original_args"], {"query": "原始委派入参"})
        self.assertEqual(info["failed_tool_name"], "task")

    def test_approval_fallback(self):
        """lifecycle 无上下文：Approval 记录匹配（tool_call_id + agent_path + 拒绝态）→ 返回入参。"""
        approval = SimpleNamespace(
            extra={"tool_call_id": "c1", "agent_path": ["main", "web-researcher"]},
            parameters={"query": "审批入参"},
            tool_name="task",
            state="rejected",
        )
        qs = mock.Mock()
        qs.filter.return_value = qs
        qs.order_by.return_value = qs
        qs.first.return_value = approval
        lifecycle = mock.Mock()
        lifecycle.get_context.return_value = None
        with mock.patch("Django_xm.common.tool_call_lifecycle.service", lifecycle), mock.patch(
            "Django_xm.apps.approvals.models.Approval.objects.filter", return_value=qs
        ):
            info = DeepResearchRetrySubagentView._validate_failed_subagent(
                _make_task(), ["main", "web-researcher"], "c1"
            )
        self.assertEqual(info["original_args"], {"query": "审批入参"})

    def test_returns_none_when_not_failed(self):
        """三级数据源均无匹配 → 返回 None（目标子代理非失败态）。"""
        lifecycle = mock.Mock()
        lifecycle.get_context.return_value = None
        with mock.patch("Django_xm.common.tool_call_lifecycle.service", lifecycle), mock.patch(
            "Django_xm.apps.approvals.models.Approval.objects.filter"
        ) as filt:
            filt.return_value.order_by.return_value.first.return_value = None
            info = DeepResearchRetrySubagentView._validate_failed_subagent(
                _make_task(), ["main", "web-researcher"], "c1"
            )
        self.assertIsNone(info)


if __name__ == "__main__":
    unittest.main()
