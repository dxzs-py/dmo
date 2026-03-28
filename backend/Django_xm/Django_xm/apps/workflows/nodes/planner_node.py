"""
学习规划节点 (Planner Node)
"""

import logging
from typing import Dict, Any
from pydantic import BaseModel, Field

from ..state import StudyFlowState
from Django_xm.apps.core.models import get_chat_model
from Django_xm.apps.core.config import get_logger

logger = get_logger(__name__)


class LearningPlanSchema(BaseModel):
    topic: str = Field(description="学习主题")
    objectives: list[str] = Field(description="学习目标列表，至少3个")
    key_points: list[str] = Field(description="关键知识点列表，至少5个")
    difficulty: str = Field(description="难度级别：beginner, intermediate, advanced")
    estimated_time: int = Field(description="预计学习时间（分钟）")


def planner_node(state: StudyFlowState) -> Dict[str, Any]:
    logger.info(f"[Planner Node] 开始生成学习计划，用户问题: {state['user_question']}")

    try:
        model = get_chat_model()
        structured_model = model.with_structured_output(LearningPlanSchema)

        system_prompt = """你是一位经验丰富的学习规划专家。

你的任务是根据用户的学习问题，制定一个详细的学习计划。

请分析问题的难度和范围，然后生成：
1. 学习主题：简洁明确的主题描述
2. 学习目标：至少3个具体、可衡量的学习目标
3. 关键知识点：至少5个需要掌握的核心知识点
4. 难度级别：beginner（入门）、intermediate（中级）或 advanced（高级）
5. 预计学习时间：合理估计完成学习所需的时间（分钟）

请确保计划具有针对性和可操作性。"""

        user_prompt = f"用户的学习问题：{state['user_question']}\n\n请为此问题制定学习计划。"

        logger.info("[Planner Node] 调用 LLM 生成学习计划...")
        plan_response = structured_model.invoke([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ])

        learning_plan = {
            "topic": plan_response.topic,
            "objectives": plan_response.objectives,
            "key_points": plan_response.key_points,
            "difficulty": plan_response.difficulty,
            "estimated_time": plan_response.estimated_time
        }

        logger.info(f"[Planner Node] 学习计划生成成功: {learning_plan['topic']}")

        return {
            "learning_plan": learning_plan,
            "retry_count": 0
        }

    except Exception as e:
        logger.error(f"[Planner Node] 生成学习计划失败: {e}")
        raise