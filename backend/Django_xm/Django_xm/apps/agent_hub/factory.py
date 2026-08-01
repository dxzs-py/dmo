from __future__ import annotations

import logging
from typing import Any

from langgraph.graph.state import CompiledStateGraph

from Django_xm.apps.agent_hub.config import AgentConfig, AgentType
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
        """基于 builder 注册表构建 AgentType -> builder 实例映射。

        单一真相源：通过 @register_builder 装饰器自动注册到
        ``_builder_registry``，本方法仅消费注册表，不再硬编码 builder 类。

        共享实例策略：同一 builder 类对应多个 AgentType 时共享同一实例
        （例如 BASE/RAG/SAFE_RAG 共享 BaseAgentBuilder 实例），
        与原硬编码实现的语义一致。
        """
        if cls._builders is None:
            # 导入 builder 模块以触发 @register_builder 装饰器注册
            from Django_xm.apps.agent_hub.builders._registry import get_registered_builders

            # deep_builder 与 research.services.adapter 存在循环依赖，
            # 不能在 builders/__init__.py 模块级导入，在此处延迟导入触发注册。
            try:
                from Django_xm.apps.agent_hub.builders.deep_builder import DeepResearchBuilder  # noqa: F401
            except ImportError:
                logger.warning("[AgentFactory] deep_builder 导入失败（循环依赖），DEEP_RESEARCH 类型将不可用")

            registry = get_registered_builders()
            builders: dict = {}
            # 同一 builder 类的多个 AgentType 共享同一实例
            instance_cache: dict = {}
            for agent_type, builder_cls in registry.items():
                if builder_cls not in instance_cache:
                    instance_cache[builder_cls] = builder_cls()
                builders[agent_type] = instance_cache[builder_cls]

            cls._builders = builders
        return cls._builders

    @classmethod
    async def create(cls, config: AgentConfig) -> Any:
        config.validate()
        config.resolve_defaults()

        # 执行预检（默认不阻止创建，仅记录问题；fail_fast_on_preflight=True 时抛出 PreflightCheckError）
        # 注意：PreflightCheckError 必须冒泡（不被 except 捕获），其他异常忽略保持向后兼容
        from .exceptions import PreflightCheckError

        try:
            from .preflight import ExecutionPreflight

            preflight = ExecutionPreflight()
            result = await preflight.check(config)
            if not result.passed:
                config._preflight_issues = result.issues
                if getattr(config, "fail_fast_on_preflight", False):
                    # 快速失败模式：抛出携带 issues 的 PreflightCheckError，不调用 builder.build
                    raise PreflightCheckError(
                        f"预检未通过，已阻止 agent 创建: {result.issues}",
                        issues=result.issues,
                    )
                logger.warning(f"[AgentFactory] 预检未通过: {result.issues}")
            if result.warnings:
                for w in result.warnings:
                    logger.warning(f"[AgentFactory] 预检警告: {w}")
        except PreflightCheckError:
            # 快速失败模式：让 PreflightCheckError 冒泡到调用方
            raise
        except Exception as e:
            # 预检本身抛异常（如网络错误）：保持向后兼容，仅 debug 日志，不阻止创建
            # 即使 fail_fast_on_preflight=True，预检内部异常也不阻止（仅预检结果未通过才阻止）
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
            logger.exception("智能体创建失败")
            raise AgentCreationError(f"智能体创建失败: {e}") from e

        if isinstance(agent, CompiledStateGraph):
            return AgentWrapper(graph=agent, work_dir=getattr(config, "work_dir", None))

        if not hasattr(agent, "graph"):
            raise AgentCreationError(f"Builder 返回的对象既不是 CompiledStateGraph 也没有 .graph 属性: {type(agent)}")

        return agent
