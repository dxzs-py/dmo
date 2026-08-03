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

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView

from Django_xm.apps.ai_engine.config import settings as app_cfg
from Django_xm.apps.core.views import health_check as core_health_check


def root_info(request):
    return JsonResponse(
        {
            "name": app_cfg.app_name,
            "version": app_cfg.app_version,
            "description": "LC-StudyLab 智能学习 & 研究助手 API",
            "api_versions": {"v1": "/api/v1/", "current": "/api/v1/"},
            "health": "/api/v1/health/",
            "docs": {
                "swagger": "/api/v1/docs/swagger/",
                "redoc": "/api/v1/docs/redoc/",
                "schema": "/api/v1/schema/",
            },
            "api_endpoints": {
                "auth": "/api/v1/users/",
                "chat": "/api/v1/chat/",
                "tools": "/api/v1/tools/",
                "attachments": "/api/v1/attachments/",
                "knowledge": "/api/v1/knowledge/",
                "learning": "/api/v1/learning/",
                "research": "/api/v1/research/",
                "analytics": "/api/v1/analytics/",
                "core": "/api/v1/core/",
                "cache": "/api/v1/cache/",
                "ai-engine": "/api/v1/ai-engine/",
                "context": "/api/v1/context/",
                "approvals": "/api/v1/approvals/",
            },
        }
    )


urlpatterns = [
    path("admin/", admin.site.urls),
    path("", root_info),
    # API v1 - 主版本
    path(
        "api/v1/",
        include(
            [
                path(
                    "",
                    include(
                        [
                            path("health/", core_health_check, name="health"),
                            path("schema/", SpectacularAPIView.as_view(), name="schema"),
                            path("docs/swagger/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger_ui"),
                            path("docs/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
                            path("users/", include("Django_xm.apps.users.urls")),
                            path("chat/", include("Django_xm.apps.chat.urls")),
                            path("tools/", include("Django_xm.apps.tools.urls")),
                            path("attachments/", include("Django_xm.apps.attachments.urls")),
                            path("knowledge/", include("Django_xm.apps.knowledge.urls")),
                            path("learning/", include("Django_xm.apps.learning.urls")),
                            path("research/", include("Django_xm.apps.research.urls")),
                            path("analytics/", include("Django_xm.apps.analytics.urls")),
                            path("core/", include("Django_xm.apps.core.urls")),
                            path("cache/", include("Django_xm.apps.cache_manager.urls")),
                            path("ai-engine/", include("Django_xm.apps.ai_engine.urls")),
                            path("context/", include("Django_xm.apps.context_manager.urls")),
                            path("approvals/", include("Django_xm.apps.approvals.urls")),
                            path("realtime/", include("Django_xm.apps.realtime.urls")),
                        ]
                    ),
                ),
            ]
        ),
    ),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
