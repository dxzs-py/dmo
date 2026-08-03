from django.urls import path

from . import views

app_name = "context_manager"

urlpatterns = [
    path("stats/", views.ContextStatsView.as_view(), name="context_stats"),
    path("knowledge-graph/", views.KnowledgeGraphView.as_view(), name="context_knowledge_graph"),
    path("token-budget/", views.TokenBudgetView.as_view(), name="context_token_budget"),
    path("compress/", views.ContextCompressView.as_view(), name="context_compress"),
    path("knowledge-graph/detail/", views.KnowledgeGraphDetailView.as_view(), name="context_knowledge_graph_detail"),
    path("capability-config/", views.capability_config_view, name="capability_config"),
]
