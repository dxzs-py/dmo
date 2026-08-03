"""子代理管理工具集

提供子代理的创建、执行、列举、清理功能。

归属说明（Task 15.1）：
    本模块原位于 ``apps/tools/langchain/agent.py``，但 ``AgentRunTool``
    需要调用 ``agent_hub.create`` 创建子代理，违反 ``tools → agent_hub``
    的分层约束。故整体迁入 ``agent_hub/tools/``。

依赖方向：
    - ``agent_hub.tools`` → ``tools.langchain.agent_context``（高层 → 低层）
    - ``agent_hub.tools`` → ``tools``（获取基础工具集）
    - ``agent_hub.tools`` → ``agent_hub``（同 app，创建子代理）
"""

import json
import logging
import os
import threading
import time
from typing import Any

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from Django_xm.apps.tools.base import AsyncToolMixin
from Django_xm.apps.tools.errors import TOOL_VERSION
from Django_xm.async_utils import run_async

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


AGENT_STORE_DIR = os.path.join(_get_data_dir(), "agents")

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
    safe_id = agent_id.replace("/", "_").replace("\\", "_")
    return os.path.join(AGENT_STORE_DIR, f"{safe_id}.json")


def _save_agent_meta(agent_id: str, meta: dict[str, Any]):
    path = _get_agent_path(agent_id)
    _ensure_agent_dir()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
    except OSError:
        logger.exception("保存代理元数据失败")


