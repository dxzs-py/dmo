"""pytest 全局 conftest（ARCH-20 测试保护网）。

- Django 初始化由 pytest-django 接管：DJANGO_SETTINGS_MODULE 已在
  pyproject.toml [tool.pytest.ini_options] 指向 Django_xm.settings.test，
  与 `manage.py test`（settings/__init__ 按 sys.argv 路由到 test.py）共用
  同一份测试配置，双跑结果可互相比对。

- 数据库隔离说明：
  * django.test.TestCase / TransactionTestCase 子类无需任何标记，
    pytest-django 自动创建 test_<DB_NAME> 临时库并按 Django 语义隔离；
  * pytest 风格的普通测试函数需显式加 @pytest.mark.django_db 才能访问数据库。

- Mock 替身统一从 Django_xm.common.testing 工厂获取（见 factories.py），
  禁止在测试文件内再复制本地替身类。

注意：本模块刻意不导入 Django_xm 包——Django_xm/__init__ 会经 celery.py
setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings")，因此工厂导入
延迟到 fixture 调用时执行，避免在 pytest-django 接管前污染环境变量。
"""

import asyncio
import sys

import pytest

if sys.platform == "win32":
    # 与 manage.py 保持一致：psycopg3 异步模式需要 SelectorEventLoop
    # （Windows 默认 ProactorEventLoop 不兼容），否则 async DB 测试会失败。
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@pytest.fixture()
def fake_redis():
    """进程内异步 Redis 替身（内存存储，可注入宕机故障模式）。"""
    from Django_xm.common.testing import create_fake_redis

    return create_fake_redis()
