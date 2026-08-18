"""
练习题生成节点 (Quiz Generator Node)
"""

from typing import Any

from django.utils import timezone
from pydantic import BaseModel, Field

from Django_xm.apps.core.config import get_logger

from ..services._model_helper import get_structured_model_from_state
from ..services.state import StudyFlowState

logger = get_logger(__name__)


class QuizQuestionSchema(BaseModel):
    id: str = Field(description="题目唯一标识，如 q1, q2")
    type: str = Field(description="题型：multiple_choice（选择题）、fill_blank（填空题）、short_answer（简答题）")
    question: str = Field(description="题目内容")
    options: list[str] | None = Field(default=None, description="选择题的选项列表")
    answer: str = Field(description="标准答案")
    explanation: str = Field(description="答案解析")
    points: int = Field(description="题目分值")


class QuizSchema(BaseModel):
    questions: list[QuizQuestionSchema] = Field(description="题目列表，至少5题")
    total_points: int = Field(description="总分")
    time_limit: int = Field(description="建议答题时间（分钟）")


def quiz_generator_node(state: StudyFlowState) -> dict[str, Any]:
    """
    练习题生成节点

    功能：
    1. 根据学习计划生成练习题
    2. 支持多种题型
    3. 提供答案解析
    """
    logger.info("[Quiz Generator Node] 开始生成练习题")

    try:
        learning_plan = state.get("learning_plan")
        retrieved_docs = state.get("retrieved_docs", [])

        if not learning_plan:
            logger.warning("[Quiz Generator Node] 学习计划不存在，跳过练习题生成")
            return {
                "quiz": None,
                "messages": [{"role": "assistant", "content": "\n\n⚠️ 学习计划生成失败，无法生成练习题。请稍后重试。"}],
                "current_step": "quiz_error",
                "updated_at": timezone.now().isoformat(),
            }

        # 结构化输出必须使用非流式模式（流式 + with_structured_output 嵌套结构会返回 None）
        # 模型来自 state 中的用户运行时配置（provider_id/model_name/special_params 等）
        structured_model = get_structured_model_from_state(state, QuizSchema)

        context_parts = []
        if retrieved_docs:
            logger.info(f"[Quiz Generator Node] 使用 {len(retrieved_docs)} 个检索文档作为参考")
            for i, doc in enumerate(retrieved_docs[:3], 1):
                context_parts.append(f"参考文档 {i}:\n{doc['content'][:500]}...")

        context = "\n\n".join(context_parts) if context_parts else "无参考文档，请基于通用知识出题。"

        system_prompt = """你是一位专业的教育测评专家，擅长设计高质量的练习题。

你的任务是根据学习计划和参考资料，生成一套完整的练习题。

要求：
1. 题目数量：至少5题
2. 题型分布：
   - 选择题（multiple_choice）：3-4题，提供4个选项
   - 填空题（fill_blank）：1-2题，答案如有多种等价表达方式，用 | 分隔（如 "11|十一"）
   - 简答题（short_answer）：1题
3. 难度适配：根据学习计划的难度级别出题
4. 覆盖知识点：题目应覆盖学习计划中的关键知识点
5. 答案解析：每题都要提供详细的答案解析
6. 分值分配：
   - 选择题：每题10-15分
   - 填空题：每题15-20分
   - 简答题：每题20-30分
   - 总分控制在100分左右

请确保题目清晰、答案准确、解析详细。"""

        user_prompt = f"""学习计划：
主题：{learning_plan["topic"]}
难度：{learning_plan["difficulty"]}
关键知识点：
{chr(10).join(f"- {point}" for point in learning_plan["key_points"])}

参考文档：
{context}

请根据以上信息生成练习题。"""

        logger.info("[Quiz Generator Node] 调用 LLM 生成练习题...")
        quiz_response = structured_model.invoke(
            [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]
        )

        # 防御性检查：所有 fallback 模型都返回空时统一报错
        if quiz_response is None or not hasattr(quiz_response, "questions"):
            error_msg = "所有结构化输出模型均返回空结果"
            logger.error(f"[Quiz Generator Node] {error_msg}")
            return {
                "quiz": None,
                "error": error_msg,
                "messages": [{"role": "assistant", "content": f"\n\n⚠️ {error_msg}，请稍后重试。"}],
                "current_step": "quiz_error",
                "updated_at": timezone.now().isoformat(),
            }

        questions = []
        for q in quiz_response.questions:
            # 对于选择题，如果 LLM 返回的 answer 是选项索引（如 "A"、"B"），转换为选项内容
            # 这样 quiz.answer 与评分/展示口径统一
            answer_value = q.answer
            if q.type == "multiple_choice" and q.options:
                ans_stripped = answer_value.strip()
                if len(ans_stripped) == 1 and ans_stripped.upper() in "ABCDEFGH":
                    idx = ord(ans_stripped.upper()) - ord("A")
                    if 0 <= idx < len(q.options):
                        answer_value = q.options[idx]

            question = {
                "id": q.id,
                "type": q.type,
                "question": q.question,
                "options": q.options,
                "answer": answer_value,
                "explanation": q.explanation,
                "points": q.points,
            }
            questions.append(question)

        quiz = {
            "questions": questions,
            "total_points": quiz_response.total_points,
            "time_limit": quiz_response.time_limit,
        }

        logger.info(f"[Quiz Generator Node] 练习题生成成功，共 {len(questions)} 题")

        # 持久化题目为 WorkflowQuestion 记录（失败不影响主流程，题目仍在 state.quiz 中）
        _persist_questions(state, questions)

        quiz_display = (
            f"\n\n📝 **练习题已生成**（共 {len(questions)} 题，"
            f"总分 {quiz['total_points']} 分，建议用时 {quiz['time_limit']} 分钟）\n\n"
        )

        for i, q in enumerate(questions, 1):
            quiz_display += f"**第 {i} 题** ({q['points']} 分)\n"
            quiz_display += f"{q['question']}\n"

            if q["type"] == "multiple_choice" and q["options"]:
                for j, opt in enumerate(q["options"], 1):
                    quiz_display += f"  {chr(64 + j)}. {opt}\n"

            quiz_display += "\n"

        return {
            "quiz": quiz,
            "messages": [{"role": "assistant", "content": quiz_display}],
            "current_step": "waiting_for_answers",
            "updated_at": timezone.now().isoformat(),
        }

    except Exception as e:
        logger.exception("[Quiz Generator Node] 生成练习题失败")
        return {
            "quiz": None,
            "error": f"练习题生成失败: {e!s}",
            "messages": [{"role": "assistant", "content": f"\n\n⚠️ 练习题生成失败: {e!s}"}],
            "current_step": "quiz_error",
            "updated_at": timezone.now().isoformat(),
        }


