"""审批序列化器安全测试（Task 2）。

覆盖：
- ``ApprovalReadSerializer`` 全字段 read_only
- ``ApprovalWriteSerializer`` 白名单字段（approved / user_input）
- ``ApprovalWriteSerializer`` 拒绝 protected 字段（state / parameters / approved_by / source / action / extra 等）
- ``ApprovalWriteSerializer`` 字段类型与长度校验
- ``ApprovalResumeView`` / ``ApprovalRejectView`` 端到端：客户端提交 protected 字段返回 400
"""

from unittest.mock import patch

from rest_framework.test import APITestCase

from Django_xm.apps.approvals.models import Approval
from Django_xm.apps.approvals.serializers import (
    ApprovalReadSerializer,
    ApprovalWriteSerializer,
)
from Django_xm.apps.users.models import User


def _make_approval(**kwargs):
    """创建测试用 Approval 记录。"""
    defaults = {
        "interrupt_id": "serializer-test-001",
        "source": Approval.SOURCE_CHAT,
        "source_id": "serializer-src-001",
        "chat_session_id": "serializer-sess-001",
        "tool_name": "shell_exec",
        "title": "确认执行",
        "description": "执行 shell 命令",
        "action": Approval.ACTION_CONFIRM,
        "operation": "rm -rf /tmp/test",
        "danger_level": "high",
        "parameters": {"command": "rm -rf /tmp/test"},
        "state": Approval.STATE_PENDING,
        "extra": {"graph_interrupt_id": "gid-001"},
    }
    defaults.update(kwargs)
    return Approval.objects.create(**defaults)


class ApprovalReadSerializerTests(APITestCase):
    """ApprovalReadSerializer 行为测试。"""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="read-ser-user",
            password="testpass123",
        )
        cls.approval = _make_approval(
            interrupt_id="read-001",
            user=cls.user,
            approved_by=cls.user,
        )

    def test_serializer_exposes_all_display_fields(self):
        """序列化结果包含所有展示字段。"""
        data = ApprovalReadSerializer(self.approval).data
        expected_fields = {
            "interrupt_id",
            "source",
            "source_id",
            "chat_session_id",
            "tool_name",
            "title",
            "description",
            "action",
            "operation",
            "danger_level",
            "parameters",
            "state",
            "user_input",
            "approved_by",
            "extra",
            "created_at",
            "resolved_at",
        }
        self.assertEqual(set(data.keys()), expected_fields)

    def test_serializer_includes_nested_approved_by(self):
        """approved_by 字段为嵌套对象（id + username）。"""
        data = ApprovalReadSerializer(self.approval).data
        self.assertIsInstance(data["approved_by"], dict)
        self.assertEqual(data["approved_by"]["id"], self.user.id)
        self.assertEqual(data["approved_by"]["username"], "read-ser-user")

    def test_serializer_does_not_expose_user_field(self):
        """user 字段（外键）不在序列化输出中暴露（内部归属字段）。"""
        data = ApprovalReadSerializer(self.approval).data
        self.assertNotIn("user", data)
        self.assertNotIn("user_id", data)


