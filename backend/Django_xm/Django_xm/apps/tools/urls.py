from django.urls import path

from . import views_mcp

app_name = "tools"

urlpatterns = [
    # MCP 只读端点（保留）
    path("mcp/tools/", views_mcp.McpToolsView.as_view(), name="mcp_tools"),
    path("mcp/status/", views_mcp.McpStatusView.as_view(), name="mcp_status"),
    path("mcp/call-log/", views_mcp.McpToolCallLogView.as_view(), name="mcp_call_log"),
    # MCP Server 资源（显式动作路由须先于 <str:name> 注册，避免被当作资源名吞掉）
    path("mcp/servers/discover/", views_mcp.McpServerDiscoverView.as_view(), name="mcp_servers_discover"),
    path("mcp/servers/", views_mcp.McpServerView.as_view(), name="mcp_servers"),
    path("mcp/servers/<str:name>/test/", views_mcp.McpServerTestView.as_view(), name="mcp_server_test"),
    path("mcp/servers/<str:name>/status/", views_mcp.McpServerStatusView.as_view(), name="mcp_server_status"),
    path("mcp/servers/<str:name>/", views_mcp.McpServerDetailView.as_view(), name="mcp_server_detail"),
    # 工具汇总与元数据（保留）
    path("list/", views_mcp.ToolListView.as_view(), name="tools_list"),
    path("meta/", views_mcp.ToolMetaView.as_view(), name="tools_meta"),
    # 自定义工具资源
    path("custom/", views_mcp.CustomToolView.as_view(), name="custom_tools_list"),
    path("custom/<str:name>/status/", views_mcp.CustomToolStatusView.as_view(), name="custom_tool_status"),
    path("custom/<str:name>/", views_mcp.CustomToolDetailView.as_view(), name="custom_tool_detail"),
    # Skill 资源（packages 子资源须先于 <str:name> 注册）
    path("skills/packages/", views_mcp.SkillPackageView.as_view(), name="skill_packages_list"),
    path(
        "skills/packages/<str:name>/status/",
        views_mcp.SkillPackageStatusView.as_view(),
        name="skill_package_status",
    ),
    path("skills/packages/<str:name>/", views_mcp.SkillPackageDetailView.as_view(), name="skill_package_detail"),
    path("skills/", views_mcp.SkillView.as_view(), name="skills_list"),
    path("skills/<str:name>/status/", views_mcp.SkillStatusView.as_view(), name="skill_status"),
    path("skills/<str:name>/", views_mcp.SkillDetailView.as_view(), name="skill_detail"),
]
