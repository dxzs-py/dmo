"""WorkflowPersistenceService（工作流状态持久化服务）单元测试（ARCH-21）。

覆盖：
- save_workflow_state / load_workflow_state DB 往返（含状态映射与消息反序列化）
- _build_session_defaults：current_step → status 映射、可选字段仅在显式提供时覆盖
- _make_serializable：messages 序列化 / model_dump 对象 / 普通值透传
- _save_to_file / _load_from_file：文件系统往返（learning_plan.md / report.md 产物）
- load_workflow_state：DB 缺失时回退文件系统、user_id 隔离
- list_user_sessions：status / search / 软删除过滤

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.learning.tests.test_persistence_service --noinput
"""

import os
import tempfile
import uuid
from pathlib import Path
from unittest import TestCase as UnitTestCase

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from Django_xm.apps.learning.models import WorkflowSession
from Django_xm.apps.learning.services.persistence_service import WorkflowPersistenceService

User = get_user_model()


def _make_service(tmp_dir: str) -> WorkflowPersistenceService:
    """在临时 DATA_DIR 下新建服务实例（隔离文件系统副作用）"""
    with override_settings(DATA_DIR=Path(tmp_dir)):
        return WorkflowPersistenceService()


def _base_state(**overrides) -> dict:
    """可落库的最小工作流状态"""
    state = {
        "thread_id": f"t-{uuid.uuid4().hex[:8]}",
        "user_question": "什么是闭包",
        "current_step": "waiting_for_answers",
        "score": None,
    }
    state.update(overrides)
    return state


