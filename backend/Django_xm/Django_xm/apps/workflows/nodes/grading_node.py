"""
自动评分节点 (Grading Node)

本节点负责对用户提交的答案进行自动评分。
"""

import logging
from typing import Dict, Any, List

from ..state import StudyFlowState, ScoreDetail
from Django_xm.apps.core.models import get_chat_model
from Django_xm.apps.core.config import get_logger

logger = get_logger(__name__)


def grading_node(state: StudyFlowState) -> Dict[str, Any]:
    """
    自动评分节点

    功能：
    1. 对比用户答案和标准答案
    2. 对于选择题和填空题，进行精确匹配
    3. 对于简答题，使用 LLM 进行语义评分
    4. 生成详细的评分报告
    """
    logger.info("[Grading Node] 开始评分")

    try:
        quiz = state.get("quiz")
        user_answers = state.get("user_answers")

        if not quiz or not user_answers:
            raise ValueError("练习题或用户答案不存在，无法评分")

        questions = quiz["questions"]
        total_points = quiz["total_points"]

        score_details: List[ScoreDetail] = []
        total_earned = 0
        correct_count = 0

        model = get_chat_model()

        for question in questions:
            q_id = question["id"]
            q_type = question["type"]
            correct_answer = question["answer"]
            points_possible = question["points"]

            user_answer = user_answers.get(q_id, "").strip()

            if q_type == "multiple_choice":
                is_correct = user_answer.upper() == correct_answer.upper()
                points_earned = points_possible if is_correct else 0
                feedback = "回答正确！" if is_correct else f"回答错误。正确答案是：{correct_answer}"

            elif q_type == "fill_blank":
                is_correct = user_answer.lower() == correct_answer.lower()
                points_earned = points_possible if is_correct else 0
                feedback = "回答正确！" if is_correct else f"回答错误。正确答案是：{correct_answer}"

            elif q_type == "short_answer":
                logger.info(f"[Grading Node] 使用 LLM 评分简答题: {q_id}")

                grading_prompt = f"""请评估以下简答题的答案质量。

题目：{question['question']}

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

                response = model.invoke([{"role": "user", "content": grading_prompt}])
                response_text = response.content

                try:
                    lines = response_text.strip().split('\n')
                    score_line = [l for l in lines if '得分' in l or 'score' in l.lower()][0]
                    points_earned = int(''.join(filter(str.isdigit, score_line)))
                    points_earned = min(max(points_earned, 0), points_possible)

                    feedback_line = [l for l in lines if '评语' in l or 'feedback' in l.lower()]
                    feedback = feedback_line[0].split(':', 1)[1].strip() if feedback_line else response_text

                except Exception as parse_error:
                    logger.warning(f"[Grading Node] 解析 LLM 评分失败: {parse_error}，使用默认评分")
                    keywords = correct_answer.lower().split()[:5]
                    matched = sum(1 for kw in keywords if kw in user_answer.lower())
                    points_earned = int((matched / len(keywords)) * points_possible)
                    feedback = f"得分基于关键词匹配。建议参考标准答案：{correct_answer}"

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
                "feedback": feedback
            }
            score_details.append(detail)

            total_earned += points_earned
            if is_correct:
                correct_count += 1

        score = int((total_earned / total_points) * 100) if total_points > 0 else 0

        logger.info(f"[Grading Node] 评分完成: {score}分 ({correct_count}/{len(questions)} 题正确)")

        return {
            "score": score,
            "score_details": {
                "total_count": len(questions),
                "correct_count": correct_count,
                "question_scores": score_details
            },
            "current_step": "grading_completed"
        }

    except Exception as e:
        logger.error(f"[Grading Node] 评分失败: {e}", exc_info=True)
        raise