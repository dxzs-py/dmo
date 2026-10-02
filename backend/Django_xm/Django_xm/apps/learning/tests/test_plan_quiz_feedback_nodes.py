"""planner / quiz_generator / feedback 节点与 should_continue 条件边测试（ARCH-21）。

覆盖：
- planner_node：已有计划跳过（不调 LLM）、成功路径、空响应守卫、异常守卫
- quiz_generator_node：缺学习计划守卫、成功路径（选项索引答案转内容）、
  空响应守卫、异常守卫、_persist_questions 落库与重跑幂等（DB）
- feedback_node：通过 / 触发重试 / 重试耗尽、LLM 失败守卫、错题分析进提示词
- study_flow.should_continue 条件边：retry / end 分支

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.learning.tests.test_plan_quiz_feedback_nodes --noinput
"""

import os
from types import SimpleNamespace
from unittest import TestCase as UnitTestCase
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.test import TestCase

from Django_xm.apps.learning.models import WorkflowQuestion, WorkflowQuestionStatus, WorkflowSession
from Django_xm.apps.learning.nodes.feedback_node import feedback_node
from Django_xm.apps.learning.nodes.planner_node import planner_node
from Django_xm.apps.learning.nodes.quiz_generator_node import _persist_questions, quiz_generator_node
from Django_xm.apps.learning.services.study_flow import human_review_node, should_continue

# 各节点命名空间内的结构化模型工厂引用（mock 目标）
_PLANNER_FACTORY = "Django_xm.apps.learning.nodes.planner_node.get_structured_model_from_state"
_QUIZ_FACTORY = "Django_xm.apps.learning.nodes.quiz_generator_node.get_structured_model_from_state"
_FEEDBACK_MODEL = "Django_xm.apps.learning.nodes.feedback_node.get_chat_model_from_state"


def _make_plan() -> dict:
    """构造学习计划字典"""
    return {
        "topic": "Python 闭包",
        "objectives": ["理解闭包定义", "掌握变量捕获", "避免常见陷阱"],
        "key_points": ["作用域", "LEGB", "延迟绑定", "装饰器", "内存泄漏"],
        "difficulty": "intermediate",
        "estimated_time": 30,
    }


def _make_plan_response():
    """模拟 planner 结构化输出"""
    return SimpleNamespace(
        topic="Python 闭包",
        objectives=["理解闭包定义", "掌握变量捕获", "避免常见陷阱"],
        key_points=["作用域", "LEGB", "延迟绑定", "装饰器", "内存泄漏"],
        difficulty="intermediate",
        estimated_time=30,
    )


def _make_quiz_response():
    """模拟 quiz 结构化输出（含选项索引答案，验证转换）"""
    return SimpleNamespace(
        questions=[
            SimpleNamespace(
                id="q1",
                type="multiple_choice",
                question="以下哪个是闭包？",
                options=["内层函数引用外层变量", "全局变量", "局部变量", "类属性"],
                answer="A",
                explanation="内层函数引用外层变量构成闭包",
                points=10,
            ),
            SimpleNamespace(
                id="q2",
                type="multiple_choice",
                question="装饰器本质是？",
                options=["函数", "类", "模块", "包"],
                answer="函数",
                explanation="装饰器本质是函数",
                points=10,
            ),
        ],
        total_points=20,
        time_limit=10,
    )


class PlannerNodeTests(UnitTestCase):
    """planner_node：学习计划生成"""

    def test_existing_plan_skips_llm(self):
        """已有 learning_plan → 直接复用，不调用 LLM（继续练习场景）"""
        plan = _make_plan()
        with mock.patch(_PLANNER_FACTORY) as factory:
            result = planner_node({"user_question": "什么是闭包", "learning_plan": plan})
        factory.assert_not_called()
        self.assertEqual(result["learning_plan"], plan)
        self.assertEqual(result["current_step"], "planner_completed")

    def test_success_builds_plan(self):
        """成功路径：结构化输出 → 学习计划字典 + 摘要消息"""
        with mock.patch(_PLANNER_FACTORY) as factory:
            factory.return_value = mock.Mock()
            factory.return_value.invoke.return_value = _make_plan_response()
            result = planner_node({"user_question": "什么是闭包"})
        plan = result["learning_plan"]
        self.assertEqual(plan["topic"], "Python 闭包")
        self.assertEqual(plan["difficulty"], "intermediate")
        self.assertEqual(plan["estimated_time"], 30)
        self.assertEqual(result["current_step"], "planner")
        self.assertTrue(any("学习计划" in getattr(m, "content", "") for m in result["messages"]))

    def test_none_response_returns_planner_error(self):
        """结构化输出为 None → planner_error + error_node"""
        with mock.patch(_PLANNER_FACTORY) as factory:
            factory.return_value = mock.Mock()
            factory.return_value.invoke.return_value = None
            result = planner_node({"user_question": "什么是闭包"})
        self.assertEqual(result["current_step"], "planner_error")
        self.assertEqual(result["error_node"], "planner")
        self.assertIn("返回空结果", result["error"])

    def test_exception_returns_planner_error(self):
        """LLM 调用异常 → planner_error（节点内捕获不向外传播）"""
        with mock.patch(_PLANNER_FACTORY) as factory:
            factory.return_value = mock.Mock()
            factory.return_value.invoke.side_effect = RuntimeError("llm down")
            result = planner_node({"user_question": "什么是闭包"})
        self.assertEqual(result["current_step"], "planner_error")
        self.assertIn("学习计划生成失败", result["error"])


