"""审批视图 API 测试。

测试 ApprovalListView、ApprovalDetailView、ApprovalResumeView、ApprovalRejectView
四个视图的请求与响应行为，mock 掉 Redis/Celery/SSE 等外部依赖。

覆盖端到端审批恢复流程：
- POST /api/v1/approvals/{interrupt_id}/resume/ chat source 返回 SSE 流
- POST /api/v1/approvals/{interrupt_id}/resume/ deep_research source 返回 JSON（Celery 任务派发，Path D）
- waiting 状态返回 JSON（content-type: application/json）
- 不存在的审批返回 404
- 非法 source 返回 400
- 幂等响应（已处理审批）返回 JSON
"""

from unittest.mock import MagicMock, patch

from django.http import StreamingHttpResponse
from rest_framework.test import APITestCase

from Django_xm.apps.approvals.models import Approval
from Django_xm.apps.users.models import User


class ApprovalViewTestBase(APITestCase):
    """审批视图测试公共 setUp。"""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="approval-view-tester",
            password="testpass123",
        )

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(user=self.user)

    def _make_approval(self, **kwargs):
        """创建一条 Approval 记录，提供合理默认值。

        默认归属当前测试用户（user=self.user），适配 Task 1 引入的 user 字段越权修复。
        调用方传 user=None 可显式模拟历史无归属数据。
        """
        defaults = {
            "interrupt_id": "test-interrupt-001",
            "source": Approval.SOURCE_CHAT,
            "source_id": "test-session-001",
            "chat_session_id": "test-session-001",
            "tool_name": "shell_exec",
            "title": "确认执行",
            "description": "执行 shell 命令",
            "action": Approval.ACTION_CONFIRM,
            "operation": "rm -rf /tmp/test",
            "danger_level": "high",
            "parameters": {"command": "rm -rf /tmp/test"},
            "state": Approval.STATE_PENDING,
            "user": self.user,
        }
        defaults.update(kwargs)
        return Approval.objects.create(**defaults)


class ApprovalListViewTests(ApprovalViewTestBase):
    """ApprovalListView 测试：GET /api/v1/approvals/。"""

    def setUp(self):
        super().setUp()
        self.approval1 = self._make_approval(
            interrupt_id="int-001",
            source_id="src-001",
            chat_session_id="sess-001",
            state=Approval.STATE_PENDING,
        )
        self.approval2 = self._make_approval(
            interrupt_id="int-002",
            source_id="src-002",
            chat_session_id="sess-002",
            state=Approval.STATE_APPROVED,
        )
        self.approval3 = self._make_approval(
            interrupt_id="int-003",
            source_id="src-001",
            chat_session_id="sess-001",
            state=Approval.STATE_REJECTED,
        )

    def test_filter_by_source_id(self):
        """GET ?source_id=xxx 返回过滤结果。"""
        resp = self.client.get("/api/v1/approvals/", {"source_id": "src-001"})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["code"], 200)
        interrupt_ids = [item["interrupt_id"] for item in body["data"]]
        self.assertIn("int-001", interrupt_ids)
        self.assertIn("int-003", interrupt_ids)
        self.assertNotIn("int-002", interrupt_ids)

    def test_filter_by_chat_session_id(self):
        """GET ?chat_session_id=xxx 返回过滤结果。"""
        resp = self.client.get("/api/v1/approvals/", {"chat_session_id": "sess-002"})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        interrupt_ids = [item["interrupt_id"] for item in body["data"]]
        self.assertEqual(interrupt_ids, ["int-002"])

    def test_filter_by_state(self):
        """GET ?state=pending 返回过滤结果。"""
        resp = self.client.get("/api/v1/approvals/", {"state": "pending"})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        interrupt_ids = [item["interrupt_id"] for item in body["data"]]
        self.assertIn("int-001", interrupt_ids)
        self.assertNotIn("int-002", interrupt_ids)
        self.assertNotIn("int-003", interrupt_ids)

    def test_unauthenticated_returns_401(self):
        """未认证请求返回 401。"""
        self.client.force_authenticate(user=None)
        resp = self.client.get("/api/v1/approvals/")
        self.assertEqual(resp.status_code, 401)


