"""
Celery 配置模块
用于 Django_xm 项目的异步任务队列管理

使用说明：
    # 开发环境启动 Worker（在 D:\Project\code\langchain_xm\backend\Django_xm 目录）
    # Windows 必须使用 -P solo 池，并监听 celery 队列
    celery -A Django_xm worker -l info -Q celery -P solo

    # 可选：启动 Flower 监控面板
    celery -A Django_xm flower

任务目录：
    Django_xm/tasks/          # 统一任务管理目录
    ├── __init__.py
    ├── base.py               # 基础、调试任务
    └── deep_research.py      # 深度研究任务
"""
import os
from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Django_xm.settings.dev')

app = Celery('Django_xm', include=['Django_xm.tasks.deep_research', 'Django_xm.tasks.base'])

app.config_from_object('django.conf:settings', namespace='CELERY')