class QuizGeneratorNodeTests(UnitTestCase):
    """quiz_generator_node：练习题生成"""

    def test_missing_plan_returns_quiz_error(self):
        """learning_plan 缺失 → quiz_error，quiz 为 None"""
        with mock.patch(_QUIZ_FACTORY) as factory:
            result = quiz_generator_node({"learning_plan": None, "retrieved_docs": []})
        factory.assert_not_called()
        self.assertIsNone(result["quiz"])
        self.assertEqual(result["current_step"], "quiz_error")

    def test_success_converts_letter_answer_to_content(self):
        """成功路径：选择题选项索引答案转换为选项内容，与评分口径统一"""
        with mock.patch(_QUIZ_FACTORY) as factory, mock.patch(
            "Django_xm.apps.learning.nodes.quiz_generator_node._persist_questions"
        ) as persist:
            factory.return_value = mock.Mock()
            factory.return_value.invoke.return_value = _make_quiz_response()
            result = quiz_generator_node({"learning_plan": _make_plan(), "retrieved_docs": []})
        questions = result["quiz"]["questions"]
        self.assertEqual(questions[0]["answer"], "内层函数引用外层变量")
        # 已是选项内容的答案保持不变
        self.assertEqual(questions[1]["answer"], "函数")
        self.assertEqual(result["quiz"]["total_points"], 20)
        self.assertEqual(result["current_step"], "waiting_for_answers")
        persist.assert_called_once()

    def test_none_response_returns_quiz_error(self):
        """结构化输出为 None → quiz_error"""
        with mock.patch(_QUIZ_FACTORY) as factory:
            factory.return_value = mock.Mock()
            factory.return_value.invoke.return_value = None
            result = quiz_generator_node({"learning_plan": _make_plan(), "retrieved_docs": []})
        self.assertEqual(result["current_step"], "quiz_error")
        self.assertIn("返回空结果", result["error"])

    def test_exception_returns_quiz_error(self):
        """LLM 调用异常 → quiz_error"""
        with mock.patch(_QUIZ_FACTORY) as factory:
            factory.return_value = mock.Mock()
            factory.return_value.invoke.side_effect = RuntimeError("llm down")
            result = quiz_generator_node({"learning_plan": _make_plan(), "retrieved_docs": []})
        self.assertEqual(result["current_step"], "quiz_error")
        self.assertIn("练习题生成失败", result["error"])


class PersistQuestionsTests(TestCase):
    """_persist_questions：题目落库与同轮次幂等（DB）"""

    def setUp(self):
        self.session = WorkflowSession.objects.create(thread_id="quiz-t1", user_question="测试问题")

    def _questions(self, keyword: str) -> list[dict]:
        return [
            {
                "id": "q1",
                "type": "multiple_choice",
                "question": f"题目{keyword}",
                "options": ["甲", "乙", "丙", "丁"],
                "answer": "甲",
                "explanation": "解析",
                "points": 10,
            },
            {
                "id": "q2",
                "type": "fill_blank",
                "question": f"填空{keyword}",
                "options": None,
                "answer": "答案",
                "explanation": "解析",
                "points": 15,
            },
        ]

    def test_creates_pending_questions(self):
        """题目持久化为 q{idx}_r{attempt} 记录，状态 PENDING"""
        state = {"thread_id": "quiz-t1", "retry_count": 0}
        _persist_questions(state, self._questions("A"))
        rows = WorkflowQuestion.objects.filter(session=self.session).order_by("question_index")
        self.assertEqual(rows.count(), 2)
        self.assertEqual(rows[0].question_id, "q1_r0")
        self.assertEqual(rows[0].attempt_index, 0)
        self.assertEqual(rows[0].status, WorkflowQuestionStatus.PENDING)
        self.assertEqual(rows[0].correct_answer, "甲")

    def test_rerun_replaces_same_attempt(self):
        """同一轮次重复生成 → 先删旧题，不重复累积"""
        state = {"thread_id": "quiz-t1", "retry_count": 0}
        _persist_questions(state, self._questions("A"))
        _persist_questions(state, self._questions("B"))
        rows = WorkflowQuestion.objects.filter(session=self.session)
        self.assertEqual(rows.count(), 2)
        self.assertTrue(all("B" in row.question for row in rows))

    def test_skips_without_session(self):
        """thread_id 无对应会话 → 跳过（不抛异常）"""
        _persist_questions({"thread_id": "no-such", "retry_count": 0}, self._questions("A"))
        self.assertEqual(WorkflowQuestion.objects.filter(session=self.session).count(), 0)


