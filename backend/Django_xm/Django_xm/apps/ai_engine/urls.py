from django.urls import path
from .model_views import ModelListView, ModelTestView, ModelSwitchView, HelperModelView
from .settings_views import AISettingsView, RebuildIndexesView

app_name = 'ai_engine'

urlpatterns = [
    path('models/', ModelListView.as_view(), name='model-list'),
    path('models/test/', ModelTestView.as_view(), name='model-test'),
    path('models/switch/', ModelSwitchView.as_view(), name='model-switch'),
    path('helper-model/', HelperModelView.as_view(), name='helper-model'),
    path('settings/', AISettingsView.as_view(), name='ai-settings'),
    path('settings/rebuild-indexes/', RebuildIndexesView.as_view(), name='rebuild-indexes'),
]
