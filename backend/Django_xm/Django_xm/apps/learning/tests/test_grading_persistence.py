"""grading_node 评分结果持久化单元测试

验证：
1. 评分完成后更新 WorkflowQuestion 记录
2. 更新 user_answer、is_correct、points_earned、scored_at
3. status 从 pending 改为 scored
4. 通过 question_id 和 session 匹配

运行方式：
    conda activate langchain_xm
    cd d:\\programming\\langchain\\langchain_xm\\backend\\Django_xm
    python manage.py test Django_xm.apps.learning.tests.test_grading_persistence -v 2 --keepdb
"""

from unittest.mock import patch, MagicMock

from django.test import TestCase

from Django_xm.apps.learning.models import (
    WorkflowSession,
    WorkflowQuestion,
    WorkflowQuestionStatus,
)
from Django_xm.apps.learning.nodes.grading_node import grading_node


class GradingPersistenceTestCase(TestCase):
    """grading_node 评分结果持久化测试"""

    def setUp(self):
        """创建测试数据"""
        self.session = WorkflowSession.objects.create(
            thread_id="test_grading_001",
            user_question="学习 Python",
            learning_plan={"topic": "Python"},
        )

        # 创建待评分的题目
        WorkflowQuestion.objects.create(
            session=self.session,
            question_id="q1_r0",
            attempt_index=0,
            question_index=1,
            type="multiple_choice",
            question="Python 是什么类型的语言？",
            options=["编译型", "解释型", "汇编型", "机器型"],
            correct_answer="B",
            explanation="Python 是解释型语言",
            points=10,
            status=WorkflowQuestionStatus.PENDING,
        )
        WorkflowQuestion.objects.create(
            session=self.session,
            question_id="q2_r0",
            attempt_index=0,
            question_index=2,
            type="fill_blank",
            question="Python 的缩进使用___",
            correct_answer="空格",
            explanation="Python 使用空格缩进",
            points=20,
            status=WorkflowQuestionStatus.PENDING,
        )

    @patch("Django_xm.apps.ai_engine.services.llm_factory.get_helper_model")
    def test_update_questions_after_grading(self, mock_get_model):
        """评分完成后更新 WorkflowQuestion 记录"""
        # 简答题才需要 LLM，这里无简答题，mock 不会被 invoke
        mock_model = MagicMock()
        mock_get_model.return_value = mock_model

        state = {
            "thread_id": "test_grading_001",
            "quiz": {
                "questions": [
                    {
                        "id": "q1",
                        "type": "multiple_choice",
                        "question": "Python 是什么？",
                        "options": ["A", "B"],
                        "answer": "B",
                        "explanation": "解析",
                        "points": 10,
                    },
                    {
                        "id": "q2",
                        "type": "fill_blank",
                        "question": "Python 的缩进？",
                        "answer": "空格",
                        "explanation": "解析",
                        "points": 20,
                    },
                ],
                "total_points": 30,
            },
            "user_answers": {"q1": "B", "q2": "空格"},
            "retry_count": 0,
            "messages": [],
        }

        result = grading_node(state)

        # 验证评分结果
        self.assertEqual(result["score"], 100)  # 全对

        # 验证 WorkflowQuestion 已更新
        q1 = WorkflowQuestion.objects.get(session=self.session, question_id="q1_r0")
        self.assertEqual(q1.user_answer, "B")
        self.assertTrue(q1.is_correct)
        self.assertEqual(q1.points_earned, 10)
        self.assertEqual(q1.status, WorkflowQuestionStatus.SCORED)
        self.assertIsNotNone(q1.scored_at)

        q2 = WorkflowQuestion.objects.get(session=self.session, question_id="q2_r0")
        self.assertEqual(q2.user_answer, "空格")
        self.assertTrue(q2.is_correct)
        self.assertEqual(q2.points_earned, 20)
        self.assertEqual(q2.status, WorkflowQuestionStatus.SCORED)

    @patch("Django_xm.apps.ai_engine.services.llm_factory.get_helper_model")
    def test_update_incorrect_answer(self, mock_get_model):
        """评分错误答案时更新 is_correct=False"""
        mock_model = MagicMock()
        mock_get_model.return_value = mock_model

        state = {
            "thread_id": "test_grading_001",
            "quiz": {
                "questions": [
                    {
                        "id": "q1",
                        "type": "multiple_choice",
                        "question": "Python 是什么？",
                        "options": ["A", "B"],
                        "answer": "B",
                        "explanation": "解析",
                        "points": 10,
                    },
                ],
                "total_points": 10,
            },
            "user_answers": {"q1": "A"},  # 错误答案
            "retry_count": 0,
            "messages": [],
        }

        grading_node(state)

        q1 = WorkflowQuestion.objects.get(session=self.session, question_id="q1_r0")
        self.assertEqual(q1.user_answer, "A")
        self.assertFalse(q1.is_correct)
        self.assertEqual(q1.points_earned, 0)
        self.assertEqual(q1.status, WorkflowQuestionStatus.SCORED)

    @patch("Django_xm.apps.ai_engine.services.llm_factory.get_helper_model")
    def test_persist_skipped_when_no_session(self, mock_get_model):
        """无 session 时跳过持久化，不报错"""
        mock_model = MagicMock()
        mock_get_model.return_value = mock_model

        state = {
            "thread_id": "nonexistent_thread",
            "quiz": {
                "questions": [
                    {
                        "id": "q1",
                        "type": "multiple_choice",
                        "question": "题",
                        "options": ["A"],
                        "answer": "A",
                        "explanation": "解析",
                        "points": 10,
                    },
                ],
                "total_points": 10,
            },
            "user_answers": {"q1": "A"},
            "retry_count": 0,
            "messages": [],
        }

        # 不应抛出异常
        result = grading_node(state)
        self.assertIsNotNone(result)
