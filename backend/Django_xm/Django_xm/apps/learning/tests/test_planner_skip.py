"""planner_node 跳过规划逻辑单元测试

验证：
1. 检测到已有 learning_plan 时跳过 LLM 调用
2. 跳过时直接返回已有计划
3. phase 设为 retrieval
4. 无 learning_plan 时正常调用 LLM

运行方式：
    conda activate langchain_xm
    cd d:\\programming\\langchain\\langchain_xm\\backend\\Django_xm
    python manage.py test learning.tests.test_planner_skip -v 2
"""

from unittest.mock import patch, MagicMock
from django.test import SimpleTestCase

from Django_xm.apps.learning.nodes.planner_node import planner_node
from Django_xm.apps.learning.services.state import WorkflowPhase


class PlannerSkipTestCase(SimpleTestCase):
    """planner_node 跳过规划单元测试"""

    def test_skip_when_learning_plan_exists(self):
        """检测到已有 learning_plan 时跳过规划"""
        existing_plan = {
            "topic": "Python 基础",
            "difficulty": "初级",
            "key_points": ["变量", "循环", "函数"]
        }
        state = {
            "user_question": "学习 Python",
            "learning_plan": existing_plan,
            "messages": []
        }

        result = planner_node(state)

        # 验证返回已有计划
        self.assertEqual(result["learning_plan"], existing_plan)
        # 验证 phase 设为 retrieval
        self.assertEqual(result["phase"], WorkflowPhase.retrieval.value)
        # 验证 current_step
        self.assertEqual(result["current_step"], "planner_completed")

    @patch("Django_xm.apps.learning.nodes.planner_node.get_structured_model_from_state")
    def test_call_llm_when_no_learning_plan(self, mock_get_model):
        """无 learning_plan 时正常调用 LLM"""
        # 模拟 LLM 返回（需覆盖 LearningPlanSchema 全部字段，
        # 因 plan_summary 生成会 enumerate objectives/key_points）
        mock_model = MagicMock()
        mock_plan = MagicMock()
        mock_plan.topic = "Python 基础"
        mock_plan.objectives = ["掌握变量", "理解循环", "学会函数"]
        mock_plan.key_points = ["变量", "循环", "函数", "列表", "字典"]
        mock_plan.difficulty = "初级"
        mock_plan.estimated_time = 120
        mock_model.invoke.return_value = mock_plan
        mock_get_model.return_value = mock_model

        state = {
            "user_question": "学习 Python",
            "learning_plan": None,  # 无学习计划
            "messages": []
        }

        result = planner_node(state)

        # 验证调用了 LLM
        mock_model.invoke.assert_called_once()
        # 验证生成了新计划
        self.assertIsNotNone(result["learning_plan"])
        self.assertEqual(result["learning_plan"]["topic"], "Python 基础")

    def test_skip_returns_messages(self):
        """跳过时返回提示消息"""
        existing_plan = {"topic": "测试", "difficulty": "初级", "key_points": []}
        state = {
            "user_question": "测试",
            "learning_plan": existing_plan,
            "messages": []
        }

        result = planner_node(state)

        # 验证返回了消息
        self.assertIn("messages", result)
        self.assertEqual(len(result["messages"]), 1)
        self.assertIn("复用已有学习计划", result["messages"][0]["content"])

    def test_skip_returns_updated_at(self):
        """跳过时返回 updated_at"""
        existing_plan = {"topic": "测试", "difficulty": "初级", "key_points": []}
        state = {
            "user_question": "测试",
            "learning_plan": existing_plan,
            "messages": []
        }

        result = planner_node(state)

        # 验证返回了 updated_at
        self.assertIn("updated_at", result)

    @patch("Django_xm.apps.learning.nodes.planner_node.get_structured_model_from_state")
    def test_skip_does_not_call_llm_when_plan_exists(self, mock_get_model):
        """检测到已有 learning_plan 时不调用 LLM"""
        mock_model = MagicMock()
        mock_get_model.return_value = mock_model

        existing_plan = {"topic": "测试", "difficulty": "初级", "key_points": []}
        state = {
            "user_question": "测试",
            "learning_plan": existing_plan,
            "messages": []
        }

        planner_node(state)

        # 验证未调用 LLM（跳过规划）
        mock_get_model.assert_not_called()
        mock_model.invoke.assert_not_called()
