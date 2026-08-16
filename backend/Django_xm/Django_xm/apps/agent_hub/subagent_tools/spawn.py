"""统一 ``spawn_sub_agent`` 子代理创建工具（SubAgentRuntime 唯一入口）。

替代 DeepAgents 阻塞 ``task`` 工具。``spawn_sub_agent`` 内部调用 ``SubAgentRuntime.spawn``：

- 异步非阻塞：立即返回 ``SubAgentInstance``（thread_id + status），父 Agent 不等待结果。
- 工具集：继承主 agent 全部工具 + 注册表专用工具（按 tool_name 去重），
  并强制剥离 ``spawn_sub_agent`` 自身（防递归）。
- 子 Agent 能力对等：独立 thread_id + 独立 checkpoint；interrupt 归属子 Agent 自身。
- 嵌套超限 / 参数非法等异常作为 Tool 错误结果返回，不终止父 graph。

依赖方向（高层 → 低层）：
- ``agent_hub.subagent_tools`` → ``ai_engine.subagent_runtime``（子代理唯一入口 + 注册表）
- ``agent_hub.subagent_tools`` → ``tools``（获取/解析工具集）
- ``agent_hub.subagent_tools`` → ``tools.langchain.agent_context``（继承父上下文）
"""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any

from langchain_core.tools import BaseTool, InjectedToolCallId
from pydantic import BaseModel, Field

from Django_xm.apps.tools.errors import TOOL_VERSION

logger = logging.getLogger(__name__)

# 子代理创建工具自身名称（剥离时使用，防递归派生）。
SPAWN_TOOL_NAME = "spawn_sub_agent"

# 子代理工具集中需强制剥离的父 Agent 工具（子代理不再派生/等待子代理）。
_SUBAGENT_STRIP_TOOL_NAMES = frozenset({"spawn_sub_agent", "wait_for_subagent"})


class SpawnInput(BaseModel):
    agent_name: str = Field(description="子代理名称/任务名，简短标识（如 web-researcher / doc-analyst / code-reviewer）")
    task: str = Field(description="子代理任务描述，详细说明需要独立完成的工作")
    tools: list[str] = Field(
        default_factory=list,
        description="可选，显式追加的工具名列表；缺省时继承主 agent 全部工具 + 注册表专用工具",
    )


