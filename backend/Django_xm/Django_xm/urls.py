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

def health_check(request):
    return JsonResponse({
        "status": "healthy",
        "version": settings.APP_VERSION,
        "debug": settings.DEBUG,
    })

def root_info(request):
    return JsonResponse({
        "name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "description": "LC-StudyLab 智能学习 & 研究助手 API",
        "health": "/health",
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
    path("health/", health_check),
    path("health", health_check),
    
    path("api/chat/", include("Django_xm.apps.chat.urls")),
    path("api/rag/", include("Django_xm.apps.rag.urls")),
    path("api/workflow/", include("Django_xm.apps.workflows.urls")),
    path("api/workflows/", include("Django_xm.apps.workflows.urls")),
    path("api/deep-research/", include("Django_xm.apps.deep_research.urls")),
    
    path("chat/", include("Django_xm.apps.chat.urls")),
    path("rag/", include("Django_xm.apps.rag.urls")),
    path("workflow/", include("Django_xm.apps.workflows.urls")),
    path("workflows/", include("Django_xm.apps.workflows.urls")),
    path("deep-research/", include("Django_xm.apps.deep_research.urls")),
]
