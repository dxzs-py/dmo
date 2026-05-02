"""
结构化输出 Schema - 使用 Pydantic 定义各种输出格式
"""

from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, field_validator
from datetime import datetime
from enum import Enum


# ============= RAG 相关 Schema =============

class RAGResponse(BaseModel):
    """RAG 回答的结构化输出"""
    
    answer: str = Field(
        description="基于检索文档生成的回答",
        min_length=10,
    )
    sources: List[str] = Field(
        description="引用的文档来源列表",
        min_length=1,
    )
    confidence: Optional[float] = Field(
        default=None,
        description="回答的置信度（0-1）",
        ge=0.0,
        le=1.0,
    )
    metadata: Optional[Dict[str, Any]] = Field(
        default_factory=dict,
        description="额外的元数据信息",
    )
    
    @field_validator("sources")
    @classmethod
    def validate_sources(cls, v):
        """验证来源不能为空"""
        if not v or len(v) == 0:
            raise ValueError("必须提供至少一个引用来源")
        return v
    
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "answer": "LangChain 是一个用于开发大语言模型应用的框架...",
                    "sources": ["langchain_docs.md", "tutorial.pdf"],
                    "confidence": 0.95,
                    "metadata": {"retrieved_chunks": 3}
                }
            ]
        }
    }


# ============= 学习计划相关 Schema =============

class DifficultyLevel(str, Enum):
    """难度级别"""
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"


class StudyPlanStep(BaseModel):
    """学习计划步骤"""
    
    step_number: int = Field(description="步骤编号", ge=1)
    title: str = Field(description="步骤标题", min_length=5)
    description: str = Field(description="步骤描述", min_length=10)
    estimated_hours: float = Field(description="预计学习时长（小时）", gt=0)
    resources: List[str] = Field(
        default_factory=list,
        description="推荐学习资源",
    )
    key_concepts: List[str] = Field(
        default_factory=list,
        description="关键概念列表",
    )
    
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "step_number": 1,
                    "title": "LangChain 基础概念",
                    "description": "学习 LangChain 的核心概念和基本用法",
                    "estimated_hours": 4.0,
                    "resources": ["官方文档", "入门教程"],
                    "key_concepts": ["Agents", "Chains", "Models"]
                }
            ]
        }
    }


class StudyPlan(BaseModel):
    """完整的学习计划"""
    
    topic: str = Field(description="学习主题", min_length=5)
    difficulty: DifficultyLevel = Field(description="难度级别")
    total_hours: float = Field(description="总学习时长（小时）", gt=0)
    steps: List[StudyPlanStep] = Field(
        description="学习步骤列表",
        min_length=1,
    )
    prerequisites: List[str] = Field(
        default_factory=list,
        description="前置知识要求",
    )
    learning_objectives: List[str] = Field(
        default_factory=list,
        description="学习目标",
    )
    created_at: datetime = Field(
        default_factory=datetime.now,
        description="创建时间",
    )
    
    @field_validator("steps")
    @classmethod
    def validate_steps(cls, v):
        """验证步骤编号连续"""
        if not v:
            raise ValueError("学习计划必须包含至少一个步骤")
        
        step_numbers = [step.step_number for step in v]
        expected = list(range(1, len(v) + 1))
        if step_numbers != expected:
            raise ValueError("步骤编号必须从 1 开始连续递增")
        
        return v
    
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "topic": "LangChain 全栈开发",
                    "difficulty": "intermediate",
                    "total_hours": 40.0,
                    "steps": [
                        {
                            "step_number": 1,
                            "title": "基础概念",
                            "description": "学习核心概念",
                            "estimated_hours": 8.0,
                            "resources": ["文档"],
                            "key_concepts": ["Agents"]
                        }
                    ],
                    "prerequisites": ["Python 基础", "机器学习基础"],
                    "learning_objectives": ["掌握 LangChain 开发"]
                }
            ]
        }
    }


# ============= 研究报告相关 Schema =============

class ResearchSection(BaseModel):
    """研究报告章节"""
    
    section_number: int = Field(description="章节编号", ge=1)
    title: str = Field(description="章节标题", min_length=5)
    content: str = Field(description="章节内容", min_length=50)
    sources: List[str] = Field(
        default_factory=list,
        description="本章节引用的来源",
    )
    key_findings: List[str] = Field(
        default_factory=list,
        description="关键发现",
    )


