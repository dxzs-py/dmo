from django.urls import path

from .model_views import ModelListView, ModelTestView
from .settings_views import SandboxAvailabilityView
from .subagent_runtime.views import SubAgentListView

app_name = "ai_engine"

urlpatterns = [
    path("models/", ModelListView.as_view(), name="model_list"),
    path("models/test/", ModelTestView.as_view(), name="model_test"),
    # AI 设置/索引重建视图已迁 knowledge app（环 2 破除，任务 4.2）；
    # 对外 URL /api/v1/ai-engine/settings(/rebuild-indexes)/ 保持不变，由根 urls.py 直挂
    path("sandbox-availability/", SandboxAvailabilityView.as_view(), name="sandbox_availability"),
    path("subagents/", SubAgentListView.as_view(), name="subagent_list"),
]
