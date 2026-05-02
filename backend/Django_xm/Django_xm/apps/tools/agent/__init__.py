import json
import os
import time
import logging
from typing import Dict, Any, Optional, List
from pathlib import Path

from langchain_core.tools import tool
from langchain_core.messages import HumanMessage

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


AGENT_STORE_DIR = os.path.join(_get_data_dir(), 'agents')

AGENT_TYPES = {
    "general-purpose": "通用代理 - 处理各类任务",
    "explore": "探索代理 - 搜索和分析信息",
    "plan": "规划代理 - 制定计划和策略",
    "verification": "验证代理 - 检查和验证结果",
    "code-review": "代码审查代理 - 分析和评审代码",
    "research": "研究代理 - 深度研究和分析",
}


def _ensure_agent_dir():
    os.makedirs(AGENT_STORE_DIR, exist_ok=True)


def _get_agent_path(agent_id: str) -> str:
    _ensure_agent_dir()
    safe_id = agent_id.replace('/', '_').replace('\\', '_')
    return os.path.join(AGENT_STORE_DIR, f"{safe_id}.json")


def _save_agent_meta(agent_id: str, meta: Dict[str, Any]):
    path = _get_agent_path(agent_id)
    _ensure_agent_dir()
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
    except IOError as e:
        logger.error(f"保存代理元数据失败: {e}")


def _load_agent_meta(agent_id: str) -> Optional[Dict[str, Any]]:
    path = _get_agent_path(agent_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"加载代理元数据失败: {e}")
        return None


def _list_agents() -> List[Dict[str, Any]]:
    _ensure_agent_dir()
    agents = []
    for filename in os.listdir(AGENT_STORE_DIR):
        if filename.endswith('.json'):
            agent_id = filename[:-5]
            meta = _load_agent_meta(agent_id)
            if meta:
                agents.append(meta)
    return agents


def _normalize_agent_type(agent_type: str) -> str:
    type_mapping = {
        "general-purpose": "general-purpose",
        "general": "general-purpose",
        "explore": "explore",
        "Explore": "explore",
        "plan": "plan",
        "Plan": "plan",
        "verification": "verification",
        "Verification": "verification",
        "code-review": "code-review",
        "research": "research",
    }
    return type_mapping.get(agent_type, "general-purpose")


def _get_system_prompt_for_type(agent_type: str, description: str) -> str:
    prompts = {
        "general-purpose": f"你是一个通用AI代理。任务描述：{description}\n\n请完成上述任务，提供详细的结果。",
        "explore": f"你是一个信息探索代理。任务描述：{description}\n\n请搜索和分析相关信息，提供全面的发现。",
        "plan": f"你是一个规划代理。任务描述：{description}\n\n请制定详细的执行计划，包括步骤、时间线和资源需求。",
        "verification": f"你是一个验证代理。任务描述：{description}\n\n请仔细检查和验证，确保结果正确和完整。",
        "code-review": f"你是一个代码审查代理。任务描述：{description}\n\n请分析代码质量、安全性和最佳实践，提供建设性的改进建议。",
        "research": f"你是一个研究代理。任务描述：{description}\n\n请深入研究该主题，提供全面的分析和结论。",
    }
    return prompts.get(agent_type, prompts["general-purpose"])


@tool
def agent_create(
    description: str,
    agent_type: str = "general-purpose",
    parent_session_id: str = "",
) -> str:
    """创建一个子代理任务。子代理可以独立执行特定类型的任务。

    Args:
        description: 任务描述，详细说明子代理需要完成的工作
        agent_type: 代理类型 (general-purpose/explore/plan/verification/code-review/research)
        parent_session_id: 父会话ID，用于关联
    """
    agent_id = f"agent_{int(time.time() * 1000)}"
    normalized_type = _normalize_agent_type(agent_type)

    meta = {
        "agent_id": agent_id,
        "type": normalized_type,
        "description": description,
        "status": "created",
        "parent_session_id": parent_session_id,
        "created_at": time.time(),
        "result": None,
    }

    _save_agent_meta(agent_id, meta)

    type_desc = AGENT_TYPES.get(normalized_type, normalized_type)
    return (
        f"子代理已创建\n"
        f"- 代理ID: {agent_id}\n"
        f"- 类型: {type_desc}\n"
        f"- 任务: {description[:100]}\n\n"
        f"使用 agent_run 工具执行此代理"
    )


@tool
def agent_run(
    agent_id: str,
    input_text: str = "",
) -> str:
    """执行一个已创建的子代理任务。

    Args:
        agent_id: 子代理ID
        input_text: 可选的额外输入文本
    """
    meta = _load_agent_meta(agent_id)
    if not meta:
        return f"错误: 未找到代理 {agent_id}"

    if meta.get("status") == "running":
        return f"代理 {agent_id} 正在运行中"

    meta["status"] = "running"
    _save_agent_meta(agent_id, meta)

    try:
        from Django_xm.apps.ai_engine.services.agent_factory import create_base_agent
        from Django_xm.apps.tools import get_basic_tools

        agent_type = meta.get("type", "general-purpose")
        description = meta.get("description", "")

        system_prompt = _get_system_prompt_for_type(agent_type, description)
        task_input = input_text or description

        agent = create_base_agent(
            tools=get_basic_tools(),
            system_prompt=system_prompt,
            prompt_mode="default",
        )

        result = agent.invoke(input_text=task_input)

        meta["status"] = "completed"
        meta["result"] = result[:5000] if result else ""
        meta["completed_at"] = time.time()
        _save_agent_meta(agent_id, meta)

        return f"子代理执行完成\n\n结果:\n{result[:3000]}"

    except Exception as e:
        meta["status"] = "failed"
        meta["error"] = str(e)
        _save_agent_meta(agent_id, meta)
        return f"子代理执行失败: {str(e)}"


@tool
def agent_list() -> str:
    """列出所有已创建的子代理任务。"""
    agents = _list_agents()

    if not agents:
        return "当前没有子代理任务"

    lines = ["🤖 **子代理列表**\n"]
    for agent in agents:
        status_icon = {
            "created": "🆕",
            "running": "🔄",
            "completed": "✅",
            "failed": "❌",
        }.get(agent.get("status", "created"), "❓")

        lines.append(
            f"{status_icon} [{agent.get('agent_id', '?')}] "
            f"类型: {agent.get('type', 'N/A')} | "
            f"状态: {agent.get('status', 'N/A')} | "
            f"任务: {agent.get('description', '')[:50]}"
        )

    return "\n".join(lines)


def get_agent_tools():
    return [agent_create, agent_run, agent_list]


AGENT_TOOLS = get_agent_tools()
