"""
Django Channels WebSocket 路由配置

提供统一的实时同步 WebSocket 入口。
"""

from django.urls import path

from Django_xm.apps.chat.consumers import RealtimeSyncConsumer


websocket_urlpatterns = [
    path("ws/realtime/", RealtimeSyncConsumer.as_asgi()),
]
