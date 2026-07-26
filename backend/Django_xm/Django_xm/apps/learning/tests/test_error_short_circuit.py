"""错误短路条件边单元测试

验证:
1. should_retry_edge 条件边函数的分支逻辑
   - score=50, retry_count=1 → "end"（自动重试已禁用）
   - score=70, retry_count=0 → "end"
   - score=50, retry_count=3 → "end"
2. 图的错误短路行为（has_error 条件边）
   - planner_node 返回 error 时，retrieval_node 不被执行

运行方式:
    python manage.py test learning.tests.test_error_short_circuit
"""

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from Django_xm.apps.learning.services.study_flow import should_retry_edge, StudyFlow


class ShouldRetryEdgeTestCase(SimpleTestCase):
    """should_retry_edge 条件边函数测试"""

    def test_should_retry_returns_end_when_score_low_and_retry_count_low(self):
        """score=50, retry_count=1 → 返回 "end"

        场景：得分低于 60 且重试次数未达上限（<3）
        预期：返回 "end"（自动重试已禁用，用户可通过"修改答案"或"继续练习"手动操作）
        """
        state = {"score": 50, "retry_count": 1}
        result = should_retry_edge(state)
        self.assertEqual(result, "end")

    def test_should_retry_returns_end_when_score_high(self):
        """score=70, retry_count=0 → 返回 "end"

        场景：得分达到 60 分及以上（通过测验）
        预期：返回 "end"，结束流程
        """
        state = {"score": 70, "retry_count": 0}
        result = should_retry_edge(state)
        self.assertEqual(result, "end")

    def test_should_retry_returns_end_when_retry_count_exhausted(self):
        """score=50, retry_count=3 → 返回 "end"

        场景：得分低于 60 但重试次数已达上限（>=3）
        预期：返回 "end"，结束流程
        """
        state = {"score": 50, "retry_count": 3}
        result = should_retry_edge(state)
        self.assertEqual(result, "end")


class ErrorShortCircuitGraphTestCase(SimpleTestCase):
    """图错误短路行为测试（has_error 条件边）"""

    @patch("Django_xm.apps.learning.services.study_flow.feedback_node")
    @patch("Django_xm.apps.learning.services.study_flow.grading_node")
    @patch("Django_xm.apps.learning.services.study_flow.quiz_generator_node")
    @patch("Django_xm.apps.learning.services.study_flow.retrieval_node")
    @patch("Django_xm.apps.learning.services.study_flow.planner_node")
    def test_error_short_circuit_skips_retrieval_node(
        self,
        mock_planner,
        mock_retrieval,
        mock_quiz,
        mock_grading,
        mock_feedback,
    ):
        """planner_node 返回 error 时，retrieval_node 不被执行

        场景：planner_node 返回 {"error": "测试错误", "error_node": "planner", "phase": "failed"}
        预期：
        - has_error 条件边返回 "end"，图直接结束
        - retrieval_node 未被调用
        - 最终状态包含 error 字段
        """
        from langgraph.checkpoint.memory import MemorySaver

        mock_planner.return_value = {
            "error": "测试错误",
            "error_node": "planner",
            "phase": "failed",
        }

        checkpointer = MemorySaver()
        flow = StudyFlow(thread_id="test_error_sc", checkpointer=checkpointer)

        initial_state = {
            "messages": [],
            "user_question": "测试问题",
            "retry_count": 0,
            "should_retry": False,
            "phase": "",
            "current_step": "start",
        }
        config = {"configurable": {"thread_id": "test_error_sc"}}
        result = flow.invoke(initial_state, config)

        # 断言 retrieval_node 未被调用（错误短路）
        mock_retrieval.assert_not_called()

        # 断言 planner_node 被调用 1 次
        self.assertEqual(mock_planner.call_count, 1)

        # 断言最终状态包含错误信息
        self.assertEqual(result.get("error"), "测试错误")
        self.assertEqual(result.get("error_node"), "planner")
