"""
LangGraph 工作流状态模型定义
"""

from typing import TypedDict, Optional, Annotated, List, Dict, Any
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage


class StudyFlowState(TypedDict):
    """
    学习工作流的全局状态
    """
    messages: Annotated[List[BaseMessage], add_messages]
    user_question: str
    learning_plan: Optional[Dict[str, Any]]
    retrieved_docs: Optional[List[Dict[str, Any]]]
    quiz: Optional[Dict[str, Any]]
    user_answers: Optional[Dict[str, str]]
    score: Optional[int]
    score_details: Optional[Dict[str, Any]]
    feedback: Optional[str]
    should_retry: bool
    retry_count: int
    current_step: str
    thread_id: str


class RetrievedDocument(TypedDict):
    content: str
    metadata: Dict[str, Any]
    relevance_score: float


class LearningPlan(TypedDict):
    topic: str
    objectives: List[str]
    key_points: List[str]
    difficulty: str
    estimated_time: int


class ScoreDetail(TypedDict):
    question_id: str
    is_correct: bool
    points_earned: int
    points_possible: int
    feedback: str