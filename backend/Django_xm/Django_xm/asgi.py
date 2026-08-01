"""
ASGI config for Django_xm project.

支持 HTTP（Django ASGI）和 WebSocket（Django Channels）双协议。
使用 daphne 启动: daphne -b 0.0.0.0 -p 8000 Django_xm.asgi:application
"""

import os

from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings")

# HTTP-only Django ASGI 应用（REST API、SSE 等）
django_asgi_app = get_asgi_application()

# 导入 WebSocket 路由（必须在 get_asgi_application() 之后，
# 因为 routing 依赖 Django apps 已初始化）
from Django_xm.routing import websocket_urlpatterns  # noqa: E402

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": URLRouter(websocket_urlpatterns),
    }
)
