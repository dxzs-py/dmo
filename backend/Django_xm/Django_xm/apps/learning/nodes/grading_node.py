"""
自动评分节点 (Grading Node)

本节点负责对用户提交的答案进行自动评分。
评分策略（针对正常作答公平）：
1. 选择题：用户提交选项字母或选项完整内容均可正确判定（双匹配）
2. 填空题：NFKC 归一化 + 去空白 + 小写 + `|` 多等价答案，仍不匹配时用 LLM 语义等价判断
3. 简答题：使用 LLM 进行语义评分（模型取自 state 中的用户运行时配置）
4. 生成详细的评分报告并持久化到 WorkflowQuestion 记录
"""

from datetime import UTC, datetime
from typing import Any

from django.utils import timezone

from Django_xm.apps.core.logging_utils import get_logger
from Django_xm.common.messages import content_to_str

from ..services._model_helper import get_chat_model_from_state
from ..services.state import ScoreDetail, StudyFlowState
from .stream_events import emit_step

logger = get_logger(__name__)


def _is_option_index(value: str) -> bool:
    """判断字符串是否为选项索引（如 "A"、"B"）。"""
    stripped = value.strip()
    return len(stripped) == 1 and stripped.upper() in "ABCDEFGH"


def _normalize_fill(value: str) -> str:
    """填空答案归一化：NFKC（统一全半角）+ 去空白 + 小写。"""
    import unicodedata

    normalized = unicodedata.normalize("NFKC", str(value)).strip().lower()
    return normalized.replace(" ", "").replace("　", "")


def _judge_fill_blank_semantic(model, question_text: str, correct_answer: str, user_answer: str) -> bool:
    """用 LLM 判断填空题答案是否语义等价（如 "11" 与 "十一" 均正确）。"""
    judge_prompt = f"""请判断填空题的学生答案是否正确。

题目：{question_text}
标准答案：{correct_answer}
学生答案：{user_answer}

判断标准：答案语义等价即可（如 "11" 和 "十一" 都正确），不要求完全一致。

请只返回"正确"或"错误"。"""
    try:
        response = model.invoke([{"role": "user", "content": judge_prompt}])
        result = content_to_str(response.content).strip()
        is_correct = "正确" in result and "错误" not in result
        logger.info(f"[Grading Node] 填空题 LLM 语义判断: {result}, is_correct={is_correct}")
        return is_correct
    except Exception as e:
        logger.warning(f"[Grading Node] 填空题 LLM 语义判断失败，按错误处理: {e}")
        return False


def _grade_short_answer(model, question: dict, correct_answer: str, user_answer: str) -> tuple[int, str]:
    """用 LLM 评分简答题，返回 (得分, 评语)。LLM 不可用时回退关键词匹配。"""
    points_possible = question["points"]

    grading_prompt = f"""请评估以下简答题的答案质量。

题目：{question["question"]}

标准答案：{correct_answer}

学生答案：{user_answer}

请根据以下标准评分：
1. 答案的准确性（是否包含关键信息）
2. 答案的完整性（是否覆盖主要知识点）
3. 表达的清晰度

满分：{points_possible} 分

请直接返回得分（0-{points_possible}之间的整数）和简短评语，格式：
得分: X
评语: XXX"""

    try:
        response = model.invoke([{"role": "user", "content": grading_prompt}])
        response_text = response.content
        if not isinstance(response_text, str):
            response_text = str(response_text)

        lines = response_text.strip().split("\n")
        score_line = next(line for line in lines if "得分" in line or "score" in line.lower())
        points_earned = int("".join(filter(str.isdigit, score_line)))
        points_earned = min(max(points_earned, 0), points_possible)

        feedback_line = [line for line in lines if "评语" in line or "feedback" in line.lower()]
        feedback = feedback_line[0].split(":", 1)[1].strip() if feedback_line else response_text
        return points_earned, feedback
    except Exception as parse_error:
        logger.warning(f"[Grading Node] 解析 LLM 评分失败: {parse_error}，使用关键词匹配回退")
        keywords = correct_answer.lower().split()[:5]
        matched = sum(1 for kw in keywords if kw in user_answer.lower())
        points_earned = int((matched / max(len(keywords), 1)) * points_possible)
        feedback = f"得分基于关键词匹配。建议参考标准答案：{correct_answer}"
        return points_earned, feedback


