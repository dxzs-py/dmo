import json
import os
import logging
from typing import List, Optional, Dict, Any
from pathlib import Path

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


def _get_data_dir() -> str:
    try:
        from django.conf import settings as django_settings
        return str(getattr(django_settings, 'DATA_DIR', 'data'))
    except (ImportError, AttributeError):
        try:
            from Django_xm.apps.ai_engine.config import settings
            return str(getattr(settings, 'data_dir', 'data'))
        except (ImportError, AttributeError):
            return 'data'


TODO_DIR = os.path.join(_get_data_dir(), 'todos')


def _ensure_todo_dir():
    os.makedirs(TODO_DIR, exist_ok=True)


def _get_todo_path(session_id: str) -> str:
    _ensure_todo_dir()
    safe_id = session_id.replace('/', '_').replace('\\', '_')
    return os.path.join(TODO_DIR, f"{safe_id}.json")


def _load_todos(session_id: str) -> List[Dict[str, Any]]:
    path = _get_todo_path(session_id)
    if not os.path.exists(path):
        return []
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"加载待办事项失败: {e}")
        return []


def _save_todos(session_id: str, todos: List[Dict[str, Any]]):
    path = _get_todo_path(session_id)
    _ensure_todo_dir()
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(todos, f, ensure_ascii=False, indent=2)
    except IOError as e:
        logger.error(f"保存待办事项失败: {e}")


def _validate_todos(todos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
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


@tool
def todo_write(todos: str, session_id: str = "default") -> str:
    """管理任务列表。创建、更新或查看待办事项。

    Args:
        todos: JSON格式的任务列表，每个任务包含 id/content/status/priority 字段。
               例如: [{"id":1,"content":"完成任务1","status":"pending","priority":"high"}]
        session_id: 会话ID，用于隔离不同会话的任务
    """
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


@tool
def todo_read(session_id: str = "default") -> str:
    """读取当前会话的任务列表。

    Args:
        session_id: 会话ID
    """
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


def get_todo_tools():
    return [todo_write, todo_read]


TODO_TOOLS = get_todo_tools()
