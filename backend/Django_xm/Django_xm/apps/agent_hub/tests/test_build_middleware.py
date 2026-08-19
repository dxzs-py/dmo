"""build_middleware 收敛点保底注入单元测试（spec: unify-context-injection）。

覆盖：
1. 空栈保底注入：capabilities=[] 控制 CapabilityRegistry 分支产出为空
   （不依赖 ai_engine 默认表），build_middleware 后栈中自动包含
   ApprovalMiddleware 与 ContextManagerMiddleware（追加在栈末，顺序
   approval → context）。
2. 幂等性：预置同类型实例的栈（config.middleware 显式栈）调用后不重复
   注入（各类型计数 == 1），在役路径零行为变化。
3. 注入失败降级：ContextManagerMiddleware 构造抛异常时 build_middleware
   不抛异常、栈仍正常返回，且 ApprovalMiddleware 保底不受影响
   （质量报告 lc-02 死路径防御 + spec 注入失败降级场景）。

运行：
    python manage.py test Django_xm.apps.agent_hub.tests.test_build_middleware
"""

import unittest
from unittest import mock

from Django_xm.apps.agent_hub.approval.middleware import ApprovalMiddleware
from Django_xm.apps.agent_hub.config import AgentConfig, AgentType
from Django_xm.apps.agent_hub.middleware import build_middleware
from Django_xm.apps.context_manager.middleware import ContextManagerMiddleware


def _make_config(**kwargs) -> AgentConfig:
    """构造聚焦保底段的轻量 AgentConfig。

    capabilities=[] 使 CapabilityRegistry 分支产出为空（聚焦保底段，
    不受 ai_engine 默认表影响）；middleware 默认 None（空显式栈）。
    """
    kwargs.setdefault("agent_type", AgentType.BASE)
    kwargs.setdefault("capabilities", [])
    kwargs.setdefault("middleware", None)
    return AgentConfig(**kwargs)


def _count_type(stack: list, cls: type) -> int:
    return sum(1 for m in stack if isinstance(m, cls))


class BuildMiddlewareFallbackTests(unittest.TestCase):
    """收敛点保底注入：空栈注入 / 幂等跳过 / 失败降级。"""

    def test_ensure_context_injects_when_missing(self):
        """空栈（capability 分支产出为空）时保底注入 Approval + Context，追加在栈末。"""
        config = _make_config(model_name="gpt-4o", user_id=1, session_id="thread-1")

        stack = build_middleware(config)

        self.assertIsInstance(stack, list)
        self.assertEqual(_count_type(stack, ContextManagerMiddleware), 1)
        self.assertEqual(_count_type(stack, ApprovalMiddleware), 1)
        # 保底追加位置在栈末（与 base/deep 现有手动 ensure 的追加位置等效）：
        # 先 approval 后 context
        self.assertIsInstance(stack[-2], ApprovalMiddleware)
        self.assertIsInstance(stack[-1], ContextManagerMiddleware)

    def test_ensure_idempotent_when_present(self):
        """栈中预置同类型实例后调用不重复注入（各计数 == 1），在役路径零行为变化。"""
        config = _make_config(
            middleware=[ContextManagerMiddleware(model_name="gpt-4o"), ApprovalMiddleware()]
        )

        stack = build_middleware(config)

        self.assertEqual(_count_type(stack, ContextManagerMiddleware), 1)
        self.assertEqual(_count_type(stack, ApprovalMiddleware), 1)
        # 预置实例保持原位（保底仅追加缺失项，不重排既有栈）
        self.assertIsInstance(stack[0], ContextManagerMiddleware)
        self.assertIsInstance(stack[1], ApprovalMiddleware)

    def test_injection_failure_degrades(self):
        """ContextManagerMiddleware 构造抛异常时降级：不抛异常、栈仍返回、approval 不受影响。"""
        config = _make_config(model_name="gpt-4o")

        with mock.patch(
            "Django_xm.apps.context_manager.middleware.ContextManagerMiddleware",
            side_effect=RuntimeError("模拟构造失败"),
        ):
            stack = build_middleware(config)

        self.assertIsInstance(stack, list)
        # context 注入失败被降级吞掉（栈中无实例），构建不失败
        self.assertEqual(_count_type(stack, ContextManagerMiddleware), 0)
        # approval 保底独立 try/except，不受 context 失败影响
        self.assertEqual(_count_type(stack, ApprovalMiddleware), 1)


if __name__ == "__main__":
    unittest.main()
