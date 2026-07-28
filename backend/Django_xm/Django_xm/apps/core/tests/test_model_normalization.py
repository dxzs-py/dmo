"""Task 12 - 数据模型规范化测试

验证：
1. ContextRule / AutoMemory / PromptCache 继承 BaseModel（含 created_at/updated_at/is_deleted/deleted_at）
2. SystemConfig 增加 created_at 字段
3. Approval.Meta.indexes 包含 source/state/created_at 复合索引
4. ChatMessage.token_count / current_version 为 PositiveIntegerField
5. ResearchTask.token_count 为 PositiveIntegerField
6. WorkflowSession.token_count 为 PositiveIntegerField

运行方式：
    cd d:\\programming\\langchain\\langchain_xm\\backend\\Django_xm
    set DJANGO_SETTINGS_MODULE=Django_xm.settings.dev
    D:\\Anaconda_envs\\envs\\langchain_xm\\python.exe -m pytest \\
        Django_xm/apps/core/tests/test_model_normalization.py -v --tb=short

    # 或使用 unittest
    D:\\Anaconda_envs\\envs\\langchain_xm\\python.exe -m unittest \\
        Django_xm.apps.core.tests.test_model_normalization -v
"""

from __future__ import annotations

import os
import unittest

# 确保 Django settings 可加载
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Django_xm.settings.dev')

import django

django.setup()

from django.apps import apps
from django.db import models

# Task 15.6: core 不应直接 import 业务 app 模型，用 apps.get_model 延迟解析
SystemConfig = apps.get_model('ai_engine', 'SystemConfig')
ResearchTask = apps.get_model('research', 'ResearchTask')

from Django_xm.apps.approvals.models import Approval
from Django_xm.apps.chat.models import ChatMessage
from Django_xm.apps.context_manager.models import (
    AutoMemory,
    ContextRule,
    PromptCache,
)
from Django_xm.apps.core.base_models import BaseModel
from Django_xm.apps.learning.models import WorkflowSession


def _field_is_instance(model: type, field_name: str, field_cls: type) -> bool:
    """检查 model 上的 field_name 是否为 field_cls 实例。"""
    try:
        field = model._meta.get_field(field_name)
    except Exception:
        return False
    return isinstance(field, field_cls)


def _has_field(model: type, field_name: str) -> bool:
    """检查 model 是否包含指定字段（含继承字段）。"""
    try:
        model._meta.get_field(field_name)
        return True
    except Exception:
        return False


def _has_index(model: type, index_name: str) -> bool:
    """检查 model.Meta.indexes 中是否包含指定名称的索引。"""
    return any(idx.name == index_name for idx in model._meta.indexes)


class TestContextRuleInheritsBaseModel(unittest.TestCase):
    """SubTask 12.1 - ContextRule 继承 BaseModel。"""

    def test_is_subclass_of_basemodel(self) -> None:
        """ContextRule 应继承 BaseModel。"""
        self.assertTrue(issubclass(ContextRule, BaseModel))

    def test_has_created_at_field(self) -> None:
        """ContextRule 应包含 created_at 字段（来自 BaseModel）。"""
        self.assertTrue(_has_field(ContextRule, 'created_at'))

    def test_has_updated_at_field(self) -> None:
        """ContextRule 应包含 updated_at 字段（来自 BaseModel）。"""
        self.assertTrue(_has_field(ContextRule, 'updated_at'))

    def test_has_is_deleted_field(self) -> None:
        """ContextRule 应包含 is_deleted 字段（来自 BaseModel，软删除标记）。"""
        self.assertTrue(_has_field(ContextRule, 'is_deleted'))

    def test_has_deleted_at_field(self) -> None:
        """ContextRule 应包含 deleted_at 字段（来自 BaseModel，软删除时间）。"""
        self.assertTrue(_has_field(ContextRule, 'deleted_at'))

    def test_objects_is_soft_delete_manager(self) -> None:
        """ContextRule.objects 应过滤 is_deleted=False（软删除管理器）。"""
        from Django_xm.apps.core.base_models import SoftDeleteManager
        self.assertIsInstance(ContextRule.objects, SoftDeleteManager)

    def test_all_objects_manager_exists(self) -> None:
        """ContextRoute 应提供 all_objects 管理器返回全部记录。"""
        from Django_xm.apps.core.base_models import AllObjectsManager
        self.assertTrue(hasattr(ContextRule, 'all_objects'))
        self.assertIsInstance(ContextRule.all_objects, AllObjectsManager)