class FeedbackNodeTests(UnitTestCase):
    """feedback_node：个性化反馈与重试决策"""

    def _run(self, state):
        with mock.patch(_FEEDBACK_MODEL) as factory:
            model = factory.return_value
            model.invoke.return_value = SimpleNamespace(content="整体表现不错，继续加油！")
            result = feedback_node(state)
        return result, model

    def test_score_pass_no_retry(self):
        """得分 >= 60 → 不重试，retry_count 不变"""
        state = {"score": 85, "score_details": {}, "learning_plan": _make_plan(), "retry_count": 0}
        result, _ = self._run(state)
        self.assertFalse(result["should_retry"])
        self.assertEqual(result["retry_count"], 0)
        self.assertEqual(result["current_step"], "feedback_completed")
        self.assertIn("恭喜", result["feedback"])

    def test_score_low_triggers_retry(self):
        """得分 < 60 且未达重试上限 → should_retry=True，retry_count+1"""
        state = {"score": 50, "score_details": {}, "learning_plan": _make_plan(), "retry_count": 1}
        result, _ = self._run(state)
        self.assertTrue(result["should_retry"])
        self.assertEqual(result["retry_count"], 2)
        self.assertIn("重新生成练习题", result["feedback"])

    def test_max_retries_exhausted(self):
        """重试次数达 3 → 不再重试"""
        state = {"score": 50, "score_details": {}, "learning_plan": _make_plan(), "retry_count": 3}
        result, _ = self._run(state)
        self.assertFalse(result["should_retry"])
        self.assertEqual(result["retry_count"], 3)
        self.assertIn("3次", result["feedback"])

    def test_wrong_questions_in_prompt(self):
        """错题明细进入 LLM 提示词（错题分析段落）"""
        state = {
            "score": 40,
            "score_details": {
                "question_scores": [
                    {"question_id": "q2", "is_correct": False, "feedback": "回答错误。正确答案是：B"}
                ]
            },
            "learning_plan": _make_plan(),
            "retry_count": 0,
        }
        with mock.patch(_FEEDBACK_MODEL) as factory:
            model = factory.return_value
            model.invoke.return_value = SimpleNamespace(content="反馈")
            result = feedback_node(state)
            prompt = model.invoke.call_args[0][0][0]["content"]
        self.assertIn("错题分析", prompt)
        self.assertIn("q2", prompt)
        self.assertTrue(result["should_retry"])

    def test_llm_failure_returns_feedback_error(self):
        """LLM 异常 → feedback_error，should_retry=False（不触发重试循环）"""
        with mock.patch(_FEEDBACK_MODEL) as factory:
            factory.return_value.invoke.side_effect = RuntimeError("llm down")
            result = feedback_node({"score": 50, "retry_count": 1, "learning_plan": {}})
        self.assertEqual(result["current_step"], "feedback_error")
        self.assertFalse(result["should_retry"])
        self.assertEqual(result["retry_count"], 1)


class ShouldContinueTests(UnitTestCase):
    """study_flow.should_continue 条件边"""

    def test_retry_when_below_threshold(self):
        """需要重试且未达 3 次 → retry"""
        self.assertEqual(should_continue({"should_retry": True, "retry_count": 2}), "retry")

    def test_end_when_max_retries_reached(self):
        """重试达 3 次 → end"""
        self.assertEqual(should_continue({"should_retry": True, "retry_count": 3}), "end")

    def test_end_when_no_retry(self):
        """无需重试 → end"""
        self.assertEqual(should_continue({"should_retry": False, "retry_count": 0}), "end")

    def test_defaults_on_empty_state(self):
        """空状态默认不重试 → end"""
        self.assertEqual(should_continue({}), "end")


class HumanReviewNodeTests(UnitTestCase):
    """human_review_node：中断等待节点"""

    def test_returns_waiting_state(self):
        """返回 waiting_for_answers 与更新时间"""
        result = human_review_node({})
        self.assertEqual(result["current_step"], "waiting_for_answers")
        self.assertIn("updated_at", result)
