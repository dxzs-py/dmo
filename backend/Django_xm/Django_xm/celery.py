"""
Celery 配置模块
用于 Django_xm 项目的异步任务队列管理

使用说明:
    开发环境启动 (Windows 需要两个窗口):
    1. Worker (窗口1): celery -A Django_xm worker -l info -Q celery -P solo
    2. Beat 定时调度 (窗口2): celery -A Django_xm beat -l info

    可选:启动 Flower 监控面板
    celery -A Django_xm flower

任务目录:
    Django_xm/tasks/          # 统一任务管理目录
    ├── __init__.py
    ├── base.py               # 基础、调试任务
    ├── deep_research.py      # 深度研究任务
    ├── rag_tasks.py          # RAG 相关任务(索引创建、文档处理等)
    └── workflow_tasks.py     # 工作流任务(学习工作流执行等)
    Django_xm/apps/chat/tasks.py  # 附件生命周期管理任务
"""

import os
from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Django_xm.settings')

app = Celery('Django_xm', include=[
    'Django_xm.tasks.deep_research',
    'Django_xm.tasks.base',
    'Django_xm.tasks.rag_tasks',
    'Django_xm.tasks.workflow_tasks',
    'Django_xm.apps.chat.tasks',
])

app.config_from_object('django.conf:settings', namespace='CELERY')

app.autodiscover_tasks()
