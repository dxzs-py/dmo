"""SnapshotView 单元测试。

覆盖范围：
    GET /api/v1/realtime/snapshot/{session_id}/
    1. 未认证访问返回 401
    2. 会话不存在返回 404
    3. 会话归属校验（用户 A 不可访问用户 B 的会话，返回 404 不泄漏存在性）
    4. 空会话返回空列表（messages/tool_calls/approvals 均为 []）
    5. 返回消息列表完整（user/assistant/tool 三条消息，含 id/session_id/role/content/created_at/tool_calls）
    6. 返回 tool_calls 聚合（assistant 消息的 tool_calls JSON 聚合，每条含 message_id）
    7. 返回 approvals 聚合（含 id/interrupt_id/tool_call_id/state/created_at/resolved_at）
    8. approval 状态合并到 tool_calls（tool_call_id 匹配时 tool_calls[i].approval 含 state/approval_id）
    9. tool_call_id 字段优先级（tool_call_id 优先于 id）
    10. session_title 字段（返回 session.title）

运行方式：
    cd d:\\programming\\langchain\\langchain_xm\\backend\\Django_xm
    conda activate langchain_xm
    python manage.py test Django_xm.apps.realtime.tests.test_snapshot_view --verbosity=2
"""

import uuid

from rest_framework.test import APITestCase

from Django_xm.apps.approvals.models import Approval
from Django_xm.apps.chat.models import ChatMessage, ChatSession, MessageRole
from Django_xm.apps.users.models import User


class SnapshotViewTestBase(APITestCase):
    """SnapshotView 测试公共 setUp。"""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="snapshot-view-tester",
            password="testpass123",
        )
        # 用户 B（用于归属校验测试）
        cls.other_user = User.objects.create_user(
            username="snapshot-view-other",
            password="testpass123",
        )

    def setUp(self):
        super().setUp()
        # 默认以 user 身份认证
        self.client.force_authenticate(user=self.user)

    @staticmethod
    def _make_session(user, *, session_id=None, title="测试会话"):
        """创建一条 ChatSession。"""
        return ChatSession.objects.create(
            session_id=session_id or str(uuid.uuid4()),
            user=user,
            title=title,
        )

    @staticmethod
    def _make_message(session, *, role=MessageRole.USER, content="", tool_calls=None):
        """创建一条 ChatMessage。"""
        return ChatMessage.objects.create(
            session=session,
            role=role,
            content=content,
            tool_calls=tool_calls if tool_calls is not None else [],
        )

    @staticmethod
    def _make_approval(
        *, interrupt_id, chat_session_id, state=Approval.STATE_PENDING, tool_call_id=None, resolved=False
    ):
        """创建一条 Approval 记录。

        Approval 没有 tool_call_id 字段，该值存放在 extra JSON 中。
        """
        extra = {"tool_call_id": tool_call_id} if tool_call_id else {}
        from django.utils import timezone

        return Approval.objects.create(
            interrupt_id=interrupt_id,
            source=Approval.SOURCE_CHAT,
            source_id=chat_session_id,
            chat_session_id=chat_session_id,
            tool_name="shell_exec",
            title="确认执行",
            description="执行命令",
            action=Approval.ACTION_CONFIRM,
            operation="ls -la",
            danger_level="medium",
            parameters={"command": "ls -la"},
            state=state,
            extra=extra,
            resolved_at=timezone.now() if resolved else None,
        )

    @staticmethod
    def _snapshot_url(session_id):
        """生成 snapshot 接口 URL。"""
        from django.urls import reverse

        return reverse("realtime:realtime-snapshot", kwargs={"session_id": str(session_id)})


class SnapshotViewAuthTests(SnapshotViewTestBase):
    """认证与权限相关测试。"""

    def test_unauthenticated_request_returns_401(self):
        """未认证访问返回 401。"""
        self.client.force_authenticate(user=None)
        url = self._snapshot_url("any-session-id")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 401)
        body = resp.json()
        # 自定义异常处理器：未认证返回 code=40101 (ErrorCode.UNAUTHORIZED)
        self.assertEqual(body["code"], 40101)

    def test_nonexistent_session_returns_404(self):
        """会话不存在返回 404。"""
        url = self._snapshot_url("nonexistent-session-id")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 404)
        body = resp.json()
        self.assertEqual(body["code"], 40401)  # ErrorCode.NOT_FOUND

    def test_other_user_session_returns_404(self):
        """用户 A 访问用户 B 的会话返回 404（不泄漏存在性）。"""
        other_session = self._make_session(self.other_user, title="他人会话")
        url = self._snapshot_url(other_session.session_id)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 404)
        body = resp.json()
        self.assertEqual(body["code"], 40401)


