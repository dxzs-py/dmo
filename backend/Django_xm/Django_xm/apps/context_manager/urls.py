from django.urls import path

from . import views

app_name = 'context_manager'

urlpatterns = [
    path('stats/', views.ContextStatsView.as_view(), name='context-stats'),
    path('knowledge-graph/', views.KnowledgeGraphView.as_view(), name='context-knowledge-graph'),
    path('token-budget/', views.TokenBudgetView.as_view(), name='context-token-budget'),
    path('compress/', views.ContextCompressView.as_view(), name='context-compress'),
    path('knowledge-graph/detail/', views.KnowledgeGraphDetailView.as_view(), name='context-knowledge-graph-detail'),
    path('capability-config/', views.capability_config_view, name='capability-config'),
]
