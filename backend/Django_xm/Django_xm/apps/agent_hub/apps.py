from django.apps import AppConfig


class AgentHubConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "Django_xm.apps.agent_hub"
    verbose_name = "Agent Hub"
    label = "agent_hub"

    def ready(self):
        # 注册统一 spawn 子代理工具到 tools 扩展注册表（SubAgentRuntime 唯一入口）。
        # 子代理管理收敛到 ai_engine.subagent_runtime，spawn 工具经注册表暴露给 tools。
        from Django_xm.apps.agent_hub.subagent_tools import get_agent_tools
        from Django_xm.apps.tools.registry import register_extension_tools

        register_extension_tools(get_agent_tools())

        # 依赖倒置注册：ai_engine.subagent_runtime 禁止直接 import agent_hub
        # （反向依赖），子代理 graph 工厂与 AgentConfig 工厂在此统一注册。
        # ready 阶段所有 app 模块均已加载，import agent_hub 自身 create 安全。
        from Django_xm.apps.agent_hub import create as agent_hub_create
        from Django_xm.apps.agent_hub.config import AgentConfig, AgentType
        from Django_xm.apps.ai_engine.subagent_runtime import (
            set_default_config_factory,
            set_default_graph_factory,
        )

        def _build_base_agent_config(**kwargs):
            return AgentConfig(agent_type=AgentType.BASE, **kwargs)

        set_default_graph_factory(agent_hub_create)
        set_default_config_factory(_build_base_agent_config)