def _load_agent_meta(agent_id: str) -> dict[str, Any] | None:
    path = _get_agent_path(agent_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        logger.exception("加载代理元数据失败")
        return None


def _list_agents() -> list[dict[str, Any]]:
    _ensure_agent_dir()
    agents = []
    for filename in os.listdir(AGENT_STORE_DIR):
        if filename.endswith(".json"):
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


class AgentCreateInput(BaseModel):
    description: str = Field(description="任务描述，详细说明子代理需要完成的工作")
    agent_type: str = Field(
        default="general-purpose",
        description="代理类型：general-purpose/explore/plan/verification/code-review/research",
    )
    parent_session_id: str = Field(default="", description="父会话ID，用于关联")


class AgentRunInput(BaseModel):
    agent_id: str = Field(description="子代理ID")
    input_text: str = Field(default="", description="可选的额外输入文本")
    timeout: int = Field(default=60, description="超时时间（秒），默认60秒")


class AgentListInput(BaseModel):
    pass


class AgentCleanupInput(BaseModel):
    agent_id: str = Field(default="", description="要清理的子代理ID，为空则清理所有已完成/失败的代理")


class AgentCreateTool(BaseTool):
    name: str = "agent_create"
    version: str = TOOL_VERSION
    metadata: dict = Field(
        default_factory=lambda: {"tier": "extended", "visibility": "selectable", "category": "agent"}
    )
    description: str = (
        "创建一个子代理任务，子代理可独立执行特定类型的工作（如探索、规划、验证、研究等）。"
        "适用场景：需要并行处理子任务、分配特定类型的工作给专门代理、拆分复杂任务。"
        "不适用：简单的单步操作、不需要子代理的常规对话。"
        "参数：description-任务描述（必填，详细说明子代理需要完成的工作），"
        "agent_type-代理类型（general-purpose/explore/plan/verification/code-review/research，默认'general-purpose'），"
        "parent_session_id-父会话ID（可选，用于关联）。"
        "边界：创建后需使用 agent_run 执行；任务描述会经过安全检测。"
    )
    args_schema: type[BaseModel] = AgentCreateInput

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # 活跃子代理状态追踪
        self._active_subagents: dict[str, dict[str, Any]] = {}

    def _run(self, description: str, agent_type: str = "general-purpose", parent_session_id: str = "") -> str:
        from Django_xm.apps.tools.langchain.agent_context import MAX_AGENT_DEPTH, get_agent_depth, is_max_depth_reached

        # 检查嵌套深度限制
        if is_max_depth_reached():
            current_depth = get_agent_depth()
            return json.dumps(
                {
                    "error": f"已达到最大子代理嵌套深度({MAX_AGENT_DEPTH})，当前深度={current_depth}。"
                    f"请直接在当前代理中完成任务，不要创建更多子代理。",
                },
                ensure_ascii=False,
            )

        agent_id = f"agent_{int(time.time() * 1000)}"
        normalized_type = _normalize_agent_type(agent_type)

        from Django_xm.apps.context_manager.services.circuit_breaker import ContextCircuitBreaker

        _cb = ContextCircuitBreaker()
        if _cb.detect_injection(description):
            return json.dumps({"error": "检测到不安全的任务描述，请修改后重试"}, ensure_ascii=False)

        from Django_xm.apps.tools.langchain.agent_context import get_parent_tool_context, has_parent_tool_context

        tool_context = get_parent_tool_context() if has_parent_tool_context() else {}

        meta = {
            "agent_id": agent_id,
            "type": normalized_type,
            "description": description,
            "status": "created",
            "parent_session_id": parent_session_id,
            "created_at": time.time(),
            "result": None,
            "parent_tool_names": tool_context.get("tool_names", []),
            "parent_mcp_servers": tool_context.get("mcp_servers", []),
            "parent_config": tool_context.get("config", {}),
        }

        _save_agent_meta(agent_id, meta)

        # 记录到活跃子代理追踪
        self._active_subagents[agent_id] = {
            "status": "created",
            "created_at": meta["created_at"],
            "type": normalized_type,
        }

        type_desc = AGENT_TYPES.get(normalized_type, normalized_type)
        return (
            f"子代理已创建\n"
            f"- 代理ID: {agent_id}\n"
            f"- 类型: {type_desc}\n"
            f"- 任务: {description[:100]}\n\n"
            f"使用 agent_run 工具执行此代理"
        )

    async def _arun(self, description: str, agent_type: str = "general-purpose", parent_session_id: str = "") -> str:
        return self._run(description=description, agent_type=agent_type, parent_session_id=parent_session_id)


class AgentRunTool(BaseTool):
    name: str = "agent_run"
    version: str = TOOL_VERSION
    metadata: dict = Field(
        default_factory=lambda: {"tier": "extended", "visibility": "selectable", "category": "agent"}
    )
    description: str = (
        "执行一个已创建的子代理任务，子代理将独立完成分配的工作并返回结果。"
        "适用场景：已通过 agent_create 创建子代理后，需要执行该代理任务获取结果。"
        "不适用：创建子代理（应使用 agent_create）、查看代理列表（应使用 agent_list）。"
        "参数：agent_id-子代理ID（必填，由 agent_create 返回），"
        "input_text-可选的额外输入文本，timeout-超时时间（秒，默认60秒）。"
        "边界：代理正在运行时不可重复执行；执行失败会记录错误状态；超时自动终止。"
    )
    args_schema: type[BaseModel] = AgentRunInput

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # 活跃子代理状态追踪
        self._active_subagents: dict[str, dict[str, Any]] = {}

    def _run(self, agent_id: str, input_text: str = "", timeout: int = 60) -> str:
        meta = _load_agent_meta(agent_id)
        if not meta:
            return f"错误: 未找到代理 {agent_id}"

        if meta.get("status") == "running":
            return f"代理 {agent_id} 正在运行中"

        meta["status"] = "running"
        meta["started_at"] = time.time()
        _save_agent_meta(agent_id, meta)

        # 更新活跃子代理追踪
        self._active_subagents[agent_id] = {
            "status": "running",
            "started_at": meta["started_at"],
            "timeout": timeout,
        }

        # 使用线程+事件实现超时控制
        result_container: dict[str, Any] = {"result": None, "error": None}
        execution_done = threading.Event()

        def _execute_agent():
            try:
                # 同 app 内导入 agent_hub.create（Task 15.1：原为 tools→agent_hub 违规，迁入后合规）
                from Django_xm.apps.agent_hub import AgentConfig, AgentType
                from Django_xm.apps.agent_hub import create as agent_hub_create
                from Django_xm.apps.tools import get_all_basic_tools
                from Django_xm.apps.tools.langchain.agent_context import (
                    MAX_AGENT_DEPTH,
                    decrement_agent_depth,
                    increment_agent_depth,
                )

                # 递增深度
                current_depth = increment_agent_depth()
                logger.info(f"子代理 {agent_id} 启动，当前嵌套深度={current_depth}/{MAX_AGENT_DEPTH}")

                try:
                    agent_type = meta.get("type", "general-purpose")
                    description = meta.get("description", "")

                    system_prompt = _get_system_prompt_for_type(agent_type, description)
                    task_input = input_text or description

                    parent_tool_names = meta.get("parent_tool_names", [])
                    parent_mcp_servers = meta.get("parent_mcp_servers", [])
                    parent_config = meta.get("parent_config", {})

                    if parent_tool_names:
                        try:
                            from Django_xm.apps.tools import get_tools_for_request_async

                            use_web_search = parent_config.get("use_web_search", False)
                            use_mcp = bool(parent_mcp_servers)

                            sub_tools = run_async(
                                get_tools_for_request_async(
                                    use_tools=True,
                                    use_web_search=use_web_search,
                                    use_mcp=use_mcp,
                                    selected_mcp_servers=parent_mcp_servers or None,
                                    selected_tools=parent_tool_names or None,
                                    user_id=parent_config.get("user_id"),
                                )
                            )

                            # 始终剥离子代理创建工具，防止无限嵌套
                            sub_agent_tool_names = {"agent_create", "agent_run", "agent_list", "agent_cleanup"}
                            sub_tools = [t for t in sub_tools if t.name not in sub_agent_tool_names]

                            # 如果已达最大深度-1，也剥离 agent 工具（双重保险）
                            if current_depth >= MAX_AGENT_DEPTH - 1:
                                logger.info(
                                    f"子代理 {agent_id} 接近最大深度({current_depth}/{MAX_AGENT_DEPTH})，已剥离 agent 工具"
                                )

                            logger.info(f"子代理 {agent_id} 继承父代理工具: {[t.name for t in sub_tools]}")
                        except Exception as e:
                            logger.warning(f"子代理加载继承工具失败，回退到基础工具: {e}")
                            sub_tools = get_all_basic_tools()
                    else:
                        sub_tools = get_all_basic_tools()

                    agent = run_async(
                        agent_hub_create(
                            AgentConfig(
                                agent_type=AgentType.BASE,
                                tools=sub_tools,
                                system_prompt=system_prompt,
                                user_id=parent_config.get("user_id"),
                                session_id=parent_config.get("session_id"),
                                model_name=parent_config.get("model_name"),
                                store=parent_config.get("store"),
                            )
                        )
                    )

                    result = agent.invoke(input_text=task_input)
                    result_container["result"] = result
                finally:
                    # 无论成功失败，都要递减深度
                    decrement_agent_depth()
                    logger.info(f"子代理 {agent_id} 执行结束，深度已恢复")
            except Exception as e:
                result_container["error"] = str(e)
            finally:
                execution_done.set()

        worker = threading.Thread(target=_execute_agent, daemon=True)
        worker.start()

        # 等待执行完成或超时
        finished = execution_done.wait(timeout=timeout)

        if not finished:
            # 超时：更新状态为超时终止
            meta["status"] = "timeout"
            meta["error"] = f"子代理执行超时 ({timeout}秒)，已自动终止"
            meta["timeout_at"] = time.time()
            _save_agent_meta(agent_id, meta)

            # 更新活跃追踪状态
            if agent_id in self._active_subagents:
                self._active_subagents[agent_id]["status"] = "timeout"

            logger.warning(f"子代理 {agent_id} 执行超时 ({timeout}秒)，已自动终止")
            return f"子代理执行超时 ({timeout}秒)，已自动终止。请尝试简化任务或增加超时时间。"

        # 正常完成
        if result_container["error"]:
            meta["status"] = "failed"
            meta["error"] = result_container["error"]
            _save_agent_meta(agent_id, meta)

            if agent_id in self._active_subagents:
                self._active_subagents[agent_id]["status"] = "failed"

            return f"子代理执行失败: {result_container['error']}"

        result = result_container["result"]
        meta["status"] = "completed"
        meta["result"] = result[:5000] if result else ""
        meta["completed_at"] = time.time()
        _save_agent_meta(agent_id, meta)

        # 更新活跃追踪状态
        if agent_id in self._active_subagents:
            self._active_subagents[agent_id]["status"] = "completed"

        return f"子代理执行完成\n\n结果:\n{result[:3000]}"

    async def _arun(self, agent_id: str, input_text: str = "", timeout: int = 60) -> str:
        return self._run(agent_id=agent_id, input_text=input_text, timeout=timeout)


class AgentListTool(BaseTool):
    name: str = "agent_list"
    version: str = TOOL_VERSION
    metadata: dict = Field(
        default_factory=lambda: {"tier": "extended", "visibility": "selectable", "category": "agent"}
    )
    description: str = (
        "列出所有已创建的子代理任务，显示代理ID、类型、状态和任务描述。"
        "适用场景：需要查看当前有哪些子代理、了解各代理的执行状态、选择代理执行。"
        "不适用：创建子代理（应使用 agent_create）、执行代理（应使用 agent_run）。"
        "参数：无。"
        "边界：仅显示已创建的代理，不包含历史已清理的代理。"
    )
    args_schema: type[BaseModel] = AgentListInput

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._active_subagents: dict[str, dict[str, Any]] = {}

    def _run(self) -> str:
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
                "timeout": "⏱️",
            }.get(agent.get("status", "created"), "❓")

            lines.append(
                f"{status_icon} [{agent.get('agent_id', '?')}] "
                f"类型: {agent.get('type', 'N/A')} | "
                f"状态: {agent.get('status', 'N/A')} | "
                f"任务: {agent.get('description', '')[:50]}"
            )

        return "\n".join(lines)

    async def _arun(self) -> str:
        return self._run()


