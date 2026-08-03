from django.urls import path

from . import views

app_name = "cache_manager"

urlpatterns = [
    path("health/", views.CacheHealthView.as_view(), name="cache_health"),
    path("stats/", views.CacheStatsView.as_view(), name="cache_stats"),
    path("invalidate/", views.CacheInvalidateView.as_view(), name="cache_invalidate"),
    path("clear/", views.CacheClearView.as_view(), name="cache_clear"),
    path("reset-stats/", views.CacheResetStatsView.as_view(), name="cache_reset_stats"),
]
