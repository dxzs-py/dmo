from django.urls import path
from . import views

urlpatterns = [
    path('start/', views.DeepResearchStartView.as_view(), name='deep-research-start'),
    path('status/<str:task_id>/', views.DeepResearchStatusView.as_view(), name='deep-research-status'),
]
