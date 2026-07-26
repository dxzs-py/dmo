"""删除工作流级联删除单元测试

验证：
1. 删除 WorkflowSession 时级联删除 WorkflowQuestion
2. 删除 WorkflowSession 时级联删除 WorkflowAttempt
3. 删除工作流时清除 StudyFlow 缓存
4. 删除工作流后 session 从数据库彻底移除（硬删除而非软删除）
5. 删除不存在的工作流抛出 ValueError
6. 删除时清理主 thread_id 和各轮次 thread_id 的 checkpoint

运行方式：
    conda activate langchain_xm
    cd d:\\programming\\langchain\\langchain_xm\\backend\\Django_xm
    python manage.py test Django_xm.apps.learning.tests.test_delete_cascade -v 2
"""

from unittest.mock import patch, AsyncMock

from django.test import TestCase
from django.contrib.auth import get_user_model

from Django_xm.apps.learning.models import (
    WorkflowSession,
    WorkflowQuestion,
    WorkflowAttempt,
    WorkflowQuestionStatus,
    WorkflowQuestionType,
)
from Django_xm.apps.learning.services.workflow_service import WorkflowService

User = get_user_model()


class DeleteCascadeTestCase(TestCase):
    """删除工作流级联删除测试"""

    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser",
            password="testpass123",
        )
        self.session = WorkflowSession.objects.create(
            thread_id="test_delete_001",
            user_question="学习 Python",
            created_by=self.user,
            learning_plan={"topic": "Python"},
            retry_count=1,
            phase="completed",
        )
        self.session_id = self.session.pk

        # 创建关联题目
        WorkflowQuestion.objects.create(
            session=self.session,
            question_id="q1_r0",
            attempt_index=0,
            question_index=1,
            type=WorkflowQuestionType.MULTIPLE_CHOICE,
            question="选择题 1",
            correct_answer="A",
            explanation="解析",
            points=10,
            status=WorkflowQuestionStatus.LOCKED,
        )
        WorkflowQuestion.objects.create(
            session=self.session,
            question_id="q1_r1",
            attempt_index=1,
            question_index=1,
            type=WorkflowQuestionType.FILL_BLANK,
            question="填空题 1",
            correct_answer="答案",
            explanation="解析",
            points=20,
            status=WorkflowQuestionStatus.LOCKED,
        )

        # 创建关联练习轮次
        WorkflowAttempt.objects.create(
            session=self.session,
            attempt_index=0,
            thread_id="test_delete_001",
            total_score=80,
            feedback="良好",
        )
        WorkflowAttempt.objects.create(
            session=self.session,
            attempt_index=1,
            thread_id="test_delete_001_r1",
            total_score=90,
            feedback="优秀",
        )

    # ===== 模型级 CASCADE 测试 =====

    def test_delete_session_cascades_questions(self):
        """删除 session 级联删除 WorkflowQuestion"""
        self.assertEqual(WorkflowQuestion.objects.filter(session=self.session).count(), 2)

        self.session.delete()

        # CASCADE 删除后，按原 session_id 查询应返回 0
        self.assertEqual(
            WorkflowQuestion.objects.filter(session_id=self.session_id).count(), 0
        )
        self.assertFalse(
            WorkflowSession.objects.filter(thread_id="test_delete_001").exists()
        )

    def test_delete_session_cascades_attempts(self):
        """删除 session 级联删除 WorkflowAttempt"""
        self.assertEqual(WorkflowAttempt.objects.filter(session=self.session).count(), 2)

        self.session.delete()

        self.assertEqual(
            WorkflowAttempt.objects.filter(session_id=self.session_id).count(), 0
        )

    def test_delete_session_removes_all_related_data(self):
        """删除 session 移除所有相关数据"""
        # 删除前确认数据存在
        self.assertTrue(WorkflowSession.objects.filter(thread_id="test_delete_001").exists())
        self.assertTrue(WorkflowQuestion.objects.filter(session=self.session).exists())
        self.assertTrue(WorkflowAttempt.objects.filter(session=self.session).exists())

        self.session.delete()

        # 删除后确认所有数据已清除
        self.assertFalse(WorkflowSession.objects.filter(thread_id="test_delete_001").exists())
        self.assertEqual(WorkflowQuestion.objects.count(), 0)
        self.assertEqual(WorkflowAttempt.objects.count(), 0)

    # ===== 服务级测试 =====

    @patch(
        "Django_xm.apps.ai_engine.services.checkpointer_factory.delete_thread_checkpoints",
        new_callable=AsyncMock,
    )
    @patch("Django_xm.apps.core.services.file_manager.get_file_manager")
    @patch("Django_xm.apps.learning.services.study_flow._invalidate_study_flow_cache")
    def test_delete_workflow_invalidates_study_flow_cache(
        self, mock_invalidate_cache, mock_get_file_manager, mock_delete_checkpoints
    ):
        """删除工作流时清除 StudyFlow 缓存"""
        WorkflowService.delete_workflow("test_delete_001", self.user.id)

        mock_invalidate_cache.assert_called_once_with("test_delete_001")

    @patch(
        "Django_xm.apps.ai_engine.services.checkpointer_factory.delete_thread_checkpoints",
        new_callable=AsyncMock,
    )
    @patch("Django_xm.apps.core.services.file_manager.get_file_manager")
    @patch("Django_xm.apps.learning.services.study_flow._invalidate_study_flow_cache")
    def test_delete_workflow_hard_deletes_session(
        self, mock_invalidate_cache, mock_get_file_manager, mock_delete_checkpoints
    ):
        """删除工作流后 session 从数据库彻底移除（非软删除）"""
        self.assertTrue(WorkflowSession.objects.filter(thread_id="test_delete_001").exists())

        WorkflowService.delete_workflow("test_delete_001", self.user.id)

        # 硬删除后，all_objects（含已软删除）也不应存在
        self.assertFalse(
            WorkflowSession.all_objects.filter(thread_id="test_delete_001").exists()
        )

    @patch(
        "Django_xm.apps.ai_engine.services.checkpointer_factory.delete_thread_checkpoints",
        new_callable=AsyncMock,
    )
    @patch("Django_xm.apps.core.services.file_manager.get_file_manager")
    @patch("Django_xm.apps.learning.services.study_flow._invalidate_study_flow_cache")
    def test_delete_workflow_cascades_via_service(
        self, mock_invalidate_cache, mock_get_file_manager, mock_delete_checkpoints
    ):
        """通过服务层删除工作流级联删除题目和练习轮次"""
        self.assertEqual(WorkflowQuestion.objects.filter(session=self.session).count(), 2)
        self.assertEqual(WorkflowAttempt.objects.filter(session=self.session).count(), 2)

        WorkflowService.delete_workflow("test_delete_001", self.user.id)

        self.assertEqual(WorkflowQuestion.objects.count(), 0)
        self.assertEqual(WorkflowAttempt.objects.count(), 0)

    @patch(
        "Django_xm.apps.ai_engine.services.checkpointer_factory.delete_thread_checkpoints",
        new_callable=AsyncMock,
    )
    @patch("Django_xm.apps.core.services.file_manager.get_file_manager")
    @patch("Django_xm.apps.learning.services.study_flow._invalidate_study_flow_cache")
    def test_delete_workflow_raises_when_not_found(
        self, mock_invalidate_cache, mock_get_file_manager, mock_delete_checkpoints
    ):
        """删除不存在的工作流抛出 ValueError"""
        with self.assertRaises(ValueError) as ctx:
            WorkflowService.delete_workflow("nonexistent_thread", self.user.id)
        self.assertIn("不存在", str(ctx.exception))

    @patch(
        "Django_xm.apps.ai_engine.services.checkpointer_factory.delete_thread_checkpoints",
        new_callable=AsyncMock,
    )
    @patch("Django_xm.apps.core.services.file_manager.get_file_manager")
    @patch("Django_xm.apps.learning.services.study_flow._invalidate_study_flow_cache")
    def test_delete_workflow_deletes_checkpoints_for_all_thread_ids(
        self, mock_invalidate_cache, mock_get_file_manager, mock_delete_checkpoints
    ):
        """删除工作流时清理主 thread_id 和各轮次 thread_id 的 checkpoint"""
        WorkflowService.delete_workflow("test_delete_001", self.user.id)

        # 主 thread_id + attempt 1 的 thread_id（attempt 0 与主 thread_id 相同，去重后 2 个）
        called_thread_ids = {
            call.args[0] for call in mock_delete_checkpoints.call_args_list
        }
        self.assertIn("test_delete_001", called_thread_ids)
        self.assertIn("test_delete_001_r1", called_thread_ids)
