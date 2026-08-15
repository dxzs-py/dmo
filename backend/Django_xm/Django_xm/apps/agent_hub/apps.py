from django.apps import AppConfig


class AgentHubConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "Django_xm.apps.agent_hub"
    verbose_name = "Agent Hub"
    label = "agent_hub"

    def ready(self):
        # 注册统一 spawn 子代理工具到 tools 扩展注册表（SubAgentRuntime 唯一入口）。
        # 子代理管理收敛到 ai_engine.subagent_runtime，spawn 工具经注册表暴露给 tools。
        from Django_xm.apps.agent_hub.tools import get_agent_tools
        from Django_xm.apps.tools.registry import register_extension_tools

        register_extension_tools(get_agent_tools())