class ResearchReport(BaseModel):
    """研究报告"""
    
    title: str = Field(description="报告标题", min_length=10)
    topic: str = Field(description="研究主题", min_length=5)
    summary: str = Field(description="执行摘要", min_length=100)
    sections: List[ResearchSection] = Field(
        description="报告章节列表",
        min_length=1,
    )
    conclusions: List[str] = Field(
        description="研究结论",
        min_length=1,
    )
    references: List[str] = Field(
        description="参考文献列表",
        min_length=1,
    )
    created_at: datetime = Field(
        default_factory=datetime.now,
        description="创建时间",
    )
    metadata: Optional[Dict[str, Any]] = Field(
        default_factory=dict,
        description="额外元数据",
    )
    
    @field_validator("sections")
    @classmethod
    def validate_sections(cls, v):
        """验证章节编号连续"""
        if not v:
            raise ValueError("报告必须包含至少一个章节")
        
        section_numbers = [section.section_number for section in v]
        expected = list(range(1, len(v) + 1))
        if section_numbers != expected:
            raise ValueError("章节编号必须从 1 开始连续递增")
        
        return v
    
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "title": "LangChain 在企业级应用中的实践研究",
                    "topic": "LangChain 企业应用",
                    "summary": "本报告深入研究了 LangChain 框架...",
                    "sections": [
                        {
                            "section_number": 1,
                            "title": "引言",
                            "content": "LangChain 是...",
                            "sources": ["doc1.pdf"],
                            "key_findings": ["发现1"]
                        }
                    ],
                    "conclusions": ["结论1", "结论2"],
                    "references": ["参考文献1"]
                }
            ]
        }
    }


# ============= 测验相关 Schema =============

class QuestionType(str, Enum):
    """题目类型"""
    SINGLE_CHOICE = "single_choice"
    MULTIPLE_CHOICE = "multiple_choice"
    TRUE_FALSE = "true_false"
    SHORT_ANSWER = "short_answer"


class QuizQuestion(BaseModel):
    """测验题目"""
    
    question_number: int = Field(description="题目编号", ge=1)
    question_type: QuestionType = Field(description="题目类型")
    question: str = Field(description="题目内容", min_length=10)
    options: Optional[List[str]] = Field(
        default=None,
        description="选项列表（选择题必填）",
    )
    correct_answer: str = Field(description="正确答案")
    explanation: Optional[str] = Field(
        default=None,
        description="答案解析",
    )
    points: int = Field(default=1, description="题目分值", ge=1)
    
    @field_validator("options")
    @classmethod
    def validate_options(cls, v, info):
        """验证选择题必须有选项"""
        question_type = info.data.get("question_type")
        if question_type in [QuestionType.SINGLE_CHOICE, QuestionType.MULTIPLE_CHOICE]:
            if not v or len(v) < 2:
                raise ValueError("选择题必须提供至少 2 个选项")
        return v


class QuizAnswer(BaseModel):
    """用户答案"""
    
    question_number: int = Field(description="题目编号", ge=1)
    user_answer: str = Field(description="用户答案")
    is_correct: Optional[bool] = Field(
        default=None,
        description="是否正确（评分后填充）",
    )
    points_earned: Optional[int] = Field(
        default=None,
        description="获得分数（评分后填充）",
    )


class Quiz(BaseModel):
    """完整测验"""
    
    title: str = Field(description="测验标题", min_length=5)
    topic: str = Field(description="测验主题", min_length=5)
    questions: List[QuizQuestion] = Field(
        description="题目列表",
        min_length=1,
    )
    total_points: int = Field(description="总分", ge=1)
    passing_score: int = Field(description="及格分数", ge=0)
    time_limit_minutes: Optional[int] = Field(
        default=None,
        description="时间限制（分钟）",
        ge=1,
    )
    created_at: datetime = Field(
        default_factory=datetime.now,
        description="创建时间",
    )
    
    @field_validator("questions")
    @classmethod
    def validate_questions(cls, v):
        """验证题目编号连续"""
        if not v:
            raise ValueError("测验必须包含至少一个题目")
        
        question_numbers = [q.question_number for q in v]
        expected = list(range(1, len(v) + 1))
        if question_numbers != expected:
            raise ValueError("题目编号必须从 1 开始连续递增")
        
        return v
    
    @field_validator("total_points")
    @classmethod
    def validate_total_points(cls, v, info):
        """验证总分等于所有题目分值之和"""
        questions = info.data.get("questions", [])
        if questions:
            calculated_total = sum(q.points for q in questions)
            if v != calculated_total:
                raise ValueError(f"总分 {v} 不等于题目分值之和 {calculated_total}")
        return v
    
    @field_validator("passing_score")
    @classmethod
    def validate_passing_score(cls, v, info):
        """验证及格分数不超过总分"""
        total_points = info.data.get("total_points", 0)
        if v > total_points:
            raise ValueError(f"及格分数 {v} 不能超过总分 {total_points}")
        return v
    
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "title": "LangChain 基础测验",
                    "topic": "LangChain 核心概念",
                    "questions": [
                        {
                            "question_number": 1,
                            "question_type": "single_choice",
                            "question": "什么是 LangChain?",
                            "options": ["A. 框架", "B. 库", "C. 工具"],
                            "correct_answer": "A",
                            "explanation": "LangChain 是一个框架",
                            "points": 1
                        }
                    ],
                    "total_points": 1,
                    "passing_score": 1,
                    "time_limit_minutes": 30
                }
            ]
        }
    }


# ============= 导出所有 Schema =============

__all__ = [
    "RAGResponse",
    "StudyPlan",
    "StudyPlanStep",
    "DifficultyLevel",
    "ResearchReport",
    "ResearchSection",
    "Quiz",
    "QuizQuestion",
    "QuizAnswer",
    "QuestionType",
]
