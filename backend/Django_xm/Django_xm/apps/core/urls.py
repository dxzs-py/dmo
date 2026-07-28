from django.urls import path

from . import views

app_name = 'core'

urlpatterns = [
    path('', views.health_check, name='health'),
    path('database/overview/', views.DatabaseOverviewView.as_view(), name='database-overview'),
    path('database/postgresql/status/', views.PostgreSQLStatusView.as_view(), name='postgresql-status'),
    path('database/vector-store/status/', views.VectorStoreStatusView.as_view(), name='vector-store-status'),
]
