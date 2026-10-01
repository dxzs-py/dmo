from django.urls import path

from . import views

app_name = "cache_manager"

urlpatterns = [
    path("health/", views.CacheHealthView.as_view(), name="cache_health"),
    path("stats/", views.CacheStatsView.as_view(), name="cache_stats"),
    path("clear/", views.CacheClearView.as_view(), name="cache_clear"),
]