class ApprovalDetailViewTests(ApprovalViewTestBase):
    """ApprovalDetailView 测试：GET /api/v1/approvals/{interrupt_id}/。"""

    def setUp(self):
        super().setUp()
        self.approval = self._make_approval(
            interrupt_id="detail-001",
            source_id="src-detail",
        )

    def test_get_detail_success(self):
        """存在的 interrupt_id 返回详情。"""
        resp = self.client.get(f"/api/v1/approvals/{self.approval.interrupt_id}/")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["code"], 200)
        self.assertEqual(body["data"]["interrupt_id"], "detail-001")
        self.assertEqual(body["data"]["tool_name"], "shell_exec")

    def test_get_detail_not_found(self):
        """不存在的 interrupt_id 返回 404。"""
        resp = self.client.get("/api/v1/approvals/nonexistent-id/")
        self.assertEqual(resp.status_code, 404)
        body = resp.json()
        self.assertEqual(body["code"], 40401)


# ==================== ApprovalResumeView 端到端测试 ====================


def _make_fake_sse_streaming_response():
    """构造一个最小的 SSE StreamingHttpResponse，用于 mock 流式恢复返回值。"""
    return StreamingHttpResponse(
        streaming_content=iter(
            [
                'data: {"type": "start", "message": "审批恢复"}\n\n',
                'data: {"type": "chunk", "content": "已恢复"}\n\n',
                "data: [DONE]\n\n",
            ]
        ),
        content_type="text/event-stream",
    )


