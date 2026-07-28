"""自定义工具审核与限流单元测试（Task 3.2 / 3.3）

覆盖 spec 1.3 节工具审核流程：
    1. CustomTool 模型包含 approval_status 字段（默认 pending）
    2. CustomTool.is_effective 仅在 approval_status='approved' + status='active' 时为 True
    3. ToolUploadView 配置 SensitiveOperationRateThrottle
    4. ToolUploadView 创建的工具默认 approval_status='pending'
    5. _load_custom_tools_for_user 仅加载 approval_status='approved' 的工具

设计说明：
    - 使用 mock + 单元测试，避免依赖测试数据库
      （项目当前 approvals.0009_add_user_field 迁移存在依赖问题）
    - 字段配置测试通过 _meta 直接验证

运行方式:
    cd d:\\programming\\langchain\\langchain_xm\\backend\\Django_xm
    conda activate langchain_xm
    python -m pytest Django_xm/apps/tools/tests/test_custom_tool_approval.py -v
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

# Django 环境初始化
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.dev")
import django
import django.apps

if not django.apps.apps.ready:
    django.setup()

from rest_framework.test import APIRequestFactory

from Django_xm.apps.core.throttling import SensitiveOperationRateThrottle
from Django_xm.apps.tools.models import CustomTool
from Django_xm.apps.tools.views_mcp import ToolUploadView

# ============================================================================
# Task 3.2a：CustomTool.approval_status 字段配置
# ============================================================================

class CustomToolApprovalStatusFieldTests(unittest.TestCase):
    """验证 CustomTool 模型的 approval_status 字段配置"""

    def test_field_exists(self):
        """CustomTool 必须包含 approval_status 字段"""
        field_names = {f.name for f in CustomTool._meta.get_fields()}
        self.assertIn(
            "approval_status", field_names,
            "CustomTool 必须有 approval_status 字段",
        )

    def test_field_default_is_pending(self):
        """approval_status 默认值为 'pending'"""
        field = CustomTool._meta.get_field("approval_status")
        self.assertEqual(field.default, "pending")

    def test_field_max_length_20(self):
        """approval_status max_length=20"""
        field = CustomTool._meta.get_field("approval_status")
        self.assertEqual(field.max_length, 20)

    def test_field_choices(self):
        """approval_status choices 必须为 pending/approved/rejected"""
        field = CustomTool._meta.get_field("approval_status")
        choices_keys = {c[0] for c in field.choices}
        self.assertEqual(
            choices_keys, {"pending", "approved", "rejected"},
            f"approval_status choices 应为 pending/approved/rejected, actual={choices_keys}",
        )

    def test_field_db_index(self):
        """approval_status 必须建索引（db_index=True）"""
        field = CustomTool._meta.get_field("approval_status")
        self.assertTrue(field.db_index, "approval_status 应建索引")

    def test_approval_status_textchoices(self):
        """CustomTool.ApprovalStatus 必须有 PENDING/APPROVED/REJECTED 三个枚举"""
        self.assertEqual(CustomTool.ApprovalStatus.PENDING, "pending")
        self.assertEqual(CustomTool.ApprovalStatus.APPROVED, "approved")
        self.assertEqual(CustomTool.ApprovalStatus.REJECTED, "rejected")


class CustomToolIsEffectiveTests(unittest.TestCase):
    """验证 is_effective 属性的判定逻辑"""

    def _make_tool(self, approval_status: str, status: str = "active") -> CustomTool:
        """构造未持久化的 CustomTool 实例"""
        tool = CustomTool(
            name="test_tool",
            code="@tool\ndef test_tool(): pass",
            approval_status=approval_status,
            status=status,
        )
        return tool

    def test_pending_not_effective(self):
        """pending 状态工具不生效"""
        tool = self._make_tool(approval_status="pending", status="active")
        self.assertFalse(tool.is_effective)

    def test_approved_active_is_effective(self):
        """approved + active 工具生效"""
        tool = self._make_tool(approval_status="approved", status="active")
        self.assertTrue(tool.is_effective)

    def test_approved_disabled_not_effective(self):
        """approved + disabled 工具不生效"""
        tool = self._make_tool(approval_status="approved", status="disabled")
        self.assertFalse(tool.is_effective)

    def test_rejected_not_effective(self):
        """rejected 状态工具不生效"""
        tool = self._make_tool(approval_status="rejected", status="active")
        self.assertFalse(tool.is_effective)


# ============================================================================
# Task 3.2b：ToolUploadView throttle 配置
# ============================================================================

class ToolUploadViewThrottleTests(unittest.TestCase):
    """验证 ToolUploadView 配置了 SensitiveOperationRateThrottle"""

    def test_throttle_classes_includes_sensitive(self):
        """ToolUploadView.throttle_classes 必须包含 SensitiveOperationRateThrottle"""
        self.assertIn(
            SensitiveOperationRateThrottle,
            ToolUploadView.throttle_classes,
            "ToolUploadView 必须配置 SensitiveOperationRateThrottle",
        )

    def test_throttle_classes_not_empty(self):
        """ToolUploadView.throttle_classes 不能为空"""
        self.assertGreaterEqual(
            len(ToolUploadView.throttle_classes), 1,
            "ToolUploadView 至少配置一个 throttle",
        )


# ============================================================================
# Task 3.2a/b：ToolUploadView 创建工具时设置 approval_status='pending'
# ============================================================================

class ToolUploadViewApprovalTests(unittest.TestCase):
    """验证 ToolUploadView 上传工具时默认 approval_status='pending'

    注意：views_mcp.py 的 ToolUploadView.post 使用局部导入
        (from Django_xm.apps.tools.models import CustomTool, ToolCategory)
    因此 patch 目标必须是原始模块路径（models / serializers），
    而非 views_mcp 模块（views_mcp 上无 CustomTool/ToolCategory 属性）。
    """

    def setUp(self):
        self.factory = APIRequestFactory()
        self.user = MagicMock()
        self.user.id = 1
        self.user.pk = 1
        self.user.is_authenticated = True
        self.user.is_staff = False
        self.user.username = "testuser"

    def _make_post_request(self, payload: dict):
        request = self.factory.post(
            "/api/v1/tools/upload/",
            data=payload,
            format="json",
        )
        request.user = self.user
        return request

    def test_new_tool_default_approval_pending(self):
        """新上传工具的 approval_status 必须为 'pending'"""
        # Mock CustomTool.objects.create 捕获传入参数
        captured_kwargs = {}

        def capture_create(**kwargs):
            for k, v in kwargs.items():
                captured_kwargs[k] = v
            mock_obj = MagicMock()
            mock_obj.id = 1
            mock_obj.name = kwargs.get("name", "test_tool")
            mock_obj.description = kwargs.get("description", "")
            mock_obj.status = kwargs.get("status", "active")
            mock_obj.approval_status = kwargs.get(
                "approval_status", CustomTool.ApprovalStatus.PENDING
            )
            return mock_obj

        # 构造合法的 @tool 代码
        valid_code = """