class ApprovalWriteSerializerFieldTests(APITestCase):
    """ApprovalWriteSerializer 字段白名单与防御性深度测试。"""

    def test_accepts_approved_and_user_input(self):
        """白名单字段：approved 与 user_input 被接受。"""
        serializer = ApprovalWriteSerializer(
            data={
                "approved": True,
                "user_input": "yes, proceed",
            }
        )
        self.assertTrue(serializer.is_valid(), msg=serializer.errors)
        self.assertTrue(serializer.validated_data["approved"])
        self.assertEqual(serializer.validated_data["user_input"], "yes, proceed")

    def test_empty_body_is_valid(self):
        """空请求体有效：approved 默认 True，user_input 默认 None。"""
        serializer = ApprovalWriteSerializer(data={})
        self.assertTrue(serializer.is_valid(), msg=serializer.errors)
        self.assertTrue(serializer.validated_data["approved"])
        self.assertIsNone(serializer.validated_data.get("user_input"))

    def test_rejects_state_field(self):
        """客户端设置 state 字段被拒绝（防御性深度）。"""
        serializer = ApprovalWriteSerializer(data={"state": "approved"})
        self.assertFalse(serializer.is_valid())
        self.assertIn("state", serializer.errors)
        self.assertIn("不允许客户端设置", str(serializer.errors["state"]))

    def test_rejects_parameters_field(self):
        """客户端设置 parameters 字段被拒绝。"""
        serializer = ApprovalWriteSerializer(data={"parameters": {"malicious": "payload"}})
        self.assertFalse(serializer.is_valid())
        self.assertIn("parameters", serializer.errors)

    def test_rejects_approved_by_field(self):
        """客户端设置 approved_by 字段被拒绝（防止越权指定审批人）。"""
        serializer = ApprovalWriteSerializer(data={"approved_by": 999})
        self.assertFalse(serializer.is_valid())
        self.assertIn("approved_by", serializer.errors)

    def test_rejects_approved_by_id_field(self):
        """客户端设置 approved_by_id 字段被拒绝。"""
        serializer = ApprovalWriteSerializer(data={"approved_by_id": 999})
        self.assertFalse(serializer.is_valid())
        self.assertIn("approved_by_id", serializer.errors)

    def test_rejects_source_field(self):
        """客户端设置 source 字段被拒绝（防止伪造审批来源）。"""
        serializer = ApprovalWriteSerializer(data={"source": "chat"})
        self.assertFalse(serializer.is_valid())
        self.assertIn("source", serializer.errors)

    def test_rejects_action_field(self):
        """客户端设置 action 字段被拒绝（防止伪造审批动作）。"""
        serializer = ApprovalWriteSerializer(data={"action": "confirm_with_input"})
        self.assertFalse(serializer.is_valid())
        self.assertIn("action", serializer.errors)

    def test_rejects_extra_field(self):
        """客户端设置 extra 字段被拒绝（防止注入批次 ID 等内部字段）。"""
        serializer = ApprovalWriteSerializer(data={"extra": {"graph_interrupt_id": "forged-gid"}})
        self.assertFalse(serializer.is_valid())
        self.assertIn("extra", serializer.errors)

    def test_rejects_interrupt_id_field(self):
        """客户端设置 interrupt_id 字段被拒绝（防止 URL 与 body 不一致）。"""
        serializer = ApprovalWriteSerializer(data={"interrupt_id": "forged-id"})
        self.assertFalse(serializer.is_valid())
        self.assertIn("interrupt_id", serializer.errors)

    def test_rejects_user_field(self):
        """客户端设置 user 字段被拒绝（防止越权指定归属用户）。"""
        serializer = ApprovalWriteSerializer(data={"user": 999})
        self.assertFalse(serializer.is_valid())
        self.assertIn("user", serializer.errors)

    def test_rejects_multiple_protected_fields(self):
        """客户端同时设置多个 protected 字段，全部在 errors 中报告。"""
        serializer = ApprovalWriteSerializer(
            data={
                "state": "approved",
                "parameters": {"x": 1},
                "source": "chat",
            }
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("state", serializer.errors)
        self.assertIn("parameters", serializer.errors)
        self.assertIn("source", serializer.errors)

    def test_approved_must_be_boolean(self):
        """approved 字段必须为布尔值。"""
        serializer = ApprovalWriteSerializer(data={"approved": "not-a-bool"})
        self.assertFalse(serializer.is_valid())
        self.assertIn("approved", serializer.errors)

    def test_user_input_must_be_string(self):
        """user_input 必须为字符串类型（dict / list 等非标量类型被拒绝）。

        注：DRF CharField 默认会把 int/float 强制转换为 str（如 12345 → "12345"），
        这是 DRF 的标准行为，本测试只验证 dict/list 等结构化类型应被拒绝。
        """
        # dict 类型应失败
        serializer_dict = ApprovalWriteSerializer(data={"user_input": {"nested": "obj"}})
        self.assertFalse(serializer_dict.is_valid())
        # list 类型应失败
        serializer_list = ApprovalWriteSerializer(data={"user_input": ["a", "b"]})
        self.assertFalse(serializer_list.is_valid())

    def test_user_input_max_length(self):
        """user_input 超过 max_length=10000 被拒绝。"""
        long_input = "a" * 10001
        serializer = ApprovalWriteSerializer(data={"user_input": long_input})
        self.assertFalse(serializer.is_valid())
        self.assertIn("user_input", serializer.errors)

    def test_user_input_blank_string_allowed(self):
        """user_input 允许空字符串（confirm_with_input 场景下空串合法）。"""
        serializer = ApprovalWriteSerializer(data={"user_input": ""})
        self.assertTrue(serializer.is_valid(), msg=serializer.errors)
        self.assertEqual(serializer.validated_data["user_input"], "")

    def test_user_input_null_allowed(self):
        """user_input 允许 None（confirm 场景下无输入）。"""
        serializer = ApprovalWriteSerializer(data={"user_input": None})
        self.assertTrue(serializer.is_valid(), msg=serializer.errors)
        self.assertIsNone(serializer.validated_data["user_input"])


class ApprovalResumeViewSerializerIntegrationTests(APITestCase):
    """ApprovalResumeView POST 端到端 serializer 集成测试。"""

    @classmethod
    def setUpTestData(cls):
        cls.user_a = User.objects.create_user(
            username="resume-ser-a",
            password="testpass123",
        )
        cls.approval_a = _make_approval(
            interrupt_id="resume-ser-001",
            source=Approval.SOURCE_CHAT,
            source_id="resume-ser-sess",
            chat_session_id="resume-ser-sess",
            state=Approval.STATE_PENDING,
            user=cls.user_a,
        )

    @patch("Django_xm.apps.approvals.views.approval_service.resume_approval")
    def test_resume_with_state_field_returns_400(self, mock_resume):
        """POST /resume/ 携带 state 字段返回 400，service 不被调用。"""
        self.client.force_authenticate(user=self.user_a)
        resp = self.client.post(
            f"/api/v1/approvals/{self.approval_a.interrupt_id}/resume/",
            {"state": "approved", "approved": True},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertEqual(body["code"], 40002)
        self.assertIn("state", body["data"]["details"])
        mock_resume.assert_not_called()

    @patch("Django_xm.apps.approvals.views.approval_service.resume_approval")
    def test_resume_with_parameters_field_returns_400(self, mock_resume):
        """POST /resume/ 携带 parameters 字段返回 400。"""
        self.client.force_authenticate(user=self.user_a)
        resp = self.client.post(
            f"/api/v1/approvals/{self.approval_a.interrupt_id}/resume/",
            {"parameters": {"malicious": "payload"}},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        mock_resume.assert_not_called()

    @patch("Django_xm.apps.approvals.views.approval_service.resume_approval")
    def test_resume_with_approved_by_field_returns_400(self, mock_resume):
        """POST /resume/ 携带 approved_by 字段返回 400（防止越权指定审批人）。"""
        self.client.force_authenticate(user=self.user_a)
        resp = self.client.post(
            f"/api/v1/approvals/{self.approval_a.interrupt_id}/resume/",
            {"approved_by": 999, "approved": True},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        mock_resume.assert_not_called()

    @patch("Django_xm.apps.approvals.views.approval_service.resume_approval")
    def test_resume_with_valid_body_calls_service(self, mock_resume):
        """POST /resume/ 携带合法 body 通过 serializer 校验，service 被调用。

        mock 抛 ValueError 让 view 提前返回（避免进入 SSE 流复杂逻辑），
        本测试只验证 serializer 校验通过 + service 被调用 + 参数来自 validated_data。
        """
        mock_resume.side_effect = ValueError("mock: stop before SSE")
        self.client.force_authenticate(user=self.user_a)
        self.client.post(
            f"/api/v1/approvals/{self.approval_a.interrupt_id}/resume/",
            {"approved": True, "user_input": "yes"},
            format="json",
        )
        # 关键断言：service 被调用（serializer 校验通过 + ownership 通过）
        mock_resume.assert_called_once()
        # 验证传给 service 的参数来自 validated_data
        _, kwargs = mock_resume.call_args
        self.assertTrue(kwargs.get("approved"))
        self.assertEqual(kwargs.get("user_input"), "yes")
        # mock 抛 ValueError → view 返回 400（VALIDATION_FAILED），不进入 SSE 流
        # 此处不强断言 status code，避免与 mock 行为耦合

    @patch("Django_xm.apps.approvals.views.approval_service.resume_approval")
    def test_resume_with_empty_body_uses_defaults(self, mock_resume):
        """POST /resume/ 空 body 使用默认值 approved=True。"""
        mock_resume.side_effect = ValueError("mock: stop before SSE")
        self.client.force_authenticate(user=self.user_a)
        self.client.post(
            f"/api/v1/approvals/{self.approval_a.interrupt_id}/resume/",
            {},
            format="json",
        )
        mock_resume.assert_called_once()
        _, kwargs = mock_resume.call_args
        self.assertTrue(kwargs.get("approved"))


class ApprovalRejectViewSerializerIntegrationTests(APITestCase):
    """ApprovalRejectView POST 端到端 serializer 集成测试。"""

    @classmethod
    def setUpTestData(cls):
        cls.user_a = User.objects.create_user(
            username="reject-ser-a",
            password="testpass123",
        )
        cls.approval_a = _make_approval(
            interrupt_id="reject-ser-001",
            source=Approval.SOURCE_CHAT,
            source_id="reject-ser-sess",
            chat_session_id="reject-ser-sess",
            state=Approval.STATE_PENDING,
            user=cls.user_a,
        )

    @patch("Django_xm.apps.approvals.views.approval_service.resume_approval")
    def test_reject_with_state_field_returns_400(self, mock_resume):
        """POST /reject/ 携带 state 字段返回 400，service 不被调用。"""
        self.client.force_authenticate(user=self.user_a)
        resp = self.client.post(
            f"/api/v1/approvals/{self.approval_a.interrupt_id}/reject/",
            {"state": "approved"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertEqual(body["code"], 40002)
        self.assertIn("state", body["data"]["details"])
        mock_resume.assert_not_called()

    @patch("Django_xm.apps.approvals.views.approval_service.resume_approval")
    def test_reject_with_parameters_field_returns_400(self, mock_resume):
        """POST /reject/ 携带 parameters 字段返回 400。"""
        self.client.force_authenticate(user=self.user_a)
        resp = self.client.post(
            f"/api/v1/approvals/{self.approval_a.interrupt_id}/reject/",
            {"parameters": {"malicious": "payload"}},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        mock_resume.assert_not_called()

    @patch("Django_xm.apps.approvals.views.approval_service.resume_approval")
    def test_reject_with_empty_body_calls_service_with_approved_false(self, mock_resume):
        """POST /reject/ 空 body 通过 serializer，service 以 approved=False 调用。"""
        mock_resume.side_effect = ValueError("mock: stop before SSE")
        self.client.force_authenticate(user=self.user_a)
        self.client.post(
            f"/api/v1/approvals/{self.approval_a.interrupt_id}/reject/",
            {},
            format="json",
        )
        mock_resume.assert_called_once()
        _, kwargs = mock_resume.call_args
        self.assertFalse(kwargs.get("approved"))

    @patch("Django_xm.apps.approvals.views.approval_service.resume_approval")
    def test_reject_with_extra_field_returns_400(self, mock_resume):
        """POST /reject/ 携带 extra 字段返回 400（防止注入批次 ID）。"""
        self.client.force_authenticate(user=self.user_a)
        resp = self.client.post(
            f"/api/v1/approvals/{self.approval_a.interrupt_id}/reject/",
            {"extra": {"graph_interrupt_id": "forged"}},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        mock_resume.assert_not_called()
