"""
结构化输出 Schema - 使用 Pydantic 定义各种输出格式
"""

from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, field_validator
from datetime import datetime
from enum import Enum


class DifficultyLevel(str, Enum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"


class QuestionType(str, Enum):
    MULTIPLE_CHOICE = "multiple_choice"
    TRUE_FALSE = "true_false"
    SHORT_ANSWER = "short_answer"


class RAGResponse(BaseModel):
    answer: str = Field(description="基于检索文档生成的回答", min_length=10)
    sources: List[str] = Field(description="引用的文档来源列表", min_length=1)
    confidence: Optional[float] = Field(default=None, description="回答的置信度（0-1）", ge=0.0, le=1.0)
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict, description="额外的元数据信息")

    @field_validator("sources")
    @classmethod
    def validate_sources(cls, v):
        if not v or len(v) == 0:
            raise ValueError("必须提供至少一个引用来源")
        return v


class StudyPlanStep(BaseModel):
    step_number: int = Field(description="步骤编号", ge=1)
    title: str = Field(description="步骤标题", min_length=5)
    description: str = Field(description="步骤描述", min_length=10)
    estimated_hours: float = Field(description="预计学习时长（小时）", gt=0)
    resources: List[str] = Field(default_factory=list, description="推荐学习资源")
    key_concepts: List[str] = Field(default_factory=list, description="关键概念列表")


class StudyPlan(BaseModel):
    topic: str = Field(description="学习主题")
    difficulty: DifficultyLevel = Field(description="难度级别")
    estimated_total_hours: float = Field(description="预计总学习时长（小时）", gt=0)
    objectives: List[str] = Field(description="学习目标列表")
    steps: List[StudyPlanStep] = Field(description="学习步骤列表")
    prerequisites: List[str] = Field(default_factory=list, description="前置知识要求")
    assessment_criteria: List[str] = Field(default_factory=list, description="评估标准")


class ResearchSection(BaseModel):
    title: str = Field(description="章节标题")
    content: str = Field(description="章节内容", min_length=10)
    sources: List[str] = Field(default_factory=list, description="参考来源")


class ResearchReport(BaseModel):
    title: str = Field(description="报告标题")
    summary: str = Field(description="研究摘要", min_length=50)
    sections: List[ResearchSection] = Field(description="报告章节")
    conclusion: str = Field(description="研究结论", min_length=20)
    references: List[str] = Field(default_factory=list, description="参考文献列表")
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict, description="元数据")


class QuizQuestion(BaseModel):
    question_number: int = Field(description="题目编号", ge=1)
    question_type: QuestionType = Field(description="题目类型")
    question: str = Field(description="问题内容", min_length=10)
    options: List[str] = Field(default_factory=list, description="选项列表（用于选择题）")
    correct_answer: str = Field(description="正确答案")
    explanation: Optional[str] = Field(default=None, description="答案解释")


class Quiz(BaseModel):
    topic: str = Field(description="测验主题")
    difficulty: DifficultyLevel = Field(description="难度级别")
    questions: List[QuizQuestion] = Field(description="题目列表")
    time_limit_minutes: Optional[int] = Field(default=None, description="建议完成时间（分钟）")
    passing_score: float = Field(description="及格分数（百分比）", ge=0, le=100)


class QuizAnswer(BaseModel):
    question_number: int = Field(description="题目编号", ge=1)
    selected_answer: str = Field(description="选择的答案")
    is_correct: bool = Field(description="是否正确")
    explanation: Optional[str] = Field(default=None, description="答案解释")