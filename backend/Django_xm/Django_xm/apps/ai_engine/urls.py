from django.urls import path
from .model_views import ModelListView, ModelTestView, ModelSwitchView

app_name = 'ai_engine'

urlpatterns = [
    path('models/', ModelListView.as_view(), name='model-list'),
    path('models/test/', ModelTestView.as_view(), name='model-test'),
    path('models/switch/', ModelSwitchView.as_view(), name='model-switch'),
]