class AgentCleanupTool(AsyncToolMixin, BaseTool):
    """代理清理工具

    审批由 ApprovalMiddleware 统一处理：
    - 批量清理（未指定 agent_id）触发审批（AgentCleanupApprovalPolicy）
    - 单个清理无需审批
    工具层不参与审批判断。
    """

    name: str = "agent_cleanup"
    version: str = TOOL_VERSION
    metadata: dict = Field(
        default_factory=lambda: {"tier": "extended", "visibility": "selectable", "category": "agent"}
    )
    description: str = (
        "清理已完成或失败的子代理资源，释放内存和磁盘空间。"
        "适用场景：子代理执行完毕后清理资源、系统资源不足时回收。"
        "不适用：清理正在运行的代理（应等待完成或超时）。"
        "参数：agent_id-要清理的子代理ID（为空则清理所有已完成/失败/超时的代理）。"
        "边界：正在运行的代理不会被清理。"
    )
    args_schema: type[BaseModel] = AgentCleanupInput

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._active_subagents: dict[str, dict[str, Any]] = {}

    def _run(self, agent_id: str = "") -> str:
        """清理代理资源

        审批由 ApprovalMiddleware 统一处理，工具层不参与审批判断。
        批量清理到达此方法时已通过审批。
        """
        if not agent_id:
            return self._cleanup_all()
        return self._cleanup_single(agent_id)

    def _cleanup_single(self, agent_id: str) -> str:
        cleaned_count = 0
        meta = _load_agent_meta(agent_id)
        if not meta:
            return f"未找到代理 {agent_id}"
        if meta.get("status") == "running":
            return f"代理 {agent_id} 正在运行中，无法清理。请等待完成或超时。"
        path = _get_agent_path(agent_id)
        try:
            os.remove(path)
            cleaned_count = 1
        except OSError as e:
            logger.exception(f"清理代理 {agent_id} 文件失败")
            return f"清理代理 {agent_id} 失败: {e}"
        return f"已清理 {cleaned_count} 个子代理资源"

    def _cleanup_all(self) -> str:
        cleaned_count = 0
        agents = _list_agents()
        for agent in agents:
            aid = agent.get("agent_id", "")
            status = agent.get("status", "")
            if status in ("completed", "failed", "timeout"):
                path = _get_agent_path(aid)
                try:
                    os.remove(path)
                    cleaned_count += 1
                except OSError:
                    logger.exception(f"清理代理 {aid} 文件失败")
        return f"已清理 {cleaned_count} 个子代理资源"


agent_create = AgentCreateTool()
agent_run = AgentRunTool()
agent_list = AgentListTool()
agent_cleanup = AgentCleanupTool()


def get_agent_tools():
    return [agent_create, agent_run, agent_list, agent_cleanup]


AGENT_TOOLS = get_agent_tools()