def grading_node(state: StudyFlowState) -> dict[str, Any]:
    """
    自动评分节点

    功能：
    1. 对比用户答案和标准答案
    2. 对于选择题和填空题，进行规范化匹配（含语义兜底）
    3. 对于简答题，使用 LLM 进行语义评分
    4. 生成详细的评分报告
    """
    logger.info("[Grading Node] 开始评分")
    emit_step("grading", "正在评分...")

    try:
        quiz = state.get("quiz")
        user_answers = state.get("user_answers")

        if not quiz or not user_answers:
            logger.warning("[Grading Node] 练习题或用户答案不存在，跳过评分")
            return {
                "score": 0,
                "score_details": {"total_count": 0, "correct_count": 0, "question_scores": []},
                "current_step": "grading_error",
                "updated_at": datetime.now(UTC).isoformat(),
            }

        questions = quiz["questions"]
        total_points = quiz["total_points"]

        score_details: list[ScoreDetail] = []
        total_earned = 0
        correct_count = 0

        # 模型取自 state 中的用户运行时配置（支持深度思考 special_params）
        model = get_chat_model_from_state(state)

        for question in questions:
            q_id = question["id"]
            q_type = question["type"]
            correct_answer = question["answer"]
            points_possible = question["points"]
            options = question.get("options") or []

            user_answer = user_answers.get(q_id, "").strip()

            if q_type == "multiple_choice":
                # 兼容两种作答：用户提交选项字母（"B"）或选项完整内容（前端 radio value = opt）
                correct_answer_display = correct_answer
                if _is_option_index(correct_answer) and options:
                    idx = ord(correct_answer.strip().upper()) - ord("A")
                    if 0 <= idx < len(options):
                        correct_answer_display = options[idx]

                user_answer_display = user_answer
                if _is_option_index(user_answer) and options:
                    idx = ord(user_answer.strip().upper()) - ord("A")
                    if 0 <= idx < len(options):
                        user_answer_display = options[idx]

                is_correct = (
                    user_answer.strip().upper() == correct_answer.strip().upper()
                    or user_answer_display.strip() == correct_answer_display.strip()
                )
                points_earned = points_possible if is_correct else 0
                feedback = "回答正确！" if is_correct else f"回答错误。正确答案是：{correct_answer_display}"
                # 评分详情统一以选项内容展示
                correct_answer = correct_answer_display
                user_answer = user_answer_display

            elif q_type == "fill_blank":
                # 1) 规范化精确匹配（快速路径）：支持 `|` 分隔的多等价答案
                normalized_user = _normalize_fill(user_answer)
                acceptable = [a.strip() for a in correct_answer.split("|") if a.strip()]
                is_correct = any(normalized_user == _normalize_fill(a) for a in acceptable)

                # 2) 精确匹配失败且用户有作答时，用 LLM 判断语义等价
                if not is_correct and user_answer.strip():
                    is_correct = _judge_fill_blank_semantic(model, question["question"], correct_answer, user_answer)

                points_earned = points_possible if is_correct else 0
                feedback = "回答正确！" if is_correct else f"回答错误。正确答案是：{correct_answer}"

            elif q_type == "short_answer":
                logger.info(f"[Grading Node] 使用 LLM 评分简答题: {q_id}")
                points_earned, feedback = _grade_short_answer(model, question, correct_answer, user_answer)
                is_correct = points_earned >= points_possible * 0.6

            else:
                logger.warning(f"[Grading Node] 未知题型: {q_type}")
                is_correct = False
                points_earned = 0
                feedback = "未知题型，无法评分"

            detail: ScoreDetail = {
                "question_id": q_id,
                "user_answer": user_answer,
                "correct_answer": correct_answer,
                "is_correct": is_correct,
                "points_earned": points_earned,
                "points_possible": points_possible,
                "feedback": feedback,
            }
            score_details.append(detail)

            total_earned += points_earned
            if is_correct:
                correct_count += 1

        score = int((total_earned / total_points) * 100) if total_points > 0 else 0

        logger.info(f"[Grading Node] 评分完成: {score}分 ({correct_count}/{len(questions)} 题正确)")

        # 持久化评分结果到 WorkflowQuestion 记录（失败不影响主流程）
        _persist_grading_results(state, score_details)

        return {
            "score": score,
            "score_details": {
                "total_count": len(questions),
                "correct_count": correct_count,
                "question_scores": score_details,
            },
            "current_step": "grading_completed",
        }

    except Exception as e:
        logger.exception("[Grading Node] 评分失败")
        return {
            "score": 0,
            "score_details": {"total_count": 0, "correct_count": 0, "question_scores": []},
            "error": f"评分失败: {e!s}",
            "current_step": "grading_error",
            "updated_at": datetime.now(UTC).isoformat(),
        }


def _persist_grading_results(state: StudyFlowState, score_details: list[ScoreDetail]) -> None:
    """将评分结果写回 WorkflowQuestion 记录（question_id 形如 q1_r0）。"""
    try:
        from ..models import WorkflowQuestion, WorkflowQuestionStatus, WorkflowSession

        thread_id = state.get("thread_id")
        attempt_index = state.get("retry_count", 0) or 0

        if not thread_id:
            return

        session = WorkflowSession.objects.filter(thread_id=thread_id, is_deleted=False).first()
        if not session:
            logger.warning(f"[Grading Node] 未找到工作流会话，跳过评分持久化: {thread_id}")
            return

        for detail in score_details:
            full_question_id = f"{detail['question_id']}_r{attempt_index}"
            WorkflowQuestion.objects.filter(session=session, question_id=full_question_id).update(
                user_answer=detail["user_answer"],
                is_correct=detail["is_correct"],
                points_earned=detail["points_earned"],
                scored_at=timezone.now(),
                status=WorkflowQuestionStatus.SCORED,
            )

        logger.info(f"[Grading Node] 已更新 {len(score_details)} 道题目的评分结果")
    except Exception:
        logger.exception("[Grading Node] 评分结果持久化失败")
