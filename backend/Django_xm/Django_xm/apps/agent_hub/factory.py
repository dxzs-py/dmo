from __future__ import annotations

import logging
from typing import Any

from langgraph.graph.state import CompiledStateGraph

from Django_xm.apps.agent_hub.config import AgentConfig
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
                import Django_xm.apps.agent_hub.builders.deep_builder  # noqa: F401 — 触发 @register_builder 装饰器注册
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

        # checkpointer 统一异步注入（D1/D2）：
        # resolve_defaults 不再注入同步 get_checkpointer()，此处注入异步 checkpointer，
        # 确保 agent_hub 创建的 agent（主/子/深度研究）统一用异步实现，
        # 避免子代理用同步 checkpointer 跑 astream 触发 NotImplementedError。
        if config.checkpointer is None:
            from Django_xm.apps.ai_engine.services.checkpointer_factory import get_async_checkpointer

            config.checkpointer = await get_async_checkpointer()
        # 防御断言（D1）：注入后仍为 None（如 get_async_checkpointer 降级返回 None），
        # 立即抛明确异常，禁止带着 None checkpointer 进入 astream。
        if config.checkpointer is None:
            raise AgentCreationError("checkpointer 注入失败，禁止带着 None checkpointer 进入 astream")

        # 执行预检（默认不阻止创建，仅记录问题；fail_fast_on_preflight=True 时抛出 PreflightCheckError）
        # 注意：PreflightCheckError 必须冒泡（不被 except 捕获），其他异常忽略保持向后兼容
        from .exceptions import PreflightCheckError

        async def _check_preflight(cfg):
            """执行预检；失败且快速失败模式时抛 PreflightCheckError（抽象 raise 避免 TRY301）。"""
            from .preflight import ExecutionPreflight

            preflight = ExecutionPreflight()
            result = await preflight.check(cfg)
            if not result.passed:
                cfg._preflight_issues = result.issues
                if getattr(cfg, "fail_fast_on_preflight", False):
                    # 快速失败模式：抛出携带 issues 的 PreflightCheckError，不调用 builder.build
                    raise PreflightCheckError(
                        f"预检未通过，已阻止 agent 创建: {result.issues}",
                        issues=result.issues,
                    )
                logger.warning(f"[AgentFactory] 预检未通过: {result.issues}")
            if result.warnings:
                for w in result.warnings:
                    logger.warning(f"[AgentFactory] 预检警告: {w}")

        try:
            await _check_preflight(config)
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
            # deepagents 已作为唯一深度研究实现（DEEP_RESEARCH_CUSTOM 已删除），
            # 不再提供自定义降级路径；框架不可用则直接抛出
            raise
        except Exception as e:
            logger.exception("智能体创建失败")
            raise AgentCreationError(f"智能体创建失败: {e}") from e

        if isinstance(agent, CompiledStateGraph):
            return AgentWrapper(graph=agent, work_dir=getattr(config, "work_dir", None))

        if not hasattr(agent, "graph"):
            raise AgentCreationError(f"Builder 返回的对象既不是 CompiledStateGraph 也没有 .graph 属性: {type(agent)}")

        return agent