class SaveLoadRoundtripTests(TestCase):
    """save_workflow_state / load_workflow_state DB 往返"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.service = _make_service(self._tmp.name)

    def test_roundtrip_preserves_fields(self):
        """保存后加载：关键字段（quiz/score/current_step）一致"""
        state = _base_state(
            quiz={"questions": [{"id": "q1"}], "total_points": 10},
            score=80,
            current_step="feedback_completed",
            learning_plan={"topic": "闭包"},
        )
        self.service.save_workflow_state(state["thread_id"], state, user_id=None)
        loaded = self.service.load_workflow_state(state["thread_id"])
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["current_step"], "feedback_completed")
        self.assertEqual(loaded["score"], 80)
        self.assertEqual(loaded["quiz"]["total_points"], 10)
        self.assertEqual(loaded["learning_plan"]["topic"], "闭包")

    def test_status_mapping_waiting(self):
        """waiting_for_answers → session.status = waiting_for_answers"""
        state = _base_state(current_step="waiting_for_answers")
        self.service.save_workflow_state(state["thread_id"], state)
        session = WorkflowSession.objects.get(thread_id=state["thread_id"])
        self.assertEqual(session.status, "waiting_for_answers")

    def test_status_mapping_completed(self):
        """feedback_completed / end → session.status = completed"""
        for step in ("feedback_completed", "end", "completed"):
            state = _base_state(current_step=step)
            self.service.save_workflow_state(state["thread_id"], state)
            session = WorkflowSession.objects.get(thread_id=state["thread_id"])
            self.assertEqual(session.status, "completed", step)

    def test_status_mapping_retry_and_running(self):
        """retry → retry；其余执行中步骤 → running"""
        state = _base_state(current_step="retry")
        self.service.save_workflow_state(state["thread_id"], state)
        self.assertEqual(WorkflowSession.objects.get(thread_id=state["thread_id"]).status, "retry")

        state = _base_state(current_step="grading")
        self.service.save_workflow_state(state["thread_id"], state)
        self.assertEqual(WorkflowSession.objects.get(thread_id=state["thread_id"]).status, "running")

    def test_save_is_idempotent_single_row(self):
        """重复保存 update_or_create → 单行"""
        state = _base_state(score=10)
        self.service.save_workflow_state(state["thread_id"], state)
        self.service.save_workflow_state(state["thread_id"], {**state, "score": 90})
        self.assertEqual(WorkflowSession.objects.filter(thread_id=state["thread_id"]).count(), 1)
        loaded = self.service.load_workflow_state(state["thread_id"])
        self.assertEqual(loaded["score"], 90)

    def test_load_missing_returns_none(self):
        """不存在的 thread_id → None"""
        self.assertIsNone(self.service.load_workflow_state("no-such-thread"))

    def test_load_user_isolation(self):
        """用户隔离（表征）：DB 层按 created_by 过滤，但文件系统回退不校验 user_id。

        现状：非本人请求时 _load_from_database 返回 None → 回退 _load_from_file
        仍加载到状态（文件不绑定用户）。若后续修复文件回退的用户隔离，
        需同步更新本表征测试。
        """
        user = User.objects.create_user(username="persist-u1", password="pw123456")
        other = User.objects.create_user(username="persist-u2", password="pw123456")
        state = _base_state()
        self.service.save_workflow_state(state["thread_id"], state, user_id=user.id)
        # 本人可从 DB 加载
        self.assertIsNotNone(self.service.load_workflow_state(state["thread_id"], user_id=user.id))
        # 表征固定：非本人经文件回退仍可加载（隔离缺口，合并修复时更新）
        leaked = self.service.load_workflow_state(state["thread_id"], user_id=other.id)
        self.assertIsNotNone(leaked)
        self.assertEqual(leaked["thread_id"], state["thread_id"])


class SessionDefaultsTests(UnitTestCase):
    """_build_session_defaults：状态映射与部分更新策略"""

    def test_optional_fields_only_when_present(self):
        """state 未显式提供的可选字段不进入 defaults（避免覆盖预创建配置）"""
        defaults = WorkflowPersistenceService._build_session_defaults({"current_step": "grading"}, None)
        self.assertEqual(defaults["status"], "running")
        self.assertNotIn("root_thread_id", defaults)
        self.assertNotIn("provider_id", defaults)
        self.assertNotIn("knowledge_base_ids", defaults)

    def test_optional_fields_overridden_when_present(self):
        """state 显式提供时写入 defaults"""
        state = {
            "current_step": "grading",
            "root_thread_id": "root-1",
            "knowledge_base_ids": ["kb1"],
            "provider_id": "openai",
        }
        defaults = WorkflowPersistenceService._build_session_defaults(state, None)
        self.assertEqual(defaults["root_thread_id"], "root-1")
        self.assertEqual(defaults["knowledge_base_ids"], ["kb1"])
        self.assertEqual(defaults["provider_id"], "openai")

    def test_failed_step_maps_to_failed(self):
        """current_step=failed → status=failed"""
        defaults = WorkflowPersistenceService._build_session_defaults({"current_step": "failed"}, None)
        self.assertEqual(defaults["status"], "failed")

    def test_error_fallback_fields(self):
        """error / retry_count / should_retry 兜底值"""
        defaults = WorkflowPersistenceService._build_session_defaults({"current_step": "grading"}, None)
        self.assertEqual(defaults["error_message"], "")
        self.assertEqual(defaults["retry_count"], 0)
        self.assertFalse(defaults["should_retry"])


class SerializationTests(UnitTestCase):
    """_make_serializable / _serialize_message / _deserialize_messages"""

    def setUp(self):
        self.service = WorkflowPersistenceService.__new__(WorkflowPersistenceService)

    def test_messages_serialized_to_dicts(self):
        """LangChain 消息对象 → type/content 字典"""
        state = {"messages": [HumanMessage(content="hi"), AIMessage(content="hello")], "score": 1}
        serializable = self.service._make_serializable(state)
        self.assertEqual(serializable["messages"][0]["type"], "human")
        self.assertEqual(serializable["messages"][1]["type"], "ai")
        self.assertEqual(serializable["messages"][1]["content"], "hello")
        self.assertEqual(serializable["score"], 1)

    def test_model_dump_objects_serialized(self):
        """带 model_dump 的对象 → 字典"""
        class _Obj:
            def model_dump(self):
                return {"x": 1}

        serializable = self.service._make_serializable({"payload": _Obj()})
        self.assertEqual(serializable["payload"], {"x": 1})

    def test_nested_dict_recursed(self):
        """嵌套字典递归处理（dict 值递归，列表原样透传——仅顶层 messages 键序列化消息）"""
        state = {"quiz": {"total_points": 10}, "messages": [AIMessage(content="a")]}
        serializable = self.service._make_serializable(state)
        self.assertEqual(serializable["quiz"]["total_points"], 10)
        self.assertEqual(serializable["messages"][0]["type"], "ai")

    def test_non_message_list_passthrough(self):
        """非 messages 键的列表值原样保留（当前实现仅处理顶层 messages）"""
        sentinel = object()
        serializable = self.service._make_serializable({"items": [sentinel]})
        self.assertIs(serializable["items"][0], sentinel)

    def test_message_deserialize_roundtrip(self):
        """ai/human/system 消息反序列化回 LangChain 对象"""
        data = [
            {"type": "ai", "content": "a"},
            {"type": "human", "content": "h"},
            {"type": "system", "content": "s"},
            {"type": "unknown", "content": "u"},
        ]
        messages = self.service._deserialize_messages(data)
        self.assertIsInstance(messages[0], AIMessage)
        self.assertIsInstance(messages[1], HumanMessage)
        self.assertIsInstance(messages[2], SystemMessage)
        # 未知类型原样保留
        self.assertEqual(messages[3], {"type": "unknown", "content": "u"})

    def test_deserialize_state_restores_messages(self):
        """_deserialize_state 恢复 messages 键"""
        state = {"messages": [{"type": "ai", "content": "a"}], "score": 5}
        restored = self.service._deserialize_state(dict(state))
        self.assertIsInstance(restored["messages"][0], AIMessage)
        self.assertEqual(restored["score"], 5)


class FilePersistenceTests(TestCase):
    """_save_to_file / _load_from_file：文件系统往返（临时 DATA_DIR 隔离）"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.service = _make_service(self._tmp.name)

    def test_state_file_roundtrip(self):
        """state.json 保存 / 加载一致"""
        state = _base_state(score=66)
        self.service._save_to_file(state["thread_id"], state)
        loaded = self.service._load_from_file(state["thread_id"])
        self.assertEqual(loaded["score"], 66)

    def test_learning_plan_markdown_written(self):
        """learning_plan → learning_plan.md 产物"""
        state = _base_state(learning_plan="# 计划\n- 闭包")
        self.service._save_to_file(state["thread_id"], state)
        plan_file = self.service.workflow_data_dir / state["thread_id"] / "learning_plan.md"
        self.assertTrue(plan_file.exists())
        self.assertIn("闭包", plan_file.read_text(encoding="utf-8"))

    def test_report_markdown_written(self):
        """feedback + score → report.md 产物"""
        state = _base_state(feedback="很好", score=88)
        self.service._save_to_file(state["thread_id"], state)
        report_file = self.service.workflow_data_dir / state["thread_id"] / "report.md"
        content = report_file.read_text(encoding="utf-8")
        self.assertIn("很好", content)
        self.assertIn("88", content)

    def test_load_missing_file_returns_none(self):
        """无 state.json → None"""
        self.assertIsNone(self.service._load_from_file("no-such-thread"))

    def test_load_falls_back_to_file_when_db_empty(self):
        """DB 无记录 → load_workflow_state 回退文件系统"""
        state = _base_state(score=42)
        self.service._save_to_file(state["thread_id"], state)
        loaded = self.service.load_workflow_state(state["thread_id"])
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["score"], 42)


