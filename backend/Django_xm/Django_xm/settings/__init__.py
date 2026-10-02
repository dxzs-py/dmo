"""
Django settings module initialization

settings 模块选择逻辑（按优先级）:
1. 测试运行: `python manage.py test ...` → test.py（测试隔离：
   屏蔽本地 .env 泄漏，如 SANDBOX_ENABLED=true 导致沙箱测试失败）
2. DJANGO_ENV=prod/production → prod.py（生产环境）
3. 其他（默认）→ dev.py（开发环境，本地 runserver/manage.py 行为不变）

注意: manage.py 的 setdefault 先于本模块执行，各测试文件内的
setdefault("DJANGO_SETTINGS_MODULE", "...settings.test") 永远无法生效
（REL-13 根因），因此测试分支必须在此检测 sys.argv。
仅当 DJANGO_SETTINGS_MODULE 为项目默认值（Django_xm.settings）时应用
测试分支；--settings 显式指定时尊重用户选择，不介入。
"""

import os
import sys

ENV = os.environ.get("DJANGO_ENV", "development").lower().strip()

# `manage.py test ...` → 路由到 test.py（显式 --settings 或外部已设置
# DJANGO_SETTINGS_MODULE 时不介入，保持用户显式选择）
_IS_TEST_RUN = len(sys.argv) > 1 and sys.argv[1] == "test"
_IS_DEFAULT_SETTINGS = (
    os.environ.get("DJANGO_SETTINGS_MODULE", "Django_xm.settings") == "Django_xm.settings"
)

if _IS_TEST_RUN and _IS_DEFAULT_SETTINGS:
    from .test import *
elif ENV in ("prod", "production"):
    from .prod import *
else:
    from .dev import *
