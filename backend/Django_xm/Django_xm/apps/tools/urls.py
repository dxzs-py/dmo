from django.urls import path
from . import views_mcp

app_name = 'tools'

urlpatterns = [
    path('mcp/tools/', views_mcp.McpToolsView.as_view(), name='mcp-tools'),
    path('mcp/status/', views_mcp.McpStatusView.as_view(), name='mcp-status'),
    path('mcp/test/', views_mcp.McpServerTestView.as_view(), name='mcp-test'),
    path('mcp/call-log/', views_mcp.McpToolCallLogView.as_view(), name='mcp-call-log'),
    path('mcp/servers/', views_mcp.McpServerListView.as_view(), name='mcp-servers'),
    path('mcp/servers/add/', views_mcp.McpServerAddView.as_view(), name='mcp-servers-add'),
    path('mcp/servers/update/', views_mcp.McpServerUpdateView.as_view(), name='mcp-servers-update'),
    path('mcp/servers/delete/', views_mcp.McpServerDeleteView.as_view(), name='mcp-servers-delete'),
    path('mcp/servers/toggle/', views_mcp.McpServerToggleView.as_view(), name='mcp-servers-toggle'),
    path('mcp/servers/discover/', views_mcp.McpServerDiscoverView.as_view(), name='mcp-servers-discover'),
    path('list/', views_mcp.ToolListView.as_view(), name='tools-list'),
    path('meta/', views_mcp.ToolMetaView.as_view(), name='tools-meta'),
    path('upload/', views_mcp.ToolUploadView.as_view(), name='tools-upload'),
    path('custom/', views_mcp.CustomToolListView.as_view(), name='custom-tools-list'),
    path('custom/delete/', views_mcp.CustomToolDeleteView.as_view(), name='custom-tools-delete'),
    path('custom/toggle/', views_mcp.CustomToolToggleView.as_view(), name='custom-tools-toggle'),
    path('custom/update/', views_mcp.CustomToolUpdateView.as_view(), name='custom-tools-update'),
    path('skills/', views_mcp.SkillListView.as_view(), name='skills-list'),
    path('skills/create/', views_mcp.SkillCreateView.as_view(), name='skills-create'),
    path('skills/update/', views_mcp.SkillUpdateView.as_view(), name='skills-update'),
    path('skills/delete/', views_mcp.SkillDeleteView.as_view(), name='skills-delete'),
    path('skills/toggle/', views_mcp.SkillToggleView.as_view(), name='skills-toggle'),
    path('skills/packages/', views_mcp.SkillPackageListView.as_view(), name='skill-packages-list'),
    path('skills/upload/', views_mcp.SkillPackageUploadView.as_view(), name='skill-packages-upload'),
    path('skills/packages/delete/', views_mcp.SkillPackageDeleteView.as_view(), name='skill-packages-delete'),
    path('skills/packages/toggle/', views_mcp.SkillPackageToggleView.as_view(), name='skill-packages-toggle'),
    path('skills/packages/detail/', views_mcp.SkillPackageDetailView.as_view(), name='skill-packages-detail'),
]
