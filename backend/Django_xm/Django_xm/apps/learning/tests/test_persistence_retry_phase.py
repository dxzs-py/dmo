"""persistence_service 状态字段持久化单元测试

验证：
1. save_workflow_state 正确保存 status、current_step、learning_plan、quiz、score 等字段
2. load_workflow_state 正确加载 status、current_step 等字段
3. current_step 到 status 的映射规则（waiting_for_answers/completed/failed/running）
4. 更新已有 session 字段

背景：
原 retry_count/phase 字段已在 migration 0009 删除，
当前 WorkflowSession 持久化字段为 status、current_step、learning_plan、quiz、
user_answers、score、score_details、feedback、should_retry、error_message。

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


class PersistenceStatusStepTestCase(TestCase):
    """persistence_service 状态字段持久化测试"""

    def setUp(self):
        self.persistence_service = get_persistence_service()
        self.thread_id = "test_persistence_001"

    def _build_state(self, **overrides):
        """构造默认 state，便于覆盖单个字段"""
        state = {
            "messages": [],
            "user_question": "学习 Python",
            "learning_plan": {"topic": "Python"},
            "quiz": None,
            "user_answers": None,
            "score": None,
            "score_details": None,
            "feedback": None,
            "should_retry": False,
            "current_step": "start",
            "thread_id": self.thread_id,
        }
        state.update(overrides)
        return state

    def test_save_status_and_current_step(self):
        """save_workflow_state 正确保存 status 和 current_step"""
        state = self._build_state(current_step="waiting_for_answers")

        self.persistence_service.save_workflow_state(
            thread_id=self.thread_id,
            state=state,
            user_id=None,
        )

        session = WorkflowSession.objects.get(thread_id=self.thread_id)
        self.assertEqual(session.current_step, "waiting_for_answers")
        self.assertEqual(session.status, "waiting_for_answers")

    def test_load_status_and_current_step(self):
        """load_workflow_state 正确加载 status 和 current_step"""
        WorkflowSession.objects.create(
            thread_id=self.thread_id,
            user_question="学习 Python",
            current_step="feedback_completed",
            status="completed",
            learning_plan={"topic": "Python"},
        )

        state = self.persistence_service.load_workflow_state(self.thread_id)

        self.assertIsNotNone(state)
        self.assertEqual(state.get("current_step"), "feedback_completed")
        self.assertEqual(state.get("status"), "completed")

    def test_current_step_to_status_mapping(self):
        """current_step 映射到正确的 status"""
        cases = [
            ("waiting_for_answers", "waiting_for_answers"),
            ("completed", "completed"),
            ("end", "completed"),
            ("feedback_completed", "completed"),
            ("failed", "failed"),
            ("retry", "retry"),
            ("planner", "running"),
            ("quiz_generator", "running"),
        ]
        for step, expected_status in cases:
            with self.subTest(current_step=step):
                thread_id = f"{self.thread_id}_{step}"
                state = self._build_state(current_step=step, thread_id=thread_id)

                self.persistence_service.save_workflow_state(
                    thread_id=thread_id,
                    state=state,
                    user_id=None,
                )

                session = WorkflowSession.objects.get(thread_id=thread_id)
                self.assertEqual(session.status, expected_status)

    def test_save_quiz_and_score_fields(self):
        """save_workflow_state 正确保存 quiz、score、score_details"""
        quiz = {
            "questions": [
                {"id": "q1", "type": "multiple_choice", "answer": "A", "points": 10}
            ],
            "total_points": 10,
        }
        score_details = {"correct_count": 1, "total_count": 1}
        state = self._build_state(
            quiz=quiz,
            score=80,
            score_details=score_details,
            feedback="良好",
            current_step="feedback_completed",
        )

        self.persistence_service.save_workflow_state(
            thread_id=self.thread_id,
            state=state,
            user_id=None,
        )

        session = WorkflowSession.objects.get(thread_id=self.thread_id)
        self.assertEqual(session.quiz, quiz)
        self.assertEqual(session.score, 80)
        self.assertEqual(session.score_details, score_details)
        self.assertEqual(session.feedback, "良好")
        self.assertEqual(session.status, "completed")

    def test_save_none_current_step_uses_default(self):
        """save_workflow_state 对缺失 current_step 使用默认 'start'"""
        state = self._build_state()
        state.pop("current_step")  # 删除 current_step key

        self.persistence_service.save_workflow_state(
            thread_id=self.thread_id,
            state=state,
            user_id=None,
        )

        session = WorkflowSession.objects.get(thread_id=self.thread_id)
        self.assertEqual(session.current_step, "start")
        self.assertEqual(session.status, "running")

    def test_save_error_message_from_error_key(self):
        """save_workflow_state 优先从 error key 写入 error_message"""
        state = self._build_state(
            current_step="failed",
            error="planner 节点超时",
        )

        self.persistence_service.save_workflow_state(
            thread_id=self.thread_id,
            state=state,
            user_id=None,
        )

        session = WorkflowSession.objects.get(thread_id=self.thread_id)
        self.assertEqual(session.error_message, "planner 节点超时")
        self.assertEqual(session.status, "failed")

    def test_update_existing_session_fields(self):
        """更新已有 session 的 status 和 current_step"""
        WorkflowSession.objects.create(
            thread_id=self.thread_id,
            user_question="学习 Python",
            current_step="waiting_for_answers",
            status="waiting_for_answers",
        )

        # 模拟评分完成后的状态更新
        state = self._build_state(
            current_step="feedback_completed",
            score=50,
            feedback="需加强",
            quiz={"questions": [], "total_points": 0},
        )

        self.persistence_service.save_workflow_state(
            thread_id=self.thread_id,
            state=state,
            user_id=None,
        )

        session = WorkflowSession.objects.get(thread_id=self.thread_id)
        self.assertEqual(session.current_step, "feedback_completed")
        self.assertEqual(session.status, "completed")
        self.assertEqual(session.score, 50)
        self.assertEqual(session.feedback, "需加强")
