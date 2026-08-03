from django.urls import path

from .model_views import HelperModelView, ModelListView, ModelSwitchView, ModelTestView
from .settings_views import AISettingsView, RebuildIndexesView

app_name = "ai_engine"

urlpatterns = [
    path("models/", ModelListView.as_view(), name="model_list"),
    path("models/test/", ModelTestView.as_view(), name="model_test"),
    path("models/switch/", ModelSwitchView.as_view(), name="model_switch"),
    path("helper-model/", HelperModelView.as_view(), name="helper_model"),
    path("settings/", AISettingsView.as_view(), name="ai_settings"),
    path("settings/rebuild-indexes/", RebuildIndexesView.as_view(), name="rebuild_indexes"),
]
