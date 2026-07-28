from django.urls import path

from . import views

app_name = 'cache_manager'

urlpatterns = [
    path('health/', views.CacheHealthView.as_view(), name='cache-health'),
    path('stats/', views.CacheStatsView.as_view(), name='cache-stats'),
    path('invalidate/', views.CacheInvalidateView.as_view(), name='cache-invalidate'),
    path('clear/', views.CacheClearView.as_view(), name='cache-clear'),
    path('reset-stats/', views.CacheResetStatsView.as_view(), name='cache-reset-stats'),
]
