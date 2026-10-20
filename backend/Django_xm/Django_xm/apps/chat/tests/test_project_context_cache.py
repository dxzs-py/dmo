"""项目上下文缓存键隔离回归测试（Spec: consistency-audit-fixes / 缓存缺陷修复）。

缺陷背景：
ProjectContextView 的响应**依赖 ?path= 查询参数**——detect_project_context 以它为
根目录，project_root / project_name / git 分支 / 语言框架 / 目录结构 / 关键文件
全部随 path 变化（ai_engine/services/project_context.py:71-88）。但修复前缓存键只有
project_context:user_{id}，**不含 path**，且 TTL 为 QUERY_LONG=86400（24h）。

后果：同一用户先请求 ?path=/a 再请求 ?path=/b，24h 内都拿到 /a 的结果，
即**项目上下文跨项目串数据**。

覆盖：
- 不同 path 产生不同缓存键（?path=/b 不会被 ?path=/a 的缓存命中）；
- 相同 path 命中缓存（detect 不重复调用），避免修复退化为"完全不用缓存"；
- 无 path 参数时也走独立缓存键。

实现说明：
- detect_project_context 在视图内是**函数级导入**，故 patch
  Django_xm.apps.ai_engine.services.project_context.detect_project_context；
- 用真实缓存（test settings 为 LocMemCache），不 mock CacheService，直接断言
  缓存键与命中行为。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python manage.py test Django_xm.apps.chat.tests.test_project_context_cache
"""

import os
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from Django_xm.apps.cache_manager.services.cache_service import (
    CACHE_PREFIX_PROJECT_CONTEXT,
    CacheService,
)

PROJECT_CONTEXT_URL = "/api/v1/chat/project-context/"

_PATCH_TARGET = "Django_xm.apps.ai_engine.services.project_context.detect_project_context"


class ProjectContextCacheKeyTest(TestCase):
    """缓存键必须包含 path，否则不同项目的上下文互相污染。"""

    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username="pcache", password="pw123456")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _fake_detect(self, calls):
        """构造 detect_project_context 替身：记录入参，to_dict 回显该 path。"""

        def _detect(search_path=None):
            calls.append(search_path)

            class _Ctx:
                def to_dict(self):
                    return {"project_root": search_path or "", "detected_for": search_path}

            return _Ctx()

        return _detect

    def test_different_path_not_served_from_previous_path_cache(self):
        """核心回归：?path=/b 不得命中 ?path=/a 的缓存。"""
        calls = []
        with mock.patch(_PATCH_TARGET, side_effect=self._fake_detect(calls)):
            r_a = self.client.get(PROJECT_CONTEXT_URL, {"path": "/a"})
            r_b = self.client.get(PROJECT_CONTEXT_URL, {"path": "/b"})

        self.assertEqual(r_a.status_code, 200)
        self.assertEqual(r_b.status_code, 200)
        # 两次请求都真正执行了探测（未被前一次缓存短路）
        self.assertEqual(calls, ["/a", "/b"])
        # 响应内容各自对应自己的 path
        self.assertEqual(r_a.json()["data"]["detected_for"], "/a")
        self.assertEqual(r_b.json()["data"]["detected_for"], "/b")

    def test_same_path_hits_cache(self):
        """相同 path 应命中缓存（修复不得退化为完全不用缓存）。"""
        calls = []
        with mock.patch(_PATCH_TARGET, side_effect=self._fake_detect(calls)):
            self.client.get(PROJECT_CONTEXT_URL, {"path": "/a"})
            self.client.get(PROJECT_CONTEXT_URL, {"path": "/a"})

        self.assertEqual(calls, ["/a"])  # 第二次未再探测

    def test_missing_path_uses_its_own_cache_key(self):
        """无 path 参数同样应有独立键，不与任意具体 path 混用。"""
        calls = []
        with mock.patch(_PATCH_TARGET, side_effect=self._fake_detect(calls)):
            self.client.get(PROJECT_CONTEXT_URL)
            self.client.get(PROJECT_CONTEXT_URL, {"path": "/a"})

        self.assertEqual(calls, [None, "/a"])

    def test_cache_key_contains_path_hash(self):
        """缓存键结构守卫：user 段 + path 哈希段，路径不同则键不同。"""
        import hashlib

        def key_for(path):
            h = hashlib.sha256((path or "").encode("utf-8"), usedforsecurity=False).hexdigest()[:24]
            return f"{CACHE_PREFIX_PROJECT_CONTEXT}:user_{self.user.id}:path_{h}"

        self.assertNotEqual(key_for("/a"), key_for("/b"))
        self.assertNotEqual(key_for(None), key_for("/a"))
        # 同一 path 必须稳定
        self.assertEqual(key_for("/a"), key_for("/a"))

        # 实际写入缓存的键应与该结构一致
        with mock.patch(_PATCH_TARGET, side_effect=self._fake_detect([])):
            self.client.get(PROJECT_CONTEXT_URL, {"path": "/a"})
        self.assertIsNotNone(CacheService.get(key_for("/a")))
        self.assertIsNone(CacheService.get(key_for("/b")))
