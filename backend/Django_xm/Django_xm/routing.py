"""
Django Channels WebSocket 路由配置

提供统一的实时同步 WebSocket 入口。
"""

from django.urls import path

from Django_xm.apps.chat.consumers import RealtimeSyncConsumer

# 当客户端发起 WebSocket 连接到 ws://host:8000/ws/realtime/ 时，交给 RealtimeSyncConsumer 处理。
websocket_urlpatterns = [
    path("ws/realtime/", RealtimeSyncConsumer.as_asgi()),  # type: ignore[arg-type]  # Django Channels ASGI app, not WSGI callable
]
