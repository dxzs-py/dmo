"""
学习规划节点 (Planner Node)

本节点负责分析用户问题，生成个性化的学习计划。
当检测到已有 learning_plan 时跳过规划（用于"继续练习"场景）。
"""

from typing import Any

from django.utils import timezone
from langchain_core.messages import AIMessage
from pydantic import BaseModel, Field

from Django_xm.apps.core.config import get_logger

from ..services._model_helper import get_structured_model_from_state
from ..services.state import StudyFlowState

logger = get_logger(__name__)


class LearningPlanSchema(BaseModel):
    """学习计划的结构化输出模式"""

    topic: str = Field(description="学习主题")
    objectives: list[str] = Field(description="学习目标列表，至少3个")
    key_points: list[str] = Field(description="关键知识点列表，至少5个")
    difficulty: str = Field(description="难度级别：beginner, intermediate, advanced")
    estimated_time: int = Field(description="预计学习时间（分钟）")


def planner_node(state: StudyFlowState) -> dict[str, Any]:
    """
    学习规划节点

    功能：
    1. 分析用户问题
    2. 生成结构化的学习计划
    3. 使用 LLM 的结构化输出功能
    """
    user_question = state.get("user_question", "")
    logger.info(f"[Planner Node] 开始生成学习计划，用户问题: {user_question}")

    try:
        # 检测已有 learning_plan，跳过规划（继续练习场景）
        existing_plan = state.get("learning_plan")
        if existing_plan:
            logger.info("[Planner Node] 检测到已有学习计划，跳过规划")
            return {
                "learning_plan": existing_plan,
                "current_step": "planner_completed",
                "messages": [AIMessage(content="\n\n📋 复用已有学习计划，继续生成新练习题...")],
                "updated_at": timezone.now().isoformat(),
            }

        # 结构化输出必须使用非流式模式（避免返回 None）
        # 模型来自 state 中的用户运行时配置（provider_id/model_name/special_params 等）
        structured_model = get_structured_model_from_state(state, LearningPlanSchema)

        system_prompt = """你是一位经验丰富的学习规划专家。

你的任务是根据用户的学习问题，制定一个详细的学习计划。

请分析问题的难度和范围，然后生成：
1. 学习主题：简洁明确的主题描述
2. 学习目标：至少3个具体、可衡量的学习目标
3. 关键知识点：至少5个需要掌握的核心知识点
4. 难度级别：beginner（入门）、intermediate（中级）或 advanced（高级）
5. 预计学习时间：合理估计完成学习所需的时间（分钟）

请确保计划具有针对性和可操作性。"""

        user_prompt = f"用户的学习问题：{user_question}\n\n请为此问题制定学习计划。"

        logger.info("[Planner Node] 调用 LLM 生成学习计划...")
        plan_response = structured_model.invoke(
            [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]
        )

        # 防御性检查：所有 fallback 模型都返回空时统一报错
        if plan_response is None or not hasattr(plan_response, "topic"):
            error_msg = "所有结构化输出模型均返回空结果"
            logger.error(f"[Planner Node] {error_msg}")
            return {
                "error": error_msg,
                "error_node": "planner",
                "current_step": "planner_error",
                "messages": [AIMessage(content=f"\n\n⚠️ {error_msg}，请稍后重试。")],
                "updated_at": timezone.now().isoformat(),
            }

        learning_plan = {
            "topic": plan_response.topic,
            "objectives": plan_response.objectives,
            "key_points": plan_response.key_points,
            "difficulty": plan_response.difficulty,
            "estimated_time": plan_response.estimated_time,
        }

        logger.info(f"[Planner Node] 学习计划生成成功: {learning_plan['topic']}")

        plan_summary = f"""已为您制定学习计划：

📚 **学习主题**: {learning_plan["topic"]}

🎯 **学习目标**:
{chr(10).join(f"{i + 1}. {obj}" for i, obj in enumerate(learning_plan["objectives"]))}

💡 **关键知识点**:
{chr(10).join(f"• {point}" for point in learning_plan["key_points"])}

📊 **难度级别**: {learning_plan["difficulty"]}
⏱️ **预计时间**: {learning_plan["estimated_time"]} 分钟
"""

        return {
            "learning_plan": learning_plan,
            "messages": [AIMessage(content=plan_summary)],
            "current_step": "planner",
            "updated_at": timezone.now().isoformat(),
        }

    except Exception as e:
        logger.exception("[Planner Node] 生成学习计划失败")
        return {
            "error": f"学习计划生成失败: {e!s}",
            "error_node": "planner",
            "current_step": "planner_error",
            "updated_at": timezone.now().isoformat(),
        }
