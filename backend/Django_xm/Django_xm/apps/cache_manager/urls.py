from django.urls import path
from . import views

app_name = 'cache_manager'

urlpatterns = [
    path('health/', views.CacheHealthView.as_view(), name='cache-health'),
    path('stats/', views.CacheStatsView.as_view(), name='cache-stats'),
    path('invalidate/', views.CacheInvalidateView.as_view(), name='cache-invalidate'),
    path('clear/', views.CacheClearView.as_view(), name='cache-clear'),
    path('reset-stats/', views.CacheResetStatsView.as_view(), name='cache-reset-stats'),
    path('database/overview/', views.DatabaseOverviewView.as_view(), name='database-overview'),
    path('database/mysql/status/', views.MySQLStatusView.as_view(), name='mysql-status'),
    path('database/vector-store/status/', views.VectorStoreStatusView.as_view(), name='vector-store-status'),
]
