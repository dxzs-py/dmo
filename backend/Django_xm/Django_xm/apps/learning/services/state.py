"""
LangGraph 工作流状态模型定义

本模块定义了学习工作流的全局状态结构，用于在各个节点之间传递和维护数据。
"""

from typing import Annotated, Any, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class StudyFlowState(TypedDict):
    """
    学习工作流的全局状态

    该状态在整个工作流执行过程中被传递和更新，包含了从用户输入到最终反馈的所有信息。
    """

    messages: Annotated[list[BaseMessage], add_messages]
    """对话历史消息列表，使用 add_messages 注解，LangGraph 会自动合并新旧消息"""

    user_question: str
    """用户提出的学习问题"""

    user_id: int | None
    """用户 ID，用于检索用户私有的知识库（retrieval_node 据此构造 user_{id}_{kb_name} 索引名）"""

    knowledge_base_ids: list[str] | None
    """用户选择的知识库名称列表（原始名称，与 KnowledgeBaseSelector 的 v-model 一致）。
    为空列表或 None 时表示未选择知识库，retrieval_node 跳过 RAG 检索。"""

    learning_plan: dict[str, Any] | None
    """
    学习计划，包含：
    - topic: 学习主题
    - objectives: 学习目标列表
    - key_points: 关键知识点列表
    - difficulty: 难度级别 (beginner/intermediate/advanced)
    - estimated_time: 预计学习时间（分钟）
    """

    retrieved_docs: list[dict[str, Any]] | None
    """
    检索到的文档列表，每个文档包含：
    - content: 文档内容
    - metadata: 元数据（来源、标题等）
    - relevance_score: 相关性分数
    """

    quiz: dict[str, Any] | None
    """
    生成的练习题，包含：
    - questions: 题目列表
      - id: 题目ID
      - type: 题型 (multiple_choice/fill_blank/short_answer)
      - question: 题目内容
      - options: 选项列表（选择题）
      - answer: 标准答案
      - explanation: 答案解析
      - points: 分值
    - total_points: 总分
    - time_limit: 答题时间限制（分钟）
    """

    user_answers: dict[str, Any] | None
    """
    用户提交的答案，格式：
    {
        "question_id": "user_answer",
        ...
    }
    """

    score: int | None
    """用户得分（0-100）"""

    score_details: dict[str, Any] | None
    """
    详细评分信息：
    - correct_count: 答对题数
    - total_count: 总题数
    - question_scores: 每题得分详情
    """

    feedback: str | None
    """个性化反馈信息"""

    retry_count: int
    """重试次数计数器"""

    should_retry: bool
    """是否需要重新出题（得分低于60分时）"""

    current_step: str
    """当前执行步骤，用于追踪工作流进度"""

    thread_id: str
    """会话线程 ID，用于标识唯一的工作流实例"""

    created_at: str | None
    """创建时间戳"""

    updated_at: str | None
    """最后更新时间戳"""

    error: str | None
    """错误信息（如果有）"""

    error_node: str | None
    """发生错误的节点名称"""


class QuizQuestion(TypedDict):
    """练习题单题结构"""
    id: str
    type: str
    question: str
    options: list[str] | None
    answer: str
    explanation: str
    points: int


class RetrievedDocument(TypedDict):
    """检索文档结构"""
    content: str
    metadata: dict[str, Any]
    relevance_score: float


class ScoreDetail(TypedDict):
    """评分详情结构"""
    question_id: str
    user_answer: str
    correct_answer: str
    is_correct: bool
    points_earned: int
    points_possible: int
    feedback: str
