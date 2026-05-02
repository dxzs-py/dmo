from django.urls import path
from . import views

app_name = 'analytics'

urlpatterns = [
    path('dashboard/', views.DashboardView.as_view(), name='dashboard'),
    path('track/page-view/', views.PageViewTrackView.as_view(), name='track-page-view'),
    path('track/feature-use/', views.FeatureUseTrackView.as_view(), name='track-feature-use'),
    path('track/event/', views.EventTrackView.as_view(), name='track-event'),
]
