"""approval_service sync/async 薄壳收敛（B⑪）表征测试。

收敛方案（仅整段复制的两对合并，有真差异者保留双实现）：
- _resolve_remaining_pending_count_async → 薄壳委托 _resolve_remaining_pending_count_sync
  （sync_to_async 线程执行，修复 async context 直接调 sync ORM 的报错语义不变）；
- _build_approval_event_params_async → 薄壳委托 _build_approval_event_params；
- _broadcast_approval_changed_async / persist_and_broadcast_async 保留双实现
  （真差异：事件发布必须在当前事件循环内 await 以保持 per-channel seq 顺序）。

本测试锁定薄壳行为：各分支输出与同步核心一致、参数透传一致。

mock 边界：_remaining_pending_condition / _count_remaining_pending /
_build_approval_event_params 均为运行时全局查找（薄壳可被替换验证），
不触 DB；approval 用 MagicMock 替身。

运行（backend/Django_xm 目录，conda env langchain_xm）：
    python -m unittest Django_xm.apps.approvals.tests.test_sync_async_shell_convergence
"""

import os
import unittest
from unittest import mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.test")
import django

django.setup()

from Django_xm.apps.approvals.services import approval_service

_SVC = "Django_xm.apps.approvals.services.approval_service"


class ResolveRemainingPendingCountShellTests(unittest.IsolatedAsyncioTestCase):
    """对 1 表征：async 薄壳与同步核心在各分支输出一致。"""

    async def test_count_branch_async_matches_sync(self):
        """APPROVED + 批次场景：condition 命中，两版本返回同一 count。"""
        approval = mock.MagicMock(interrupt_id="i1")
        with (
            mock.patch(
                f"{_SVC}._remaining_pending_condition", return_value=("gid-1", "i1")
            ) as condition,
            mock.patch(f"{_SVC}._count_remaining_pending", return_value=3) as count_fn,
        ):
            sync_result = approval_service._resolve_remaining_pending_count_sync(approval, "approved")
            async_result = await approval_service._resolve_remaining_pending_count_async(approval, "approved")

        self.assertEqual(sync_result, 3)
        self.assertEqual(async_result, 3)
        # 薄壳委托同步核心 → condition 与 ORM count 均被同步核心消费一次
        self.assertEqual(condition.call_count, 2)
        self.assertEqual(count_fn.call_count, 2)
        count_fn.assert_called_with("gid-1", "i1")

    async def test_unset_branch_async_matches_sync(self):
        """非 APPROVED / 非批次场景：condition 返回 None，两版本都返回 _UNSET，不触 ORM。"""
        approval = mock.MagicMock(interrupt_id="i1")
        with (
            mock.patch(f"{_SVC}._remaining_pending_condition", return_value=None),
            mock.patch(f"{_SVC}._count_remaining_pending") as count_fn,
        ):
            sync_result = approval_service._resolve_remaining_pending_count_sync(approval, "pending")
            async_result = await approval_service._resolve_remaining_pending_count_async(approval, "pending")

        self.assertIs(sync_result, approval_service._UNSET)
        self.assertIs(async_result, approval_service._UNSET)
        count_fn.assert_not_called()

    async def test_count_failure_falls_back_to_zero_in_both(self):
        """ORM count 失败：两版本都回退 0（保证字段不缺失的既有语义）。"""
        approval = mock.MagicMock(interrupt_id="i1")
        with (
            mock.patch(
                f"{_SVC}._remaining_pending_condition", return_value=("gid-1", "i1")
            ),
            mock.patch(f"{_SVC}._count_remaining_pending", side_effect=RuntimeError("db down")),
        ):
            sync_result = approval_service._resolve_remaining_pending_count_sync(approval, "approved")
            async_result = await approval_service._resolve_remaining_pending_count_async(approval, "approved")

        self.assertEqual(sync_result, 0)
        self.assertEqual(async_result, 0)


class BuildApprovalEventParamsShellTests(unittest.IsolatedAsyncioTestCase):
    """对 2 表征：async 薄壳透传参数给同步核心，输出一致。"""

    async def test_async_shell_delegates_with_same_args(self):
        """薄壳以相同 (approval, state, extra) 调用同步核心并原样返回其结果。"""
        approval = mock.MagicMock(interrupt_id="i1")
        extra = {"reason": "batch"}
        sentinel = {"event_type": "SENTINEL"}
        with mock.patch(f"{_SVC}._build_approval_event_params", return_value=sentinel) as core:
            async_result = await approval_service._build_approval_event_params_async(approval, "approved", extra)

        core.assert_called_once_with(approval, "approved", extra)
        self.assertIs(async_result, sentinel)

    async def test_async_shell_defaults_extra_to_none(self):
        """extra 缺省时薄壳以 None 透传（与同步核心默认签名一致）。"""
        approval = mock.MagicMock(interrupt_id="i1")
        with mock.patch(f"{_SVC}._build_approval_event_params", return_value={}) as core:
            await approval_service._build_approval_event_params_async(approval, "pending")

        core.assert_called_once_with(approval, "pending", None)


class DualImplementationContractTests(unittest.TestCase):
    """保留对契约：真差异双实现的符号仍在，不可被误删。"""

    def test_retained_pair_symbols_exist(self):
        for name in (
            "_broadcast_approval_changed",
            "_broadcast_approval_changed_async",
            "persist_and_broadcast",
            "persist_and_broadcast_async",
        ):
            self.assertTrue(hasattr(approval_service, name), f"保留的双实现符号缺失: {name}")


if __name__ == "__main__":
    unittest.main()
