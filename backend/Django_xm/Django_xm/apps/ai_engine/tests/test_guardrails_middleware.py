"""Guardrails 中间件单元测试（覆盖 ``guardrails/middleware.py``）

验证目标：
1. ``HumanInTheLoopMiddleware``（spec 中称为 ``ApprovalMiddleware``）触发/放行/拒绝路径
   - 不需要审批的工具 → 直接放行（不调用 callback）
   - 需要审批但未配置 callback → 拒绝（返回 ToolMessage）
   - 需要审批且 callback 返回 True → 放行（调用 handler）
   - 需要审批且 callback 返回 False → 拒绝（返回 ToolMessage）
   - 异步路径 ``awrap_tool_call`` 同等覆盖
2. ``create_human_in_the_loop_middleware`` / ``build_middleware_stack`` 工厂函数
3. ``GuardrailsMiddleware.wrap_tool_call`` 危险工具拦截（默认 ``DANGEROUS_TOOLS``）
4. ``_content_to_str`` 多模态消息文本提取
5. ``RateLimitMiddleware._check_rate_limit`` 速率超限触发
   - ``graceful_degradation=True`` → 返回 reason 字符串
   - ``graceful_degradation=False`` → 抛 ``RuntimeError``
6. ``RateLimitMiddleware._args_fingerprint`` 边界条件
7. ``PIIMiddleware.wrap_model_call`` PII 检测与脱敏
   - ``mask_pii=True`` → 替换消息内容
   - ``reject_on_pii=True`` → 抛 ``ValueError``

源码注记：
- spec 中的 ``ApprovalMiddleware`` 实际指的是 ``guardrails/middleware.py`` 中的
  ``HumanInTheLoopMiddleware``（该文件无 ``ApprovalMiddleware`` 类）。
  ``ApprovalMiddleware`` 真身位于 ``agent_hub/approval/middleware.py``，
  已由 ``tests/test_approval_middleware_integration.py`` 覆盖。

mock 策略：
- ``ToolCallRequest`` / ``ModelRequest`` / ``Runtime`` / ``AgentState`` 均为
  LangChain v1.2+ 的 Pydantic 模型，构造复杂，使用 ``MagicMock`` + 真实 dict 模拟
- ``handler`` callable 使用 ``MagicMock`` 或 ``AsyncMock``，返回 ``ToolMessage`` 或受控值
- 不依赖真实 LLM / Redis / Celery / DB
- ``InputValidator`` / ``OutputValidator`` 使用真实实例（纯 Python 逻辑，无外部依赖）

运行方式：
    cd d:\\programming\\langchain\\langchain_xm\\backend\\Django_xm
    conda activate langchain_xm
    python -m pytest Django_xm/apps/ai_engine/tests/test_guardrails_middleware.py -v
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Django 环境初始化（兼容 pytest 和 unittest 直接运行）
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Django_xm.settings.dev")
import django
import django.apps

if not django.apps.apps.ready:
    django.setup()

from langchain_core.messages import HumanMessage, ToolMessage

from Django_xm.apps.ai_engine.guardrails.content_filters import ContentSafetyLevel
from Django_xm.apps.ai_engine.guardrails.middleware import (
    GuardrailsMiddleware,
    HumanInTheLoopMiddleware,
    PIIMiddleware,
    RateLimitMiddleware,
    _content_to_str,
    build_middleware_stack,
    create_human_in_the_loop_middleware,
)
from Django_xm.common.risk_levels import DANGEROUS_TOOLS

# ============================================================================
# Mock 工厂
# ============================================================================


def make_tool_call_request(
    name: str = "search",
    args: dict | None = None,
    call_id: str = "tc-1",
) -> MagicMock:
    """构造模拟的 ToolCallRequest。

    中间件访问 ``tool_call.tool_call.get("name"/"args"/"id", default)``，
    因此 ``.tool_call`` 必须是真实 dict（不能用 MagicMock，否则 .get 返回 MagicMock）。
    """
    req = MagicMock(name=f"ToolCallRequest({name})")
    req.tool_call = {
        "name": name,
        "args": args if args is not None else {},
        "id": call_id,
    }
    return req


def make_model_request(messages: list | None = None) -> MagicMock:
    """构造模拟的 ModelRequest。

    中间件访问 ``request.state.get("messages", [])``，
    因此 ``.state`` 必须是真实 dict。
    """
    req = MagicMock(name="ModelRequest")
    req.state = {"messages": messages if messages is not None else []}
    return req


# ============================================================================
# HumanInTheLoopMiddleware（spec: ApprovalMiddleware 触发/放行/拒绝路径）
# ============================================================================


class HumanInTheLoopMiddlewareTestCase(unittest.TestCase):
    """``HumanInTheLoopMiddleware`` 同步路径覆盖：触发 / 放行 / 拒绝。"""

    def test_passes_through_when_tool_not_requiring_approval(self) -> None:
        """非审批工具集 → 直接调用 handler，不触发审批 callback。"""
        callback = MagicMock(return_value=True)
        mw = HumanInTheLoopMiddleware(on_approval_request=callback)
        # 默认审批集为 {fs_write_file, bash_execute, repl_execute, notebook_edit, shell_exec, execute_code}
        tool_call = make_tool_call_request(name="search_web", args={"q": "test"})
        handler = MagicMock(return_value=ToolMessage(content="ok", tool_call_id="tc-1"))

        result = mw.wrap_tool_call(tool_call, handler)

        self.assertIs(result, handler.return_value)
        handler.assert_called_once_with(tool_call)
        # callback 不应被调用（search_web 不在审批集）
        callback.assert_not_called()

    def test_blocks_when_approval_callback_not_configured(self) -> None:
        """审批工具 + 未配置 callback → 返回拒绝 ToolMessage，不调用 handler。"""
        mw = HumanInTheLoopMiddleware(on_approval_request=None)
        tool_call = make_tool_call_request(name="shell_exec", args={"cmd": "ls"})
        handler = MagicMock()

        result = mw.wrap_tool_call(tool_call, handler)

        self.assertIsInstance(result, ToolMessage)
        assert isinstance(result, ToolMessage)  # type narrowing for mypy
        self.assertIn("被拒绝", result.content)
        self.assertIn("未配置审批回调", result.content)
        self.assertEqual(result.tool_call_id, "tc-1")
        # handler 不应被调用
        handler.assert_not_called()

    def test_passes_through_when_callback_approves(self) -> None:
        """审批工具 + callback 返回 True → 调用 handler 放行。"""
        callback = MagicMock(return_value=True)
        mw = HumanInTheLoopMiddleware(on_approval_request=callback)
        tool_call = make_tool_call_request(name="bash_execute", args={"cmd": "echo hi"})
        expected = ToolMessage(content="executed", tool_call_id="tc-1")
        handler = MagicMock(return_value=expected)

        result = mw.wrap_tool_call(tool_call, handler)

        self.assertIs(result, expected)
        handler.assert_called_once_with(tool_call)
        callback.assert_called_once_with("bash_execute", {"cmd": "echo hi"})

    def test_blocks_when_callback_rejects(self) -> None:
        """审批工具 + callback 返回 False → 返回拒绝 ToolMessage。"""
        callback = MagicMock(return_value=False)
        mw = HumanInTheLoopMiddleware(on_approval_request=callback)
        tool_call = make_tool_call_request(name="repl_execute", args={"code": "x=1"})
        handler = MagicMock()

        result = mw.wrap_tool_call(tool_call, handler)

        self.assertIsInstance(result, ToolMessage)
        assert isinstance(result, ToolMessage)  # type narrowing for mypy
        self.assertIn("人工拒绝", result.content)
        self.assertEqual(result.tool_call_id, "tc-1")
        handler.assert_not_called()

    def test_custom_approval_set_overrides_default(self) -> None:
        """自定义 tools_requiring_approval 覆盖默认集合（如把 search 也加入审批）。"""
        callback = MagicMock(return_value=True)
        mw = HumanInTheLoopMiddleware(
            tools_requiring_approval={"search"},
            on_approval_request=callback,
        )
        tool_call = make_tool_call_request(name="search", args={"q": "secret"})
        handler = MagicMock(return_value=ToolMessage(content="ok", tool_call_id="tc-1"))

        mw.wrap_tool_call(tool_call, handler)

        # search 不在默认集合，但自定义集合包含它 → 触发 callback
        callback.assert_called_once_with("search", {"q": "secret"})


class HumanInTheLoopMiddlewareAsyncTestCase(unittest.IsolatedAsyncioTestCase):
    """``HumanInTheLoopMiddleware.awrap_tool_call`` 异步路径覆盖。"""

    async def test_async_passes_through_when_not_requiring_approval(self) -> None:
        """异步：非审批工具 → 直接 await handler。"""
        mw = HumanInTheLoopMiddleware(on_approval_request=MagicMock())
        tool_call = make_tool_call_request(name="search", args={})
        expected = ToolMessage(content="ok", tool_call_id="tc-1")
        handler = AsyncMock(return_value=expected)

        result = await mw.awrap_tool_call(tool_call, handler)

        self.assertIs(result, expected)
        handler.assert_awaited_once_with(tool_call)

    async def test_async_blocks_when_callback_rejects(self) -> None:
        """异步：审批工具 + callback 返回 False → 返回拒绝 ToolMessage，不调用 handler。"""
        callback = MagicMock(return_value=False)
        mw = HumanInTheLoopMiddleware(on_approval_request=callback)
        tool_call = make_tool_call_request(name="shell_exec", args={"cmd": "rm -rf /"})
        handler = AsyncMock()

        result = await mw.awrap_tool_call(tool_call, handler)

        self.assertIsInstance(result, ToolMessage)
        assert isinstance(result, ToolMessage)  # type narrowing for mypy
        self.assertIn("人工拒绝", result.content)
        handler.assert_not_awaited()

    async def test_async_passes_through_when_callback_approves(self) -> None:
        """异步：审批工具 + callback 返回 True → await handler 放行。"""
        callback = MagicMock(return_value=True)
        mw = HumanInTheLoopMiddleware(on_approval_request=callback)
        tool_call = make_tool_call_request(name="execute_code", args={"code": "print(1)"})
        expected = ToolMessage(content="done", tool_call_id="tc-1")
        handler = AsyncMock(return_value=expected)

        result = await mw.awrap_tool_call(tool_call, handler)

        self.assertIs(result, expected)
        callback.assert_called_once_with("execute_code", {"code": "print(1)"})


# ============================================================================
# 工厂函数与栈构建
# ============================================================================


class FactoryFunctionsTestCase(unittest.TestCase):
    """``create_human_in_the_loop_middleware`` / ``build_middleware_stack``。"""

    def test_create_human_in_the_loop_middleware_returns_instance(self) -> None:
        """工厂函数返回 HumanInTheLoopMiddleware 实例并注入参数。"""
        callback = MagicMock(return_value=True)
        mw = create_human_in_the_loop_middleware(
            tools_requiring_approval={"dangerous_tool"},
            on_approval_request=callback,
        )

        self.assertIsInstance(mw, HumanInTheLoopMiddleware)
        self.assertEqual(mw.tools_requiring_approval, {"dangerous_tool"})
        self.assertIs(mw.on_approval_request, callback)

    def test_build_middleware_stack_includes_human_in_loop_when_enabled(self) -> None:
        """enable_human_in_loop=True → 栈中包含 HumanInTheLoopMiddleware。"""
        callback = MagicMock(return_value=True)
        stack = build_middleware_stack(
            enable_guardrails=False,
            enable_pii=False,
            enable_rate_limit=False,
            enable_human_in_loop=True,
            on_approval_request=callback,
        )

        # 栈中应至少包含一个 HumanInTheLoopMiddleware
        hitl = [m for m in stack if isinstance(m, HumanInTheLoopMiddleware)]
        self.assertEqual(len(hitl), 1)
        self.assertIs(hitl[0].on_approval_request, callback)

    def test_build_middleware_stack_excludes_human_in_loop_when_disabled(self) -> None:
        """enable_human_in_loop=False → 栈中不包含 HumanInTheLoopMiddleware。"""
        stack = build_middleware_stack(
            enable_guardrails=False,
            enable_pii=False,
            enable_rate_limit=False,
            enable_human_in_loop=False,
        )

        self.assertFalse(any(isinstance(m, HumanInTheLoopMiddleware) for m in stack))

    def test_build_middleware_stack_default_has_rate_limit_and_guardrails(self) -> None:
        """默认参数 → 栈包含 RateLimitMiddleware + GuardrailsMiddleware。"""
        stack = build_middleware_stack()

        self.assertTrue(any(isinstance(m, RateLimitMiddleware) for m in stack))
        self.assertTrue(any(isinstance(m, GuardrailsMiddleware) for m in stack))


# ============================================================================
# GuardrailsMiddleware：危险工具默认拦截
# ============================================================================


class GuardrailsMiddlewareDangerousToolsTestCase(unittest.TestCase):
    """``GuardrailsMiddleware.wrap_tool_call`` 危险工具拦截。"""

    def test_blocks_dangerous_tool_when_raise_on_error_true(self) -> None:
        """DANGEROUS_TOOLS 中的工具 + raise_on_error=True → 抛 ValueError。"""
        mw = GuardrailsMiddleware(raise_on_error=True)
        # 取 DANGEROUS_TOOLS 中任意一个
        dangerous = next(iter(DANGEROUS_TOOLS))
        tool_call = make_tool_call_request(name=dangerous, args={})
        handler = MagicMock()

        with self.assertRaises(ValueError) as ctx:
            mw.wrap_tool_call(tool_call, handler)

        self.assertIn(dangerous, str(ctx.exception))
        handler.assert_not_called()

    def test_returns_tool_message_when_raise_on_error_false(self) -> None:
        """DANGEROUS_TOOLS 中的工具 + raise_on_error=False → 返回拒绝 ToolMessage。"""
        mw = GuardrailsMiddleware(raise_on_error=False)
        dangerous = next(iter(DANGEROUS_TOOLS))
        tool_call = make_tool_call_request(name=dangerous, args={})
        handler = MagicMock()

        result = mw.wrap_tool_call(tool_call, handler)

        self.assertIsInstance(result, ToolMessage)
        assert isinstance(result, ToolMessage)  # type narrowing for mypy
        self.assertIn("被安全策略禁止", result.content)
        self.assertEqual(result.tool_call_id, "tc-1")
        handler.assert_not_called()


# ============================================================================
# _content_to_str 多模态文本提取
# ============================================================================


class ContentToStrTestCase(unittest.TestCase):
    """``_content_to_str`` 处理 str / list[str|dict] 多种格式。"""

    def test_returns_str_unchanged(self) -> None:
        """输入为 str → 原样返回。"""
        self.assertEqual(_content_to_str("hello"), "hello")

    def test_joins_list_of_strings(self) -> None:
        """输入为 str 列表 → 空格拼接。"""
        self.assertEqual(_content_to_str(["hello", "world"]), "hello world")

    def test_extracts_text_field_from_dict_items(self) -> None:
        """输入为 dict 列表 → 提取 text 字段后拼接。"""
        content = [
            {"type": "text", "text": "first"},
            {"type": "image", "text": "second"},
        ]
        self.assertEqual(_content_to_str(content), "first second")

    def test_handles_empty_list(self) -> None:
        """输入为空列表 → 返回空字符串。"""
        self.assertEqual(_content_to_str([]), "")

    def test_handles_mixed_str_and_dict_items(self) -> None:
        """混合 str + dict 列表 → 分别处理后拼接。"""
        content = ["pre", {"text": "post"}]
        self.assertEqual(_content_to_str(content), "pre post")


# ============================================================================
# RateLimitMiddleware：速率限制
# ============================================================================


class RateLimitMiddlewareTestCase(unittest.TestCase):
    """``RateLimitMiddleware._check_rate_limit`` 与辅助方法。"""

    def test_rate_limit_returns_reason_in_graceful_mode(self) -> None:
        """调用频率超限 + graceful_degradation=True → 返回 reason 字符串。"""
        # 设置 max_calls_per_second=1，连续两次调用必然超限
        mw = RateLimitMiddleware(
            max_calls_per_second=1,
            max_total_calls=1000,
            graceful_degradation=True,
        )
        first = mw._check_rate_limit("model")
        second = mw._check_rate_limit("model")

        self.assertIsNone(first)  # 第一次不超限
        self.assertIsNotNone(second)  # 第二次超限
        assert second is not None  # type narrowing for mypy
        self.assertIn("model调用频率超过", second)
        self.assertIn("1/s", second)

    def test_rate_limit_raises_runtime_error_in_strict_mode(self) -> None:
        """调用频率超限 + graceful_degradation=False → 抛 RuntimeError。"""
        mw = RateLimitMiddleware(
            max_calls_per_second=1,
            max_total_calls=1000,
            graceful_degradation=False,
        )
        mw._check_rate_limit("model")  # 第一次通过

        with self.assertRaises(RuntimeError) as ctx:
            mw._check_rate_limit("model")  # 第二次超限 → 抛异常

        self.assertIn("速率限制", str(ctx.exception))

    def test_total_calls_limit_triggers_reason(self) -> None:
        """总调用次数超 max_total_calls → 返回 reason。"""
        mw = RateLimitMiddleware(
            max_calls_per_second=1000,  # 不限速
            max_total_calls=2,
            graceful_degradation=True,
        )
        # 模拟已经累计 2 次调用
        mw._model_call_count = 1
        mw._tool_call_count = 1

        reason = mw._check_rate_limit("model")

        self.assertIsNotNone(reason)
        assert reason is not None  # type narrowing for mypy
        self.assertIn("总调用次数", reason)
        self.assertIn("超过2", reason)

    def test_args_fingerprint_handles_none(self) -> None:
        """``_args_fingerprint(None)`` → 空字符串。"""
        self.assertEqual(RateLimitMiddleware._args_fingerprint(None), "")

    def test_args_fingerprint_stable_for_same_dict(self) -> None:
        """相同 dict（顺序不同）→ 相同 fingerprint（基于 sort_keys 序列化）。"""
        args_a = {"a": 1, "b": 2}
        args_b = {"b": 2, "a": 1}
        fp_a = RateLimitMiddleware._args_fingerprint(args_a)
        fp_b = RateLimitMiddleware._args_fingerprint(args_b)

        self.assertEqual(fp_a, fp_b)
        self.assertEqual(len(fp_a), 12)  # md5[:12]

    def test_args_preview_truncates_long_values(self) -> None:
        """长字符串值 → 截断并加 ``...`` 后缀。"""
        long_value = "x" * 100
        preview = RateLimitMiddleware._args_preview({"k": long_value})

        self.assertIn("k=", preview)
        self.assertIn("...", preview)

    def test_args_preview_for_none_returns_empty(self) -> None:
        """``_args_preview(None)`` → 空字符串。"""
        self.assertEqual(RateLimitMiddleware._args_preview(None), "")


# ============================================================================
# PIIMiddleware：PII 检测与脱敏
# ============================================================================


class PIIMiddlewareTestCase(unittest.TestCase):
    """``PIIMiddleware.wrap_model_call`` PII 处理。

    修复说明（issue_round3_pii_middleware_ineffective）：
    原 middleware 用 ``not filter_result.is_safe`` 判定 PII 命中，但 ``is_safe``
    仅在 ``UNSAFE`` 时为 False，而 PII 检测命中只置 ``WARNING``，且 middleware
    禁用了 injection/content_safety 检测（唯一会置 ``UNSAFE`` 的路径），导致
    mask_pii / reject_on_pii 对纯 PII 输入失效。

    修复后改用 ``safety_level != SAFE`` 判定，覆盖 WARNING（PII）与 UNSAFE 两种
    命中态。本测试验证修复后的正确行为：纯 PII 输入应触发脱敏/拒绝。
    """

    def test_pii_alone_gets_masked_due_to_warning_level(self) -> None:
        """纯 PII 输入 → safety_level=WARNING → mask_pii=True 触发脱敏。

        修复后：``safety_level != SAFE`` 覆盖 WARNING，middleware 应使用
        ContentFilter 的 filtered_content 替换原始消息。
        """
        mw = PIIMiddleware(mask_pii=True, reject_on_pii=False)
        original_text = "我的手机号是 13800138000 请联系我"
        request = make_model_request(messages=[HumanMessage(content=original_text)])
        handler = MagicMock(return_value=MagicMock())

        mw.wrap_model_call(request, handler)

        # handler 被调用
        handler.assert_called_once_with(request)
        # 消息应被替换为脱敏内容（13800138000 → 138****8000）
        self.assertEqual(
            request.state["messages"][0].content,
            "我的手机号是 138****8000 请联系我",
        )

    def test_pii_alone_raises_when_reject_on_pii_true(self) -> None:
        """纯 PII 输入 + reject_on_pii=True → 抛 ValueError。

        修复后：``safety_level != SAFE`` 覆盖 WARNING，reject_on_pii 应阻止
        handler 调用并抛出 ValueError。
        """
        mw = PIIMiddleware(mask_pii=False, reject_on_pii=True)
        request = make_model_request(messages=[HumanMessage(content="我的邮箱是 test@example.com")])
        handler = MagicMock(return_value=MagicMock())

        with self.assertRaises(ValueError) as ctx:
            mw.wrap_model_call(request, handler)

        self.assertIn("个人身份信息", str(ctx.exception))
        # handler 不应被调用
        handler.assert_not_called()

    def test_masks_message_when_filter_returns_unsafe(self) -> None:
        """mock _content_filter 返回 UNSAFE → middleware 进入脱敏分支。

        验证 middleware 自身逻辑：当 ContentFilter 判定 UNSAFE 且 mask_pii=True 时，
        middleware 应使用 filter 的 filtered_content 替换原始消息。
        """
        mw = PIIMiddleware(mask_pii=True, reject_on_pii=False)
        # 构造 UNSAFE filter_result：safety_level=UNSAFE，filtered_content 为脱敏后文本
        mock_filter_result = MagicMock()
        mock_filter_result.safety_level = ContentSafetyLevel.UNSAFE
        mock_filter_result.filtered_content = "我的手机号是 138****8000 请联系我"
        mock_filter_result.issues = ["检测到个人敏感信息: phone"]

        original_text = "我的手机号是 13800138000 请联系我"
        request = make_model_request(messages=[HumanMessage(content=original_text)])
        handler = MagicMock(return_value=MagicMock())

        with patch.object(mw._content_filter, "filter_input", return_value=mock_filter_result):
            mw.wrap_model_call(request, handler)

        # 消息应被替换为脱敏内容
        self.assertEqual(
            request.state["messages"][0].content,
            "我的手机号是 138****8000 请联系我",
        )
        handler.assert_called_once_with(request)

    def test_raises_value_error_when_filter_unsafe_and_reject_enabled(self) -> None:
        """mock _content_filter 返回 UNSAFE + reject_on_pii=True → 抛 ValueError。

        验证 middleware 自身逻辑：当 ContentFilter 判定 UNSAFE 且 reject_on_pii=True 时，
        middleware 应抛 ValueError 并阻止 handler 调用。
        """
        mw = PIIMiddleware(mask_pii=False, reject_on_pii=True)
        mock_filter_result = MagicMock()
        mock_filter_result.safety_level = ContentSafetyLevel.UNSAFE
        mock_filter_result.filtered_content = ""
        mock_filter_result.issues = ["检测到个人敏感信息: phone"]

        request = make_model_request(messages=[HumanMessage(content="我的手机号是 13800138000")])
        handler = MagicMock()

        with (
            patch.object(mw._content_filter, "filter_input", return_value=mock_filter_result),
            self.assertRaises(ValueError) as ctx,
        ):
            mw.wrap_model_call(request, handler)

        self.assertIn("个人身份信息", str(ctx.exception))
        handler.assert_not_called()

    def test_passes_through_when_no_pii(self) -> None:
        """无 PII 的输入 → safety_level=SAFE → 直接调用 handler，不修改消息。"""
        mw = PIIMiddleware(mask_pii=True, reject_on_pii=True)
        original_text = "今天天气真好"
        request = make_model_request(messages=[HumanMessage(content=original_text)])
        expected = MagicMock()
        handler = MagicMock(return_value=expected)

        result = mw.wrap_model_call(request, handler)

        self.assertIs(result, expected)
        # 消息内容不变
        self.assertEqual(request.state["messages"][0].content, original_text)


if __name__ == "__main__":
    unittest.main()
