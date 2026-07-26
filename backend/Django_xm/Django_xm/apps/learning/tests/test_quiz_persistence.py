"""quiz_generator_node 题目持久化单元测试

验证：
1. 生成题目后持久化为 WorkflowQuestion 记录
2. question_id 格式为 q{idx}_r{attempt_index}
3. status 初始为 pending
4. 继续练习时（attempt_index > 0）新题目追加，旧题目保留

运行方式：
    conda activate langchain_xm
    cd d:\\programming\\langchain\\langchain_xm\\backend\\Django_xm
    python manage.py test Django_xm.apps.learning.tests.test_quiz_persistence -v 2
"""

from unittest.mock import patch, MagicMock

from django.test import TestCase

from Django_xm.apps.learning.models import (
    WorkflowSession,
    WorkflowQuestion,
    WorkflowQuestionStatus,
)
from Django_xm.apps.learning.nodes.quiz_generator_node import quiz_generator_node


class QuizPersistenceTestCase(TestCase):
    """quiz_generator_node 题目持久化测试"""

    def setUp(self):
        """创建测试数据"""
        self.session = WorkflowSession.objects.create(
            thread_id="test_thread_001",
            user_question="学习 Python",
            learning_plan={"topic": "Python", "difficulty": "初级", "key_points": ["变量"]},
        )

    @patch("Django_xm.apps.learning.nodes.quiz_generator_node.get_structured_model_from_state")
    def test_persist_questions_on_generate(self, mock_get_model):
        """生成题目后持久化为 WorkflowQuestion 记录"""
        # 模拟 LLM 返回
        mock_model = MagicMock()
        mock_quiz = MagicMock()
        mock_quiz.questions = [
            MagicMock(id="q1", type="multiple_choice", question="题1", options=["A", "B"], answer="A", explanation="解析1", points=10),
            MagicMock(id="q2", type="fill_blank", question="题2", options=None, answer="答案", explanation="解析2", points=20),
        ]
        mock_quiz.total_points = 30
        mock_quiz.time_limit = 30
        mock_model.invoke.return_value = mock_quiz
        mock_get_model.return_value = mock_model

        state = {
            "thread_id": "test_thread_001",
            "learning_plan": {"topic": "Python", "difficulty": "初级", "key_points": ["变量"]},
            "retrieved_docs": [],
            "retry_count": 0,
            "messages": [],
        }

        result = quiz_generator_node(state)

        # 验证主流程正常返回
        self.assertIsNotNone(result)
        self.assertIsNotNone(result["quiz"])

        # 验证题目已持久化
        questions = WorkflowQuestion.objects.filter(session=self.session).order_by("question_index")
        self.assertEqual(questions.count(), 2)

        # 验证 question_id 格式
        self.assertEqual(questions[0].question_id, "q1_r0")
        self.assertEqual(questions[1].question_id, "q2_r0")

        # 验证 attempt_index
        self.assertEqual(questions[0].attempt_index, 0)
        self.assertEqual(questions[1].attempt_index, 0)

        # 验证 question_index
        self.assertEqual(questions[0].question_index, 1)
        self.assertEqual(questions[1].question_index, 2)

        # 验证 status
        self.assertEqual(questions[0].status, WorkflowQuestionStatus.PENDING)
        self.assertEqual(questions[1].status, WorkflowQuestionStatus.PENDING)

        # 验证 correct_answer（从 answer 字段映射）
        self.assertEqual(questions[0].correct_answer, "A")
        self.assertEqual(questions[1].correct_answer, "答案")

        # 验证其他字段
        self.assertEqual(questions[0].question, "题1")
        self.assertEqual(questions[0].points, 10)
        self.assertEqual(questions[0].explanation, "解析1")
        self.assertEqual(questions[0].type, "multiple_choice")

    @patch("Django_xm.apps.learning.nodes.quiz_generator_node.get_structured_model_from_state")
    def test_append_questions_on_retry(self, mock_get_model):
        """继续练习时（attempt_index=1）新题目追加，旧题目保留"""
        # 先创建第1轮题目
        WorkflowQuestion.objects.create(
            session=self.session,
            question_id="q1_r0",
            attempt_index=0,
            question_index=1,
            type="multiple_choice",
            question="旧题1",
            correct_answer="A",
            explanation="旧解析",
            points=10,
            status=WorkflowQuestionStatus.LOCKED,
        )

        # 模拟第2轮 LLM 返回
        mock_model = MagicMock()
        mock_quiz = MagicMock()
        mock_quiz.questions = [
            MagicMock(id="q1", type="multiple_choice", question="新题1", options=["A", "B"], answer="B", explanation="新解析", points=10),
        ]
        mock_quiz.total_points = 10
        mock_quiz.time_limit = 15
        mock_model.invoke.return_value = mock_quiz
        mock_get_model.return_value = mock_model

        state = {
            "thread_id": "test_thread_001",
            "learning_plan": {"topic": "Python", "difficulty": "初级", "key_points": ["变量"]},
            "retrieved_docs": [],
            "retry_count": 1,  # 第2轮
            "messages": [],
        }

        result = quiz_generator_node(state)

        # 验证主流程正常返回
        self.assertIsNotNone(result)

        # 验证旧题目保留
        old_question = WorkflowQuestion.objects.get(session=self.session, question_id="q1_r0")
        self.assertEqual(old_question.question, "旧题1")
        self.assertEqual(old_question.status, WorkflowQuestionStatus.LOCKED)

        # 验证新题目已追加
        new_question = WorkflowQuestion.objects.get(session=self.session, question_id="q1_r1")
        self.assertEqual(new_question.question, "新题1")
        self.assertEqual(new_question.attempt_index, 1)
        self.assertEqual(new_question.question_index, 1)
        self.assertEqual(new_question.status, WorkflowQuestionStatus.PENDING)
        self.assertEqual(new_question.correct_answer, "B")

        # 验证总题目数
        self.assertEqual(WorkflowQuestion.objects.filter(session=self.session).count(), 2)

    @patch("Django_xm.apps.learning.nodes.quiz_generator_node.get_structured_model_from_state")
    def test_delete_old_questions_on_regenerate_same_attempt(self, mock_get_model):
        """同一轮次重新生成时删除旧题目"""
        # 先创建第1轮的旧题目
        WorkflowQuestion.objects.create(
            session=self.session,
            question_id="q1_r0",
            attempt_index=0,
            question_index=1,
            type="multiple_choice",
            question="旧题1",
            correct_answer="A",
            explanation="旧解析",
            points=10,
            status=WorkflowQuestionStatus.PENDING,
        )

        # 模拟重新生成
        mock_model = MagicMock()
        mock_quiz = MagicMock()
        mock_quiz.questions = [
            MagicMock(id="q1", type="multiple_choice", question="新题1", options=["A", "B"], answer="B", explanation="新解析", points=10),
            MagicMock(id="q2", type="fill_blank", question="新题2", options=None, answer="答案", explanation="解析2", points=20),
        ]
        mock_quiz.total_points = 30
        mock_quiz.time_limit = 30
        mock_model.invoke.return_value = mock_quiz
        mock_get_model.return_value = mock_model

        state = {
            "thread_id": "test_thread_001",
            "learning_plan": {"topic": "Python", "difficulty": "初级", "key_points": ["变量"]},
            "retrieved_docs": [],
            "retry_count": 0,  # 同一轮次
            "messages": [],
        }

        quiz_generator_node(state)

        # 验证旧题目被删除，新题目已创建
        questions = WorkflowQuestion.objects.filter(session=self.session, attempt_index=0)
        self.assertEqual(questions.count(), 2)
        self.assertFalse(questions.filter(question="旧题1").exists())
        self.assertTrue(questions.filter(question="新题1").exists())
        self.assertTrue(questions.filter(question="新题2").exists())

        # 验证 question_id 重新生成
        self.assertTrue(questions.filter(question_id="q1_r0").exists())
        self.assertTrue(questions.filter(question_id="q2_r0").exists())

    @patch("Django_xm.apps.learning.nodes.quiz_generator_node.get_structured_model_from_state")
    def test_persist_skipped_when_no_session(self, mock_get_model):
        """thread_id 不存在对应 session 时跳过持久化（不报错）"""
        mock_model = MagicMock()
        mock_quiz = MagicMock()
        mock_quiz.questions = [
            MagicMock(id="q1", type="multiple_choice", question="题1", options=["A"], answer="A", explanation="解析", points=10),
        ]
        mock_quiz.total_points = 10
        mock_quiz.time_limit = 10
        mock_model.invoke.return_value = mock_quiz
        mock_get_model.return_value = mock_model

        state = {
            "thread_id": "non_existent_thread",
            "learning_plan": {"topic": "Python", "difficulty": "初级", "key_points": ["变量"]},
            "retrieved_docs": [],
            "retry_count": 0,
            "messages": [],
        }

        # 不应抛出异常
        result = quiz_generator_node(state)
        self.assertIsNotNone(result)
        self.assertIsNotNone(result["quiz"])

        # 不应创建任何题目
        self.assertEqual(WorkflowQuestion.objects.filter(session=self.session).count(), 0)

    @patch("Django_xm.apps.learning.nodes.quiz_generator_node.get_structured_model_from_state")
    def test_persist_skipped_when_no_thread_id(self, mock_get_model):
        """state 中无 thread_id 时跳过持久化（不报错）"""
        mock_model = MagicMock()
        mock_quiz = MagicMock()
        mock_quiz.questions = [
            MagicMock(id="q1", type="multiple_choice", question="题1", options=["A"], answer="A", explanation="解析", points=10),
        ]
        mock_quiz.total_points = 10
        mock_quiz.time_limit = 10
        mock_model.invoke.return_value = mock_quiz
        mock_get_model.return_value = mock_model

        state = {
            "thread_id": None,
            "learning_plan": {"topic": "Python", "difficulty": "初级", "key_points": ["变量"]},
            "retrieved_docs": [],
            "retry_count": 0,
            "messages": [],
        }

        # 不应抛出异常
        result = quiz_generator_node(state)
        self.assertIsNotNone(result)
        self.assertIsNotNone(result["quiz"])

        # 不应创建任何题目
        self.assertEqual(WorkflowQuestion.objects.filter(session=self.session).count(), 0)