def _persist_questions(state: StudyFlowState, questions: list[dict[str, Any]]) -> None:
    """将生成的题目持久化为 WorkflowQuestion 记录（question_id 形如 q1_r0）。

    同一轮次重复生成时先删除旧题目（防止 restart/重试时重复累积）。
    """
    try:
        from ..models import WorkflowQuestion, WorkflowQuestionStatus, WorkflowSession

        thread_id = state.get("thread_id")
        attempt_index = state.get("retry_count", 0) or 0

        if not thread_id:
            return

        session = WorkflowSession.objects.filter(thread_id=thread_id, is_deleted=False).first()
        if not session:
            logger.warning(f"[Quiz Generator Node] 未找到工作流会话，跳过题目持久化: {thread_id}")
            return

        # 先删除该轮次的旧题目（防止 restart/重试时重复生成）
        WorkflowQuestion.objects.filter(session=session, attempt_index=attempt_index).delete()

        questions_to_create = []
        for idx, q in enumerate(questions, 1):
            question_id = f"q{idx}_r{attempt_index}"
            questions_to_create.append(
                WorkflowQuestion(
                    session=session,
                    question_id=question_id,
                    attempt_index=attempt_index,
                    question_index=idx,
                    type=q["type"],
                    question=q["question"],
                    options=q["options"],
                    correct_answer=q["answer"],
                    explanation=q["explanation"],
                    points=q["points"],
                    status=WorkflowQuestionStatus.PENDING,
                )
            )

        if questions_to_create:
            WorkflowQuestion.objects.bulk_create(questions_to_create)
            logger.info(
                f"[Quiz Generator Node] 已持久化 {len(questions_to_create)} 道题目，"
                f"attempt_index={attempt_index}"
            )
    except Exception:
        logger.exception("[Quiz Generator Node] 题目持久化失败")
