"""
自动评分节点 (Grading Node)
"""

import logging
from typing import Dict, Any, List

from ..state import StudyFlowState, ScoreDetail
from Django_xm.apps.core.models import get_chat_model
from Django_xm.apps.core.config import get_logger

logger = get_logger(__name__)


def grading_node(state: StudyFlowState) -> Dict[str, Any]:
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

                try:
                    content = response.content
                    lines = content.split("\n")
                    score_line = [l for l in lines if l.startswith("得分:")][0]
                    points_earned = int(score_line.split(":")[1].strip())
                    feedback_lines = [l for l in lines if l.startswith("评语:")]
                    feedback = feedback_lines[0].split(":", 1)[1].strip() if feedback_lines else ""
                    is_correct = points_earned >= points_possible * 0.6
                except Exception:
                    points_earned = 0
                    feedback = "评分失败"
                    is_correct = False

            else:
                points_earned = 0
                feedback = "未知题型"
                is_correct = False

            score_details.append({
                "question_id": q_id,
                "is_correct": is_correct,
                "points_earned": points_earned,
                "points_possible": points_possible,
                "feedback": feedback
            })

            total_earned += points_earned
            if is_correct:
                correct_count += 1

        score = (total_earned / total_points * 100) if total_points > 0 else 0

        logger.info(f"[Grading Node] 评分完成: {score}分 ({correct_count}/{len(questions)})")

        return {
            "score": score,
            "score_details": {
                "total_count": len(questions),
                "correct_count": correct_count,
                "question_scores": score_details
            }
        }

    except Exception as e:
        logger.error(f"[Grading Node] 评分失败: {e}")
        raise