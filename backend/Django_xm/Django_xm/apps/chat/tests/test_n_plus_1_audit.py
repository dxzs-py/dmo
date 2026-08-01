"""跨端点 N+1 查询回归断言（SubTask 12.3 补充）。

补充 `harden-test-coverage-and-code-quality` spec 阶段 3 要求的 N+1 审计：
- `test_chat_session_list_n_plus_1.py` 已覆盖 ChatSessionListView
- 本文件补充：
  - ChatSessionDetailView：prefetch_related(messages, messages__attachments) +
    build_research_task_deleted_map 批量预查
  - ApprovalListView：select_related(approved_by, user) 覆盖嵌套 ApprovedBySerializer
  - DeepResearchTaskListView：select_related(created_by, parent_task,
    parent_task__parent_task) 覆盖 version_chain 属性的递归访问

设计原则：
- 使用 ``CaptureQueriesContext`` 捕获 SQL，断言查询数绝对上限
- 数据量翻倍后查询数应保持恒定（N+1 的本质是查询数随数据量增长）
- mock 掉缓存层（SecureSessionCacheService）强制走 DB 路径
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APITestCase

from Django_xm.apps.approvals.models import Approval
from Django_xm.apps.chat.models import ChatMessage, ChatSession, MessageRole
from Django_xm.apps.research.models import ResearchDepth, ResearchTask, ResearchTaskStatus
from Django_xm.apps.users.models import User

# ============================================================================
# ChatSessionDetailView N+1 断言
# ============================================================================


class ChatSessionDetailNPlusOneTests(APITestCase):
    """ChatSessionDetailView N+1 查询回归断言。

    View 已通过 ``prefetch_related(Prefetch("messages",
    queryset=ChatMessage.objects.prefetch_related("attachments")))``
    + ``build_research_task_deleted_map`` 批量预查优化。

    本测试验证：
    - 消息数量翻倍后查询数不增长（N+1 检测）
    - 查询数绝对上限 ≤ 8（session + messages + attachments + research_task 批量预查 +
      pending_approvals + 其他余量）
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="session-detail-n1-tester",
            password="testpass123",
        )

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(user=self.user)

    def _make_session_with_messages(self, message_count, with_attachments=False):
        """创建一个会话并填充指定数量的消息。"""
        session = ChatSession.objects.create(user=self.user)
        for j in range(message_count):
            msg = ChatMessage.objects.create(
                session=session,
                role=MessageRole.ASSISTANT if j % 2 else MessageRole.USER,
                content=f"detail-msg-{j}",
            )
            if with_attachments and j == 0:
                from django.core.files.uploadedfile import SimpleUploadedFile

                from Django_xm.apps.attachments.models import ChatAttachment

                ChatAttachment.objects.create(
                    session=session,
                    message=msg,
                    file=SimpleUploadedFile(
                        f"file-{j}.txt",
                        b"hello world",
                        content_type="text/plain",
                    ),
                    original_name=f"file-{j}.txt",
                    file_size=11,
                    file_type="text/plain",
                    mime_type="text/plain",
                )
        return session

    @patch("Django_xm.apps.chat.views_chat.SecureSessionCacheService")
    def test_detail_no_n_plus_1(self, _mock_cache):
        """消息数翻倍后查询数不增长。"""
        session_a = self._make_session_with_messages(5)
        session_b = self._make_session_with_messages(10)

        with CaptureQueriesContext(connection) as ctx_a:
            resp = self.client.get(f"/api/v1/chat/sessions/{session_a.session_id}/")
        self.assertEqual(resp.status_code, 200, ctx_a.captured_queries)
        queries_a = len(ctx_a)

        with CaptureQueriesContext(connection) as ctx_b:
            resp = self.client.get(f"/api/v1/chat/sessions/{session_b.session_id}/")
        self.assertEqual(resp.status_code, 200, ctx_b.captured_queries)
        queries_b = len(ctx_b)

        self.assertEqual(
            queries_a,
            queries_b,
            f"N+1 回归：5 messages {queries_a} 查询 vs 10 messages {queries_b} 查询。"
            f"SQL_a: {[q['sql'][:120] for q in ctx_a.captured_queries]}",
        )

    @patch("Django_xm.apps.chat.views_chat.SecureSessionCacheService")
    def test_detail_query_count_upper_bound(self, _mock_cache):
        """查询数绝对上限 ≤ 12（含 pending_approvals 注入余量）。"""
        session = self._make_session_with_messages(8, with_attachments=True)

        with CaptureQueriesContext(connection) as ctx:
            resp = self.client.get(f"/api/v1/chat/sessions/{session.session_id}/")
        self.assertEqual(resp.status_code, 200)

        # 验证 attachments 序列化结果非空（prefetch 生效）
        body = resp.json()
        messages = body.get("data", {}).get("messages", [])
        self.assertTrue(len(messages) > 0, "应返回消息列表")
        # 至少一条 message 含 attachments（第一条消息创建了附件）
        attachment_msg = [m for m in messages if m.get("attachments")]
        self.assertTrue(len(attachment_msg) > 0, "prefetch 失效：attachments 为空")
        self.assertGreater(len(attachment_msg[0]["attachments"]), 0)

        self.assertLessEqual(
            len(ctx),
            12,
            f"detail 端点查询数 {len(ctx)} 超过上限 12，疑似 N+1 回归。"
            f"SQL: {[q['sql'][:120] for q in ctx.captured_queries]}",
        )


