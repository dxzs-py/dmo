"""
WSGI config for Django_xm project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings")

application = get_wsgi_application()

# 缓存预热：与 asgi.py 相同策略（get_wsgi_application() 后 apps.ready 已为 True，
# 不触发 "Accessing the database during app initialization" 警告）。
from Django_xm.apps.ai_engine.models import warmup_system_config_cache
from Django_xm.apps.ai_engine.services.registry_service import warmup_cache

warmup_cache()
warmup_system_config_cache()
