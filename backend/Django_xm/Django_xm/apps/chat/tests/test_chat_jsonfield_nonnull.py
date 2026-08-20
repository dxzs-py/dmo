"""chat JSONField 非空不变式测试（dj-18 回归）。

覆盖：
- 未传可选字段创建 ChatMessage / ChatSession → JSONField 为空容器（[] / {}）而非 NULL
- 数据库存储层原生 SQL 双重验证非 NULL（NOT NULL 约束 + default 生效）
- ChatMessageSerializer payload 中 JSONField 不出现 null（空容器替代）

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.chat.tests.test_chat_jsonfield_nonnull
"""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

import json

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase

from Django_xm.apps.chat.models import ChatMessage, ChatSession
from Django_xm.apps.chat.serializers import ChatMessageSerializer, build_research_task_map

# list 语义 JSONField（空值为 []）
LIST_FIELDS = ("sources", "chain_of_thought", "suggestions", "versions", "tool_calls")
# dict 语义 JSONField（空值为 {}）
DICT_FIELDS = ("plan", "approval", "reasoning", "subagent_contents", "token_detail")
ALL_JSON_FIELDS = [*LIST_FIELDS, *DICT_FIELDS]


class ChatJSONFieldNonNullTests(TestCase):
    """dj-18：chat app JSONField 永不为 NULL，空值统一为空容器。"""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="dj18", password="pw123456")
        self.session = ChatSession.objects.create(user=self.user, title="s", session_id="sess-dj18")

    def test_message_defaults_are_empty_containers(self):
        """未传可选字段创建消息：list 字段 [] / dict 字段 {}，非 None。"""
        msg = ChatMessage.objects.create(session=self.session, role="assistant", content="hello")
        for field in LIST_FIELDS:
            self.assertEqual(getattr(msg, field), [])
        for field in DICT_FIELDS:
            self.assertEqual(getattr(msg, field), {})

    def test_message_storage_is_not_null(self):
        """数据库存储层非 NULL：原生 SQL 读取的列值均为空容器（psycopg 解码后）。"""
        msg = ChatMessage.objects.create(session=self.session, role="assistant", content="hello")
        columns = ", ".join(ALL_JSON_FIELDS)
        with connection.cursor() as cursor:
            cursor.execute(
                f'SELECT {columns} FROM "{ChatMessage._meta.db_table}" WHERE id = %s',
                [msg.pk],
            )
            row = cursor.fetchone()
        values = dict(zip(ALL_JSON_FIELDS, row))
        for field in LIST_FIELDS:
            self.assertEqual(json.loads(values[field]), [])
        for field in DICT_FIELDS:
            self.assertEqual(json.loads(values[field]), {})

    def test_session_selected_knowledge_bases_default(self):
        """ChatSession.selected_knowledge_bases 默认 []（模型层 + 存储层）。"""
        self.assertEqual(self.session.selected_knowledge_bases, [])
        with connection.cursor() as cursor:
            cursor.execute(
                f'SELECT selected_knowledge_bases FROM "{ChatSession._meta.db_table}" WHERE id = %s',
                [self.session.pk],
            )
            (value,) = cursor.fetchone()
        self.assertEqual(json.loads(value), [])

    def test_serializer_payload_jsonfields_never_null(self):
        """序列化 payload：JSONField 输出空容器，不出现 null。"""
        msg = ChatMessage.objects.create(session=self.session, role="assistant", content="hello")
        data = ChatMessageSerializer(
            msg, context={"research_task_map": build_research_task_map([msg])}
        ).data
        for field in LIST_FIELDS:
            self.assertEqual(data[field], [])
        for field in DICT_FIELDS:
            self.assertEqual(data[field], {})
