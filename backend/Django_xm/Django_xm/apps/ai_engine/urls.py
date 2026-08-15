from django.urls import path

from .model_views import HelperModelView, ModelListView, ModelSwitchView, ModelTestView
from .settings_views import AISettingsView, RebuildIndexesView
from .subagent_runtime.views import SubAgentListView, SubAgentResumeView

app_name = "ai_engine"

urlpatterns = [
    path("models/", ModelListView.as_view(), name="model_list"),
    path("models/test/", ModelTestView.as_view(), name="model_test"),
    path("models/switch/", ModelSwitchView.as_view(), name="model_switch"),
    path("helper-model/", HelperModelView.as_view(), name="helper_model"),
    path("settings/", AISettingsView.as_view(), name="ai_settings"),
    path("settings/rebuild-indexes/", RebuildIndexesView.as_view(), name="rebuild_indexes"),
    path("subagents/", SubAgentListView.as_view(), name="subagent_list"),
    path("subagents/resume/", SubAgentResumeView.as_view(), name="subagent_resume"),
]