class SnapshotViewEmptyTests(SnapshotViewTestBase):
    """空会话场景测试。"""

    def test_empty_session_returns_empty_lists(self):
        """空会话（无消息、无审批）返回空列表。"""
        session = self._make_session(self.user, title="空会话")
        url = self._snapshot_url(session.session_id)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["code"], 200)
        data = body["data"]
        self.assertEqual(data["session_id"], session.session_id)
        self.assertEqual(data["session_title"], "空会话")
        self.assertEqual(data["messages"], [])
        self.assertEqual(data["tool_calls"], [])
        self.assertEqual(data["approvals"], [])


class SnapshotViewMessagesTests(SnapshotViewTestBase):
    """消息列表聚合测试。"""

    def test_messages_list_contains_all_messages(self):
        """会话有 3 条消息（user/assistant/tool）→ messages 长度=3，每条含必填字段。"""
        session = self._make_session(self.user)
        self._make_message(session, role=MessageRole.USER, content="你好")
        self._make_message(
            session,
            role=MessageRole.ASSISTANT,
            content="执行工具",
            tool_calls=[
                {
                    "tool_call_id": "call_001",
                    "tool_name": "shell_exec",
                    "parameters": {"command": "ls"},
                }
            ],
        )
        self._make_message(session, role=MessageRole.USER, content="继续")

        url = self._snapshot_url(session.session_id)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]

        self.assertEqual(len(data["messages"]), 3)
        for msg in data["messages"]:
            # 每条消息含必填字段
            self.assertIn("id", msg)
            self.assertIn("session_id", msg)
            self.assertIn("role", msg)
            self.assertIn("content", msg)
            self.assertIn("created_at", msg)
            self.assertIn("tool_calls", msg)
            # session_id 与会话一致
            self.assertEqual(msg["session_id"], session.session_id)

        # 顺序按 created_at 升序
        roles = [m["role"] for m in data["messages"]]
        self.assertEqual(roles, ["user", "assistant", "user"])

    def test_tool_calls_aggregated_with_message_id(self):
        """assistant 消息含 tool_calls JSON → tool_calls 聚合，每条含 message_id。"""
        session = self._make_session(self.user)
        # assistant 消息含 2 个 tool_calls
        assistant_msg = self._make_message(
            session,
            role=MessageRole.ASSISTANT,
            content="调用工具",
            tool_calls=[
                {"tool_call_id": "call_A", "tool_name": "shell_exec"},
                {"tool_call_id": "call_B", "tool_name": "file_read"},
            ],
        )
        # user 消息无 tool_calls
        self._make_message(session, role=MessageRole.USER, content="hi")

        url = self._snapshot_url(session.session_id)
        resp = self.client.get(url)
        data = resp.json()["data"]

        # 聚合所有 tool_calls（assistant 消息中 2 个）
        self.assertEqual(len(data["tool_calls"]), 2)
        for tc in data["tool_calls"]:
            self.assertEqual(tc["message_id"], str(assistant_msg.id))
        tool_call_ids = {tc["tool_call_id"] for tc in data["tool_calls"]}
        self.assertEqual(tool_call_ids, {"call_A", "call_B"})


