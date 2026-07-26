"""root_thread_id 跨会话聚合查询单元测试

验证:
1. 初始工作流 session（无 root_thread_id）查询题目只返回该 session 的题目
2. 继续练习创建新 session（root_thread_id = 旧 thread_id），通过新 thread_id 查询能返回所有轮次题目
3. 继续练习后修改旧轮次题目答案，能找到题目并重新评分
4. 删除工作流时，所有相关 session（含旧轮次）都被删除
5. 查询 attempts 时跨会话聚合

运行方式:
    conda activate langchain_xm
    cd d:\\programming\\langchain\\langchain_xm\\backend\\Django_xm
    python manage.py test Django_xm.apps.learning.tests.test_root_thread_id --keepdb
"""

from unittest.mock import patch, AsyncMock

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from Django_xm.apps.learning.models import (
    WorkflowSession,
    WorkflowQuestion,
    WorkflowAttempt,
    WorkflowQuestionStatus,
    WorkflowQuestionType,
)
from Django_xm.apps.learning.services.workflow_service import WorkflowService
from Django_xm.common.error_codes import ErrorCode

User = get_user_model()


class RootThreadIdTestCase(TestCase):
    """root_thread_id 跨会话聚合查询测试"""

    def setUp(self):
        """创建测试数据：模拟继续练习场景

        结构：
        - old_session (thread_id=root_001, root_thread_id=None)
            - q1_r0 (attempt_index=0)
            - q2_r0 (attempt_index=0)
            - attempt_0
        - new_session (thread_id=new_001, root_thread_id=root_001)
            - q1_r1 (attempt_index=1)
            - q2_r1 (attempt_index=1)
            - attempt_1
        """
        self.user = User.objects.create_user(
            username="testuser",
            password="testpass123",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

        # 旧 session（初始工作流，无 root_thread_id）
        self.old_thread_id = "root_001"
        self.old_session = WorkflowSession.objects.create(
            thread_id=self.old_thread_id,
            user_question="学习 Python",
            learning_plan={"topic": "Python"},
            retry_count=0,
            phase="completed",
            created_by=self.user,
        )

        # 旧轮次题目（attempt_index=0）
        self.q1_r0 = WorkflowQuestion.objects.create(
            session=self.old_session,
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
        self.q2_r0 = WorkflowQuestion.objects.create(
            session=self.old_session,
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

        # 旧轮次 attempt
        WorkflowAttempt.objects.create(
            session=self.old_session,
            attempt_index=0,
            thread_id=self.old_thread_id,
            total_score=66,
            feedback="良好",
        )

        # 新 session（继续练习，root_thread_id 指向旧 session）
        self.new_thread_id = "new_001"
        self.new_session = WorkflowSession.objects.create(
            thread_id=self.new_thread_id,
            user_question="学习 Python",
            learning_plan={"topic": "Python"},
            retry_count=1,
            phase="quiz",
            root_thread_id=self.old_thread_id,
            created_by=self.user,
        )

        # 新轮次题目（attempt_index=1）
        self.q1_r1 = WorkflowQuestion.objects.create(
            session=self.new_session,
            question_id="q1_r1",
            attempt_index=1,
            question_index=1,
            type=WorkflowQuestionType.MULTIPLE_CHOICE,
            question="Python 中列表用什么符号？",
            options=["()", "[]", "{}", "<>"],
            correct_answer="B",
            explanation="列表使用方括号",
            points=10,
            user_answer="A",
            is_correct=False,
            points_earned=0,
            status=WorkflowQuestionStatus.LOCKED,
        )
        self.q2_r1 = WorkflowQuestion.objects.create(
            session=self.new_session,
            question_id="q2_r1",
            attempt_index=1,
            question_index=2,
            type=WorkflowQuestionType.FILL_BLANK,
            question="Python 字典使用___符号",
            correct_answer="{}",
            explanation="字典使用花括号",
            points=20,
            user_answer="{}",
            is_correct=True,
            points_earned=20,
            status=WorkflowQuestionStatus.LOCKED,
        )

        # 新轮次 attempt
        WorkflowAttempt.objects.create(
            session=self.new_session,
            attempt_index=1,
            thread_id=self.new_thread_id,
            total_score=66,
            feedback="良好",
        )

    # ===== 场景 1：初始 session 查询 =====

    def test_initial_session_queries_only_own_questions(self):
        """初始 session（无 root_thread_id 且无关联 session）查询题目只返回该 session 的题目"""
        # 创建一个独立的 session，无 root_thread_id，且无其他 session 指向它
        isolated_thread_id = "isolated_001"
        isolated_session = WorkflowSession.objects.create(
            thread_id=isolated_thread_id,
            user_question="学习 Java",
            learning_plan={"topic": "Java"},
            retry_count=0,
            phase="completed",
            created_by=self.user,
        )
        WorkflowQuestion.objects.create(
            session=isolated_session,
            question_id="q1_r0",
            attempt_index=0,
            question_index=1,
            type=WorkflowQuestionType.MULTIPLE_CHOICE,
            question="Java 是什么类型的语言？",
            options=["编译型", "解释型"],
            correct_answer="A",
            explanation="Java 是编译型语言",
            points=10,
            status=WorkflowQuestionStatus.LOCKED,
        )

        response = self.client.get(
            f"/api/v1/learning/{isolated_thread_id}/questions/"
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["code"], ErrorCode.SUCCESS)
        # 只返回该 session 的 1 道题
        self.assertEqual(len(data["data"]), 1)
        self.assertEqual(data["data"][0]["question_id"], "q1_r0")

    def test_initial_session_root_thread_id_is_none(self):
        """初始 session 的 root_thread_id 为 None"""
        self.old_session.refresh_from_db()
        self.assertIsNone(self.old_session.root_thread_id)

    # ===== 场景 2：继续练习后跨会话查询题目 =====

    def test_cross_session_query_returns_all_questions(self):
        """通过新 thread_id 查询能返回所有轮次题目（跨会话聚合）"""
        response = self.client.get(
            f"/api/v1/learning/{self.new_thread_id}/questions/"
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["code"], ErrorCode.SUCCESS)
        # 应返回所有 4 道题（旧轮次 2 + 新轮次 2）
        self.assertEqual(len(data["data"]), 4)
        question_ids = {q["question_id"] for q in data["data"]}
        self.assertEqual(
            question_ids, {"q1_r0", "q2_r0", "q1_r1", "q2_r1"}
        )

    def test_cross_session_query_ordered_by_attempt_and_index(self):
        """跨会话查询题目按 attempt_index, question_index 排序"""
        response = self.client.get(
            f"/api/v1/learning/{self.new_thread_id}/questions/"
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        # 验证排序：q1_r0, q2_r0, q1_r1, q2_r1
        self.assertEqual(data["data"][0]["question_id"], "q1_r0")
        self.assertEqual(data["data"][1]["question_id"], "q2_r0")
        self.assertEqual(data["data"][2]["question_id"], "q1_r1")
        self.assertEqual(data["data"][3]["question_id"], "q2_r1")

    def test_cross_session_query_filter_by_attempt_index(self):
        """跨会话查询支持按 attempt_index 过滤"""
        response = self.client.get(
            f"/api/v1/learning/{self.new_thread_id}/questions/?attempt_index=1"
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        # 只返回新轮次的 2 道题
        self.assertEqual(len(data["data"]), 2)
        question_ids = {q["question_id"] for q in data["data"]}
        self.assertEqual(question_ids, {"q1_r1", "q2_r1"})

    def test_cross_session_query_from_old_thread_also_aggregates(self):
        """通过旧 thread_id 查询也能返回所有轮次题目"""
        response = self.client.get(
            f"/api/v1/learning/{self.old_thread_id}/questions/"
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        # 旧 session 无 root_thread_id，root_id = 旧 thread_id
        # 查询条件：Q(thread_id=root_id) | Q(root_thread_id=root_id)
        # 匹配旧 session（thread_id=root_001）和新 session（root_thread_id=root_001）
        self.assertEqual(len(data["data"]), 4)

    # ===== 场景 3：跨会话修改旧轮次题目并重新评分 =====

    def test_update_old_question_from_new_thread(self):
        """通过新 thread_id 修改旧轮次题目答案，能找到题目并重新评分"""
        response = self.client.put(
            f"/api/v1/learning/{self.new_thread_id}/questions/q1_r0/",
            {"user_answer": "B"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["code"], ErrorCode.SUCCESS)

        # 验证旧 session 的题目已更新
        self.q1_r0.refresh_from_db()
        self.assertEqual(self.q1_r0.user_answer, "B")
        self.assertTrue(self.q1_r0.is_correct)
        self.assertEqual(self.q1_r0.points_earned, 10)
        self.assertEqual(self.q1_r0.status, WorkflowQuestionStatus.SCORED)

    def test_update_old_question_recalculates_attempt_score(self):
        """通过新 thread_id 修改旧轮次题目后，重新计算旧轮次 attempt 的 total_score"""
        # 初始：q1_r0=0, q2_r0=20, total=20/30*100=66
        response = self.client.put(
            f"/api/v1/learning/{self.new_thread_id}/questions/q1_r0/",
            {"user_answer": "B"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        # q1_r0=10, q2_r0=20, total=30/30*100=100
        self.assertEqual(data["data"]["attempt_total_score"], 100)

        # 验证旧 session 的 attempt_0 的 total_score 已更新
        attempt_0 = WorkflowAttempt.objects.get(
            session=self.old_session, attempt_index=0
        )
        self.assertEqual(attempt_0.total_score, 100)

    def test_update_new_question_from_new_thread(self):
        """通过新 thread_id 修改新轮次题目答案，能找到题目并重新评分"""
        response = self.client.put(
            f"/api/v1/learning/{self.new_thread_id}/questions/q1_r1/",
            {"user_answer": "B"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["code"], ErrorCode.SUCCESS)

        # 验证新 session 的题目已更新
        self.q1_r1.refresh_from_db()
        self.assertEqual(self.q1_r1.user_answer, "B")
        self.assertTrue(self.q1_r1.is_correct)

    def test_update_question_not_found_across_sessions(self):
        """跨会话查询不存在的题目返回 404"""
        response = self.client.put(
            f"/api/v1/learning/{self.new_thread_id}/questions/nonexistent/",
            {"user_answer": "A"},
            format="json",
        )
        self.assertEqual(response.status_code, 404)

    # ===== 场景 4：删除工作流时删除所有相关 session =====

    @patch(
        "Django_xm.apps.ai_engine.services.checkpointer_factory.delete_thread_checkpoints",
        new_callable=AsyncMock,
    )
    @patch("Django_xm.apps.core.services.file_manager.get_file_manager")
    @patch("Django_xm.apps.learning.services.study_flow._invalidate_study_flow_cache")
    def test_delete_workflow_removes_all_related_sessions(
        self, mock_invalidate_cache, mock_get_file_manager, mock_delete_checkpoints
    ):
        """删除根工作流时，所有相关 session（含旧轮次）都被删除

        通过根 thread_id（old_thread_id）删除：删除整棵树（根 + 所有分支/叶子）。
        业务逻辑：根节点删除 → 级联删除所有相关 session；
        非根节点删除 → 仅删除当前 session（见 test_delete_workflow_from_non_root_only_removes_current_session）。
        """
        # 删除前确认两个 session 都存在
        self.assertTrue(
            WorkflowSession.objects.filter(thread_id=self.old_thread_id).exists()
        )
        self.assertTrue(
            WorkflowSession.objects.filter(thread_id=self.new_thread_id).exists()
        )
        self.assertEqual(WorkflowQuestion.objects.count(), 4)
        self.assertEqual(WorkflowAttempt.objects.count(), 2)

        # 通过根 thread_id 删除工作流（删除整棵树）
        WorkflowService.delete_workflow(self.old_thread_id, self.user.id)

        # 验证两个 session 都被硬删除
        self.assertFalse(
            WorkflowSession.all_objects.filter(thread_id=self.old_thread_id).exists()
        )
        self.assertFalse(
            WorkflowSession.all_objects.filter(thread_id=self.new_thread_id).exists()
        )
        # 级联删除题目和轮次
        self.assertEqual(WorkflowQuestion.objects.count(), 0)
        self.assertEqual(WorkflowAttempt.objects.count(), 0)

    @patch(
        "Django_xm.apps.ai_engine.services.checkpointer_factory.delete_thread_checkpoints",
        new_callable=AsyncMock,
    )
    @patch("Django_xm.apps.core.services.file_manager.get_file_manager")
    @patch("Django_xm.apps.learning.services.study_flow._invalidate_study_flow_cache")
    def test_delete_workflow_from_old_thread_removes_all(
        self, mock_invalidate_cache, mock_get_file_manager, mock_delete_checkpoints
    ):
        """通过旧 thread_id 删除工作流也能删除所有相关 session"""
        WorkflowService.delete_workflow(self.old_thread_id, self.user.id)

        self.assertFalse(
            WorkflowSession.all_objects.filter(thread_id=self.old_thread_id).exists()
        )
        self.assertFalse(
            WorkflowSession.all_objects.filter(thread_id=self.new_thread_id).exists()
        )
        self.assertEqual(WorkflowQuestion.objects.count(), 0)
        self.assertEqual(WorkflowAttempt.objects.count(), 0)

    @patch(
        "Django_xm.apps.ai_engine.services.checkpointer_factory.delete_thread_checkpoints",
        new_callable=AsyncMock,
    )
    @patch("Django_xm.apps.core.services.file_manager.get_file_manager")
    @patch("Django_xm.apps.learning.services.study_flow._invalidate_study_flow_cache")
    def test_delete_workflow_from_non_root_only_removes_current_session(
        self, mock_invalidate_cache, mock_get_file_manager, mock_delete_checkpoints
    ):
        """通过非根 thread_id 删除工作流时，只删除当前 session，不影响根节点和其他轮次

        业务逻辑：非根节点删除 → 仅删除该 session（及其 attempt/question）；
        根节点删除 → 删除整棵树（见 test_delete_workflow_removes_all_related_sessions）。
        """
        # 删除前确认两个 session 都存在
        self.assertTrue(
            WorkflowSession.objects.filter(thread_id=self.old_thread_id).exists()
        )
        self.assertTrue(
            WorkflowSession.objects.filter(thread_id=self.new_thread_id).exists()
        )

        # 通过非根 thread_id 删除工作流（只删除当前 session）
        WorkflowService.delete_workflow(self.new_thread_id, self.user.id)

        # 验证新 session 被硬删除
        self.assertFalse(
            WorkflowSession.all_objects.filter(thread_id=self.new_thread_id).exists()
        )
        # 旧 session（根节点）保留
        self.assertTrue(
            WorkflowSession.all_objects.filter(thread_id=self.old_thread_id).exists()
        )
        # 旧轮次的题目和 attempt 保留（仅新轮次被级联删除）
        self.assertEqual(WorkflowQuestion.objects.filter(session=self.old_session).count(), 2)
        self.assertEqual(WorkflowAttempt.objects.filter(session=self.old_session).count(), 1)
        self.assertEqual(WorkflowQuestion.objects.filter(session=self.new_session).count(), 0)
        self.assertEqual(WorkflowAttempt.objects.filter(session=self.new_session).count(), 0)

    @patch(
        "Django_xm.apps.ai_engine.services.checkpointer_factory.delete_thread_checkpoints",
        new_callable=AsyncMock,
    )
    @patch("Django_xm.apps.core.services.file_manager.get_file_manager")
    @patch("Django_xm.apps.learning.services.study_flow._invalidate_study_flow_cache")
    def test_delete_workflow_clears_checkpoints_for_all_thread_ids(
        self, mock_invalidate_cache, mock_get_file_manager, mock_delete_checkpoints
    ):
        """删除根工作流时清理所有相关 thread_id 的 checkpoint

        通过根 thread_id 删除：清理整棵树所有 session 和 attempt 的 thread_id 对应 checkpoint。
        """
        WorkflowService.delete_workflow(self.old_thread_id, self.user.id)

        # 删除根节点时，应清理旧 thread_id 和新 thread_id 的 checkpoint
        called_thread_ids = {
            call.args[0] for call in mock_delete_checkpoints.call_args_list
        }
        self.assertIn(self.old_thread_id, called_thread_ids)
        self.assertIn(self.new_thread_id, called_thread_ids)

    @patch(
        "Django_xm.apps.ai_engine.services.checkpointer_factory.delete_thread_checkpoints",
        new_callable=AsyncMock,
    )
    @patch("Django_xm.apps.core.services.file_manager.get_file_manager")
    @patch("Django_xm.apps.learning.services.study_flow._invalidate_study_flow_cache")
    def test_delete_workflow_from_non_root_clears_only_current_checkpoint(
        self, mock_invalidate_cache, mock_get_file_manager, mock_delete_checkpoints
    ):
        """通过非根 thread_id 删除工作流时，只清理当前 thread_id 的 checkpoint

        业务逻辑：非根节点删除 → 仅清理当前 session 的 checkpoint，不影响根节点。
        """
        WorkflowService.delete_workflow(self.new_thread_id, self.user.id)

        # 非根节点删除时，只清理新 thread_id 的 checkpoint
        called_thread_ids = {
            call.args[0] for call in mock_delete_checkpoints.call_args_list
        }
        self.assertIn(self.new_thread_id, called_thread_ids)
        self.assertNotIn(self.old_thread_id, called_thread_ids)

    # ===== 场景 5：跨会话查询 attempts =====

    def test_cross_session_query_attempts(self):
        """查询 attempts 时跨会话聚合"""
        response = self.client.get(
            f"/api/v1/learning/{self.new_thread_id}/attempts/"
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["code"], ErrorCode.SUCCESS)
        # 应返回所有 2 个轮次（旧 session 的 attempt_0 + 新 session 的 attempt_1）
        self.assertEqual(len(data["data"]), 2)
        attempt_indices = {a["attempt_index"] for a in data["data"]}
        self.assertEqual(attempt_indices, {0, 1})

    def test_cross_session_query_attempts_from_old_thread(self):
        """通过旧 thread_id 查询 attempts 也能跨会话聚合"""
        response = self.client.get(
            f"/api/v1/learning/{self.old_thread_id}/attempts/"
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data["data"]), 2)

    def test_cross_session_query_attempts_ordered(self):
        """跨会话查询 attempts 按 attempt_index 排序"""
        response = self.client.get(
            f"/api/v1/learning/{self.new_thread_id}/attempts/"
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["data"][0]["attempt_index"], 0)
        self.assertEqual(data["data"][1]["attempt_index"], 1)

    # ===== 场景 6：restart_quiz 设置 root_thread_id =====

    @patch("Django_xm.apps.learning.services.study_flow._get_study_flow")
    @patch("Django_xm.apps.learning.services.study_flow.persistence_service")
    def test_restart_quiz_sets_root_thread_id(
        self, mock_persistence, mock_get_study_flow
    ):
        """restart_quiz 创建新 session 时设置 root_thread_id = 旧 thread_id"""
        from unittest.mock import MagicMock

        mock_flow = MagicMock()
        mock_flow.invoke.return_value = {
            "learning_plan": {"topic": "Python"},
            "quiz": {"questions": []},
            "phase": "quiz",
            "current_step": "waiting_for_answers",
            "retry_count": 1,
            "updated_at": "2025-01-01T00:00:00",
        }
        mock_get_study_flow.return_value = mock_flow

        from Django_xm.apps.learning.services.study_flow import restart_quiz

        result = restart_quiz(self.old_thread_id, user_id=self.user.id)

        # 验证新 session 的 root_thread_id = 旧 thread_id
        new_session = WorkflowSession.objects.get(
            thread_id=result["new_thread_id"]
        )
        self.assertEqual(new_session.root_thread_id, self.old_thread_id)

    @patch("Django_xm.apps.learning.services.study_flow._get_study_flow")
    @patch("Django_xm.apps.learning.services.study_flow.persistence_service")
    def test_restart_quiz_preserves_root_thread_id_chain(
        self, mock_persistence, mock_get_study_flow
    ):
        """对已设置 root_thread_id 的 session 再次继续练习，root_thread_id 保持不变"""
        from unittest.mock import MagicMock

        mock_flow = MagicMock()
        mock_flow.invoke.return_value = {
            "learning_plan": {"topic": "Python"},
            "quiz": {"questions": []},
            "phase": "quiz",
            "current_step": "waiting_for_answers",
            "retry_count": 2,
            "updated_at": "2025-01-01T00:00:00",
        }
        mock_get_study_flow.return_value = mock_flow

        from Django_xm.apps.learning.services.study_flow import restart_quiz

        # 对 new_session（已有 root_thread_id=root_001）再次继续练习
        result = restart_quiz(self.new_thread_id, user_id=self.user.id)

        # 验证新 session 的 root_thread_id 仍指向最初的 root_001
        newest_session = WorkflowSession.objects.get(
            thread_id=result["new_thread_id"]
        )
        self.assertEqual(newest_session.root_thread_id, self.old_thread_id)
