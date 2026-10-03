from django.urls import path

from .model_views import ModelListView, ModelTestView
from .settings_views import AISettingsView, RebuildIndexesView, SandboxAvailabilityView
from .subagent_runtime.views import SubAgentListView

app_name = "ai_engine"

urlpatterns = [
    path("models/", ModelListView.as_view(), name="model_list"),
    path("models/test/", ModelTestView.as_view(), name="model_test"),
    path("settings/", AISettingsView.as_view(), name="ai_settings"),
    path("settings/rebuild-indexes/", RebuildIndexesView.as_view(), name="rebuild_indexes"),
    path("sandbox-availability/", SandboxAvailabilityView.as_view(), name="sandbox_availability"),
    path("subagents/", SubAgentListView.as_view(), name="subagent_list"),
]
