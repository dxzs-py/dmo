"""ChatMessageSerializer research_task 批量化测试（dj-04 回归：逐条 N+1 查询）。

覆盖：
- 批量映射序列化：ResearchTask 查询次数恒为 1（assertNumQueries 语义，按表名过滤捕获）
- research_task_status / research_task_deleted 输出与逐条查询版（原实现语义）完全一致：
  任务不存在 → status None / deleted True；软删除任务 → deleted True；无关联 → None/None
- ChatSessionDetailSerializer 嵌套 messages 路径同样只查 1 次
- 契约：序列化实例缺 research_task_map 时 KeyError 快速失败（禁止逐条查询兜底）

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.chat.tests.test_chat_message_serializer_research
"""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from Django_xm.apps.chat.models import ChatMessage, ChatSession
from Django_xm.apps.chat.serializers import (
    ChatMessageSerializer,
    ChatSessionDetailSerializer,
    build_research_task_map,
)
from Django_xm.apps.research.models import ResearchTask, ResearchTaskStatus


def _research_query_count(captured):
    """统计捕获查询中命中 research_task 表的次数（精确匹配带引号表名）。"""
    return sum(1 for q in captured if '"research_task"' in q["sql"])


def _legacy_status(task_id):
    """原逐条查询版 get_research_task_status 语义（对照基准）。"""
    if not task_id:
        return None
    task = ResearchTask.all_objects.filter(task_id=task_id).first()
    return task.status if task else None


def _legacy_deleted(task_id):
    """原逐条查询版 get_research_task_deleted 语义（对照基准）。"""
    if not task_id:
        return None
    task = ResearchTask.all_objects.filter(task_id=task_id).first()
    return task.is_deleted if task else True


class ChatMessageSerializerResearchBatchTests(TestCase):
    """dj-04：research 状态批量映射序列化。"""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="serializer", password="pw123456")
        self.session = ChatSession.objects.create(user=self.user, title="s", session_id="sess-ser")

        self.active_task = ResearchTask.objects.create(
            task_id="task-active", query="q", status=ResearchTaskStatus.RUNNING, created_by=self.user
        )
        self.deleted_task = ResearchTask.objects.create(
            task_id="task-deleted", query="q", status=ResearchTaskStatus.COMPLETED, created_by=self.user
        )
        # 软删除（绕过 save 的审计钩子，直接置位）
        ResearchTask.objects.filter(pk=self.deleted_task.pk).update(is_deleted=True)

        # 5 条消息：活跃任务 / 软删除任务 / 不存在任务 / 无关联（None、空串）
        self.messages = [
            ChatMessage.objects.create(
                session=self.session, role="assistant", content="a", research_task_id="task-active"
            ),
            ChatMessage.objects.create(
                session=self.session, role="assistant", content="b", research_task_id="task-deleted"
            ),
            ChatMessage.objects.create(
                session=self.session, role="assistant", content="c", research_task_id="task-missing"
            ),
            ChatMessage.objects.create(session=self.session, role="user", content="d", research_task_id=None),
            ChatMessage.objects.create(session=self.session, role="user", content="e", research_task_id=""),
        ]

    def test_batch_output_matches_legacy_per_message_semantics(self):
        """status/deleted 输出与逐条查询版完全一致。"""
        research_task_map = build_research_task_map(self.messages)
        data = ChatMessageSerializer(
            self.messages, many=True, context={"research_task_map": research_task_map}
        ).data

        for msg, item in zip(self.messages, data, strict=True):
            self.assertEqual(item["research_task_status"], _legacy_status(msg.research_task_id))
            self.assertEqual(item["research_task_deleted"], _legacy_deleted(msg.research_task_id))

        # 关键语义显式锁定
        self.assertEqual(data[0]["research_task_status"], ResearchTaskStatus.RUNNING)
        self.assertFalse(data[0]["research_task_deleted"])
        self.assertEqual(data[1]["research_task_status"], ResearchTaskStatus.COMPLETED)
        self.assertTrue(data[1]["research_task_deleted"])
        self.assertIsNone(data[2]["research_task_status"])
        self.assertTrue(data[2]["research_task_deleted"])
        self.assertIsNone(data[3]["research_task_status"])
        self.assertIsNone(data[3]["research_task_deleted"])
        self.assertIsNone(data[4]["research_task_status"])
        self.assertIsNone(data[4]["research_task_deleted"])

    def test_batch_mapping_single_research_query(self):
        """映射构建 + 序列化全程 research_task 表查询恒为 1 次（含软删除）。"""
        with CaptureQueriesContext(connection) as ctx:
            research_task_map = build_research_task_map(self.messages)
            data = ChatMessageSerializer(
                self.messages, many=True, context={"research_task_map": research_task_map}
            ).data

        self.assertEqual(_research_query_count(ctx.captured_queries), 1)
        self.assertEqual(len(data), 5)
        # 映射含软删除任务（all_objects 语义保持）
        self.assertIn("task-deleted", research_task_map)
        self.assertTrue(research_task_map["task-deleted"].is_deleted)

    def test_no_research_ids_no_query(self):
        """消息集无 research_task_id 时不发起任何 research 查询。"""
        plain_messages = [
            ChatMessage.objects.create(session=self.session, role="user", content="x"),
            ChatMessage.objects.create(session=self.session, role="user", content="y"),
        ]
        with CaptureQueriesContext(connection) as ctx:
            research_task_map = build_research_task_map(plain_messages)
            data = ChatMessageSerializer(
                plain_messages, many=True, context={"research_task_map": research_task_map}
            ).data

        self.assertEqual(research_task_map, {})
        self.assertEqual(_research_query_count(ctx.captured_queries), 0)
        self.assertEqual(len(data), 2)

    def test_nested_session_detail_serializer_single_query(self):
        """ChatSessionDetailSerializer 嵌套 messages 路径同样只查 1 次。"""
        session = ChatSession.objects.prefetch_related("messages", "messages__attachments").get(
            pk=self.session.pk
        )
        with CaptureQueriesContext(connection) as ctx:
            data = ChatSessionDetailSerializer(
                session, context={"research_task_map": build_research_task_map(session.messages.all())}
            ).data

        self.assertEqual(_research_query_count(ctx.captured_queries), 1)
        self.assertEqual(len(data["messages"]), 5)
        for msg, item in zip(self.messages, data["messages"], strict=True):
            self.assertEqual(item["research_task_status"], _legacy_status(msg.research_task_id))
            self.assertEqual(item["research_task_deleted"], _legacy_deleted(msg.research_task_id))

    def test_missing_context_fails_fast(self):
        """契约：序列化缺 research_task_map 时 KeyError 快速失败（无逐条查询兜底）。"""
        with self.assertRaises(KeyError):
            _ = ChatMessageSerializer(self.messages[0]).data

    def test_single_message_map_semantics(self):
        """单条消息场景：单元素映射输出与逐条查询版一致。"""
        msg = self.messages[0]
        with CaptureQueriesContext(connection) as ctx:
            data = ChatMessageSerializer(
                msg, context={"research_task_map": build_research_task_map([msg])}
            ).data

        self.assertEqual(_research_query_count(ctx.captured_queries), 1)
        self.assertEqual(data["research_task_status"], ResearchTaskStatus.RUNNING)
        self.assertFalse(data["research_task_deleted"])
