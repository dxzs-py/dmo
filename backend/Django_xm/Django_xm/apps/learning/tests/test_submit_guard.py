"""submit_answers 状态守卫单元测试

验证:
1. phase="completed" 时抛出 WorkflowAlreadyFinishedError
2. phase="failed" 时抛出 WorkflowAlreadyFinishedError
3. phase="quiz" 时正常执行（不抛异常）

运行方式:
    python manage.py test learning.tests.test_submit_guard
"""

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from Django_xm.apps.learning.services.study_flow import (
    submit_answers,
    WorkflowAlreadyFinishedError,
)


class SubmitAnswersGuardTestCase(SimpleTestCase):
    """submit_answers 状态守卫测试"""

    def _make_initial_flow_with_empty_state(self):
        """构造 get_state 返回空状态的 mock study_flow（触发 ensure_active 分支）

        submit_answers 首次调用 _get_study_flow().get_state()，
        若状态为空则调用 WorkflowService.ensure_active 恢复状态。
        这里将 values 显式设为空 dict 以触发该分支。
        """
        mock_flow = MagicMock()
        mock_state = MagicMock()
        mock_state.values = {}
        mock_flow.get_state.return_value = mock_state
        return mock_flow

    def _make_active_flow_with_phase(self, phase, current_step="feedback_completed"):
        """构造 get_state 返回指定 phase 的 mock study_flow（ensure_active 返回）

        Args:
            phase: 工作流阶段（completed/failed/quiz）
            current_step: 当前步骤标识
        """
        mock_flow = MagicMock()
        mock_state = MagicMock()
        mock_state.values = {
            "phase": phase,
            "current_step": current_step,
        }
        mock_flow.get_state.return_value = mock_state
        return mock_flow

    @patch("Django_xm.apps.learning.services.study_flow.persistence_service")
    @patch("Django_xm.apps.learning.services.study_flow._get_study_flow")
    @patch("Django_xm.apps.learning.services.workflow_service.WorkflowService")
    def test_submit_answers_raises_when_phase_completed(
        self, mock_workflow_service, mock_get_study_flow, mock_persistence
    ):
        """phase="completed" 时抛出 WorkflowAlreadyFinishedError

        场景：工作流已完成，用户尝试再次提交答案
        预期：抛出 WorkflowAlreadyFinishedError，current_phase="completed"
        """
        mock_get_study_flow.return_value = self._make_initial_flow_with_empty_state()

        active_flow = self._make_active_flow_with_phase(phase="completed")
        mock_workflow_service.ensure_active.return_value = active_flow

        with self.assertRaises(WorkflowAlreadyFinishedError) as ctx:
            submit_answers("test_thread_completed", {"q1": "a1"})

        self.assertEqual(ctx.exception.current_phase, "completed")
        # 确保未执行后续流程
        active_flow.update_state.assert_not_called()
        active_flow.invoke.assert_not_called()

    @patch("Django_xm.apps.learning.services.study_flow.persistence_service")
    @patch("Django_xm.apps.learning.services.study_flow._get_study_flow")
    @patch("Django_xm.apps.learning.services.workflow_service.WorkflowService")
    def test_submit_answers_raises_when_phase_failed(
        self, mock_workflow_service, mock_get_study_flow, mock_persistence
    ):
        """phase="failed" 时抛出 WorkflowAlreadyFinishedError

        场景：工作流已失败，用户尝试提交答案
        预期：抛出 WorkflowAlreadyFinishedError，current_phase="failed"
        """
        mock_get_study_flow.return_value = self._make_initial_flow_with_empty_state()

        active_flow = self._make_active_flow_with_phase(phase="failed", current_step="failed")
        mock_workflow_service.ensure_active.return_value = active_flow

        with self.assertRaises(WorkflowAlreadyFinishedError) as ctx:
            submit_answers("test_thread_failed", {"q1": "a1"})

        self.assertEqual(ctx.exception.current_phase, "failed")
        active_flow.update_state.assert_not_called()
        active_flow.invoke.assert_not_called()

    @patch("Django_xm.apps.learning.services.study_flow._update_workflow_session_tokens")
    @patch("Django_xm.apps.learning.services.study_flow.get_workflow_state")
    @patch("Django_xm.apps.learning.services.study_flow.TokenUsageCallbackHandler")
    @patch("Django_xm.apps.learning.services.study_flow.persistence_service")
    @patch("Django_xm.apps.learning.services.study_flow._get_study_flow")
    @patch("Django_xm.apps.learning.services.workflow_service.WorkflowService")
    def test_submit_answers_success_when_phase_quiz(
        self,
        mock_workflow_service,
        mock_get_study_flow,
        mock_persistence,
        mock_token_cb,
        mock_get_workflow_state,
        mock_update_tokens,
    ):
        """phase="quiz" 时正常执行（不抛异常）

        场景：工作流处于答题阶段，用户提交答案
        预期：不抛出异常，正常执行 update_state/invoke/save_workflow_state
        """
        mock_get_study_flow.return_value = self._make_initial_flow_with_empty_state()

        active_flow = self._make_active_flow_with_phase(
            phase="quiz", current_step="waiting_for_answers"
        )
        mock_workflow_service.ensure_active.return_value = active_flow

        mock_get_workflow_state.return_value = {
            "current_step": "feedback_completed",
            "phase": "completed",
            "score": 80,
        }

        mock_cb = MagicMock()
        mock_cb.prompt_tokens = 10
        mock_cb.completion_tokens = 20
        mock_token_cb.return_value = mock_cb

        result = submit_answers("test_thread_quiz", {"q1": "a1"})

        self.assertIsNotNone(result)
        self.assertEqual(result["score"], 80)
        active_flow.update_state.assert_called()
        active_flow.invoke.assert_called_once()
        mock_persistence.save_workflow_state.assert_called_once()
        mock_update_tokens.assert_called_once()
