from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.health_check, name="health"),
    path("database/overview/", views.DatabaseOverviewView.as_view(), name="database_overview"),
]