class TestAutoMemoryInheritsBaseModel(unittest.TestCase):
    """SubTask 12.1 - AutoMemory 继承 BaseModel。"""

    def test_is_subclass_of_basemodel(self) -> None:
        """AutoMemory 应继承 BaseModel。"""
        self.assertTrue(issubclass(AutoMemory, BaseModel))

    def test_has_created_at_field(self) -> None:
        """AutoMemory 应包含 created_at 字段。"""
        self.assertTrue(_has_field(AutoMemory, 'created_at'))

    def test_has_updated_at_field(self) -> None:
        """AutoMemory 应包含 updated_at 字段（继承自 BaseModel）。"""
        self.assertTrue(_has_field(AutoMemory, 'updated_at'))

    def test_has_is_deleted_field(self) -> None:
        """AutoMemory 应包含 is_deleted 字段。"""
        self.assertTrue(_has_field(AutoMemory, 'is_deleted'))

    def test_has_deleted_at_field(self) -> None:
        """AutoMemory 应包含 deleted_at 字段。"""
        self.assertTrue(_has_field(AutoMemory, 'deleted_at'))

    def test_objects_is_soft_delete_manager(self) -> None:
        """AutoMemory.objects 应为 SoftDeleteManager 实例。"""
        from Django_xm.apps.core.base_models import SoftDeleteManager
        self.assertIsInstance(AutoMemory.objects, SoftDeleteManager)


class TestPromptCacheInheritsBaseModel(unittest.TestCase):
    """SubTask 12.1 - PromptCache 继承 BaseModel。"""

    def test_is_subclass_of_basemodel(self) -> None:
        """PromptCache 应继承 BaseModel。"""
        self.assertTrue(issubclass(PromptCache, BaseModel))

    def test_has_created_at_field(self) -> None:
        """PromptCache 应包含 created_at 字段。"""
        self.assertTrue(_has_field(PromptCache, 'created_at'))

    def test_has_updated_at_field(self) -> None:
        """PromptCache 应包含 updated_at 字段。"""
        self.assertTrue(_has_field(PromptCache, 'updated_at'))

    def test_has_is_deleted_field(self) -> None:
        """PromptCache 应包含 is_deleted 字段。"""
        self.assertTrue(_has_field(PromptCache, 'is_deleted'))

    def test_has_deleted_at_field(self) -> None:
        """PromptCache 应包含 deleted_at 字段。"""
        self.assertTrue(_has_field(PromptCache, 'deleted_at'))

    def test_objects_is_soft_delete_manager(self) -> None:
        """PromptCache.objects 应为 SoftDeleteManager 实例。"""
        from Django_xm.apps.core.base_models import SoftDeleteManager
        self.assertIsInstance(PromptCache.objects, SoftDeleteManager)


class TestSystemConfigCreatedAt(unittest.TestCase):
    """SubTask 12.2 - SystemConfig 增加 created_at 字段。"""

    def test_has_created_at_field(self) -> None:
        """SystemConfig 应包含 created_at 字段。"""
        self.assertTrue(_has_field(SystemConfig, 'created_at'))

    def test_created_at_is_datetime_field(self) -> None:
        """SystemConfig.created_at 应为 DateTimeField。"""
        self.assertTrue(_field_is_instance(SystemConfig, 'created_at', models.DateTimeField))

    def test_created_at_auto_now_add(self) -> None:
        """SystemConfig.created_at 应设置 auto_now_add=True。"""
        field = SystemConfig._meta.get_field('created_at')
        self.assertTrue(field.auto_now_add)

    def test_has_updated_at_field(self) -> None:
        """SystemConfig.updated_at 应仍存在（与 created_at 保持一致）。"""
        self.assertTrue(_has_field(SystemConfig, 'updated_at'))


