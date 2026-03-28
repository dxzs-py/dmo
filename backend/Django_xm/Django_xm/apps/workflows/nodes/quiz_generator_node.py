"""
练习题生成节点 (Quiz Generator Node)
"""

import logging
from typing import Dict, Any, List
from pydantic import BaseModel, Field

from ..state import StudyFlowState
from Django_xm.apps.core.models import get_chat_model
from Django_xm.apps.core.config import get_logger

logger = get_logger(__name__)


class QuizQuestionSchema(BaseModel):
    id: str = Field(description="题目唯一标识，如 q1, q2")
    type: str = Field(description="题型：multiple_choice（选择题）、fill_blank（填空题）、short_answer（简答题）")
    question: str = Field(description="题目内容")
    options: List[str] | None = Field(default=None, description="选择题的选项列表")
    answer: str = Field(description="标准答案")
    explanation: str = Field(description="答案解析")
    points: int = Field(description="题目分值")


class QuizSchema(BaseModel):
    questions: List[QuizQuestionSchema] = Field(description="题目列表，至少5题")
    total_points: int = Field(description="总分")
    time_limit: int = Field(description="建议答题时间（分钟）")


def quiz_generator_node(state: StudyFlowState) -> Dict[str, Any]:
    logger.info("[Quiz Generator Node] 开始生成练习题")

    try:
        learning_plan = state.get("learning_plan")
        retrieved_docs = state.get("retrieved_docs", [])

        if not learning_plan:
            raise ValueError("学习计划不存在，无法生成练习题")

        model = get_chat_model()
        structured_model = model.with_structured_output(QuizSchema)

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
   - 填空题（fill_blank）：1-2题
   - 简答题（short_answer）：1题
3. 难度适配：根据学习计划的难度级别出题
4. 覆盖知识点：题目应覆盖学习计划中的关键知识点
5. 答案解析：每题都要提供详细的答案解析
6. 分值分配：总分控制在100分左右

请确保题目清晰、答案准确、解析详细。"""

        user_prompt = f"""学习计划：
主题：{learning_plan['topic']}
难度：{learning_plan['difficulty']}
关键知识点：
{', '.join(learning_plan['key_points'])}

参考文档：
{context}

请根据以上信息生成一套练习题。"""

        logger.info("[Quiz Generator Node] 调用 LLM 生成练习题...")
        quiz_response = structured_model.invoke([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ])

        questions = []
        for q in quiz_response.questions:
            questions.append({
                "id": q.id,
                "type": q.type,
                "question": q.question,
                "options": q.options,
                "answer": q.answer,
                "explanation": q.explanation,
                "points": q.points
            })

        quiz = {
            "questions": questions,
            "total_points": quiz_response.total_points,
            "time_limit": quiz_response.time_limit
        }

        logger.info(f"[Quiz Generator Node] 练习题生成成功，共 {len(questions)} 题")

        return {"quiz": quiz}

    except Exception as e:
        logger.error(f"[Quiz Generator Node] 生成练习题失败: {e}")
        raise