class SnapshotViewApprovalsTests(SnapshotViewTestBase):
    """审批聚合与合并测试。"""

    def test_approvals_aggregated_with_required_fields(self):
        """会话有 2 个 Approval → approvals 长度=2，每个含必填字段。"""
        session = self._make_session(self.user)
        apv1 = self._make_approval(
            interrupt_id="int_001",
            chat_session_id=session.session_id,
            state=Approval.STATE_PENDING,
            tool_call_id="call_001",
        )
        apv2 = self._make_approval(
            interrupt_id="int_002",
            chat_session_id=session.session_id,
            state=Approval.STATE_APPROVED,
            tool_call_id="call_002",
            resolved=True,
        )

        url = self._snapshot_url(session.session_id)
        resp = self.client.get(url)
        data = resp.json()["data"]

        self.assertEqual(len(data["approvals"]), 2)
        for apv in data["approvals"]:
            self.assertIn("id", apv)
            self.assertIn("interrupt_id", apv)
            self.assertIn("tool_call_id", apv)
            self.assertIn("state", apv)
            self.assertIn("created_at", apv)
            self.assertIn("resolved_at", apv)

        # 校验具体值
        by_interrupt = {a["interrupt_id"]: a for a in data["approvals"]}
        self.assertIn("int_001", by_interrupt)
        self.assertIn("int_002", by_interrupt)
        self.assertEqual(by_interrupt["int_001"]["state"], Approval.STATE_PENDING)
        self.assertEqual(by_interrupt["int_002"]["state"], Approval.STATE_APPROVED)
        # tool_call_id 从 extra.tool_call_id 取
        self.assertEqual(by_interrupt["int_001"]["tool_call_id"], "call_001")
        self.assertEqual(by_interrupt["int_002"]["tool_call_id"], "call_002")
        # id 字段为字符串形式的 Approval.id
        self.assertEqual(by_interrupt["int_001"]["id"], str(apv1.id))
        self.assertEqual(by_interrupt["int_002"]["id"], str(apv2.id))

    def test_approval_state_merged_into_tool_calls(self):
        """tool_call_id 匹配的 approval 状态合并到 tool_calls[i].approval。"""
        session = self._make_session(self.user)
        # assistant 消息含 tool_call（tool_call_id=call_001）
        self._make_message(
            session,
            role=MessageRole.ASSISTANT,
            content="调用工具",
            tool_calls=[
                {
                    "tool_call_id": "call_001",
                    "tool_name": "shell_exec",
                }
            ],
        )
        # 对应的 approval（已通过）
        self._make_approval(
            interrupt_id="int_001",
            chat_session_id=session.session_id,
            state=Approval.STATE_APPROVED,
            tool_call_id="call_001",
            resolved=True,
        )

        url = self._snapshot_url(session.session_id)
        resp = self.client.get(url)
        data = resp.json()["data"]

        self.assertEqual(len(data["tool_calls"]), 1)
        tc = data["tool_calls"][0]
        self.assertIn("approval", tc)
        # approval 字段含 state 与 approval_id（= interrupt_id）
        self.assertEqual(tc["approval"]["state"], Approval.STATE_APPROVED)
        self.assertEqual(tc["approval"]["approval_id"], "int_001")

    def test_tool_call_id_field_takes_precedence_over_id(self):
        """tool_calls 中 tool_call_id 字段优先于 id 字段进行 approval 匹配。"""
        session = self._make_session(self.user)
        # tool_call 同时含 id（旧格式）和 tool_call_id（新格式）
        # 二者不同，approval 的 tool_call_id 应匹配新字段 tool_call_id
        self._make_message(
            session,
            role=MessageRole.ASSISTANT,
            content="调用工具",
            tool_calls=[
                {
                    "id": "legacy_id_X",
                    "tool_call_id": "call_new_001",
                    "tool_name": "shell_exec",
                }
            ],
        )
        # approval 关联到新字段 tool_call_id
        self._make_approval(
            interrupt_id="int_new_001",
            chat_session_id=session.session_id,
            state=Approval.STATE_REJECTED,
            tool_call_id="call_new_001",
            resolved=True,
        )

        url = self._snapshot_url(session.session_id)
        resp = self.client.get(url)
        data = resp.json()["data"]

        self.assertEqual(len(data["tool_calls"]), 1)
        tc = data["tool_calls"][0]
        # 应通过 tool_call_id 字段匹配，而非 id 字段
        self.assertIn("approval", tc)
        self.assertEqual(tc["approval"]["state"], Approval.STATE_REJECTED)
        self.assertEqual(tc["approval"]["approval_id"], "int_new_001")

    def test_session_title_returned(self):
        """session_title 字段返回 session.title。"""
        session = self._make_session(self.user, title="定制标题 XYZ")
        url = self._snapshot_url(session.session_id)
        resp = self.client.get(url)
        data = resp.json()["data"]
        self.assertEqual(data["session_title"], "定制标题 XYZ")

    def test_session_title_falls_back_to_empty_when_missing(self):
        """session.title 为空字符串时返回空字符串（视图实现使用 `session.title or ''`）。"""
        session = ChatSession.objects.create(
            session_id=str(uuid.uuid4()),
            user=self.user,
            title="",  # 空标题
        )
        url = self._snapshot_url(session.session_id)
        resp = self.client.get(url)
        data = resp.json()["data"]
        self.assertEqual(data["session_title"], "")