class TestApprovalSourceStateCreatedIndex(unittest.TestCase):
    """SubTask 12.3 - Approval 增加 source/state/created_at 复合索引。"""

    def test_index_exists(self) -> None:
        """Approval.Meta.indexes 应包含 approval_src_state_created_idx 索引。"""
        self.assertTrue(
            _has_index(Approval, 'approval_src_state_created_idx'),
            'Approval.Meta.indexes 缺少 approval_src_state_created_idx 索引',
        )

    def test_index_fields(self) -> None:
        """索引应覆盖 source/state/created_at 三个字段。"""
        index = next(
            (idx for idx in Approval._meta.indexes if idx.name == 'approval_src_state_created_idx'),
            None,
        )
        self.assertIsNotNone(index)
        assert index is not None  # 窄化类型
        self.assertEqual(list(index.fields), ['source', 'state', 'created_at'])

    def test_user_state_created_index_still_exists(self) -> None:
        """Task 1.1 引入的 user/state/created_at 索引应仍存在（按字段匹配）。"""
        user_state_idx_exists = any(
            list(idx.fields) == ['user', 'state', 'created_at']
            for idx in Approval._meta.indexes
        )
        self.assertTrue(
            user_state_idx_exists,
            'Approval 应保留 user/state/created_at 索引（Task 1.1）',
        )


class TestChatMessageTokenCountPositive(unittest.TestCase):
    """SubTask 12.4 - ChatMessage.token_count / current_version 改为 PositiveIntegerField。"""

    def test_token_count_is_positive_integer_field(self) -> None:
        """ChatMessage.token_count 应为 PositiveIntegerField。"""
        self.assertTrue(
            _field_is_instance(ChatMessage, 'token_count', models.PositiveIntegerField),
            'ChatMessage.token_count 应为 PositiveIntegerField',
        )

    def test_token_count_not_plain_integer_field(self) -> None:
        """ChatMessage.token_count 不应为 IntegerField（非 Positive）。"""
        field = ChatMessage._meta.get_field('token_count')
        # PositiveIntegerField 是 IntegerField 的子类，需精确匹配类型
        self.assertEqual(type(field).__name__, 'PositiveIntegerField')

    def test_current_version_is_positive_integer_field(self) -> None:
        """ChatMessage.current_version 应为 PositiveIntegerField。"""
        self.assertTrue(
            _field_is_instance(ChatMessage, 'current_version', models.PositiveIntegerField),
            'ChatMessage.current_version 应为 PositiveIntegerField',
        )

    def test_current_version_not_plain_integer_field(self) -> None:
        """ChatMessage.current_version 不应为 IntegerField（非 Positive）。"""
        field = ChatMessage._meta.get_field('current_version')
        self.assertEqual(type(field).__name__, 'PositiveIntegerField')

    def test_token_count_default_zero(self) -> None:
        """ChatMessage.token_count 默认值应为 0。"""
        field = ChatMessage._meta.get_field('token_count')
        self.assertEqual(field.default, 0)

    def test_current_version_default_zero(self) -> None:
        """ChatMessage.current_version 默认值应为 0。"""
        field = ChatMessage._meta.get_field('current_version')
        self.assertEqual(field.default, 0)


class TestResearchTaskTokenCountPositive(unittest.TestCase):
    """SubTask 12.4 - ResearchTask.token_count 改为 PositiveIntegerField。"""

    def test_token_count_is_positive_integer_field(self) -> None:
        """ResearchTask.token_count 应为 PositiveIntegerField。"""
        self.assertTrue(
            _field_is_instance(ResearchTask, 'token_count', models.PositiveIntegerField),
            'ResearchTask.token_count 应为 PositiveIntegerField',
        )

    def test_token_count_not_plain_integer_field(self) -> None:
        """ResearchTask.token_count 不应为 IntegerField（非 Positive）。"""
        field = ResearchTask._meta.get_field('token_count')
        self.assertEqual(type(field).__name__, 'PositiveIntegerField')

    def test_token_count_default_zero(self) -> None:
        """ResearchTask.token_count 默认值应为 0。"""
        field = ResearchTask._meta.get_field('token_count')
        self.assertEqual(field.default, 0)


