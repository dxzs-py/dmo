from typing import Optional, Dict, List, Any
import re
from langchain_core.messages import AIMessage, ToolMessage, BaseMessage

from ..config import get_logger

logger = get_logger(__name__)


def extract_reasoning(message: BaseMessage) -> Optional[Dict[str, Any]]:
    if not isinstance(message, AIMessage):
        return None

    response_metadata = getattr(message, "response_metadata", {})

    if "reasoning" in response_metadata:
        reasoning_data = response_metadata["reasoning"]
        return {
            "content": reasoning_data.get("content", ""),
            "duration": reasoning_data.get("duration_ms", 0) / 1000,
        }

    content = message.content
    if isinstance(content, str):
        thinking_match = re.search(r'<thinking>(.*?)</thinking>', content, re.DOTALL)
        if thinking_match:
            return {
                "content": thinking_match.group(1).strip(),
                "duration": 0,
            }

    return None


def extract_tool_calls(message: BaseMessage) -> List[Dict[str, Any]]:
    if not isinstance(message, AIMessage):
        return []

    tool_calls = getattr(message, "tool_calls", [])

    if not tool_calls:
        return []

    result = []
    for tool_call in tool_calls:
        tool_info = {
            "id": tool_call.get("id", ""),
            "name": tool_call.get("name", ""),
            "type": f"tool-call-{tool_call.get('name', 'unknown')}",
            "state": "input-available",
            "parameters": tool_call.get("args", {}),
            "result": None,
            "error": None,
        }
        result.append(tool_info)

    logger.debug(f"提取到 {len(result)} 个工具调用")
    return result


def extract_tool_result(message: ToolMessage) -> Optional[Dict[str, Any]]:
    if not isinstance(message, ToolMessage):
        return None

    tool_call_id = getattr(message, "tool_call_id", "")
    content = message.content

    is_error = getattr(message, "status", None) == "error"

    return {
        "id": tool_call_id,
        "state": "output-error" if is_error else "output-available",
        "result": None if is_error else content,
        "error": content if is_error else None,
    }


def extract_sources(message: BaseMessage, context: Optional[Dict] = None) -> List[Dict[str, str]]:
    sources = []

    if context and "retrieved_docs" in context:
        docs = context["retrieved_docs"]
        for doc in docs:
            source = {
                "href": doc.get("metadata", {}).get("source", "#"),
                "title": doc.get("metadata", {}).get("title", "Unknown Source"),
            }
            sources.append(source)

    if isinstance(message, AIMessage):
        response_metadata = getattr(message, "response_metadata", {})
        if "sources" in response_metadata:
            sources.extend(response_metadata["sources"])

    logger.debug(f"提取到 {len(sources)} 个来源")
    return sources


def extract_citations(content: str) -> List[Dict[str, Any]]:
    citations = []

    pattern = r'\[(\d+)\]'
    matches = re.finditer(pattern, content)

    for match in matches:
        citation_num = int(match.group(1))
        citations.append({
            "index": citation_num,
            "position": match.start(),
            "text": match.group(0),
        })

    return citations


def extract_plan(message: BaseMessage) -> Optional[Dict[str, Any]]:
    if not isinstance(message, AIMessage):
        return None

    content = message.content

    if not isinstance(content, str):
        return None

    plan_match = re.search(
        r'##?\s*(?:Plan|计划|步骤)\s*\n((?:\d+\.\s*.+\n?)+)',
        content,
        re.IGNORECASE | re.MULTILINE
    )

    if not plan_match:
        return None

    plan_text = plan_match.group(1)
    steps = []

    for line in plan_text.split('\n'):
        step_match = re.match(r'(\d+)\.\s*(.+)', line.strip())
        if step_match:
            steps.append({
                "id": f"step-{step_match.group(1)}",
                "title": step_match.group(2).strip(),
                "status": "pending",
            })

    if not steps:
        return None

    return {
        "title": "执行计划",
        "description": f"共 {len(steps)} 个步骤",
        "steps": steps,
    }


def extract_tasks(message: BaseMessage) -> List[Dict[str, Any]]:
    if not isinstance(message, AIMessage):
        return []

    content = message.content
    if not isinstance(content, str):
        return []

    tasks = []

    task_pattern = r'-\s*\[([ x])\]\s*(.+)'

    for match in re.finditer(task_pattern, content, re.MULTILINE):
        is_completed = match.group(1).lower() == 'x'
        task_title = match.group(2).strip()

        tasks.append({
            "id": f"task-{len(tasks) + 1}",
            "title": task_title,
            "completed": is_completed,
        })

    return tasks


def extract_chain_of_thought(message: BaseMessage) -> Optional[Dict[str, Any]]:
    if not isinstance(message, AIMessage):
        return None

    response_metadata = getattr(message, "response_metadata", {})

    if "chain_of_thought" in response_metadata:
        cot_data = response_metadata["chain_of_thought"]
        return {
            "steps": cot_data.get("steps", [])
        }

    content = message.content
    if not isinstance(content, str):
        return None

    steps = []

    step_matches = re.finditer(
        r'<step(?:\s+id="([^"]+)")?>(.+?)</step>',
        content,
        re.DOTALL
    )

    for idx, match in enumerate(step_matches):
        step_id = match.group(1) or f"step-{idx + 1}"
        step_content = match.group(2).strip()

        steps.append({
            "id": step_id,
            "label": f"Step {idx + 1}",
            "description": step_content,
            "status": "complete",
        })

    if steps:
        return {"steps": steps}

    return None


def extract_queue_items(context: Optional[Dict] = None) -> List[Dict[str, Any]]:
    if not context:
        return []

    if "queue" in context:
        return context["queue"]

    if "pending_tasks" in context:
        tasks = context["pending_tasks"]
        return [
            {
                "id": task.get("id", f"task-{idx}"),
                "title": task.get("title", "Unknown Task"),
                "status": task.get("status", "pending"),
            }
            for idx, task in enumerate(tasks)
        ]

    return []


class MessageExtractor:
    def __init__(self):
        self.context: Dict[str, Any] = {}

    def set_context(self, context: Dict[str, Any]):
        self.context = context

    def extract_all(self, message: BaseMessage) -> Dict[str, Any]:
        extracted = {
            "reasoning": extract_reasoning(message),
            "tools": extract_tool_calls(message),
            "sources": extract_sources(message, self.context),
            "plan": extract_plan(message),
            "tasks": extract_tasks(message),
            "chainOfThought": extract_chain_of_thought(message),
            "queue": extract_queue_items(self.context),
        }

        if isinstance(message, AIMessage) and message.content:
            extracted["citations"] = extract_citations(message.content)

        return {k: v for k, v in extracted.items() if v}