class ApprovalResumeViewTests(ApprovalViewTestBase):
    """ApprovalResumeView 测试：POST /api/v1/approvals/{interrupt_id}/resume/。

    覆盖 SubTask 17.5 要求的场景：
    - chat source 返回 SSE 流（content-type: text/event-stream）
    - deep_research source 返回 SSE 流
    - waiting 状态返回 JSON（content-type: application/json）
    - 不存在的审批返回 404
    - 非法 source 返回 400
    - 幂等响应返回 JSON
    """

    def setUp(self):
        super().setUp()
        self.chat_approval = self._make_approval(
            interrupt_id="resume-chat-001",
            source=Approval.SOURCE_CHAT,
            source_id="chat-session-001",
            chat_session_id="chat-session-001",
        )
        self.research_approval = self._make_approval(
            interrupt_id="resume-research-001",
            source=Approval.SOURCE_DEEP_RESEARCH,
            source_id="research-task-001",
            chat_session_id="chat-session-001",
        )

    @patch("Django_xm.apps.approvals.views.approval_service.resume_approval")
    @patch("Django_xm.apps.approvals.views.gateway")
    def test_resume_chat_source_returns_sse_stream(self, mock_gateway, mock_resume):
        """chat source 审批恢复返回 SSE 流（content-type: text/event-stream）。

        Path D 后路由由 ApprovalGateway.route_resume 统一处理，
        chat source 返回 StreamingHttpResponse（SSE 流）。
        """
        mock_resume.return_value = {
            "approval": self.chat_approval,
            "resume_value": True,
        }
        mock_gateway.route_resume.return_value = _make_fake_sse_streaming_response()

        resp = self.client.post(
            f"/api/v1/approvals/{self.chat_approval.interrupt_id}/resume/",
            {"approved": True},
            format="json",
        )

        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/event-stream", resp["Content-Type"])
        # 消费流式响应内容
        events = b"".join(resp.streaming_content)
        self.assertIn(b'"type": "start"', events)
        self.assertIn(b'"type": "chunk"', events)
        self.assertIn(b"[DONE]", events)
        # 验证 resume_approval 调用参数
        mock_resume.assert_called_once()
        kwargs = mock_resume.call_args.kwargs
        self.assertEqual(kwargs["interrupt_id"], "resume-chat-001")
        self.assertTrue(kwargs["approved"])
        # 验证路由到 chat 恢复流（gateway.route_resume 返回 SSE）
        mock_gateway.route_resume.assert_called_once()

    @patch("Django_xm.apps.approvals.views.approval_service.resume_approval")
    @patch("Django_xm.apps.approvals.views.gateway")
    def test_resume_deep_research_source_returns_json(self, mock_gateway, mock_resume):
        """deep_research source 审批恢复派发 Celery 任务，返回 JSON（Path D）。

        Path D 后 deep_research 不再走 SSE，而是派发 research_resume_task Celery 任务，
        gateway.route_resume 返回 None，view 返回 JSON 响应。
        """
        mock_resume.return_value = {
            "approval": self.research_approval,
            "resume_value": True,
        }
        # deep_research → gateway.route_resume 返回 None（Celery 任务已派发）
        mock_gateway.route_resume.return_value = None

        resp = self.client.post(
            f"/api/v1/approvals/{self.research_approval.interrupt_id}/resume/",
            {"approved": True},
            format="json",
        )

        self.assertEqual(resp.status_code, 200)
        content_type = resp["Content-Type"]
        self.assertIn("application/json", content_type, f"deep_research 应返回 JSON，实际: {content_type}")
        body = resp.json()
        self.assertEqual(body["code"], 0)
        self.assertEqual(body["data"]["source"], Approval.SOURCE_DEEP_RESEARCH)
        self.assertEqual(body["data"]["status"], "resumed")
        # 验证 gateway 路由被调用
        mock_gateway.route_resume.assert_called_once()

    @patch("Django_xm.apps.approvals.views.approval_service.resume_approval")
    def test_resume_waiting_state_returns_json(self, mock_resume):
        """waiting 状态（同批次还有 pending）返回 JSON，content-type 为 application/json。

        前端 _executeChatApproval 通过 content-type 区分 SSE 流与 waiting JSON 响应。
        """
        mock_resume.return_value = {
            "approval": self.chat_approval,
            "resume_value": True,
            "state": "waiting",
        }

        resp = self.client.post(
            f"/api/v1/approvals/{self.chat_approval.interrupt_id}/resume/",
            {"approved": True},
            format="json",
        )

        self.assertEqual(resp.status_code, 200)
        content_type = resp["Content-Type"]
        self.assertIn("application/json", content_type, f"waiting 响应必须返回 application/json，实际: {content_type}")
        body = resp.json()
        self.assertEqual(body["code"], 0)
        self.assertEqual(body["data"]["state"], "waiting")
        self.assertEqual(body["data"]["status"], "waiting_for_others")
        self.assertEqual(body["data"]["interrupt_id"], "resume-chat-001")

    @patch("Django_xm.apps.approvals.views.approval_service.resume_approval")
    def test_resume_not_found_returns_404(self, mock_resume):
        """审批不存在时返回 404。"""
        mock_resume.return_value = {
            "approval": None,
            "resume_value": None,
            "not_found": True,
        }

        resp = self.client.post(
            "/api/v1/approvals/nonexistent-interrupt/resume/",
            {"approved": True},
            format="json",
        )

        self.assertEqual(resp.status_code, 404)
        body = resp.json()
        self.assertEqual(body["code"], 40401)
        self.assertIn("不存在", body["message"])

    @patch("Django_xm.apps.approvals.views.approval_service.resume_approval")
    @patch("Django_xm.apps.approvals.views.approval_service.complete_approval")
    def test_resume_unknown_source_returns_400(self, mock_complete, mock_resume):
        """非法 source 返回 400，并将审批标记为 rejected 释放锁。"""
        unknown_approval = self._make_approval(
            interrupt_id="resume-unknown-001",
            source="unknown_source",
            source_id="unknown-src-001",
        )
        mock_resume.return_value = {
            "approval": unknown_approval,
            "resume_value": True,
        }

        resp = self.client.post(
            f"/api/v1/approvals/{unknown_approval.interrupt_id}/resume/",
            {"approved": True},
            format="json",
        )

        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertEqual(body["code"], 40002)
        self.assertIn("不支持的审批来源", body["message"])
        # 验证释放锁（complete_approval 用 kwargs 调用）
        mock_complete.assert_called_once()
        complete_kwargs = mock_complete.call_args.kwargs
        self.assertEqual(complete_kwargs["interrupt_id"], "resume-unknown-001")
        self.assertEqual(complete_kwargs["state"], Approval.STATE_REJECTED)

    @patch("Django_xm.apps.approvals.views.approval_service.resume_approval")
    def test_resume_idempotent_returns_json(self, mock_resume):
        """已处理审批（幂等）返回 JSON，包含完整审批状态。"""
        # 模拟已 approved 的审批再次 resume
        self.chat_approval.state = Approval.STATE_APPROVED
        self.chat_approval.save(update_fields=["state"])
        mock_resume.return_value = {
            "approval": self.chat_approval,
            "resume_value": True,
            "idempotent": True,
        }

        resp = self.client.post(
            f"/api/v1/approvals/{self.chat_approval.interrupt_id}/resume/",
            {"approved": True},
            format="json",
        )

        self.assertEqual(resp.status_code, 200)
        content_type = resp["Content-Type"]
        self.assertIn("application/json", content_type, f"幂等响应必须返回 application/json，实际: {content_type}")
        body = resp.json()
        # 幂等响应走 success_response，code=200（非 waiting 状态分支）
        self.assertEqual(body["code"], 200)
        self.assertEqual(body["data"]["interrupt_id"], "resume-chat-001")
        self.assertTrue(body["data"]["idempotent"])

    @patch("Django_xm.apps.approvals.views.approval_service.resume_approval")
    def test_resume_validation_error_returns_400(self, mock_resume):
        """resume_approval 抛出 ValueError 时返回 400。"""
        mock_resume.side_effect = ValueError("审批状态非 pending，无法恢复: state=approved")

        resp = self.client.post(
            f"/api/v1/approvals/{self.chat_approval.interrupt_id}/resume/",
            {"approved": True},
            format="json",
        )

        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertEqual(body["code"], 40002)
        self.assertIn("审批状态非 pending", body["message"])

    def test_resume_invalid_body_returns_400(self):
        """请求体校验失败（approved 字段类型错误）返回 400。"""
        resp = self.client.post(
            f"/api/v1/approvals/{self.chat_approval.interrupt_id}/resume/",
            {"approved": "not_a_boolean"},
            format="json",
        )

        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertEqual(body["code"], 40002)