class TestWorkflowSessionTokenCountPositive(unittest.TestCase):
    """SubTask 12.4 - WorkflowSession.token_count 改为 PositiveIntegerField。"""

    def test_token_count_is_positive_integer_field(self) -> None:
        """WorkflowSession.token_count 应为 PositiveIntegerField。"""
        self.assertTrue(
            _field_is_instance(WorkflowSession, 'token_count', models.PositiveIntegerField),
            'WorkflowSession.token_count 应为 PositiveIntegerField',
        )

    def test_token_count_not_plain_integer_field(self) -> None:
        """WorkflowSession.token_count 不应为 IntegerField（非 Positive）。"""
        field = WorkflowSession._meta.get_field('token_count')
        self.assertEqual(type(field).__name__, 'PositiveIntegerField')

    def test_token_count_default_zero(self) -> None:
        """WorkflowSession.token_count 默认值应为 0。"""
        field = WorkflowSession._meta.get_field('token_count')
        self.assertEqual(field.default, 0)


class TestMigrationsReversibility(unittest.TestCase):
    """SubTask 12.5 - 验证 Task 12 相关迁移文件可逆。"""

    def _load_migration(self, app_label: str, migration_name: str):
        """动态加载指定迁移模块。"""
        import importlib
        module_path = f'Django_xm.apps.{app_label}.migrations.{migration_name}'
        return importlib.import_module(module_path)

    def test_ai_engine_migration_reversible(self) -> None:
        """ai_engine 0010 - AddField 操作应可逆（reverse 为 RemoveField）。"""
        mod = self._load_migration('ai_engine', '0010_systemconfig_created_at')
        for op in mod.Migration.operations:
            self.assertTrue(
                op.reversible,
                f'ai_engine 0010 操作 {op} 应可逆',
            )

    def test_approvals_migration_reversible(self) -> None:
        """approvals 0010 - AddIndex 操作应可逆（reverse 为 RemoveIndex）。"""
        mod = self._load_migration('approvals', '0010_approval_approval_src_state_created_idx')
        for op in mod.Migration.operations:
            self.assertTrue(
                op.reversible,
                f'approvals 0010 操作 {op} 应可逆',
            )

    def test_context_manager_migration_reversible(self) -> None:
        """context_manager 0002 - AddField/AlterField 操作应可逆。"""
        mod = self._load_migration(
            'context_manager',
            '0002_automemory_deleted_at_automemory_is_deleted_and_more',
        )
        for op in mod.Migration.operations:
            self.assertTrue(
                op.reversible,
                f'context_manager 0002 操作 {op} 应可逆',
            )

    def test_chat_migration_reversible(self) -> None:
        """chat 0014 - 包含 token_count AlterField，应可逆。"""
        mod = self._load_migration(
            'chat',
            '0014_remove_chatmessage_is_streaming_and_more',
        )
        for op in mod.Migration.operations:
            self.assertTrue(
                op.reversible,
                f'chat 0014 操作 {op} 应可逆',
            )

    def test_research_migration_reversible(self) -> None:
        """research 0009 - 包含 token_count AlterField，应可逆。"""
        mod = self._load_migration(
            'research',
            '0009_remove_researchtask_approval_history_and_more',
        )
        for op in mod.Migration.operations:
            self.assertTrue(
                op.reversible,
                f'research 0009 操作 {op} 应可逆',
            )

    def test_learning_migration_reversible(self) -> None:
        """learning 0009 - 包含 token_count AlterField，应可逆。"""
        mod = self._load_migration(
            'learning',
            '0009_alter_workflowquestion_unique_together_and_more',
        )
        for op in mod.Migration.operations:
            self.assertTrue(
                op.reversible,
                f'learning 0009 操作 {op} 应可逆',
            )


if __name__ == '__main__':
    unittest.main()