# ============================================================================
# ApprovalListView N+1 断言
# ============================================================================


class ApprovalListNPlusOneTests(APITestCase):
    """ApprovalListView N+1 查询回归断言。

    View 已通过 ``select_related("approved_by", "user")`` 覆盖
    ApprovalReadSerializer 嵌套的 ApprovedBySerializer。
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="approval-list-n1-tester",
            password="testpass123",
        )
        # 第二个用户作为 approved_by
        cls.approver = User.objects.create_user(
            username="approval-approver-n1",
            password="testpass123",
        )

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(user=self.user)

    def _make_approvals(self, count, approved=False):
        """批量创建 Approval 记录。"""
        for i in range(count):
            state = Approval.STATE_APPROVED if approved else Approval.STATE_PENDING
            approval = Approval.objects.create(
                interrupt_id=f"n1-int-{uuid.uuid4().hex[:12]}",
                source=Approval.SOURCE_CHAT,
                source_id=f"n1-src-{i}",
                chat_session_id=f"n1-sess-{i}",
                tool_name="shell_exec",
                title=f"测试审批 {i}",
                description="N+1 测试",
                action=Approval.ACTION_CONFIRM,
                operation=f"echo {i}",
                danger_level="low",
                parameters={"command": f"echo {i}"},
                state=state,
                user=self.user,
                approved_by=self.approver if approved else None,
            )
            approval.save()

    def test_list_no_n_plus_1(self):
        """审批数翻倍后查询数不增长。"""
        self._make_approvals(5, approved=True)

        with CaptureQueriesContext(connection) as ctx_5:
            resp = self.client.get("/api/v1/approvals/")
        self.assertEqual(resp.status_code, 200)
        queries_5 = len(ctx_5)

        self._make_approvals(10, approved=True)  # 再加 10 个，共 15 个

        with CaptureQueriesContext(connection) as ctx_15:
            resp = self.client.get("/api/v1/approvals/")
        self.assertEqual(resp.status_code, 200)
        queries_15 = len(ctx_15)

        self.assertEqual(
            queries_5,
            queries_15,
            f"N+1 回归：5 approvals {queries_5} 查询 vs 15 approvals {queries_15} 查询。",
        )

    def test_list_query_count_upper_bound(self):
        """查询数绝对上限 ≤ 3（list SQL + count + 余量）。"""
        self._make_approvals(8, approved=True)

        with CaptureQueriesContext(connection) as ctx:
            resp = self.client.get("/api/v1/approvals/")
        self.assertEqual(resp.status_code, 200)

        # 验证 approved_by 嵌套序列化非空
        body = resp.json()
        items = body.get("data", [])
        self.assertTrue(len(items) > 0)
        approved_by_present = [item for item in items if item.get("approved_by")]
        self.assertTrue(
            len(approved_by_present) > 0,
            "approved_by 嵌套序列化为空，select_related 可能失效",
        )
        self.assertIn("username", approved_by_present[0]["approved_by"])

        self.assertLessEqual(
            len(ctx),
            3,
            f"approvals list 查询数 {len(ctx)} 超过上限 3，疑似 N+1 回归。"
            f"SQL: {[q['sql'][:120] for q in ctx.captured_queries]}",
        )


# ============================================================================
# DeepResearchTaskListView N+1 断言
# ============================================================================


class ResearchTaskListNPlusOneTests(APITestCase):
    """DeepResearchTaskListView N+1 查询回归断言。

    View 已通过 ``select_related("created_by", "parent_task", "parent_task__parent_task")``
    覆盖 version_chain 属性的递归 parent_task 访问（固定 2 层）。
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="research-list-n1-tester",
            password="testpass123",
        )

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(user=self.user)

    def _make_research_tasks(self, count, with_version_chain=False):
        """批量创建 ResearchTask 记录。

        Args:
            with_version_chain: 是否创建 parent_task 链（version_chain 属性访问）
        """
        for i in range(count):
            parent = None
            if with_version_chain:
                # 创建 2 层 parent 链（被 select_related 覆盖）
                grandparent = ResearchTask.objects.create(
                    task_id=f"n1-gp-{uuid.uuid4().hex[:8]}",
                    query=f"grandparent query {i}",
                    created_by=self.user,
                    version=1,
                )
                parent = ResearchTask.objects.create(
                    task_id=f"n1-p-{uuid.uuid4().hex[:8]}",
                    query=f"parent query {i}",
                    created_by=self.user,
                    parent_task=grandparent,
                    version=2,
                )
            ResearchTask.objects.create(
                task_id=f"n1-t-{uuid.uuid4().hex[:12]}",
                query=f"research query {i}",
                status=ResearchTaskStatus.COMPLETED,
                research_depth=ResearchDepth.STANDARD,
                created_by=self.user,
                parent_task=parent,
                version=3 if parent else 1,
            )

    def test_list_no_n_plus_1(self):
        """任务数翻倍后查询数不增长。"""
        self._make_research_tasks(5, with_version_chain=True)

        with CaptureQueriesContext(connection) as ctx_5:
            resp = self.client.get("/api/v1/research/tasks/")
        self.assertEqual(resp.status_code, 200)
        queries_5 = len(ctx_5)

        self._make_research_tasks(10, with_version_chain=True)  # 再加 10 个

        with CaptureQueriesContext(connection) as ctx_15:
            resp = self.client.get("/api/v1/research/tasks/")
        self.assertEqual(resp.status_code, 200)
        queries_15 = len(ctx_15)

        self.assertEqual(
            queries_5,
            queries_15,
            f"N+1 回归：5 tasks {queries_5} 查询 vs 15 tasks {queries_15} 查询。",
        )

    def test_list_query_count_upper_bound(self):
        """查询数绝对上限 ≤ 3（count + list SQL + 余量）。"""
        self._make_research_tasks(8, with_version_chain=True)

        with CaptureQueriesContext(connection) as ctx:
            resp = self.client.get("/api/v1/research/tasks/")
        self.assertEqual(resp.status_code, 200)

        # 验证 version_chain 序列化结果非空（select_related 生效）
        body = resp.json()
        items = body.get("data", {}).get("items", [])
        self.assertTrue(len(items) > 0)
        chain_present = [item for item in items if item.get("version_chain")]
        self.assertTrue(
            len(chain_present) > 0,
            "version_chain 序列化为空，select_related(parent_task) 可能失效",
        )
        # 2 层 parent 链 → 至少一个 version_chain 含 3 个元素（grandparent + parent + self）
        # 注意：不能假设 chain_present[0] 是最深的节点，因为 -created_at 排序在
        # 快速连续创建时微秒级时间戳可能导致 parent 节点排在 self 节点之前
        max_chain_len = max(len(item["version_chain"]) for item in chain_present)
        self.assertGreaterEqual(
            max_chain_len,
            3,
            f"version_chain 最大深度 {max_chain_len} < 3，select_related(parent_task__parent_task) 可能失效",
        )

        self.assertLessEqual(
            len(ctx),
            3,
            f"research list 查询数 {len(ctx)} 超过上限 3，疑似 N+1 回归。"
            f"SQL: {[q['sql'][:120] for q in ctx.captured_queries]}",
        )
