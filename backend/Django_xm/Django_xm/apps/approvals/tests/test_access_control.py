"""审批越权访问控制测试。

覆盖 Task 1 修复的越权漏洞：
- 用户 A 不能读取用户 B 的审批列表 / 详情 / 状态
- 用户 A 不能恢复 / 拒绝用户 B 的审批
- user 字段为 NULL 的历史数据通过三路关联 fallback 校验：
  * chat_session_id → ChatSession.user
  * source='deep_research' → ResearchTask.created_by
  * source='learning' → WorkflowSession.created_by
"""

from unittest.mock import patch

from rest_framework.test import APITestCase

from Django_xm.apps.approvals.models import Approval
from Django_xm.apps.chat.models import ChatSession
from Django_xm.apps.learning.models import WorkflowSession
from Django_xm.apps.research.models import ResearchTask
from Django_xm.apps.users.models import User


def _make_approval(**kwargs):
    """创建测试用 Approval 记录，提供合理默认值。

    默认 user=None 以模拟历史数据；调用方按需传入 user=... 显式归属。
    """
    defaults = {
        "interrupt_id": "test-interrupt-id",
        "source": Approval.SOURCE_CHAT,
        "source_id": "test-source-id",
        "chat_session_id": "test-chat-session-id",
        "tool_name": "shell_exec",
        "title": "确认执行",
        "description": "执行 shell 命令",
        "action": Approval.ACTION_CONFIRM,
        "operation": "rm -rf /tmp/test",
        "danger_level": "high",
        "parameters": {"command": "rm -rf /tmp/test"},
        "state": Approval.STATE_PENDING,
        "extra": {},
    }
    defaults.update(kwargs)
    return Approval.objects.create(**defaults)


def _create_chat_session(session_id, user, title="新对话"):
    """创建测试用 ChatSession。"""
    return ChatSession.objects.create(
        session_id=session_id,
        user=user,
        title=title,
        mode="agent",
        created_by=user,
    )


def _create_workflow_session(thread_id, user, user_question="test question"):
    """创建测试用 WorkflowSession。"""
    return WorkflowSession.objects.create(
        thread_id=thread_id,
        user_question=user_question,
        status="running",
        created_by=user,
    )


def _create_research_task(task_id, user, query="test query"):
    """创建测试用 ResearchTask。"""
    return ResearchTask.objects.create(
        task_id=task_id,
        query=query,
        status="pending",
        created_by=user,
    )


class ApprovalAccessControlTestBase(APITestCase):
    """审批越权测试公共 setUp。"""

    @classmethod
    def setUpTestData(cls):
        cls.user_a = User.objects.create_user(
            username="approval-owner-a",
            password="testpass123",
        )
        cls.user_b = User.objects.create_user(
            username="approval-owner-b",
            password="testpass123",
        )


class ApprovalListViewAccessTests(ApprovalAccessControlTestBase):
    """ApprovalListView 越权测试：GET /api/v1/approvals/。"""

    def setUp(self):
        super().setUp()
        # user_a 拥有的审批
        self.approval_a1 = _make_approval(
            interrupt_id="a-int-001",
            source_id="a-src-001",
            chat_session_id="a-sess-001",
            state=Approval.STATE_PENDING,
            user=self.user_a,
        )
        self.approval_a2 = _make_approval(
            interrupt_id="a-int-002",
            source_id="a-src-002",
            chat_session_id="a-sess-002",
            state=Approval.STATE_APPROVED,
            user=self.user_a,
        )
        # user_b 拥有的审批
        self.approval_b1 = _make_approval(
            interrupt_id="b-int-001",
            source_id="b-src-001",
            chat_session_id="b-sess-001",
            state=Approval.STATE_PENDING,
            user=self.user_b,
        )

    def test_user_a_list_excludes_user_b_approvals(self):
        """用户 A 列表只返回自己的审批，不包含用户 B 的。"""
        self.client.force_authenticate(user=self.user_a)
        resp = self.client.get("/api/v1/approvals/")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["code"], 200)
        interrupt_ids = [item["interrupt_id"] for item in body["data"]]
        self.assertIn("a-int-001", interrupt_ids)
        self.assertIn("a-int-002", interrupt_ids)
        self.assertNotIn("b-int-001", interrupt_ids)

    def test_user_b_list_excludes_user_a_approvals(self):
        """用户 B 列表只返回自己的审批，不包含用户 A 的。"""
        self.client.force_authenticate(user=self.user_b)
        resp = self.client.get("/api/v1/approvals/")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        interrupt_ids = [item["interrupt_id"] for item in body["data"]]
        self.assertIn("b-int-001", interrupt_ids)
        self.assertNotIn("a-int-001", interrupt_ids)
        self.assertNotIn("a-int-002", interrupt_ids)

    def test_user_null_approval_excluded_from_list(self):
        """user 字段为 NULL 的历史审批不通过列表接口暴露（无归属）。"""
        _make_approval(interrupt_id="null-int-001", user=None)
        self.client.force_authenticate(user=self.user_a)
        resp = self.client.get("/api/v1/approvals/")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        interrupt_ids = [item["interrupt_id"] for item in body["data"]]
        self.assertNotIn("null-int-001", interrupt_ids)

    def test_unauthenticated_returns_401(self):
        """未认证请求返回 401。"""
        self.client.force_authenticate(user=None)
        resp = self.client.get("/api/v1/approvals/")
        self.assertEqual(resp.status_code, 401)


