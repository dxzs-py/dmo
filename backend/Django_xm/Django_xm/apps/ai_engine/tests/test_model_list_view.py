"""ModelListView providers 响应默认模型标记测试（spec P4：模型列表加载零 403）。

覆盖：
- SystemConfig.default_chat_model 已配置 → 响应含 default_provider/default_model（普通用户认证即可取到）
- 未配置 → 回退 default_provider="openai"、default_model=None（历史行为不变）
- 配置缺 model_name → default_model=None（前端按 provider 默认模型兜底）

隔离策略：get_available_providers 打桩（注册表依赖 DB 预热缓存，与本测试无关）；
SystemConfig 走测试库 + LocMemCache，setUp 清理。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    conda run -n langchain_xm python -m unittest Django_xm.apps.ai_engine.tests.test_model_list_view -v
"""

import os
from unittest import mock

# python -m unittest 直跑时：Django_xm/__init__.py → celery.py 已将
# DJANGO_SETTINGS_MODULE setdefault 为 dev settings，此处必须强制覆盖为 test
os.environ["DJANGO_SETTINGS_MODULE"] = "Django_xm.settings.test"
import django

django.setup()

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from Django_xm.apps.ai_engine import model_views
from Django_xm.apps.ai_engine.models import SystemConfig

MODELS_URL = "/api/v1/ai-engine/models/"

_STUB_PROVIDERS = [
    {
        "id": "openai",
        "provider": "openai",
        "label": "OpenAI",
        "icon": "🔵",
        "models": [{"name": "gpt-4o-mini", "capabilities": ["tool_calling", "streaming"]}],
        "default_model": "gpt-4o-mini",
        "available": True,
        "special_params": {},
    },
    {
        "id": "deepseek",
        "provider": "deepseek",
        "label": "DeepSeek",
        "icon": "🤖",
        "models": [{"name": "deepseek-v4-flash", "capabilities": ["tool_calling", "streaming"]}],
        "default_model": "deepseek-v4-flash",
        "available": True,
        "special_params": {},
    },
]


class ModelListViewDefaultTests(TestCase):
    """providers 响应含系统默认 provider/model 标记（公开非敏感）。"""

    def setUp(self):
        cache.clear()
        SystemConfig.objects.all().delete()
        self.client = APIClient()
        # 普通用户（非 staff）：IsAdminUser 的 settings 端点对其必 403
        self.user = get_user_model().objects.create_user(username="plain", password="pw123456")
        self.client.force_authenticate(user=self.user)

    def _get_models(self):
        with mock.patch.object(
            model_views, "get_available_providers", return_value=_STUB_PROVIDERS
        ):
            return self.client.get(MODELS_URL)

    def test_default_from_system_config(self):
        """① 已配置系统默认：default_provider/default_model 透出，普通用户 200 可取。"""
        SystemConfig.set_value(
            "default_chat_model",
            {"provider_id": "deepseek", "model_name": "deepseek-v4-flash"},
        )

        resp = self._get_models()

        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()["data"]
        self.assertEqual(data["default_provider"], "deepseek")
        self.assertEqual(data["default_model"], "deepseek-v4-flash")
        self.assertEqual(data["providers"], _STUB_PROVIDERS)

    def test_fallback_without_system_config(self):
        """② 未配置系统默认：default_provider 回退 openai、default_model 为 None。"""
        resp = self._get_models()

        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()["data"]
        self.assertEqual(data["default_provider"], "openai")
        self.assertIsNone(data["default_model"])

    def test_system_config_without_model_name(self):
        """③ 配置缺 model_name：default_provider 透出、default_model 为 None（前端按 provider 默认模型兜底）。"""
        SystemConfig.set_value("default_chat_model", {"provider_id": "openai"})

        resp = self._get_models()

        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()["data"]
        self.assertEqual(data["default_provider"], "openai")
        self.assertIsNone(data["default_model"])
