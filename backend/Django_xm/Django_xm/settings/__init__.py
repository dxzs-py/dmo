"""
Django settings module initialization

根据 DJANGO_ENV 环境变量动态加载对应配置:
- production / prod → prod.py (生产环境)
- 其他(默认) → dev.py (开发环境)
"""
import os

ENV = os.environ.get("DJANGO_ENV", "development").lower().strip()

if ENV in ("prod", "production"):
    from .prod import *
else:
    from .dev import *
