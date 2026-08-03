from django.urls import path

from . import views_mcp

app_name = "tools"

urlpatterns = [
    path("mcp/tools/", views_mcp.McpToolsView.as_view(), name="mcp_tools"),
    path("mcp/status/", views_mcp.McpStatusView.as_view(), name="mcp_status"),
    path("mcp/test/", views_mcp.McpServerTestView.as_view(), name="mcp_test"),
    path("mcp/call-log/", views_mcp.McpToolCallLogView.as_view(), name="mcp_call_log"),
    path("mcp/servers/", views_mcp.McpServerListView.as_view(), name="mcp_servers"),
    path("mcp/servers/add/", views_mcp.McpServerAddView.as_view(), name="mcp_servers_add"),
    path("mcp/servers/update/", views_mcp.McpServerUpdateView.as_view(), name="mcp_servers_update"),
    path("mcp/servers/delete/", views_mcp.McpServerDeleteView.as_view(), name="mcp_servers_delete"),
    path("mcp/servers/toggle/", views_mcp.McpServerToggleView.as_view(), name="mcp_servers_toggle"),
    path("mcp/servers/discover/", views_mcp.McpServerDiscoverView.as_view(), name="mcp_servers_discover"),
    path("list/", views_mcp.ToolListView.as_view(), name="tools_list"),
    path("meta/", views_mcp.ToolMetaView.as_view(), name="tools_meta"),
    path("upload/", views_mcp.ToolUploadView.as_view(), name="tools_upload"),
    path("custom/", views_mcp.CustomToolListView.as_view(), name="custom_tools_list"),
    path("custom/delete/", views_mcp.CustomToolDeleteView.as_view(), name="custom_tools_delete"),
    path("custom/toggle/", views_mcp.CustomToolToggleView.as_view(), name="custom_tools_toggle"),
    path("custom/update/", views_mcp.CustomToolUpdateView.as_view(), name="custom_tools_update"),
    path("skills/", views_mcp.SkillListView.as_view(), name="skills_list"),
    path("skills/create/", views_mcp.SkillCreateView.as_view(), name="skills_create"),
    path("skills/update/", views_mcp.SkillUpdateView.as_view(), name="skills_update"),
    path("skills/delete/", views_mcp.SkillDeleteView.as_view(), name="skills_delete"),
    path("skills/toggle/", views_mcp.SkillToggleView.as_view(), name="skills_toggle"),
    path("skills/packages/", views_mcp.SkillPackageListView.as_view(), name="skill_packages_list"),
    path("skills/upload/", views_mcp.SkillPackageUploadView.as_view(), name="skill_packages_upload"),
    path("skills/packages/delete/", views_mcp.SkillPackageDeleteView.as_view(), name="skill_packages_delete"),
    path("skills/packages/toggle/", views_mcp.SkillPackageToggleView.as_view(), name="skill_packages_toggle"),
    path("skills/packages/detail/", views_mcp.SkillPackageDetailView.as_view(), name="skill_packages_detail"),
]
