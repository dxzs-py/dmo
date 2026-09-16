"""
Django_xm 项目的 URL 配置
    `urlpatterns` 列表用于将路由地址映射到视图。更多信息请参考：
    https://docs.djangoproject.com/en/5.2/topics/http/urls/

示例：

     函数视图
        1. 导入模块：`from my_app import views`
        2. 在 urlpatterns 中添加路由：`path('', views.home, name='home')`

     类视图
        1. 导入模块：`from other_app.views import Home`
        2. 在 urlpatterns 中添加路由：`path('', Home.as_view(), name='home')`

     引入其他 URL 配置文件
        1. 导入 `include()` 函数：`from django.urls import include, path`
        2. 在 urlpatterns 中添加路由：`path('blog/', include('blog.urls'))`
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
            "description": " 智能学习 & 研究助手 Agent",
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
