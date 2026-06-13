from __future__ import annotations

import logging
from typing import Any

from langgraph.graph.state import CompiledStateGraph

from Django_xm.apps.agent_hub.config import AgentType, AgentConfig
from Django_xm.apps.agent_hub.exceptions import AgentCreationError, FrameworkNotAvailableError

logger = logging.getLogger(__name__)


class AgentWrapper:
    __slots__ = ("graph", "work_dir")

    def __init__(self, graph: CompiledStateGraph, work_dir: str | None = None):
        self.graph = graph
        self.work_dir = work_dir

    def __getattr__(self, name: str) -> Any:
        return getattr(self.graph, name)


class AgentFactory:
    _builders = None

    @classmethod
    def _get_builders(cls):
        if cls._builders is None:
            from Django_xm.apps.agent_hub.builders.base_builder import BaseAgentBuilder
            from Django_xm.apps.agent_hub.builders.deep_builder import DeepAgentBuilder
            from Django_xm.apps.agent_hub.builders.custom_builder import CustomWorkflowBuilder
            from Django_xm.apps.agent_hub.builders.subagent_builder import SubAgentBuilder

            base = BaseAgentBuilder()
            deep = DeepAgentBuilder()
            custom = CustomWorkflowBuilder()
            subagent = SubAgentBuilder()

            cls._builders = {
                AgentType.BASE: base,
                AgentType.RAG: base,
                AgentType.SAFE_RAG: base,
                AgentType.DEEP_RESEARCH: deep,
                AgentType.DEEP_RESEARCH_CUSTOM: custom,
                AgentType.WEB_RESEARCHER: subagent,
                AgentType.DOC_ANALYST: subagent,
                AgentType.REPORT_WRITER: subagent,
            }
        return cls._builders

    @classmethod
    async def create(cls, config: AgentConfig) -> Any:
        config.validate()
        config.resolve_defaults()

        # 执行预检（不阻止创建，仅记录问题）
        try:
            from .preflight import ExecutionPreflight
            preflight = ExecutionPreflight()
            result = await preflight.check(config)
            if not result.passed:
                logger.warning(f"[AgentFactory] 预检未通过: {result.issues}")
                config._preflight_issues = result.issues
            if result.warnings:
                for w in result.warnings:
                    logger.warning(f"[AgentFactory] 预检警告: {w}")
        except Exception as e:
            logger.debug(f"[AgentFactory] 预检异常（忽略）: {e}")

        builders = cls._get_builders()
        builder = builders.get(config.agent_type)

        if builder is None:
            raise AgentCreationError(f"不支持的智能体类型: {config.agent_type}")

        try:
            agent = await builder.build(config)
        except FrameworkNotAvailableError:
            if config.agent_type == AgentType.DEEP_RESEARCH:
                logger.warning("DeepAgent 框架不可用，降级到 CustomWorkflow")
                config.agent_type = AgentType.DEEP_RESEARCH_CUSTOM
                config.resolve_defaults()
                custom_builder = builders[AgentType.DEEP_RESEARCH_CUSTOM]
                agent = await custom_builder.build(config)
            else:
                raise
        except Exception as e:
            logger.error(f"智能体创建失败: {e}")
            raise AgentCreationError(f"智能体创建失败: {e}") from e

        if isinstance(agent, CompiledStateGraph):
            return AgentWrapper(graph=agent, work_dir=getattr(config, 'work_dir', None))

        if not hasattr(agent, "graph"):
            raise AgentCreationError(
                f"Builder 返回的对象既不是 CompiledStateGraph 也没有 .graph 属性: {type(agent)}"
            )

        return agent