class ApprovalRejectViewTests(ApprovalViewTestBase):
    """ApprovalRejectView 测试：POST /api/v1/approvals/{interrupt_id}/reject/。"""

    def setUp(self):
        super().setUp()
        self.chat_approval = self._make_approval(
            interrupt_id="reject-chat-001",
            source=Approval.SOURCE_CHAT,
            source_id="chat-session-002",
            chat_session_id="chat-session-002",
        )
        self.research_approval = self._make_approval(
            interrupt_id="reject-research-001",
            source=Approval.SOURCE_DEEP_RESEARCH,
            source_id="research-task-002",
            chat_session_id="chat-session-002",
        )

    @patch("Django_xm.apps.approvals.views.approval_service.resume_approval")
    @patch("Django_xm.apps.approvals.views.gateway")
    def test_reject_chat_source_returns_sse_stream(self, mock_gateway, mock_resume):
        """chat source 拒绝审批返回 SSE 流（Path D 后经 gateway.route_resume 路由）。"""
        mock_resume.return_value = {
            "approval": self.chat_approval,
            "resume_value": False,
        }
        mock_gateway.route_resume.return_value = _make_fake_sse_streaming_response()

        resp = self.client.post(
            f"/api/v1/approvals/{self.chat_approval.interrupt_id}/reject/",
            format="json",
        )

        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/event-stream", resp["Content-Type"])
        # 验证拒绝时 approved=False
        mock_resume.assert_called_once()
        kwargs = mock_resume.call_args.kwargs
        self.assertFalse(kwargs["approved"])
        # 验证 gateway 路由被调用（approved=False）
        mock_gateway.route_resume.assert_called_once()

    @patch("Django_xm.apps.approvals.views.approval_service.resume_approval")
    def test_reject_not_found_returns_error(self, mock_resume):
        """审批不存在时返回错误。"""
        mock_resume.side_effect = ValueError("审批记录不存在: interrupt_id=nonexistent")

        resp = self.client.post("/api/v1/approvals/nonexistent/reject/")

        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertEqual(body["code"], 40002)
        self.assertIn("审批记录不存在", body["message"])

    @patch("Django_xm.apps.approvals.views.approval_service.resume_approval")
    def test_reject_waiting_state_returns_json(self, mock_resume):
        """拒绝时同批次还有 pending 也返回 JSON waiting 响应。"""
        mock_resume.return_value = {
            "approval": self.chat_approval,
            "resume_value": False,
            "state": "waiting",
        }

        resp = self.client.post(
            f"/api/v1/approvals/{self.chat_approval.interrupt_id}/reject/",
            format="json",
        )

        self.assertEqual(resp.status_code, 200)
        content_type = resp["Content-Type"]
        self.assertIn("application/json", content_type)
        body = resp.json()
        self.assertEqual(body["data"]["state"], "waiting")
        self.assertEqual(body["data"]["status"], "waiting_for_others")


# ==================== ApprovalResumeView 直接调用生成器单元测试 ====================


