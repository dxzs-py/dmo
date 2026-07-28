import operator
from collections.abc import Sequence
from typing import Annotated, NotRequired, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel


class PlanStep(BaseModel):
    """计划步骤"""
    description: str
    tool: str
    args: dict = {}


class PlanModel(BaseModel):
    """结构化执行计划"""
    steps: list[PlanStep]
    total_steps: int


class WorkflowState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]
    mode: NotRequired[str]
    query: NotRequired[str]
    plan: NotRequired[PlanModel | None]
    current_step: NotRequired[int]
    tool_results: Annotated[list[dict], operator.add]
    research_queries: list[str]
    research_results: Annotated[list[str], operator.add]
    search_errors: Annotated[list[str], operator.add]
    available_tools: NotRequired[dict]
    final_response: NotRequired[str | None]
    error: NotRequired[str | None]
