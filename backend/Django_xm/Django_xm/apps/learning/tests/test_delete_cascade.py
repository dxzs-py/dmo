"""delete_workflow 服务层单元测试

验证 WorkflowService.delete_workflow 的行为：
1. 软删除 WorkflowSession（is_deleted=True，记录仍存在于 all_objects）
2. 不存在或非本人 session 时抛出 ValueError
3. 删除 LangGraph checkpoint 数据
4. 删除工作流相关文件
5. 清除 StudyFlow 进程内缓存

背景：
migration 0009 删除了 WorkflowQuestion/WorkflowAttempt 模型，
原"级联删除题目/练习轮次"测试场景已失效，本测试聚焦当前 service 层行为。

运行方式：
    conda activate langchain_xm
    cd d:\\programming\\langchain\\langchain_xm\\backend\\Django_xm
    python manage.py test Django_xm.apps.learning.tests.test_delete_cascade -v 2
"""

from unittest.mock import AsyncMock, MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from Django_xm.apps.learning.models import WorkflowSession
from Django_xm.apps.learning.services.workflow_service import WorkflowService

User = get_user_model()


class DeleteWorkflowTestCase(TestCase):
    """WorkflowService.delete_workflow 测试"""

    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser",
            password="testpass123",
        )
        self.other_user = User.objects.create_user(
            username="otheruser",
            password="testpass123",
        )
        self.thread_id = "test_delete_001"
        self.session = WorkflowSession.objects.create(
            thread_id=self.thread_id,
            user_question="学习 Python",
            created_by=self.user,
            learning_plan={"topic": "Python"},
            current_step="waiting_for_answers",
            status="waiting_for_answers",
        )

    @patch(
        "Django_xm.apps.ai_engine.services.checkpointer_factory.delete_thread_checkpoints",
        new_callable=AsyncMock,
    )
    @patch("Django_xm.apps.core.services.file_manager.get_file_manager")
    def test_delete_workflow_soft_deletes_session(self, mock_get_file_manager, mock_delete_checkpoints):
        """删除工作流软删除 session（记录仍保留在 all_objects 中）"""
        self.assertTrue(WorkflowSession.objects.filter(thread_id=self.thread_id).exists())

        WorkflowService.delete_workflow(self.thread_id, self.user.id)

        # 默认 manager 已过滤 is_deleted=False，应查不到
        self.assertFalse(WorkflowSession.objects.filter(thread_id=self.thread_id).exists())
        # all_objects 仍保留记录（软删除）
        self.assertTrue(WorkflowSession.all_objects.filter(thread_id=self.thread_id).exists())

        # 验证 is_deleted 已标记
        session = WorkflowSession.all_objects.get(thread_id=self.thread_id)
        self.assertTrue(session.is_deleted)
        self.assertIsNotNone(session.deleted_at)

    @patch(
        "Django_xm.apps.ai_engine.services.checkpointer_factory.delete_thread_checkpoints",
        new_callable=AsyncMock,
    )
    @patch("Django_xm.apps.core.services.file_manager.get_file_manager")
    def test_delete_workflow_raises_when_not_found(self, mock_get_file_manager, mock_delete_checkpoints):
        """删除不存在的 thread_id 抛出 ValueError"""
        with self.assertRaises(ValueError) as ctx:
            WorkflowService.delete_workflow("nonexistent_thread", self.user.id)
        self.assertIn("不存在", str(ctx.exception))

    @patch(
        "Django_xm.apps.ai_engine.services.checkpointer_factory.delete_thread_checkpoints",
        new_callable=AsyncMock,
    )
    @patch("Django_xm.apps.core.services.file_manager.get_file_manager")
    def test_delete_workflow_raises_when_not_owner(self, mock_get_file_manager, mock_delete_checkpoints):
        """删除他人 session 抛出 ValueError（user_id 过滤后查不到）"""
        with self.assertRaises(ValueError) as ctx:
            WorkflowService.delete_workflow(self.thread_id, self.other_user.id)
        self.assertIn("不存在", str(ctx.exception))

        # 验证原 session 未被删除
        self.assertTrue(WorkflowSession.objects.filter(thread_id=self.thread_id).exists())

    @patch(
        "Django_xm.apps.ai_engine.services.checkpointer_factory.delete_thread_checkpoints",
        new_callable=AsyncMock,
    )
    @patch("Django_xm.apps.core.services.file_manager.get_file_manager")
    def test_delete_workflow_deletes_checkpoints(self, mock_get_file_manager, mock_delete_checkpoints):
        """删除工作流时清理 LangGraph checkpoint"""
        WorkflowService.delete_workflow(self.thread_id, self.user.id)

        mock_delete_checkpoints.assert_called_once_with(self.thread_id)

    @patch(
        "Django_xm.apps.ai_engine.services.checkpointer_factory.delete_thread_checkpoints",
        new_callable=AsyncMock,
    )
    @patch("Django_xm.apps.core.services.file_manager.get_file_manager")
    def test_delete_workflow_deletes_files(self, mock_get_file_manager, mock_delete_checkpoints):
        """删除工作流时清理相关文件"""
        mock_file_manager = MagicMock()
        mock_get_file_manager.return_value = mock_file_manager

        WorkflowService.delete_workflow(self.thread_id, self.user.id)

        mock_file_manager.delete_task_files.assert_called_once_with(self.thread_id, "workflow")

    @patch(
        "Django_xm.apps.ai_engine.services.checkpointer_factory.delete_thread_checkpoints",
        new_callable=AsyncMock,
    )
    @patch("Django_xm.apps.core.services.file_manager.get_file_manager")
    def test_delete_workflow_clears_study_flow_cache(self, mock_get_file_manager, mock_delete_checkpoints):
        """删除工作流时清除 StudyFlow 进程内缓存"""
        from Django_xm.apps.learning.services.study_flow import _study_flow_cache

        # 预填充缓存
        _study_flow_cache[self.thread_id] = (MagicMock(), 0)
        self.assertIn(self.thread_id, _study_flow_cache)

        WorkflowService.delete_workflow(self.thread_id, self.user.id)

        # 验证缓存已清除
        self.assertNotIn(self.thread_id, _study_flow_cache)