class ApprovalDetailViewAccessTests(ApprovalAccessControlTestBase):
    """ApprovalDetailView 越权测试：GET /api/v1/approvals/{interrupt_id}/。"""

    def setUp(self):
        super().setUp()
        self.approval_a = _make_approval(
            interrupt_id="detail-a-001",
            user=self.user_a,
        )

    def test_user_a_can_read_own_approval(self):
        """用户 A 可以读取自己的审批详情。"""
        self.client.force_authenticate(user=self.user_a)
        resp = self.client.get(f"/api/v1/approvals/{self.approval_a.interrupt_id}/")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["code"], 200)
        self.assertEqual(body["data"]["interrupt_id"], "detail-a-001")

    def test_user_b_cannot_read_user_a_approval(self):
        """用户 B 不能读取用户 A 的审批详情，返回 403。"""
        self.client.force_authenticate(user=self.user_b)
        resp = self.client.get(f"/api/v1/approvals/{self.approval_a.interrupt_id}/")
        self.assertEqual(resp.status_code, 403)
        body = resp.json()
        self.assertEqual(body["code"], 40302)

    def test_nonexistent_returns_404(self):
        """不存在的 interrupt_id 返回 404。"""
        self.client.force_authenticate(user=self.user_a)
        resp = self.client.get("/api/v1/approvals/nonexistent-id/")
        self.assertEqual(resp.status_code, 404)
        body = resp.json()
        self.assertEqual(body["code"], 40401)


class ApprovalResumeViewAccessTests(ApprovalAccessControlTestBase):
    """ApprovalResumeView 越权测试：POST /api/v1/approvals/{interrupt_id}/resume/。"""

    def setUp(self):
        super().setUp()
        self.approval_a = _make_approval(
            interrupt_id="resume-a-001",
            source=Approval.SOURCE_CHAT,
            source_id="resume-a-sess",
            chat_session_id="resume-a-sess",
            state=Approval.STATE_PENDING,
            user=self.user_a,
        )

    @patch("Django_xm.apps.approvals.views.approval_service.resume_approval")
    def test_user_a_resume_passes_ownership_check(self, mock_resume):
        """用户 A 恢复自己的审批：越权校验通过，service 被调用。"""
        # 通过 side_effect 让 service 抛 ValueError 以终止后续 SSE 流程
        mock_resume.side_effect = ValueError("mock: state not pending")

        self.client.force_authenticate(user=self.user_a)
        resp = self.client.post(
            f"/api/v1/approvals/{self.approval_a.interrupt_id}/resume/",
            {"approved": True},
            format="json",
        )

        # 越权校验通过 → service 被调用 → ValueError → 400
        self.assertNotEqual(resp.status_code, 403)
        mock_resume.assert_called_once()

    @patch("Django_xm.apps.approvals.views.approval_service.resume_approval")
    def test_user_b_cannot_resume_user_a_approval(self, mock_resume):
        """用户 B 恢复用户 A 的审批：返回 403，service 不被调用。"""
        self.client.force_authenticate(user=self.user_b)
        resp = self.client.post(
            f"/api/v1/approvals/{self.approval_a.interrupt_id}/resume/",
            {"approved": True},
            format="json",
        )

        self.assertEqual(resp.status_code, 403)
        body = resp.json()
        self.assertEqual(body["code"], 40302)
        mock_resume.assert_not_called()


