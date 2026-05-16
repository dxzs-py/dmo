"""
Celery 配置模块

启动方式:
    开发环境 (Windows 必须使用 solo 池，prefork 在 Windows 上不可用):
        1. Worker:  celery -A Django_xm worker -P solo -l info
        2. Beat:    celery -A Django_xm beat -l info

    生产环境 (Linux，多 Worker):
        1. 默认:    celery -A Django_xm worker -Q celery,chat -c 4 -l info
        2. RAG:     celery -A Django_xm worker -Q rag -c 1 -P solo -l info
        3. 研究:    celery -A Django_xm worker -Q research -c 2 -l info
        4. 工作流:  celery -A Django_xm worker -Q workflow -c 2 -l info
        5. Beat:    celery -A Django_xm beat -l info

    监控: celery -A Django_xm flower

任务目录:
    Django_xm/tasks/
    ├── __init__.py           # 统一导出
    ├── base.py               # TrackedTask 追踪混入 + 基础维护任务
    ├── chat_tasks.py         # 附件生命周期管理
    ├── deep_research.py      # 深度研究
    ├── rag_tasks.py          # RAG 索引操作
    ├── signals.py            # Celery 信号处理（自动注册，无需 include）
    └── workflow_tasks.py     # 工作流执行

队列划分:
    celery    - 默认队列，轻量维护任务
    rag       - RAG 索引操作（IO 密集，耗时长）
    research  - 深度研究（CPU+IO 密集）
    workflow  - 工作流执行（CPU 密集）
    chat      - 附件管理（IO 密集）
"""

import os
import sys
from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Django_xm.settings')

app = Celery('Django_xm', include=[
    'Django_xm.tasks.base',
    'Django_xm.tasks.chat_tasks',
    'Django_xm.tasks.deep_research',
    'Django_xm.tasks.rag_tasks',
    'Django_xm.tasks.workflow_tasks',
])

app.config_from_object('django.conf:settings', namespace='CELERY')

if sys.platform == 'win32':
    app.conf.worker_pool = 'solo'

import Django_xm.tasks.signals  # noqa: E402, F401 — 注册 Celery 信号钩子（直接导入模块，绕过 __init__.py 避免循环依赖）