from langchain_core.tools import tool

@tool
def my_test_tool(query: str) -> str:
    \"\"\"测试工具\"\"\"
    return f"result: {query}"
"""

        # patch 原始模块路径（views_mcp.post 内部使用局部导入）
        # 同时 patch ToolUploadView.throttle_classes 以跳过限流（避免测试间缓存污染）
        with patch(
            "Django_xm.apps.tools.models.CustomTool"
        ) as mock_custom_tool_cls, patch(
            "Django_xm.apps.tools.models.ToolCategory"
        ) as mock_category_cls, patch(
            "Django_xm.apps.tools.serializers.McpToolUploadSerializer"
        ) as mock_serializer_cls, patch.object(
            ToolUploadView, "throttle_classes", []
        ):
            # Mock serializer
            mock_serializer = MagicMock()
            mock_serializer.is_valid.return_value = True
            mock_serializer.validated_data = {
                "name": "my_test_tool",
                "code": valid_code,
                "description": "测试工具",
                "category": "general",
            }
            mock_serializer_cls.return_value = mock_serializer

            # Mock category
            mock_category = MagicMock()
            mock_category_cls.objects.get.return_value = mock_category

            # Mock CustomTool.objects 链式调用
            mock_manager = MagicMock()
            mock_manager.create.side_effect = capture_create
            # filter(user=..., name=...).exists() 必须返回 False（工具未存在）
            mock_exists_qs = MagicMock()
            mock_exists_qs.exists.return_value = False
            mock_manager.filter.return_value = mock_exists_qs
            mock_custom_tool_cls.objects = mock_manager
            mock_custom_tool_cls.ApprovalStatus = CustomTool.ApprovalStatus

            request = self._make_post_request({
                "name": "my_test_tool",
                "code": valid_code,
                "description": "测试工具",
                "category": "general",
            })
            response = ToolUploadView.as_view()(request)
            response.render()

        # 验证 create 被调用
        self.assertTrue(
            mock_manager.create.called,
            "CustomTool.objects.create 必须被调用",
        )
        # 验证 approval_status 未显式设置（使用默认值 'pending'）
        self.assertNotIn(
            "approval_status", captured_kwargs,
            "ToolUploadView 不应显式设置 approval_status（应使用模型默认值 'pending'）",
        )
        # 验证响应成功
        self.assertEqual(
            response.status_code, 200,
            f"工具上传应成功，actual={response.status_code}, body={response.content[:300]}",
        )


# ============================================================================
# Task 3.2a：_load_custom_tools_for_user 仅加载 approved 工具
# ============================================================================

class LoadCustomToolsApprovalFilterTests(unittest.TestCase):
    """验证 _load_custom_tools_for_user 仅加载 approval_status='approved' 的工具"""

    def test_pending_tool_not_loaded(self):
        """pending 状态工具不被加载"""
        from Django_xm.apps.tools import _load_custom_tools_for_user

        # Mock CustomTool.objects.filter 链式调用
        mock_pending = MagicMock()
        mock_pending.name = "pending_tool"
        mock_approved = MagicMock()
        mock_approved.name = "approved_tool"

        mock_qs = MagicMock()
        mock_qs.filter.return_value = mock_qs  # name__in=selected_names 链式
        mock_qs.__iter__ = MagicMock(return_value=iter([]))  # 空结果（pending 被过滤掉）

        with patch(
            "Django_xm.apps.tools.models.CustomTool"
        ) as mock_custom_tool_cls:
            mock_custom_tool_cls.ApprovalStatus = CustomTool.ApprovalStatus
            mock_manager = MagicMock()
            mock_manager.filter.return_value = mock_qs
            mock_custom_tool_cls.objects = mock_manager

            _load_custom_tools_for_user(user_id=1)

        # 验证 filter 调用包含 approval_status=approved
        filter_call = mock_manager.filter.call_args
        self.assertIsNotNone(filter_call, "CustomTool.objects.filter 必须被调用")
        # 验证关键字参数包含 approval_status
        kwargs = filter_call.kwargs
        self.assertIn("approval_status", kwargs)
        self.assertEqual(
            kwargs["approval_status"],
            CustomTool.ApprovalStatus.APPROVED,
            "必须按 approval_status=approved 过滤",
        )

    def test_approved_tool_loaded(self):
        """approved 工具可被加载"""
        from Django_xm.apps.tools import _load_custom_tools_for_user

        # 构造一个 mock tool_obj
        mock_tool_obj = MagicMock()
        mock_tool_obj.name = "approved_tool"
        mock_tool_obj.approval_status = "approved"

        mock_qs = MagicMock()
        mock_qs.filter.return_value = mock_qs
        mock_qs.__iter__ = MagicMock(return_value=iter([mock_tool_obj]))

        with patch(
            "Django_xm.apps.tools.models.CustomTool"
        ) as mock_custom_tool_cls, patch(
            "Django_xm.apps.tools._instantiate_custom_tool"
        ) as mock_instantiate:
            mock_custom_tool_cls.ApprovalStatus = CustomTool.ApprovalStatus
            mock_manager = MagicMock()
            mock_manager.filter.return_value = mock_qs
            mock_custom_tool_cls.objects = mock_manager

            mock_instantiate.return_value = MagicMock(name="loaded_tool")

            tools = _load_custom_tools_for_user(user_id=1)

        self.assertEqual(
            len(tools), 1,
            f"应加载 1 个 approved 工具, actual={len(tools)}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