class ListUserSessionsTests(TestCase):
    """list_user_sessions：列表 / 筛选 / 软删除"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.service = _make_service(self._tmp.name)
        self.user = User.objects.create_user(username="persist-list", password="pw123456")
        WorkflowSession.objects.create(
            thread_id="ls-1", user_question="什么是装饰器", status="completed", created_by=self.user
        )
        WorkflowSession.objects.create(
            thread_id="ls-2", user_question="什么是闭包", status="running", created_by=self.user
        )
        deleted = WorkflowSession.objects.create(thread_id="ls-3", user_question="已删除", created_by=self.user)
        deleted.soft_delete()

    def test_lists_only_own_undeleted(self):
        """仅返回本人未软删除会话，按创建时间倒序"""
        sessions = list(self.service.list_user_sessions(self.user.id))
        self.assertEqual([s.thread_id for s in sessions], ["ls-2", "ls-1"])

    def test_status_filter_matches_status_or_step(self):
        """status 参数同时匹配 status 与 current_step 字段"""
        self.assertEqual(self.service.list_user_sessions(self.user.id, status="completed").count(), 1)
        self.assertEqual(self.service.list_user_sessions(self.user.id, status="running").count(), 1)

    def test_search_filter(self):
        """search 按问题模糊匹配"""
        self.assertEqual(self.service.list_user_sessions(self.user.id, search="闭包").count(), 1)

    def test_soft_deleted_excluded(self):
        """软删除会话不出现在列表"""
        self.assertEqual(self.service.list_user_sessions(self.user.id).count(), 2)


class SingletonTests(UnitTestCase):
    """get_persistence_service 单例"""

    def test_singleton_returns_same_instance(self):
        from Django_xm.apps.learning.services import persistence_service as module

        self.assertIs(module.get_persistence_service(), module.get_persistence_service())
