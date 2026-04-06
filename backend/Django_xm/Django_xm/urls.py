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
        "api_versions": {
            "v1": "/api/v1/",
            "current": "/api/v1/"
        },
        "health": "/api/v1/health/",
        "docs": {
            "swagger": "/api/v1/docs/swagger/",
            "redoc": "/api/v1/docs/redoc/",
            "schema": "/api/v1/schema/",
        },
        "api_endpoints": {
            "auth": "/api/v1/users/",
            "chat": "/api/v1/chat/",
            "rag": "/api/v1/rag/",
            "workflow": "/api/v1/workflow/",
            "deep_research": "/api/v1/deep-research/",
        }
    })


urlpatterns = [
    path("admin/", admin.site.urls),
    path("", root_info),

    # API v1 - 主版本
    path("api/v1/", include([
        path("", include([
            path("health/", core_health_check, name="health"),
            path("monitor/", request_monitor, name="monitor"),

            path("schema/", SpectacularAPIView.as_view(), name="schema"),
            path("docs/swagger/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
            path("docs/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),

            path("users/", include("Django_xm.apps.users.urls")),
            path("chat/", include("Django_xm.apps.chat.urls")),
            path("rag/", include("Django_xm.apps.rag.urls")),
            path("workflow/", include("Django_xm.apps.workflows.urls")),
            path("deep-research/", include("Django_xm.apps.deep_research.urls")),
        ])),
    ])),

    # 兼容旧版API（无版本前缀），重定向到v1
    path("api/health/", core_health_check),
    path("api/schema/", SpectacularAPIView.as_view()),
    path("api/docs/swagger/", SpectacularSwaggerView.as_view(url_name="schema")),
    path("api/docs/redoc/", SpectacularRedocView.as_view(url_name="schema")),
]
