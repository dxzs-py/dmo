from django.apps import AppConfig


class AgentHubConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "Django_xm.apps.agent_hub"
    verbose_name = "Agent Hub"
    label = "agent_hub"

    def ready(self):
        # 注册子代理管理工具到 tools 扩展注册表（Task 15.1）
        # 这些工具原位于 tools/langchain/agent.py，但调用 agent_hub.create
        # 违反 tools→agent_hub 分层，迁入 agent_hub/tools/ 后通过注册表暴露
        from Django_xm.apps.agent_hub.tools import get_agent_tools
        from Django_xm.apps.tools.registry import register_extension_tools

        register_extension_tools(get_agent_tools())