class ApprovalRejectViewAccessTests(ApprovalAccessControlTestBase):
    """ApprovalRejectView 越权测试：POST /api/v1/approvals/{interrupt_id}/reject/。"""

    def setUp(self):
        super().setUp()
        self.approval_a = _make_approval(
            interrupt_id="reject-a-001",
            source=Approval.SOURCE_CHAT,
            source_id="reject-a-sess",
            chat_session_id="reject-a-sess",
            state=Approval.STATE_PENDING,
            user=self.user_a,
        )

    @patch("Django_xm.apps.approvals.views.approval_service.resume_approval")
    def test_user_a_reject_passes_ownership_check(self, mock_resume):
        """用户 A 拒绝自己的审批：越权校验通过，service 被调用。"""
        mock_resume.side_effect = ValueError("mock: state not pending")

        self.client.force_authenticate(user=self.user_a)
        resp = self.client.post(
            f"/api/v1/approvals/{self.approval_a.interrupt_id}/reject/",
            format="json",
        )

        self.assertNotEqual(resp.status_code, 403)
        mock_resume.assert_called_once()

    @patch("Django_xm.apps.approvals.views.approval_service.resume_approval")
    def test_user_b_cannot_reject_user_a_approval(self, mock_resume):
        """用户 B 拒绝用户 A 的审批：返回 403，service 不被调用。"""
        self.client.force_authenticate(user=self.user_b)
        resp = self.client.post(
            f"/api/v1/approvals/{self.approval_a.interrupt_id}/reject/",
            format="json",
        )

        self.assertEqual(resp.status_code, 403)
        body = resp.json()
        self.assertEqual(body["code"], 40302)
        mock_resume.assert_not_called()


class ApprovalStateViewAccessTests(ApprovalAccessControlTestBase):
    """ApprovalStateView 越权测试：GET /api/v1/approvals/{interrupt_id}/state/。"""

    def setUp(self):
        super().setUp()
        self.approval_a = _make_approval(
            interrupt_id="state-a-001",
            user=self.user_a,
        )

    def test_user_a_can_read_own_state(self):
        """用户 A 可以查询自己审批的状态。"""
        self.client.force_authenticate(user=self.user_a)
        resp = self.client.get(f"/api/v1/approvals/{self.approval_a.interrupt_id}/state/")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["code"], 200)
        self.assertEqual(body["data"]["interrupt_id"], "state-a-001")

    def test_user_b_cannot_read_user_a_state(self):
        """用户 B 不能查询用户 A 审批的状态，返回 403。"""
        self.client.force_authenticate(user=self.user_b)
        resp = self.client.get(f"/api/v1/approvals/{self.approval_a.interrupt_id}/state/")
        self.assertEqual(resp.status_code, 403)
        body = resp.json()
        self.assertEqual(body["code"], 40302)


class ApprovalFallbackChatSessionTests(ApprovalAccessControlTestBase):
    """user=NULL 历史数据 fallback 测试：通过 chat_session_id 关联回退校验。"""

    def setUp(self):
        super().setUp()
        # user_a 拥有的 ChatSession
        self.chat_session_a = _create_chat_session(
            session_id="fallback-chat-sess-a",
            user=self.user_a,
        )
        # user=NULL 的历史审批，chat_session_id 指向 user_a 的会话
        self.approval_null = _make_approval(
            interrupt_id="fallback-chat-001",
            source=Approval.SOURCE_CHAT,
            source_id="fallback-chat-sess-a",
            chat_session_id="fallback-chat-sess-a",
            user=None,
        )

    def test_user_a_can_read_via_fallback(self):
        """归属用户 A 通过 chat_session fallback 校验通过。"""
        self.client.force_authenticate(user=self.user_a)
        resp = self.client.get(f"/api/v1/approvals/{self.approval_null.interrupt_id}/")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["code"], 200)

    def test_user_b_rejected_via_fallback(self):
        """用户 B 因 fallback 关联失败被拒绝，返回 403。"""
        self.client.force_authenticate(user=self.user_b)
        resp = self.client.get(f"/api/v1/approvals/{self.approval_null.interrupt_id}/")
        self.assertEqual(resp.status_code, 403)
        body = resp.json()
        self.assertEqual(body["code"], 40302)


