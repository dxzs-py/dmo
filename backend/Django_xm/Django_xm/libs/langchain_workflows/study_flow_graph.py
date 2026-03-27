from typing import TypedDict, Annotated, Sequence
from langchain_core.messages import BaseMessage
from langgraph.graph import StateGraph, START, END
from ..langchain_core.models import get_chat_model

class StudyState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], "add"]
    query: str
    plan: str
    materials: list
    quiz: str
    grade: str
    feedback: str

def create_study_flow(thread_id=None):
    """创建学习工作流"""
    model = get_chat_model()
    
    def planner_node(state: StudyState):
        """规划节点"""
        query = state["query"]
        prompt = f"""请为以下学习主题制定一个学习计划：
主题：{query}

请包含：
1. 学习目标
2. 学习内容大纲
3. 学习步骤
"""
        result = model.invoke([{"role": "user", "content": prompt}])
        return {"plan": result.content if hasattr(result, 'content') else str(result)}
    
    def retrieval_node(state: StudyState):
        """检索节点"""
        return {"materials": ["学习资料1", "学习资料2", "学习资料3"]}
    
    def quiz_generator_node(state: StudyState):
        """测验生成节点"""
        query = state["query"]
        prompt = f"""请针对以下主题生成5道测验题：
主题：{query}
"""
        result = model.invoke([{"role": "user", "content": prompt}])
        return {"quiz": result.content if hasattr(result, 'content') else str(result)}
    
    def grading_node(state: StudyState):
        """评分节点"""
        return {"grade": "已完成学习"}
    
    def feedback_node(state: StudyState):
        """反馈节点"""
        query = state["query"]
        prompt = f"""请为以下学习主题提供学习总结和反馈：
主题：{query}
"""
        result = model.invoke([{"role": "user", "content": prompt}])
        return {"feedback": result.content if hasattr(result, 'content') else str(result)}
    
    workflow = StateGraph(StudyState)
    workflow.add_node("planner", planner_node)
    workflow.add_node("retrieval", retrieval_node)
    workflow.add_node("quiz_generator", quiz_generator_node)
    workflow.add_node("grading", grading_node)
    workflow.add_node("feedback", feedback_node)
    
    workflow.add_edge(START, "planner")
    workflow.add_edge("planner", "retrieval")
    workflow.add_edge("retrieval", "quiz_generator")
    workflow.add_edge("quiz_generator", "grading")
    workflow.add_edge("grading", "feedback")
    workflow.add_edge("feedback", END)
    
    return workflow.compile()
