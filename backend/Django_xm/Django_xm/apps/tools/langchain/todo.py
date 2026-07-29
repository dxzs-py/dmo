import json
import logging
import os
from typing import Any

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


def _get_data_dir() -> str:
    try:
        from django.conf import settings as django_settings

        return str(
            getattr(
                django_settings,
                "TOOLS_LANGCHAIN_DIR",
                os.path.join(str(django_settings.DATA_DIR), "tools", "langchain"),
            )
        )
    except (ImportError, AttributeError):
        try:
            from Django_xm.apps.ai_engine.config import settings

            return str(
                getattr(
                    settings,
                    "TOOLS_LANGCHAIN_DIR",
                    os.path.join(str(getattr(settings, "data_dir", "data")), "tools", "langchain"),
                )
            )
        except (ImportError, AttributeError):
            return os.path.join("data", "tools", "langchain")


TODO_DIR = os.path.join(_get_data_dir(), "todos")


def _ensure_todo_dir():
    os.makedirs(TODO_DIR, exist_ok=True)


def _get_todo_path(session_id: str) -> str:
    _ensure_todo_dir()
    safe_id = session_id.replace("/", "_").replace("\\", "_")
    return os.path.join(TODO_DIR, f"{safe_id}.json")


def _load_todos(session_id: str) -> list[dict[str, Any]]:
    path = _get_todo_path(session_id)
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        logger.exception("加载待办事项失败")
        return []


def _save_todos(session_id: str, todos: list[dict[str, Any]]):
    path = _get_todo_path(session_id)
    _ensure_todo_dir()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(todos, f, ensure_ascii=False, indent=2)
    except OSError:
        logger.exception("保存待办事项失败")


def _validate_todos(todos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    valid = []
    for i, todo in enumerate(todos):
        if not isinstance(todo, dict):
            continue
        valid_todo = {
            "id": todo.get("id", i + 1),
            "content": str(todo.get("content", "")),
            "status": todo.get("status", "pending"),
            "priority": todo.get("priority", "medium"),
        }
        if valid_todo["status"] not in ("pending", "in_progress", "completed"):
            valid_todo["status"] = "pending"
        if valid_todo["priority"] not in ("low", "medium", "high"):
            valid_todo["priority"] = "medium"
        valid.append(valid_todo)
    return valid


class TodoWriteInput(BaseModel):
    todos: str = Field(
        description="JSON格式的任务列表，每个任务包含id/content/status/priority字段，"
        '例如:[{"id":1,"content":"完成任务1","status":"pending","priority":"high"}]'
    )
    session_id: str = Field(default="default", description="会话ID，用于隔离不同会话的任务")


class TodoReadInput(BaseModel):
    session_id: str = Field(default="default", description="会话ID")


class TodoWriteTool(BaseTool):
    name: str = "todo_write"
    metadata: dict = Field(default_factory=lambda: {"tier": "extended", "visibility": "selectable", "category": "todo"})
    description: str = (
        "管理任务列表，创建或更新待办事项，支持任务状态和优先级管理。"
        "适用场景：需要跟踪任务进度、管理待办事项列表、规划工作步骤。"
        "不适用：搜索网络信息、文件操作、数学计算。"
        "参数：todos-JSON格式的任务列表（必填，每个任务包含id/content/status/priority字段，"
        "status取值pending/in_progress/completed，priority取值low/medium/high），"
        "session_id-会话ID（用于隔离不同会话的任务，默认'default'）。"
        "边界：todos参数必须是有效的JSON数组格式。"
    )
    args_schema: type[BaseModel] = TodoWriteInput

    def _run(self, todos: str, session_id: str = "default") -> str:
        try:
            if isinstance(todos, str):
                todo_list = json.loads(todos)
            else:
                todo_list = todos
        except json.JSONDecodeError:
            return "错误: todos 参数必须是有效的 JSON 格式"

        if not isinstance(todo_list, list):
            return "错误: todos 必须是数组格式"

        validated = _validate_todos(todo_list)
        _save_todos(session_id, validated)

        pending = sum(1 for t in validated if t["status"] == "pending")
        in_progress = sum(1 for t in validated if t["status"] == "in_progress")
        completed = sum(1 for t in validated if t["status"] == "completed")

        return (
            f"任务列表已更新 (共 {len(validated)} 项)\n"
            f"- 待处理: {pending}\n"
            f"- 进行中: {in_progress}\n"
            f"- 已完成: {completed}"
        )

    async def _arun(self, todos: str, session_id: str = "default") -> str:
        return self._run(todos=todos, session_id=session_id)


class TodoReadTool(BaseTool):
    name: str = "todo_read"
    metadata: dict = Field(default_factory=lambda: {"tier": "extended", "visibility": "selectable", "category": "todo"})
    description: str = (
        "读取当前会话的任务列表，显示所有待办事项及其状态、优先级。"
        "适用场景：需要查看当前任务列表、了解任务进度、确认任务完成情况。"
        "不适用：修改任务（应使用 todo_write）、搜索网络信息、文件操作。"
        "参数：session_id-会话ID（默认'default'）。"
        "边界：无任务时返回提示信息。"
    )
    args_schema: type[BaseModel] = TodoReadInput

    def _run(self, session_id: str = "default") -> str:
        todos = _load_todos(session_id)

        if not todos:
            return "当前没有待办事项"

        lines = ["📋 **任务列表**\n"]
        for todo in todos:
            status_icon = {
                "pending": "⬜",
                "in_progress": "🔄",
                "completed": "✅",
            }.get(todo.get("status", "pending"), "⬜")

            priority_icon = {
                "high": "🔴",
                "medium": "🟡",
                "low": "🟢",
            }.get(todo.get("priority", "medium"), "🟡")

            lines.append(
                f"{status_icon} {priority_icon} [{todo.get('id', '?')}] "
                f"{todo.get('content', '')} ({todo.get('status', 'pending')})"
            )

        return "\n".join(lines)

    async def _arun(self, session_id: str = "default") -> str:
        return self._run(session_id=session_id)


todo_write = TodoWriteTool()
todo_read = TodoReadTool()


def get_todo_tools():
    return [todo_write, todo_read]


TODO_TOOLS = get_todo_tools()
