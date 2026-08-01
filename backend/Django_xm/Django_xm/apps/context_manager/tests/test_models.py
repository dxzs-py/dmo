"""context_manager 模型测试。

覆盖 ContextRule / AutoMemory / PromptCache 三个模型的：
- CRUD 全流程（create / read / update / delete）
- 软删除行为（BaseManager.objects 过滤 is_deleted，all_objects 返回全部）
- 字段默认值（path_patterns / relevance_tags / variables 为空列表）
- choices 约束（scope / source / cache_type）
- Meta ordering（priority DESC / last_accessed_at DESC / sort_order ASC）
- user 外键级联删除
- save() 中 None → [] 的防御逻辑

设计原则：
- 使用 Django TestCase（DB 事务自动管理）
- 不依赖 Redis / LLM / 向量库等外部服务
- 每个测试方法独立，setUp 创建公共用户
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from Django_xm.apps.context_manager.models import (
    AutoMemory,
    ContextRule,
    PromptCache,
)

User = get_user_model()


class ContextRuleTests(TestCase):
    """ContextRule 模型 CRUD 与约束测试。"""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="ctx_rule_user", password="pass123")

    def test_create_context_rule_defaults(self):
        """创建 ContextRule：默认值正确（scope=user_global, priority=0, is_active=True, path_patterns=[]）。"""
        rule = ContextRule.objects.create(
            user=self.user,
            name="测试规则",
            content="不要使用 root 权限",
        )
        self.assertEqual(rule.scope, "user_global")
        self.assertEqual(rule.priority, 0)
        self.assertTrue(rule.is_active)
        self.assertEqual(rule.path_patterns, [])
        self.assertEqual(rule.project_id, "")
        self.assertFalse(rule.is_deleted)

    def test_create_context_rule_all_fields(self):
        """创建 ContextRule：指定全部字段。"""
        rule = ContextRule.objects.create(
            user=self.user,
            scope="project",
            project_id="proj-001",
            name="项目规则",
            content="使用 Python 3.12",
            path_patterns=["*.py", "src/**/*.py"],
            priority=10,
            is_active=False,
        )
        self.assertEqual(rule.scope, "project")
        self.assertEqual(rule.project_id, "proj-001")
        self.assertEqual(rule.path_patterns, ["*.py", "src/**/*.py"])
        self.assertEqual(rule.priority, 10)
        self.assertFalse(rule.is_active)

    def test_path_patterns_none_becomes_empty_list(self):
        """save() 防御：path_patterns=None 时自动转为 []。"""
        rule = ContextRule(user=self.user, name="规则", content="内容", path_patterns=None)
        rule.save()
        self.assertEqual(rule.path_patterns, [])

    def test_str_representation(self):
        """__str__ 返回 [scope] name 格式。"""
        rule = ContextRule.objects.create(user=self.user, scope="local", name="本地规则", content="内容")
        self.assertEqual(str(rule), "[local] 本地规则")

    def test_soft_delete(self):
        """软删除：objects 不包含已删除记录，all_objects 包含。"""
        rule = ContextRule.objects.create(user=self.user, name="规则", content="内容")
        rule.soft_delete()

        self.assertNotIn(rule, ContextRule.objects.all())
        self.assertIn(rule, ContextRule.all_objects.all())
        self.assertTrue(rule.is_deleted)
        self.assertIsNotNone(rule.deleted_at)

    def test_ordering_by_priority_desc(self):
        """Meta.ordering：priority DESC, updated_at DESC。"""
        ContextRule.objects.create(user=self.user, name="低", content="c", priority=1)
        ContextRule.objects.create(user=self.user, name="高", content="c", priority=10)
        ContextRule.objects.create(user=self.user, name="中", content="c", priority=5)

        rules = list(ContextRule.objects.all())
        self.assertEqual([r.priority for r in rules], [10, 5, 1])

    def test_user_cascade_delete(self):
        """user 删除时，关联 ContextRule 级联删除。"""
        rule = ContextRule.objects.create(user=self.user, name="规则", content="内容")
        self.user.delete()
        self.assertFalse(ContextRule.objects.filter(id=rule.id).exists())

    def test_user_isolation(self):
        """不同用户的 ContextRule 互相隔离。"""
        other_user = User.objects.create_user(username="other", password="pass")
        ContextRule.objects.create(user=self.user, name="我的规则", content="c")
        ContextRule.objects.create(user=other_user, name="他人规则", content="c")

        my_rules = ContextRule.objects.filter(user=self.user)
        self.assertEqual(my_rules.count(), 1)
        self.assertEqual(my_rules.first().name, "我的规则")


class AutoMemoryTests(TestCase):
    """AutoMemory 模型 CRUD 与约束测试。"""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="auto_mem_user", password="pass123")

    def test_create_auto_memory_defaults(self):
        """创建 AutoMemory：默认值正确（source=other, access_count=0, relevance_tags=[]）。"""
        mem = AutoMemory.objects.create(user=self.user, content="使用 pytest 运行测试")
        self.assertEqual(mem.source, "other")
        self.assertEqual(mem.access_count, 0)
        self.assertEqual(mem.relevance_tags, [])
        self.assertEqual(mem.project_id, "")
        self.assertFalse(mem.is_deleted)

    def test_create_auto_memory_all_fields(self):
        """创建 AutoMemory：指定全部字段。"""
        mem = AutoMemory.objects.create(
            user=self.user,
            project_id="proj-001",
            content="npm run dev 启动前端",
            source="build_command",
            relevance_tags=["frontend", "npm"],
            access_count=5,
        )
        self.assertEqual(mem.source, "build_command")
        self.assertEqual(mem.relevance_tags, ["frontend", "npm"])
        self.assertEqual(mem.access_count, 5)

    def test_relevance_tags_none_becomes_empty_list(self):
        """save() 防御：relevance_tags=None 时自动转为 []。"""
        mem = AutoMemory(user=self.user, content="内容", relevance_tags=None)
        mem.save()
        self.assertEqual(mem.relevance_tags, [])

    def test_str_representation(self):
        """__str__ 返回 [source] content[:50] 格式。"""
        mem = AutoMemory.objects.create(
            user=self.user, content="这是一段很长的记忆内容应该被截断", source="debug_insight"
        )
        self.assertTrue(str(mem).startswith("[debug_insight]"))

    def test_soft_delete(self):
        """软删除：objects 不包含已删除记录。"""
        mem = AutoMemory.objects.create(user=self.user, content="内容")
        mem.soft_delete()
        self.assertNotIn(mem, AutoMemory.objects.all())
        self.assertIn(mem, AutoMemory.all_objects.all())

    def test_ordering_by_last_accessed_desc(self):
        """Meta.ordering：last_accessed_at DESC。"""
        from datetime import timedelta

        from django.utils import timezone

        mem1 = AutoMemory.objects.create(user=self.user, content="第一")
        mem2 = AutoMemory.objects.create(user=self.user, content="第二")
        # auto_now=True 无法通过 save() 覆盖，使用 update() 设置不同的时间戳
        AutoMemory.objects.filter(id=mem1.id).update(last_accessed_at=timezone.now() - timedelta(hours=1))
        AutoMemory.objects.filter(id=mem2.id).update(last_accessed_at=timezone.now())

        memories = list(AutoMemory.objects.all())
        self.assertEqual(memories[0].id, mem2.id)
        self.assertEqual(memories[1].id, mem1.id)

    def test_user_cascade_delete(self):
        """user 删除时，关联 AutoMemory 级联删除。"""
        mem = AutoMemory.objects.create(user=self.user, content="内容")
        self.user.delete()
        self.assertFalse(AutoMemory.objects.filter(id=mem.id).exists())


class PromptCacheTests(TestCase):
    """PromptCache 模型 CRUD 与约束测试。"""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="prompt_cache_user", password="pass123")

    def test_create_prompt_cache_defaults(self):
        """创建 PromptCache：默认值正确（cache_type=user_template, is_active=True, sort_order=0, token_count=0, usage_count=0, variables=[]）。"""  # noqa: E501
        cache = PromptCache.objects.create(user=self.user, name="模板1", content="你好")
        self.assertEqual(cache.cache_type, "user_template")
        self.assertTrue(cache.is_active)
        self.assertEqual(cache.sort_order, 0)
        self.assertEqual(cache.token_count, 0)
        self.assertEqual(cache.usage_count, 0)
        self.assertEqual(cache.variables, [])
        self.assertEqual(cache.description, "")

    def test_create_prompt_cache_all_fields(self):
        """创建 PromptCache：指定全部字段。"""
        cache = PromptCache.objects.create(
            user=self.user,
            name="系统前缀",
            cache_type="system_prefix",
            content="你是一个助手",
            variables=[{"name": "lang", "description": "语言", "default": "中文"}],
            description="系统前缀缓存",
            is_active=False,
            sort_order=5,
            token_count=100,
            usage_count=10,
        )
        self.assertEqual(cache.cache_type, "system_prefix")
        self.assertEqual(cache.variables, [{"name": "lang", "description": "语言", "default": "中文"}])
        self.assertEqual(cache.sort_order, 5)
        self.assertEqual(cache.token_count, 100)
        self.assertFalse(cache.is_active)

    def test_variables_none_becomes_empty_list(self):
        """save() 防御：variables=None 时自动转为 []。"""
        cache = PromptCache(user=self.user, name="模板", content="内容", variables=None)
        cache.save()
        self.assertEqual(cache.variables, [])

    def test_str_representation(self):
        """__str__ 返回 [cache_type] name 格式。"""
        cache = PromptCache.objects.create(
            user=self.user, name="上下文模板", cache_type="context_template", content="内容"
        )
        self.assertEqual(str(cache), "[context_template] 上下文模板")

    def test_soft_delete(self):
        """软删除：objects 不包含已删除记录。"""
        cache = PromptCache.objects.create(user=self.user, name="模板", content="内容")
        cache.soft_delete()
        self.assertNotIn(cache, PromptCache.objects.all())
        self.assertIn(cache, PromptCache.all_objects.all())

    def test_ordering_by_sort_order_asc(self):
        """Meta.ordering：sort_order ASC, updated_at DESC。"""
        PromptCache.objects.create(user=self.user, name="C", content="c", sort_order=3)
        PromptCache.objects.create(user=self.user, name="A", content="c", sort_order=1)
        PromptCache.objects.create(user=self.user, name="B", content="c", sort_order=2)

        caches = list(PromptCache.objects.all())
        self.assertEqual([c.sort_order for c in caches], [1, 2, 3])

    def test_user_cascade_delete(self):
        """user 删除时，关联 PromptCache 级联删除。"""
        cache = PromptCache.objects.create(user=self.user, name="模板", content="内容")
        self.user.delete()
        self.assertFalse(PromptCache.objects.filter(id=cache.id).exists())

    def test_update_usage_count(self):
        """更新 usage_count：模拟模板使用计数。"""
        cache = PromptCache.objects.create(user=self.user, name="模板", content="内容")
        self.assertEqual(cache.usage_count, 0)

        cache.usage_count += 1
        cache.save()
        cache.refresh_from_db()
        self.assertEqual(cache.usage_count, 1)