class SpawnSubAgentTool(BaseTool):
    name: str = SPAWN_TOOL_NAME
    version: str = TOOL_VERSION
    metadata: dict = Field(
        default_factory=lambda: {"tier": "extended", "visibility": "selectable", "category": "agent"}
    )
    description: str = (
        "派生一个子代理独立执行指定任务。子代理异步运行，本工具立即返回子代理 thread_id，"
        "父代理不会等待其完成。适用：并行处理子任务、把独立工作委派给专门代理。"
        "不适用：需要立即拿到结果的单步操作（应由父代理直接完成）。"
        "参数：agent_name-子代理名称（必填，简短标识，可用 web-researcher/doc-analyst/general-purpose 命中注册表角色），"
        "task-子代理任务描述（必填，详细说明），"
        "tools-可选追加工具名列表（缺省继承主 agent 全部工具 + 注册表专用工具）。"
        "边界：子代理有最大嵌套深度限制（最多 3 层）；创建失败返回错误说明，父代理可据此调整策略。"
    )
    args_schema: type[BaseModel] = SpawnInput

    # 父 graph 的 configurable（由 ainvoke/invoke 从 RunnableConfig 捕获），
    # 含 thread_id / _on_tool_event / _on_subagent_content / chat_session_id 等。
    parent_configurable: dict = Field(default_factory=dict, description="父 graph configurable（运行时注入）")

    # ── config 注入 ─────────────────────────────────────────────────────────────

    def invoke(self, input: str | dict | BaseModel, config=None, **kwargs) -> Any:
        self._capture_configurable(config)
        return super().invoke(input, config=config, **kwargs)

    async def ainvoke(self, input: str | dict | BaseModel, config=None, **kwargs) -> Any:
        self._capture_configurable(config)
        return await super().ainvoke(input, config=config, **kwargs)

    def _capture_configurable(self, config) -> None:
        if isinstance(config, dict):
            configurable = config.get("configurable", {})
            if isinstance(configurable, dict):
                self.parent_configurable = configurable

    # ── 执行逻辑 ────────────────────────────────────────────────────────────────

    def _run(self, agent_name: str, task: str, tools: list[str] | None = None) -> str:
        return "spawn_sub_agent 需要异步执行，请通过 ainvoke 调用"

    async def _arun(
        self,
        agent_name: str,
        task: str,
        tools: list[str] | None = None,
        tool_call_id: Annotated[str, InjectedToolCallId] = None,
    ) -> str:
        """派生子代理（异步非阻塞，立即返回实例元数据）。

        异常透传：SubAgentError 等作为字符串错误返回（父 graph 不终止）。
        """
        try:
            from Django_xm.apps.agent_hub import AgentConfig, AgentType
            from Django_xm.apps.ai_engine.subagent_runtime import get_subagent_runtime
            from Django_xm.apps.ai_engine.subagent_runtime.registry import (
                get_subagent_spec,
                resolve_dedicated_tools,
                resolve_system_prompt,
            )
            from Django_xm.apps.tools.langchain.agent_context import get_parent_tool_context

            parent_ctx = get_parent_tool_context()
            main_tool_names = parent_ctx.get("tool_names", []) or []
            parent_config = parent_ctx.get("config", {}) or {}
            configurable = dict(self.parent_configurable or {})

            # 自身工具调用 ID：LangChain 通过 InjectedToolCallId 在工具执行时注入
            # （ToolNode 场景）；直接 ainvoke 等非工具调用上下文时为 None，防御为空串。
            spawn_tool_call_id = tool_call_id or ""

            # 深度思考继承：读取主 agent 实际生效的深度思考开关
            # （chat_service 在 set_parent_tool_context 时写入 config.enable_deep_thinking，
            # 已含模型能力判定），子代理 AgentConfig 对齐继承。
            enable_deep_thinking = bool(parent_config.get("enable_deep_thinking", False))
            # 透传到子代理 configurable：SubAgentContentMiddleware 据此在关闭深度思考
            # 时丢弃模型仍输出的 reasoning（部分模型无法被 thinking=disabled 关闭）。
            configurable["enable_deep_thinking"] = enable_deep_thinking

            # 父 thread_id：优先 RunnableConfig 的 thread_id（主 agent = session_id），
            # 回退父上下文 session_id。
            parent_thread_id = configurable.get("thread_id") or parent_config.get("session_id") or ""
            user_id = parent_config.get("user_id")
            session_id = parent_config.get("session_id") or parent_thread_id
            model_name = parent_config.get("model_name")
            store = parent_config.get("store")

            # 工具集：继承主 agent 全部工具 + 注册表专用工具 + 显式 tools（去重），
            # 强制剥离 spawn_sub_agent 自身防递归。
            main_tools = await self._resolve_main_tools(main_tool_names, user_id)

            spec = get_subagent_spec(agent_name)
            dedicated_tools = resolve_dedicated_tools(spec, main_tools) if spec else []
            extra_tools = await self._resolve_extra_tools(tools, user_id) if tools else []

            merged = self._merge_tools(main_tools, dedicated_tools, extra_tools)
            merged = [t for t in merged if getattr(t, "name", "") not in _SUBAGENT_STRIP_TOOL_NAMES]

            # system_prompt：注册表角色提示（命中注册表时）；否则空，由 task 作为任务输入。
            system_prompt = resolve_system_prompt(agent_name) if spec else ""

            agent_config = AgentConfig(
                agent_type=AgentType.BASE,
                name=agent_name,
                system_prompt=system_prompt,
                tools=merged,
                user_id=user_id,
                session_id=session_id,
                model_name=model_name,
                store=store,
                # 深度思考继承主 agent 实际生效开关（含 provider 禁用注入，见 model_resolver）
                enable_deep_thinking=enable_deep_thinking,
            )

            runtime = get_subagent_runtime()
            instance = await runtime.spawn(
                parent_thread_id=parent_thread_id,
                agent_config=agent_config,
                task=task,
                configurable=configurable,
                spawn_tool_call_id=spawn_tool_call_id,
            )

            return (
                "子代理已启动\n"
                f"- 名称: {agent_name}\n"
                f"- thread_id: {instance.thread_id}\n"
                f"- 状态: {instance.status}\n\n"
                "子代理独立异步运行，父代理无需等待；其思考/工具/审批将展示在子代理卡片中。"
            )
        except Exception as e:
            # 嵌套超限 / 参数非法等：作为 Tool 错误结果返回，不终止父 graph
            import traceback

            logger.warning(
                f"spawn_sub_agent 失败: agent_name={agent_name}, err={e}\n{traceback.format_exc()}"
            )
            return json.dumps(
                {"error": f"子代理创建失败: {str(e) or repr(e)}"},
                ensure_ascii=False,
            )

    async def _resolve_main_tools(self, main_tool_names: list[str], user_id: int | None) -> list:
        """继承主 agent 全部工具（按工具名解析）。

        main_tool_names 来自 get_parent_tool_context（主 agent 执行前由
        set_parent_tool_context 写入）。为空（如深度研究未设置父上下文）时回退基础工具集。
        """
        if not main_tool_names:
            from Django_xm.apps.tools import get_all_basic_tools

            return get_all_basic_tools()
        try:
            from Django_xm.apps.tools import get_tools_for_request_async

            return await get_tools_for_request_async(
                use_tools=True,
                selected_tools=list(main_tool_names),
                user_id=user_id,
            )
        except Exception as e:
            logger.warning(f"继承主 agent 工具失败，回退基础工具集: {e}")
            from Django_xm.apps.tools import get_all_basic_tools

            return get_all_basic_tools()

    async def _resolve_extra_tools(self, tools: list[str], user_id: int | None) -> list:
        """解析显式追加工具名列表。"""
        try:
            from Django_xm.apps.tools import get_tools_for_request_async

            return await get_tools_for_request_async(
                use_tools=True,
                selected_tools=list(tools),
                user_id=user_id,
            )
        except Exception as e:
            logger.warning(f"解析显式追加工具失败: {e}")
            return []

    @staticmethod
    def _merge_tools(*tool_lists) -> list:
        """合并多个工具列表，按工具名去重（保留首次出现的版本）。"""
        seen: set[str] = set()
        merged: list = []
        for tools in tool_lists:
            if not tools:
                continue
            for t in tools:
                name = getattr(t, "name", "") or ""
                if name and name in seen:
                    continue
                seen.add(name)
                merged.append(t)
        return merged


spawn_sub_agent = SpawnSubAgentTool()
