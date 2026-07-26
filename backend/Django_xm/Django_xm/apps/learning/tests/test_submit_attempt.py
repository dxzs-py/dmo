"""submit_answers 创建 WorkflowAttempt + 锁定题目单元测试

验证：
1. 评分完成后创建 WorkflowAttempt 记录
2. WorkflowAttempt 包含 attempt_index、thread_id、total_score、feedback
3. 评分完成后将当前轮次的 WorkflowQuestion.status 从 scored 改为 locked
4. attempt_index 正确自增（基于 retry_count）

运行方式：
    conda activate langchain_xm
    cd d:\\programming\\langchain\\langchain_xm\\backend\\Django_xm
    python manage.py test Django_xm.apps.learning.tests.test_submit_attempt -v 2 --keepdb
"""

from unittest.mock import patch, MagicMock

from django.test import TestCase

from Django_xm.apps.learning.models import (
    WorkflowSession,
    WorkflowQuestion,
    WorkflowAttempt,
    WorkflowQuestionStatus,
)


class SubmitAttemptTestCase(TestCase):
    """submit_answers 创建 WorkflowAttempt + 锁定题目测试"""

    def setUp(self):
        self.thread_id = "test_submit_001"
        self.session = WorkflowSession.objects.create(
            thread_id=self.thread_id,
            user_question="学习 Python",
            learning_plan={"topic": "Python"},
            retry_count=0,
            phase="quiz",
        )

        # 创建已评分的题目（attempt_index=0）
        WorkflowQuestion.objects.create(
            session=self.session,
            question_id="q1_r0",
            attempt_index=0,
            question_index=1,
            type="multiple_choice",
            question="题1",
            correct_answer="A",
            explanation="解析",
            points=10,
            user_answer="A",
            is_correct=True,
            points_earned=10,
            status=WorkflowQuestionStatus.SCORED,
        )
        WorkflowQuestion.objects.create(
            session=self.session,
            question_id="q2_r0",
            attempt_index=0,
            question_index=2,
            type="fill_blank",
            question="题2",
            correct_answer="答案",
            explanation="解析",
            points=20,
            user_answer="答案",
            is_correct=True,
            points_earned=20,
            status=WorkflowQuestionStatus.SCORED,
        )

    def _setup_common_mocks(
        self,
        mock_token_cb,
        mock_get_state,
        mock_persistence,
        mock_get_study_flow,
        mock_update_tokens,
        retry_count=0,
        score=100,
        feedback="优秀",
    ):
        """配置 submit_answers 依赖的通用 mock

        - study_flow.get_state 返回 quiz 阶段（提交前状态），用于通过状态守卫
        - get_workflow_state 返回评分完成后的状态（含 score/feedback/retry_count）
        - _update_workflow_session_tokens 屏蔽 token 统计写入
        """
        # mock study_flow：get_state 返回 quiz 阶段（提交前的状态）
        mock_study_flow = MagicMock()
        mock_state = MagicMock()
        mock_state.values = {
            "current_step": "waiting_for_answers",
            "phase": "quiz",
            "retry_count": retry_count,
        }
        mock_study_flow.get_state.return_value = mock_state
        mock_get_study_flow.return_value = mock_study_flow

        # mock get_workflow_state 返回评分完成后的状态
        mock_get_state.return_value = {
            "current_step": "feedback_completed",
            "phase": "completed",
            "retry_count": retry_count,
            "score": score,
            "feedback": feedback,
            "thread_id": self.thread_id,
        }

        # mock persistence_service
        mock_persistence.save_workflow_state = MagicMock()

        # mock TokenUsageCallbackHandler
        mock_cb = MagicMock()
        mock_cb.prompt_tokens = 10
        mock_cb.completion_tokens = 20
        mock_token_cb.return_value = mock_cb

        # mock _update_workflow_session_tokens（避免 MagicMock 相加报错）
        mock_update_tokens.return_value = None

    @patch("Django_xm.apps.learning.services.study_flow._update_workflow_session_tokens")
    @patch("Django_xm.apps.learning.services.study_flow._get_study_flow")
    @patch("Django_xm.apps.learning.services.study_flow.persistence_service")
    @patch("Django_xm.apps.learning.services.study_flow.get_workflow_state")
    @patch("Django_xm.apps.learning.services.study_flow.TokenUsageCallbackHandler")
    def test_create_attempt_after_grading(
        self,
        mock_token_cb,
        mock_get_state,
        mock_persistence,
        mock_get_study_flow,
        mock_update_tokens,
    ):
        """评分完成后创建 WorkflowAttempt 记录"""
        self._setup_common_mocks(
            mock_token_cb,
            mock_get_state,
            mock_persistence,
            mock_get_study_flow,
            mock_update_tokens,
        )

        from Django_xm.apps.learning.services.study_flow import submit_answers

        result = submit_answers(
            self.thread_id, {"q1": "A", "q2": "答案"}, user_id=None
        )

        # 验证返回结果
        self.assertIsNotNone(result)

        # 验证 WorkflowAttempt 已创建
        attempt = WorkflowAttempt.objects.get(
            session=self.session, attempt_index=0
        )
        self.assertEqual(attempt.thread_id, self.thread_id)
        self.assertEqual(attempt.total_score, 100)
        self.assertEqual(attempt.feedback, "优秀")

    @patch("Django_xm.apps.learning.services.study_flow._update_workflow_session_tokens")
    @patch("Django_xm.apps.learning.services.study_flow._get_study_flow")
    @patch("Django_xm.apps.learning.services.study_flow.persistence_service")
    @patch("Django_xm.apps.learning.services.study_flow.get_workflow_state")
    @patch("Django_xm.apps.learning.services.study_flow.TokenUsageCallbackHandler")
    def test_lock_questions_after_grading(
        self,
        mock_token_cb,
        mock_get_state,
        mock_persistence,
        mock_get_study_flow,
        mock_update_tokens,
    ):
        """评分完成后锁定题目（scored -> locked）"""
        self._setup_common_mocks(
            mock_token_cb,
            mock_get_state,
            mock_persistence,
            mock_get_study_flow,
            mock_update_tokens,
        )

        from Django_xm.apps.learning.services.study_flow import submit_answers

        submit_answers(
            self.thread_id, {"q1": "A", "q2": "答案"}, user_id=None
        )

        # 验证当前轮次题目已锁定
        questions = WorkflowQuestion.objects.filter(
            session=self.session, attempt_index=0
        )
        self.assertEqual(questions.count(), 2)
        for q in questions:
            self.assertEqual(q.status, WorkflowQuestionStatus.LOCKED)

    @patch("Django_xm.apps.learning.services.study_flow._update_workflow_session_tokens")
    @patch("Django_xm.apps.learning.services.study_flow._get_study_flow")
    @patch("Django_xm.apps.learning.services.study_flow.persistence_service")
    @patch("Django_xm.apps.learning.services.study_flow.get_workflow_state")
    @patch("Django_xm.apps.learning.services.study_flow.TokenUsageCallbackHandler")
    def test_attempt_index_increments_with_retry_count(
        self,
        mock_token_cb,
        mock_get_state,
        mock_persistence,
        mock_get_study_flow,
        mock_update_tokens,
    ):
        """attempt_index 基于 retry_count 自增（第2轮练习）"""
        # 模拟第1轮已结束：将第1轮题目置为 LOCKED（上一轮 submit 已锁定）
        WorkflowQuestion.objects.filter(
            session=self.session, attempt_index=0
        ).update(status=WorkflowQuestionStatus.LOCKED)

        # 设置 retry_count=1（进入第2轮）
        self.session.retry_count = 1
        self.session.save()

        # 创建第2轮题目
        WorkflowQuestion.objects.create(
            session=self.session,
            question_id="q1_r1",
            attempt_index=1,
            question_index=1,
            type="multiple_choice",
            question="新题1",
            correct_answer="B",
            explanation="解析",
            points=10,
            user_answer="B",
            is_correct=True,
            points_earned=10,
            status=WorkflowQuestionStatus.SCORED,
        )

        self._setup_common_mocks(
            mock_token_cb,
            mock_get_state,
            mock_persistence,
            mock_get_study_flow,
            mock_update_tokens,
            retry_count=1,
            feedback="良好",
        )

        from Django_xm.apps.learning.services.study_flow import submit_answers

        submit_answers(self.thread_id, {"q1": "B"}, user_id=None)

        # 验证第2轮的 WorkflowAttempt 已创建
        attempt = WorkflowAttempt.objects.get(
            session=self.session, attempt_index=1
        )
        self.assertEqual(attempt.total_score, 100)
        self.assertEqual(attempt.feedback, "良好")

        # 验证第2轮题目已锁定
        q = WorkflowQuestion.objects.get(
            session=self.session, question_id="q1_r1"
        )
        self.assertEqual(q.status, WorkflowQuestionStatus.LOCKED)

        # 验证第1轮题目状态不变（仍为 LOCKED）
        q_old = WorkflowQuestion.objects.get(
            session=self.session, question_id="q1_r0"
        )
        self.assertEqual(q_old.status, WorkflowQuestionStatus.LOCKED)

    @patch("Django_xm.apps.learning.services.study_flow._update_workflow_session_tokens")
    @patch("Django_xm.apps.learning.services.study_flow._get_study_flow")
    @patch("Django_xm.apps.learning.services.study_flow.persistence_service")
    @patch("Django_xm.apps.learning.services.study_flow.get_workflow_state")
    @patch("Django_xm.apps.learning.services.study_flow.TokenUsageCallbackHandler")
    def test_update_or_create_idempotent(
        self,
        mock_token_cb,
        mock_get_state,
        mock_persistence,
        mock_get_study_flow,
        mock_update_tokens,
    ):
        """同一轮次多次提交使用 update_or_create 不产生重复记录"""
        self._setup_common_mocks(
            mock_token_cb,
            mock_get_state,
            mock_persistence,
            mock_get_study_flow,
            mock_update_tokens,
        )

        from Django_xm.apps.learning.services.study_flow import submit_answers

        # 第一次提交
        submit_answers(
            self.thread_id, {"q1": "A", "q2": "答案"}, user_id=None
        )
        self.assertEqual(
            WorkflowAttempt.objects.filter(
                session=self.session, attempt_index=0
            ).count(),
            1,
        )

        # 重置题目状态为 SCORED（模拟再次评分）
        WorkflowQuestion.objects.filter(
            session=self.session, attempt_index=0
        ).update(status=WorkflowQuestionStatus.SCORED)

        # 第二次提交（同一轮次）
        submit_answers(
            self.thread_id, {"q1": "A", "q2": "答案"}, user_id=None
        )
        self.assertEqual(
            WorkflowAttempt.objects.filter(
                session=self.session, attempt_index=0
            ).count(),
            1,
        )
