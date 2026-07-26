"""题目列表 + 单题重新评分 + 练习轮次历史 API 单元测试

运行方式：
    conda activate langchain_xm
    cd d:\\programming\\langchain\\langchain_xm\\backend\\Django_xm
    python manage.py test Django_xm.apps.learning.tests.test_question_api --keepdb
"""

from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from Django_xm.apps.learning.models import (
    WorkflowSession, WorkflowQuestion, WorkflowAttempt,
    WorkflowQuestionStatus, WorkflowQuestionType
)
from Django_xm.common.error_codes import ErrorCode

User = get_user_model()


class QuestionApiTestCase(TestCase):
    """题目 API 测试"""

    def setUp(self):
        self.user = User.objects.create_user(username="testuser", password="testpass123")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

        self.session = WorkflowSession.objects.create(
            thread_id="test_api_001",
            user_question="学习 Python",
            created_by=self.user,
            learning_plan={"topic": "Python"},
        )

        # 创建题目
        self.q1 = WorkflowQuestion.objects.create(
            session=self.session,
            question_id="q1_r0",
            attempt_index=0,
            question_index=1,
            type=WorkflowQuestionType.MULTIPLE_CHOICE,
            question="Python 是什么类型的语言？",
            options=["编译型", "解释型", "汇编型", "机器型"],
            correct_answer="B",
            explanation="Python 是解释型语言",
            points=10,
            user_answer="A",
            is_correct=False,
            points_earned=0,
            status=WorkflowQuestionStatus.LOCKED,
        )
        self.q2 = WorkflowQuestion.objects.create(
            session=self.session,
            question_id="q2_r0",
            attempt_index=0,
            question_index=2,
            type=WorkflowQuestionType.FILL_BLANK,
            question="Python 的缩进使用___",
            correct_answer="空格",
            explanation="Python 使用空格缩进",
            points=20,
            user_answer="空格",
            is_correct=True,
            points_earned=20,
            status=WorkflowQuestionStatus.LOCKED,
        )

        # 创建练习轮次
        WorkflowAttempt.objects.create(
            session=self.session,
            attempt_index=0,
            thread_id="test_api_001",
            total_score=50,
            feedback="良好",
        )

    def test_get_questions_list(self):
        """GET /api/v1/learning/<thread_id>/questions/ 返回题目列表"""
        response = self.client.get(f"/api/v1/learning/{self.session.thread_id}/questions/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["code"], ErrorCode.SUCCESS)
        self.assertEqual(len(data["data"]), 2)
        self.assertEqual(data["data"][0]["question_id"], "q1_r0")

    def test_get_questions_filter_by_attempt_index(self):
        """GET /api/v1/learning/<thread_id>/questions/?attempt_index=0 按 attempt_index 过滤"""
        response = self.client.get(
            f"/api/v1/learning/{self.session.thread_id}/questions/?attempt_index=0"
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data["data"]), 2)

    def test_get_questions_not_found(self):
        """GET 不存在的 thread_id 返回 404"""
        response = self.client.get("/api/v1/learning/nonexistent_thread/questions/")
        self.assertEqual(response.status_code, 404)

    def test_update_question_regrade_multiple_choice(self):
        """PUT /api/v1/learning/<thread_id>/questions/<question_id>/ 重新评分选择题"""
        response = self.client.put(
            f"/api/v1/learning/{self.session.thread_id}/questions/q1_r0/",
            {"user_answer": "B"},
            format="json"
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["code"], ErrorCode.SUCCESS)

        # 验证题目已更新
        self.q1.refresh_from_db()
        self.assertEqual(self.q1.user_answer, "B")
        self.assertTrue(self.q1.is_correct)
        self.assertEqual(self.q1.points_earned, 10)
        self.assertEqual(self.q1.status, WorkflowQuestionStatus.SCORED)

    def test_update_question_regrade_fill_blank(self):
        """PUT 重新评分填空题"""
        response = self.client.put(
            f"/api/v1/learning/{self.session.thread_id}/questions/q2_r0/",
            {"user_answer": "tab"},
            format="json"
        )
        self.assertEqual(response.status_code, 200)

        self.q2.refresh_from_db()
        self.assertEqual(self.q2.user_answer, "tab")
        self.assertFalse(self.q2.is_correct)
        self.assertEqual(self.q2.points_earned, 0)

    def test_update_question_recalculates_attempt_score(self):
        """PUT 重新评分后重新计算 WorkflowAttempt.total_score"""
        # 初始 q1=0, q2=20, total=20/30*100=66
        response = self.client.put(
            f"/api/v1/learning/{self.session.thread_id}/questions/q1_r0/",
            {"user_answer": "B"},
            format="json"
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        # q1=10, q2=20, total=30/30*100=100
        self.assertEqual(data["data"]["attempt_total_score"], 100)

    def test_update_question_not_found(self):
        """PUT 不存在的题目返回 404"""
        response = self.client.put(
            f"/api/v1/learning/{self.session.thread_id}/questions/nonexistent/",
            {"user_answer": "A"},
            format="json"
        )
        self.assertEqual(response.status_code, 404)

    def test_get_attempts_list(self):
        """GET /api/v1/learning/<thread_id>/attempts/ 返回练习轮次历史"""
        response = self.client.get(f"/api/v1/learning/{self.session.thread_id}/attempts/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["code"], ErrorCode.SUCCESS)
        self.assertEqual(len(data["data"]), 1)
        self.assertEqual(data["data"][0]["attempt_index"], 0)
        self.assertEqual(data["data"][0]["total_score"], 50)
