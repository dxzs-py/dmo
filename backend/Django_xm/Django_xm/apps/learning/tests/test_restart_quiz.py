"""restart_quiz 功能单元测试

验证:
1. restart_quiz 创建新 thread_id（不同于旧 thread_id）
2. 新 thread_id 的 session 已创建
3. retry_count 递增
4. 旧 session 的 retry_count 已更新
5. 新 session 的 learning_plan 与旧 session 相同
6. 新题目已持久化为 WorkflowQuestion（attempt_index = new_retry_count）
7. 旧 session 不存在时抛出 ValueError
8. learning_plan 不存在时抛出 ValueError

运行方式:
    conda activate langchain_xm
    cd d:\\programming\\langchain\\langchain_xm\\backend\\Django_xm
    python manage.py test Django_xm.apps.learning.tests.test_restart_quiz -v 2 --keepdb
"""

from unittest.mock import patch, MagicMock

from django.contrib.auth import get_user_model
from django.test import TestCase

from Django_xm.apps.learning.models import (
    WorkflowSession,
    WorkflowQuestion,
    WorkflowQuestionStatus,
)
from Django_xm.apps.learning.services.study_flow import restart_quiz

User = get_user_model()


class RestartQuizTestCase(TestCase):
    """restart_quiz 功能测试"""

    def setUp(self):
        """创建测试数据"""
        self.user = User.objects.create_user(
            username="testuser",
            password="testpass123",
        )
        self.old_thread_id = "test_thread_old_001"
        self.learning_plan = {
            "topic": "Python 基础",
            "objectives": ["掌握变量", "理解循环", "学会函数"],
            "key_points": ["变量", "循环", "函数", "列表", "字典"],
            "difficulty": "beginner",
            "estimated_time": 120,
        }
        self.old_session = WorkflowSession.objects.create(
            thread_id=self.old_thread_id,
            user_question="学习 Python",
            learning_plan=self.learning_plan,
            retry_count=0,
            current_step="feedback_completed",
            phase="completed",
            created_by=self.user,
        )

    def _build_mock_study_flow(self, invoke_result=None, side_effect=None):
        """构建 mock StudyFlow

        Args:
            invoke_result: invoke 方法返回的预设结果
            side_effect: invoke 方法的 side_effect（优先于 invoke_result）

        Returns:
            mock StudyFlow 实例
        """
        mock_flow = MagicMock()
        if side_effect is not None:
            mock_flow.invoke.side_effect = side_effect
        else:
            mock_flow.invoke.return_value = invoke_result or {
                "learning_plan": self.learning_plan,
                "quiz": {
                    "questions": [{"id": "q1", "question": "新题目"}],
                    "total_points": 100,
                    "time_limit": 30,
                },
                "phase": "quiz",
                "current_step": "waiting_for_answers",
                "retry_count": 1,
                "updated_at": "2025-01-01T00:00:00",
            }
        return mock_flow

    @patch("Django_xm.apps.learning.services.study_flow._get_study_flow")
    @patch("Django_xm.apps.learning.services.study_flow.persistence_service")
    def test_restart_quiz_creates_new_thread_id(
        self, mock_persistence, mock_get_study_flow
    ):
        """restart_quiz 创建新 thread_id（不同于旧 thread_id）"""
        mock_get_study_flow.return_value = self._build_mock_study_flow()

        result = restart_quiz(self.old_thread_id, user_id=self.user.id)

        # 验证返回结果包含 new_thread_id
        self.assertIn("new_thread_id", result)
        # 验证新 thread_id 不同于旧 thread_id
        self.assertNotEqual(result["new_thread_id"], self.old_thread_id)
        # 验证新 thread_id 以 study_ 开头
        self.assertTrue(result["new_thread_id"].startswith("study_"))

    @patch("Django_xm.apps.learning.services.study_flow._get_study_flow")
    @patch("Django_xm.apps.learning.services.study_flow.persistence_service")
    def test_restart_quiz_creates_new_session(
        self, mock_persistence, mock_get_study_flow
    ):
        """新 thread_id 的 session 已创建"""
        mock_get_study_flow.return_value = self._build_mock_study_flow()

        result = restart_quiz(self.old_thread_id, user_id=self.user.id)

        # 验证新 session 已创建（预创建）
        new_session = WorkflowSession.objects.filter(
            thread_id=result["new_thread_id"],
            is_deleted=False
        ).first()
        self.assertIsNotNone(new_session)

    @patch("Django_xm.apps.learning.services.study_flow._get_study_flow")
    @patch("Django_xm.apps.learning.services.study_flow.persistence_service")
    def test_restart_quiz_increments_retry_count(
        self, mock_persistence, mock_get_study_flow
    ):
        """retry_count 递增"""
        mock_get_study_flow.return_value = self._build_mock_study_flow()

        result = restart_quiz(self.old_thread_id, user_id=self.user.id)

        # 验证新 session 的 retry_count = 旧值 + 1
        new_session = WorkflowSession.objects.get(
            thread_id=result["new_thread_id"]
        )
        self.assertEqual(new_session.retry_count, 1)

        # 验证返回结果中的 retry_count
        self.assertEqual(result["retry_count"], 1)

    @patch("Django_xm.apps.learning.services.study_flow._get_study_flow")
    @patch("Django_xm.apps.learning.services.study_flow.persistence_service")
    def test_restart_quiz_updates_old_session_retry_count(
        self, mock_persistence, mock_get_study_flow
    ):
        """旧 session 的 retry_count 已更新"""
        mock_get_study_flow.return_value = self._build_mock_study_flow()

        restart_quiz(self.old_thread_id, user_id=self.user.id)

        # 刷新旧 session
        self.old_session.refresh_from_db()
        self.assertEqual(self.old_session.retry_count, 1)

    @patch("Django_xm.apps.learning.services.study_flow._get_study_flow")
    @patch("Django_xm.apps.learning.services.study_flow.persistence_service")
    def test_restart_quiz_reuses_learning_plan(
        self, mock_persistence, mock_get_study_flow
    ):
        """新 session 的 learning_plan 与旧 session 相同"""
        mock_get_study_flow.return_value = self._build_mock_study_flow()

        result = restart_quiz(self.old_thread_id, user_id=self.user.id)

        # 验证新 session 的 learning_plan = 旧 session 的 learning_plan
        new_session = WorkflowSession.objects.get(
            thread_id=result["new_thread_id"]
        )
        self.assertEqual(new_session.learning_plan, self.learning_plan)

    @patch("Django_xm.apps.learning.services.study_flow._get_study_flow")
    @patch("Django_xm.apps.learning.services.study_flow.persistence_service")
    @patch(
        "Django_xm.apps.learning.nodes.quiz_generator_node.get_structured_model_from_state"
    )
    def test_restart_quiz_persists_questions(
        self, mock_get_model, mock_persistence, mock_get_study_flow
    ):
        """新题目已持久化为 WorkflowQuestion（attempt_index = new_retry_count）"""
        # 模拟 LLM 返回
        mock_model = MagicMock()
        mock_quiz = MagicMock()
        mock_quiz.questions = [
            MagicMock(
                id="q1",
                type="multiple_choice",
                question="新题1",
                options=["A", "B"],
                answer="A",
                explanation="解析1",
                points=10,
            ),
            MagicMock(
                id="q2",
                type="fill_blank",
                question="新题2",
                options=None,
                answer="答案",
                explanation="解析2",
                points=20,
            ),
        ]
        mock_quiz.total_points = 30
        mock_quiz.time_limit = 30
        mock_model.invoke.return_value = mock_quiz
        mock_get_model.return_value = mock_model

        # 让 invoke 调用真实的 quiz_generator_node 生成并持久化题目
        from Django_xm.apps.learning.nodes.quiz_generator_node import (
            quiz_generator_node,
        )

        def invoke_side_effect(initial_state, config=None):
            """模拟工作流执行：调用 quiz_generator_node 生成题目"""
            result = quiz_generator_node(initial_state)
            # 合并初始状态和 quiz_generator_node 的返回
            return {
                **initial_state,
                **result,
            }

        mock_flow = self._build_mock_study_flow(side_effect=invoke_side_effect)
        mock_get_study_flow.return_value = mock_flow

        result = restart_quiz(self.old_thread_id, user_id=self.user.id)

        # 验证新 session 的题目已持久化
        new_session = WorkflowSession.objects.get(
            thread_id=result["new_thread_id"]
        )
        questions = WorkflowQuestion.objects.filter(
            session=new_session,
            attempt_index=1,  # new_retry_count
        ).order_by("question_index")

        self.assertEqual(questions.count(), 2)
        self.assertEqual(questions[0].question_id, "q1_r1")
        self.assertEqual(questions[1].question_id, "q2_r1")
        self.assertEqual(questions[0].attempt_index, 1)
        self.assertEqual(questions[1].attempt_index, 1)
        self.assertEqual(questions[0].status, WorkflowQuestionStatus.PENDING)
        self.assertEqual(questions[1].status, WorkflowQuestionStatus.PENDING)

    @patch("Django_xm.apps.learning.services.study_flow._get_study_flow")
    @patch("Django_xm.apps.learning.services.study_flow.persistence_service")
    def test_restart_quiz_raises_when_session_not_found(
        self, mock_persistence, mock_get_study_flow
    ):
        """旧 session 不存在时抛出 ValueError"""
        mock_get_study_flow.return_value = self._build_mock_study_flow()

        with self.assertRaises(ValueError) as ctx:
            restart_quiz("non_existent_thread", user_id=self.user.id)

        self.assertIn("不存在", str(ctx.exception))

    @patch("Django_xm.apps.learning.services.study_flow._get_study_flow")
    @patch("Django_xm.apps.learning.services.study_flow.persistence_service")
    def test_restart_quiz_raises_when_no_learning_plan(
        self, mock_persistence, mock_get_study_flow
    ):
        """learning_plan 不存在时抛出 ValueError"""
        # 更新旧 session 的 learning_plan 为 None
        WorkflowSession.objects.filter(thread_id=self.old_thread_id).update(
            learning_plan=None
        )

        mock_get_study_flow.return_value = self._build_mock_study_flow()

        with self.assertRaises(ValueError) as ctx:
            restart_quiz(self.old_thread_id, user_id=self.user.id)

        self.assertIn("学习计划不存在", str(ctx.exception))