class StreamChatResumeGeneratorTests(ApprovalViewTestBase):
    """_stream_chat_resume_generator 单元测试。

    直接调用生成器，验证基本行为：
    - 产出 SSE 事件流
    - 异常时产出 error 事件
    - finally 块释放 checkpointer
    """

    def _make_mock_request(self, data=None):
        """构造 mock request，用于直接调用生成器。"""
        mock_request = MagicMock()
        mock_request.user.id = self.user.id
        mock_request.data = data or {
            "use_tools": True,
            "use_web_search": False,
            "use_mcp": False,
            "selected_mcp_servers": None,
            "selected_tools": None,
            "use_knowledge_base": False,
            "selected_knowledge_bases": [],
            "provider_id": None,
            "model_name": None,
            "use_deep_thinking": False,
            "special_params": None,
            "temperature": None,
            "max_tokens": None,
        }
        return mock_request

    def _setup_mock_chat_service(self, mock_chat_service_class, mock_agent=None):
        """配置 ChatService mock，返回 mock_service。"""
        mock_service = MagicMock()
        mock_chat_service_class.return_value = mock_service
        mock_chat_service_class._resolve_model_instance.return_value = MagicMock()
        mock_service._build_tool_config.return_value = {"use_tools": True}

        async def mock_get_tools(data):
            return []

        mock_service._get_tools = mock_get_tools

        if mock_agent is None:
            mock_agent = MagicMock()
            mock_graph = MagicMock()

            async def mock_aget_state(config):
                mock_state = MagicMock()
                mock_state.tasks = []
                mock_state.values = {"messages": []}
                return mock_state

            mock_graph.aget_state = mock_aget_state
            mock_agent.graph = mock_graph

        async def mock_create_agent(data, prompt_mode="agent", model_instance=None, tool_config=None, tools=None):
            return (mock_agent, {"configurable": {"thread_id": "test-session"}}, True)

        mock_service._create_agent_with_memory = mock_create_agent

        return mock_service, mock_agent

    @patch("Django_xm.apps.ai_engine.services.checkpointer_factory.release_async_checkpointer", new_callable=MagicMock)
    @patch("Django_xm.apps.chat.services.stream_helpers.process_stream_chunk")
    @patch("Django_xm.apps.chat.services.chat_service.ChatService")
    def test_generator_produces_done_event(
        self,
        mock_chat_service_class,
        mock_process_chunk,
        mock_release,
    ):
        """生成器正常结束时产出 [DONE] 事件。"""
        from asgiref.sync import async_to_sync

        from Django_xm.apps.chat.views_chat import _stream_chat_resume_generator

        # 构造 mock agent，astream 产出一个 messages 模式 chunk
        mock_agent = MagicMock()
        mock_graph = MagicMock()

        async def mock_astream(command, config=None, stream_mode=None, **kwargs):
            yield ("messages", (MagicMock(content="Hello"), {"langgraph_node": "agent"}))

        mock_graph.astream = mock_astream

        async def mock_aget_state(config):
            mock_state = MagicMock()
            mock_state.tasks = []
            mock_state.values = {"messages": []}
            return mock_state

        mock_graph.aget_state = mock_aget_state
        mock_agent.graph = mock_graph

        self._setup_mock_chat_service(mock_chat_service_class, mock_agent=mock_agent)

        # mock process_stream_chunk 返回 chunk 事件
        mock_process_chunk.return_value = [{"type": "chunk", "content": "Hello"}]

        mock_approval = self._make_approval(interrupt_id="gen-test-001")
        mock_request = self._make_mock_request()

        async def _collect():
            events = []
            async for event in _stream_chat_resume_generator(
                mock_request,
                mock_approval,
                True,
                "test-session-id",
                request_data=mock_request.data,
            ):
                events.append(event)
            return events

        events = async_to_sync(_collect)()

        # 验证 SSE 事件格式
        event_text = "".join(events)
        self.assertIn('"type": "chunk"', event_text)
        self.assertIn("[DONE]", event_text)

    @patch("Django_xm.apps.ai_engine.services.checkpointer_factory.release_async_checkpointer", new_callable=MagicMock)
    @patch("Django_xm.apps.chat.services.chat_service.ChatService")
    def test_generator_emits_error_on_exception(
        self,
        mock_chat_service_class,
        mock_release,
    ):
        """生成器内部异常时产出 error SSE 事件，并在 finally 释放 checkpointer。"""
        from asgiref.sync import async_to_sync

        from Django_xm.apps.chat.views_chat import _stream_chat_resume_generator

        # ChatService 构造时抛异常
        mock_chat_service_class.side_effect = RuntimeError("agent build failed")

        mock_approval = self._make_approval(interrupt_id="gen-error-001")
        mock_request = self._make_mock_request()

        async def _collect():
            events = []
            async for event in _stream_chat_resume_generator(
                mock_request,
                mock_approval,
                True,
                "test-session-id",
                request_data=mock_request.data,
            ):
                events.append(event)
            return events

        events = async_to_sync(_collect)()

        event_text = "".join(events)
        self.assertIn("event: error", event_text)
        self.assertIn("approval_error", event_text)
        self.assertIn("agent build failed", event_text)
        # 验证 finally 释放 checkpointer
        mock_release.assert_called_once()
