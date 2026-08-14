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

# 缓存预热：get_asgi_application() 完成 django.setup()（apps.ready 已为 True），
# 此处访问数据库不会触发 "Accessing the database during app initialization" 警告
# （AppConfig.ready() 中预热会触发该警告）。预热统一在各进程入口执行：
# - Web：本文件（asgi）与 wsgi.py
# - FastAPI 执行服务（8001）：main.py lifespan（sync_to_async）
from Django_xm.apps.ai_engine.models import warmup_system_config_cache
from Django_xm.apps.ai_engine.services.registry_service import warmup_cache

warmup_cache()
warmup_system_config_cache()

# 导入 WebSocket 路由（必须在 get_asgi_application() 之后，
# 因为 routing 依赖 Django apps 已初始化）
from Django_xm.routing import websocket_urlpatterns

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": URLRouter(websocket_urlpatterns),
    }
)
