"""grading_node（自动评分节点）单元测试（ARCH-21 learning 补测）。

覆盖：
- _is_option_index / _normalize_fill 纯函数
- _judge_fill_blank_semantic LLM 语义判断（mock 模型，含异常回退）
- _grade_short_answer LLM 评分解析 + 解析失败关键词回退（mock 模型）
- grading_node 主流程：缺参守卫、选择题双匹配、填空多等价答案、
  简答 LLM 评分、未知题型、百分比换算、异常守卫
- _persist_grading_results 评分结果落库（WorkflowQuestion scored -> DB）

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.learning.tests.test_grading_node --noinput
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
from Django_xm.apps.learning.nodes.grading_node import (
    _grade_short_answer,
    _is_option_index,
    _judge_fill_blank_semantic,
    _normalize_fill,
    grading_node,
)

# _model_helper 在 grading_node 命名空间内的引用（mock 目标）
_MODEL_HELPER = "Django_xm.apps.learning.nodes.grading_node.get_chat_model_from_state"


def _make_mc_question(q_id: str = "q1", answer: str = "B", points: int = 10) -> dict:
    """构造单道选择题"""
    return {
        "id": q_id,
        "type": "multiple_choice",
        "question": "1+1=?",
        "options": ["选项一", "选项二", "选项三", "选项四"],
        "answer": answer,
        "explanation": "解析",
        "points": points,
    }


class IsOptionIndexTests(UnitTestCase):
    """_is_option_index：单字母 A-H 判定"""

    def test_single_letter_is_index(self):
        """A-H 单字母返回 True"""
        for letter in "ABCDEFGH":
            self.assertTrue(_is_option_index(letter))

    def test_lowercase_letter_is_index(self):
        """小写字母归一化后判定为选项索引"""
        self.assertTrue(_is_option_index("b"))

    def test_multi_char_not_index(self):
        """多字符不是选项索引"""
        self.assertFalse(_is_option_index("AB"))

    def test_digit_not_index(self):
        """数字不是选项索引"""
        self.assertFalse(_is_option_index("2"))

    def test_empty_not_index(self):
        """空串不是选项索引"""
        self.assertFalse(_is_option_index(""))


class NormalizeFillTests(UnitTestCase):
    """_normalize_fill：NFKC + 去空白 + 小写"""

    def test_full_width_to_half_width(self):
        """全角字符归一化为半角"""
        self.assertEqual(_normalize_fill("１２３"), "123")

    def test_whitespace_removed(self):
        """空白（含全角空格）被移除"""
        self.assertEqual(_normalize_fill("  ab  c　"), "abc")

    def test_lowercased(self):
        """英文统一小写"""
        self.assertEqual(_normalize_fill("Hello"), "hello")


class JudgeFillBlankSemanticTests(UnitTestCase):
    """_judge_fill_blank_semantic：LLM 语义等价判断（mock 模型）"""

    def test_returns_true_when_model_says_correct(self):
        """模型返回"正确" → 判定通过"""
        model = mock.Mock()
        model.invoke.return_value = SimpleNamespace(content="正确")
        self.assertTrue(_judge_fill_blank_semantic(model, "题目", "11", "十一"))

    def test_returns_false_when_model_says_wrong(self):
        """模型返回"错误" → 判定不通过"""
        model = mock.Mock()
        model.invoke.return_value = SimpleNamespace(content="错误")
        self.assertFalse(_judge_fill_blank_semantic(model, "题目", "11", "十二"))

    def test_returns_false_on_model_error(self):
        """模型调用异常 → 按错误处理返回 False（不抛出）"""
        model = mock.Mock()
        model.invoke.side_effect = RuntimeError("llm down")
        self.assertFalse(_judge_fill_blank_semantic(model, "题目", "11", "十一"))


class GradeShortAnswerTests(UnitTestCase):
    """_grade_short_answer：简答题 LLM 评分与回退"""

    def test_parses_score_and_feedback(self):
        """解析"得分/评语"格式"""
        model = mock.Mock()
        model.invoke.return_value = SimpleNamespace(content="得分: 8\n评语: 表达清晰")
        points, feedback = _grade_short_answer(model, {"question": "Q", "points": 10}, "标准答案", "学生答案")
        self.assertEqual(points, 8)
        self.assertEqual(feedback, "表达清晰")

    def test_score_clamped_to_points(self):
        """得分超出满分时截断"""
        model = mock.Mock()
        model.invoke.return_value = SimpleNamespace(content="得分: 50\n评语: 超纲")
        points, _ = _grade_short_answer(model, {"question": "Q", "points": 10}, "标准答案", "学生答案")
        self.assertEqual(points, 10)

    def test_fallback_to_keywords_on_parse_error(self):
        """LLM 输出无法解析 → 关键词匹配回退"""
        model = mock.Mock()
        model.invoke.return_value = SimpleNamespace(content="无法解析的输出")
        points, feedback = _grade_short_answer(
            model, {"question": "Q", "points": 10}, "machine learning basics", "machine learning"
        )
        # 关键词 [machine, learning, basics] 命中 2/3 → int(2/3*10)=6
        self.assertEqual(points, 6)
        self.assertIn("关键词匹配", feedback)

    def test_fallback_on_model_exception(self):
        """模型调用异常 → 关键词匹配回退"""
        model = mock.Mock()
        model.invoke.side_effect = RuntimeError("llm down")
        points, feedback = _grade_short_answer(model, {"question": "Q", "points": 10}, "alpha beta", "alpha beta")
        self.assertEqual(points, 10)
        self.assertIn("关键词匹配", feedback)


class GradingNodeTests(UnitTestCase):
    """grading_node 主流程（mock 模型工厂与评分落库）"""

    def _run(self, quiz, user_answers, thread_id=None):
        """在 mock 模型工厂 + mock 落库下执行 grading_node"""
        with mock.patch(_MODEL_HELPER) as model_factory, mock.patch(
            "Django_xm.apps.learning.nodes.grading_node._persist_grading_results"
        ) as persist:
            model_factory.return_value = mock.Mock()
            state = {"quiz": quiz, "user_answers": user_answers, "retry_count": 0}
            if thread_id:
                state["thread_id"] = thread_id
            result = grading_node(state)
        return result, model_factory, persist

    def test_missing_quiz_returns_error(self):
        """quiz 缺失 → grading_error，得分为 0"""
        result, _, persist = self._run(None, {"q1": "A"})
        self.assertEqual(result["current_step"], "grading_error")
        self.assertEqual(result["score"], 0)
        self.assertEqual(result["score_details"]["total_count"], 0)
        persist.assert_not_called()

    def test_missing_answers_returns_error(self):
        """user_answers 缺失 → grading_error"""
        result, _, _ = self._run({"questions": [], "total_points": 0}, None)
        self.assertEqual(result["current_step"], "grading_error")

    def test_multiple_choice_letter_answer_correct(self):
        """选择题：用户提交选项字母，按选项内容匹配判定正确"""
        quiz = {"questions": [_make_mc_question(answer="B")], "total_points": 10}
        result, _, _ = self._run(quiz, {"q1": "B"})
        self.assertEqual(result["score"], 100)
        self.assertEqual(result["score_details"]["correct_count"], 1)
        detail = result["score_details"]["question_scores"][0]
        self.assertTrue(detail["is_correct"])
        # 评分详情以选项内容展示
        self.assertEqual(detail["correct_answer"], "选项二")
        self.assertEqual(detail["user_answer"], "选项二")

    def test_multiple_choice_content_answer_correct(self):
        """选择题：用户提交选项完整内容，同样判定正确（双匹配）"""
        quiz = {"questions": [_make_mc_question(answer="B")], "total_points": 10}
        result, _, _ = self._run(quiz, {"q1": "选项二"})
        self.assertEqual(result["score"], 100)

    def test_multiple_choice_wrong_answer(self):
        """选择题答错：0 分 + 正确答案提示"""
        quiz = {"questions": [_make_mc_question(answer="B")], "total_points": 10}
        result, _, _ = self._run(quiz, {"q1": "选项三"})
        self.assertEqual(result["score"], 0)
        detail = result["score_details"]["question_scores"][0]
        self.assertFalse(detail["is_correct"])
        self.assertIn("回答错误", detail["feedback"])

    def test_fill_blank_multi_answer_normalized_match(self):
        """填空题：`|` 多等价答案 + 归一化精确匹配，不触发 LLM"""
        fill = {
            "id": "q2",
            "type": "fill_blank",
            "question": "十一等于？",
            "options": None,
            "answer": "11|十一",
            "explanation": "",
            "points": 15,
        }
        quiz = {"questions": [fill], "total_points": 15}
        result, model_factory, _ = self._run(quiz, {"q2": " 十一 "})
        self.assertEqual(result["score"], 100)
        model_factory.return_value.invoke.assert_not_called()

    def test_fill_blank_semantic_fallback(self):
        """填空题：精确匹配失败且已作答 → LLM 语义兜底判定正确"""
        fill = {
            "id": "q2",
            "type": "fill_blank",
            "question": "法国首都？",
            "options": None,
            "answer": "Paris",
            "explanation": "",
            "points": 15,
        }
        quiz = {"questions": [fill], "total_points": 15}
        with mock.patch(_MODEL_HELPER) as model_factory, mock.patch(
            "Django_xm.apps.learning.nodes.grading_node._persist_grading_results"
        ):
            model = model_factory.return_value
            model.invoke.return_value = SimpleNamespace(content="正确")
            result = grading_node({"quiz": quiz, "user_answers": {"q2": "巴黎"}, "retry_count": 0})
        self.assertEqual(result["score"], 100)
        model.invoke.assert_called_once()

    def test_fill_blank_empty_answer_skips_llm(self):
        """填空题：空白作答不触发 LLM 语义判断，直接判错"""
        fill = {
            "id": "q2",
            "type": "fill_blank",
            "question": "法国首都？",
            "options": None,
            "answer": "Paris",
            "explanation": "",
            "points": 15,
        }
        quiz = {"questions": [fill], "total_points": 15}
        result, model_factory, _ = self._run(quiz, {"q2": "   "})
        self.assertEqual(result["score"], 0)
        model_factory.return_value.invoke.assert_not_called()

    def test_short_answer_llm_scoring(self):
        """简答题：LLM 评分，得分 >= 60% 视为答对"""
        short = {
            "id": "q3",
            "type": "short_answer",
            "question": "简述闭包",
            "options": None,
            "answer": "标准答案",
            "explanation": "",
            "points": 20,
        }
        quiz = {"questions": [short], "total_points": 20}
        with mock.patch(_MODEL_HELPER) as model_factory, mock.patch(
            "Django_xm.apps.learning.nodes.grading_node._persist_grading_results"
        ):
            model = model_factory.return_value
            model.invoke.return_value = SimpleNamespace(content="得分: 15\n评语: 基本正确")
            result = grading_node({"quiz": quiz, "user_answers": {"q3": "学生答案"}, "retry_count": 0})
        self.assertEqual(result["score"], 75)
        detail = result["score_details"]["question_scores"][0]
        self.assertTrue(detail["is_correct"])

    def test_unknown_question_type(self):
        """未知题型：0 分 + 未知题型提示"""
        weird = {
            "id": "q4",
            "type": "true_false",
            "question": "对吗？",
            "options": None,
            "answer": "对",
            "explanation": "",
            "points": 10,
        }
        quiz = {"questions": [weird], "total_points": 10}
        result, _, _ = self._run(quiz, {"q4": "对"})
        detail = result["score_details"]["question_scores"][0]
        self.assertFalse(detail["is_correct"])
        self.assertEqual(detail["points_earned"], 0)
        self.assertIn("未知题型", detail["feedback"])

    def test_score_percentage_partial(self):
        """百分比换算：两题对一题 → 50 分"""
        quiz = {
            "questions": [_make_mc_question("q1", answer="B"), _make_mc_question("q2", answer="A")],
            "total_points": 20,
        }
        result, _, _ = self._run(quiz, {"q1": "B", "q2": "选项四"})
        self.assertEqual(result["score"], 50)
        self.assertEqual(result["current_step"], "grading_completed")
        self.assertEqual(result["score_details"]["total_count"], 2)

    def test_exception_returns_grading_error(self):
        """内部异常（questions 非法）→ 捕获并返回 grading_error + error 信息"""
        quiz = {"questions": None, "total_points": 10}
        result, _, _ = self._run(quiz, {"q1": "A"})
        self.assertEqual(result["current_step"], "grading_error")
        self.assertIn("评分失败", result.get("error", ""))
        self.assertEqual(result["score"], 0)


class GradingNodePersistenceTests(TestCase):
    """_persist_grading_results：评分结果写回 WorkflowQuestion（DB）"""

    def setUp(self):
        self.session = WorkflowSession.objects.create(thread_id="grade-t1", user_question="测试问题")

    def test_persist_updates_question_records(self):
        """评分详情写回对应 q{id}_r{attempt} 记录，状态置为 scored"""
        WorkflowQuestion.objects.create(
            session=self.session,
            question_id="q1_r0",
            attempt_index=0,
            question_index=1,
            type="multiple_choice",
            question="1+1=?",
            correct_answer="2",
            points=10,
            status=WorkflowQuestionStatus.PENDING,
        )
        with mock.patch(_MODEL_HELPER):
            grading_node(
                {
                    "quiz": {"questions": [_make_mc_question(answer="B")], "total_points": 10},
                    "user_answers": {"q1": "B"},
                    "thread_id": "grade-t1",
                    "retry_count": 0,
                }
            )
        question = WorkflowQuestion.objects.get(session=self.session, question_id="q1_r0")
        self.assertEqual(question.status, WorkflowQuestionStatus.SCORED)
        # 选择题作答以选项内容展示口径落库
        self.assertEqual(question.user_answer, "选项二")
        self.assertTrue(question.is_correct)
        self.assertEqual(question.points_earned, 10)
        self.assertIsNotNone(question.scored_at)
        # 显式调用路径同样生效（防回归：直接写回评分详情）
        from Django_xm.apps.learning.nodes.grading_node import _persist_grading_results

        WorkflowQuestion.objects.create(
            session=self.session,
            question_id="q2_r0",
            attempt_index=0,
            question_index=2,
            type="fill_blank",
            question="十一等于？",
            correct_answer="11|十一",
            points=15,
            status=WorkflowQuestionStatus.PENDING,
        )
        _persist_grading_results(
            {"thread_id": "grade-t1", "retry_count": 0},
            [
                {
                    "question_id": "q2",
                    "user_answer": "十一",
                    "correct_answer": "11|十一",
                    "is_correct": True,
                    "points_earned": 15,
                    "points_possible": 15,
                    "feedback": "回答正确！",
                }
            ],
        )
        question2 = WorkflowQuestion.objects.get(session=self.session, question_id="q2_r0")
        self.assertEqual(question2.status, WorkflowQuestionStatus.SCORED)
        self.assertEqual(question2.user_answer, "十一")

    def test_persist_skips_without_session(self):
        """thread_id 无对应会话 → 跳过落库（不抛异常）"""
        from Django_xm.apps.learning.nodes.grading_node import _persist_grading_results

        _persist_grading_results({"thread_id": "no-such-thread", "retry_count": 0}, [])
        self.assertTrue(WorkflowSession.objects.filter(thread_id="grade-t1").exists())

    def test_persist_skips_without_thread_id(self):
        """state 无 thread_id → 直接返回"""
        from Django_xm.apps.learning.nodes.grading_node import _persist_grading_results

        _persist_grading_results({"retry_count": 0}, [])
        # 无异常即通过
        self.assertTrue(True)
