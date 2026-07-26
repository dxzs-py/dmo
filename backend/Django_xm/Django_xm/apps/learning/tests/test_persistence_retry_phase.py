"""persistence_service retry_count/phase 持久化单元测试

验证：
1. save_workflow_state 正确保存 retry_count 和 phase
2. load_workflow_state 正确加载 retry_count 和 phase
3. None 值防御性处理
4. 缺失字段防御性处理
5. 更新已有 session 的 retry_count 和 phase

运行方式：
    conda activate langchain_xm
    cd d:\\programming\\langchain\\langchain_xm\\backend\\Django_xm
    python manage.py test Django_xm.apps.learning.tests.test_persistence_retry_phase -v 2
"""

from django.test import TestCase

from Django_xm.apps.learning.models import WorkflowSession
from Django_xm.apps.learning.services.persistence_service import (
    get_persistence_service,
)


class PersistenceRetryPhaseTestCase(TestCase):
    """persistence_service retry_count/phase 持久化测试"""

    def setUp(self):
        self.persistence_service = get_persistence_service()
        self.thread_id = "test_persistence_001"

    def test_save_retry_count_and_phase(self):
        """save_workflow_state 正确保存 retry_count 和 phase"""
        state = {
            "messages": [],
            "user_question": "学习 Python",
            "learning_plan": {"topic": "Python"},
            "quiz": None,
            "user_answers": None,
            "score": None,
            "score_details": None,
            "feedback": None,
            "retry_count": 2,
            "phase": "quiz",
            "current_step": "waiting_for_answers",
            "thread_id": self.thread_id,
        }

        self.persistence_service.save_workflow_state(
            thread_id=self.thread_id,
            state=state,
            user_id=None,
        )

        session = WorkflowSession.objects.get(thread_id=self.thread_id)
        self.assertEqual(session.retry_count, 2)
        self.assertEqual(session.phase, "quiz")

    def test_load_retry_count_and_phase(self):
        """load_workflow_state 正确加载 retry_count 和 phase"""
        # 先创建 session
        WorkflowSession.objects.create(
            thread_id=self.thread_id,
            user_question="学习 Python",
            retry_count=3,
            phase="completed",
            learning_plan={"topic": "Python"},
        )

        state = self.persistence_service.load_workflow_state(self.thread_id)

        self.assertIsNotNone(state)
        self.assertEqual(state.get("retry_count"), 3)
        self.assertEqual(state.get("phase"), "completed")

    def test_save_none_retry_count_uses_default(self):
        """save_workflow_state 对 None retry_count 使用默认值 0"""
        state = {
            "messages": [],
            "user_question": "学习 Python",
            "learning_plan": None,
            "quiz": None,
            "user_answers": None,
            "score": None,
            "score_details": None,
            "feedback": None,
            "retry_count": None,  # None 值
            "phase": None,  # None 值
            "current_step": "start",
            "thread_id": self.thread_id,
        }

        self.persistence_service.save_workflow_state(
            thread_id=self.thread_id,
            state=state,
            user_id=None,
        )

        session = WorkflowSession.objects.get(thread_id=self.thread_id)
        self.assertEqual(session.retry_count, 0)
        self.assertEqual(session.phase, "planner")

    def test_save_missing_retry_count_uses_default(self):
        """save_workflow_state 对缺失的 retry_count 使用默认值 0"""
        state = {
            "messages": [],
            "user_question": "学习 Python",
            "learning_plan": None,
            "quiz": None,
            "user_answers": None,
            "score": None,
            "score_details": None,
            "feedback": None,
            # retry_count 和 phase 缺失
            "current_step": "start",
            "thread_id": self.thread_id,
        }

        self.persistence_service.save_workflow_state(
            thread_id=self.thread_id,
            state=state,
            user_id=None,
        )

        session = WorkflowSession.objects.get(thread_id=self.thread_id)
        self.assertEqual(session.retry_count, 0)
        self.assertEqual(session.phase, "planner")

    def test_update_existing_session_retry_count(self):
        """更新已有 session 的 retry_count 和 phase"""
        # 先创建 session
        WorkflowSession.objects.create(
            thread_id=self.thread_id,
            user_question="学习 Python",
            retry_count=0,
            phase="planner",
        )

        # 更新
        state = {
            "messages": [],
            "user_question": "学习 Python",
            "learning_plan": {"topic": "Python"},
            "quiz": None,
            "user_answers": None,
            "score": 50,
            "score_details": None,
            "feedback": None,
            "retry_count": 1,
            "phase": "feedback",
            "current_step": "feedback_completed",
            "thread_id": self.thread_id,
        }

        self.persistence_service.save_workflow_state(
            thread_id=self.thread_id,
            state=state,
            user_id=None,
        )

        session = WorkflowSession.objects.get(thread_id=self.thread_id)
        self.assertEqual(session.retry_count, 1)
        self.assertEqual(session.phase, "feedback")
        self.assertEqual(session.score, 50)