class ApprovalFallbackDeepResearchTests(ApprovalAccessControlTestBase):
    """user=NULL 历史数据 fallback 测试：通过 ResearchTask.created_by 关联回退校验。"""

    def setUp(self):
        super().setUp()
        # user_a 拥有的 ResearchTask
        self.research_task_a = _create_research_task(
            task_id="fallback-research-task-a",
            user=self.user_a,
        )
        # user=NULL 的深度研究审批，source_id 指向 user_a 的 task_id
        self.approval_null = _make_approval(
            interrupt_id="fallback-research-001",
            source=Approval.SOURCE_DEEP_RESEARCH,
            source_id="fallback-research-task-a",
            chat_session_id=None,
            user=None,
        )

    def test_user_a_can_read_via_fallback(self):
        """归属用户 A 通过 ResearchTask.created_by fallback 校验通过。"""
        self.client.force_authenticate(user=self.user_a)
        resp = self.client.get(f"/api/v1/approvals/{self.approval_null.interrupt_id}/")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["code"], 200)

    def test_user_b_rejected_via_fallback(self):
        """用户 B 因 ResearchTask fallback 关联失败被拒绝，返回 403。"""
        self.client.force_authenticate(user=self.user_b)
        resp = self.client.get(f"/api/v1/approvals/{self.approval_null.interrupt_id}/")
        self.assertEqual(resp.status_code, 403)


class ApprovalFallbackLearningTests(ApprovalAccessControlTestBase):
    """user=NULL 历史数据 fallback 测试：通过 WorkflowSession.created_by 关联回退校验。"""

    def setUp(self):
        super().setUp()
        # user_a 拥有的 WorkflowSession
        self.workflow_session_a = _create_workflow_session(
            thread_id="fallback-learning-thread-a",
            user=self.user_a,
        )
        # user=NULL 的 learning 审批，source_id 指向 user_a 的 thread_id
        self.approval_null = _make_approval(
            interrupt_id="fallback-learning-001",
            source=Approval.SOURCE_LEARNING,
            source_id="fallback-learning-thread-a",
            chat_session_id=None,
            user=None,
        )

    def test_user_a_can_read_via_fallback(self):
        """归属用户 A 通过 WorkflowSession.created_by fallback 校验通过。"""
        self.client.force_authenticate(user=self.user_a)
        resp = self.client.get(f"/api/v1/approvals/{self.approval_null.interrupt_id}/")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["code"], 200)

    def test_user_b_rejected_via_fallback(self):
        """用户 B 因 WorkflowSession fallback 关联失败被拒绝，返回 403。"""
        self.client.force_authenticate(user=self.user_b)
        resp = self.client.get(f"/api/v1/approvals/{self.approval_null.interrupt_id}/")
        self.assertEqual(resp.status_code, 403)


class ApprovalMigrationBackfillTests(ApprovalAccessControlTestBase):
    """验证数据回填迁移逻辑（直接调用 mixin._user_owns_approval 等价路径）。"""

    def test_owned_approval_query_prefers_user_field(self):
        """_get_owned_approval 优先按 user 字段查询。"""
        from Django_xm.apps.approvals.mixins import BaseApprovalAccessMixin

        _make_approval(
            interrupt_id="owned-query-001",
            user=self.user_a,
        )
        result = BaseApprovalAccessMixin._get_owned_approval("owned-query-001", self.user_a)
        self.assertIsNotNone(result)
        self.assertEqual(result.interrupt_id, "owned-query-001")

    def test_owned_approval_query_returns_none_for_other_user(self):
        """user=A 的审批，user=B 查询返回 None。"""
        from Django_xm.apps.approvals.mixins import BaseApprovalAccessMixin

        _make_approval(interrupt_id="owned-query-002", user=self.user_a)
        result = BaseApprovalAccessMixin._get_owned_approval("owned-query-002", self.user_b)
        self.assertIsNone(result)

    def test_assert_ownership_raises_for_other_user(self):
        """_assert_ownership 对非归属用户抛 PermissionDenied。"""
        from django.core.exceptions import PermissionDenied

        from Django_xm.apps.approvals.mixins import BaseApprovalAccessMixin

        approval = _make_approval(
            interrupt_id="assert-001",
            user=self.user_a,
        )
        # 归属用户通过
        BaseApprovalAccessMixin._assert_ownership(approval, self.user_a)
        # 非归属用户抛异常
        with self.assertRaises(PermissionDenied):
            BaseApprovalAccessMixin._assert_ownership(approval, self.user_b)
        # None 抛异常
        with self.assertRaises(PermissionDenied):
            BaseApprovalAccessMixin._assert_ownership(None, self.user_a)
