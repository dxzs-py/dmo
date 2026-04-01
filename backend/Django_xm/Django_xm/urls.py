"""
URL configuration for Django_xm project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.contrib import admin
from django.urls import path, include
from django.http import JsonResponse
from django.conf import settings
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView
from Django_xm.apps.core.views import health_check as core_health_check, request_monitor

def root_info(request):
    return JsonResponse({
        "name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "description": "LC-StudyLab 智能学习 & 研究助手 API",
        "health": "/api/health/",
        "docs": {
            "swagger": "/api/docs/swagger/",
            "redoc": "/api/docs/redoc/",
            "schema": "/api/schema/",
        },
        "api": {
            "chat": "/api/chat/",
            "rag": "/api/rag/",
            "workflow": "/api/workflow/",
            "deep_research": "/api/deep-research/",
        }
    })

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", root_info),
    path("api/health/", core_health_check),
    path("api/monitor/", request_monitor),
    
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/swagger/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/docs/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    
    path("api/chat/", include("Django_xm.apps.chat.urls")),
    path("api/rag/", include("Django_xm.apps.rag.urls")),
    path("api/workflow/", include("Django_xm.apps.workflows.urls")),
    path("api/deep-research/", include("Django_xm.apps.deep_research.urls")),
]
