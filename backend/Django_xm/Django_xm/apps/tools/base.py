"""工具公共基类和混入

提供：
- AsyncToolMixin: 自动将同步 _run 包装为异步 _arun（使用 asyncio.to_thread）
- SafeConfigMixin: 安全获取 AI 引擎配置（降级到环境变量）
- interrupt_for_approval: 通用人工审批中断机制（任何工具可调用）
"""
import asyncio
import os
import logging
from typing import Any, Optional, Dict

logger = logging.getLogger(__name__)


class AsyncToolMixin:
    """异步工具混入

    自动将同步 _run 方法包装为真正的异步 _arun，
    使用 asyncio.to_thread 在线程池中执行，避免阻塞事件循环。

    用法：
        class MyTool(AsyncToolMixin, BaseTool):
            def _run(self, query: str) -> str:
                # 同步实现
                return result
            # 无需定义 _arun，自动从 _run 生成
    """

    def _arun(self, **kwargs: Any) -> Any:
        """真正的异步执行：在线程池中运行同步 _run"""
        # 获取 _run 方法的参数名列表，只传递 _run 接受的参数
        import inspect
        run_params = set(inspect.signature(self._run).parameters.keys())
        filtered_kwargs = {k: v for k, v in kwargs.items() if k in run_params}
        return asyncio.to_thread(self._run, **filtered_kwargs)


class SafeConfigMixin:
    """安全配置获取混入

    优先从 ai_engine.config.settings 获取配置，
    降级到环境变量，避免硬编码和循环导入。
    """

    @staticmethod
    def get_config(key: str, default: Any = None, env_key: str = '') -> Any:
        """安全获取配置

        Args:
            key: ai_engine.config.settings 中的属性名
            default: 默认值
            env_key: 环境变量名（默认与 key 相同，全大写）
        """
        try:
            from Django_xm.apps.ai_engine.config import settings
            value = getattr(settings, key, None)
            if value is not None:
                return value
        except ImportError:
            pass

        env = env_key or key.upper()
        return os.environ.get(env, default)


TOOL_METADATA_DEFAULTS = {
    "tier": "extended",
    "visibility": "selectable",
    "category": "other",
}

VALID_TOOL_TIERS = {"core", "standard", "extended"}
VALID_TOOL_VISIBILITIES = {"core", "switch", "selectable"}
VALID_TOOL_CATEGORIES = {"basic", "web_search", "web_fetch", "file", "weather", "agent", "todo", "translation", "system", "other"}

def validate_tool_metadata(metadata: dict) -> dict:
    merged = {**TOOL_METADATA_DEFAULTS, **(metadata or {})}
    if merged["tier"] not in VALID_TOOL_TIERS:
        raise ValueError(f"Invalid tool tier: {merged['tier']}")
    if merged["visibility"] not in VALID_TOOL_VISIBILITIES:
        raise ValueError(f"Invalid tool visibility: {merged['visibility']}")
    if merged["category"] not in VALID_TOOL_CATEGORIES:
        raise ValueError(f"Invalid tool category: {merged['category']}")
    return merged


# ── 通用人工审批中断机制 ──────────────────────────────────────────

class ApprovalAction:
    """审批动作类型"""
    CONFIRM = "confirm"           # 确认/取消（二元选择）
    CONFIRM_WITH_INPUT = "confirm_with_input"  # 确认 + 用户输入值


def interrupt_for_approval(
    tool_name: str,
    title: str,
    description: str,
    action: str = ApprovalAction.CONFIRM,
    *,
    operation: str = "",
    danger_level: str = "medium",
    input_placeholder: str = "",
    extra: Optional[Dict[str, Any]] = None,
    command: str = "",  # deprecated: 向后兼容，自动赋值给 operation
) -> Any:
    """通用人工审批中断函数

    任何 LangChain 工具、MCP 工具、Skill 工具都可以调用此函数，
    暂停 Agent 执行并等待用户确认。

    ⚠️ 重要：此函数必须在异步上下文（_arun）中调用，不能在 asyncio.to_thread
    的线程池中调用。原因是 LangGraph 的 interrupt() 依赖 ContextVar 传播上下文，
    而 asyncio.to_thread 会创建新的线程上下文，导致 interrupt 值丢失。

    正确用法：
        async def _arun(self, ...):
            approval = interrupt_for_approval(...)
            if approval is True:
                result = await asyncio.to_thread(实际执行函数, ...)

    错误用法：
        def _run(self, ...):
            approval = interrupt_for_approval(...)  # ❌ 在 asyncio.to_thread 中调用

    使用方式：
        approval = interrupt_for_approval(
            tool_name="shell_exec",
            title="确认执行命令",
            description="Agent 请求执行以下非白名单命令",
            operation="rm -rf /tmp/test",
        )
        if approval is True:
            # 用户确认
        else:
            # 用户拒绝

    Args:
        tool_name: 工具名称（如 "shell_exec", "fs_write_file" 等）
        title: 审批标题（前端显示）
        description: 审批描述（前端显示）
        action: 审批动作类型，默认 CONFIRM（确认/取消）
        operation: 待审批的操作描述（前端代码块显示），如 shell 命令、文件路径等
        danger_level: 危险等级 "low"/"medium"/"high"
        input_placeholder: CONFIRM_WITH_INPUT 模式下输入框的占位文本
        extra: 工具自定义数据（随 interrupt 传递，resume 时原样返回）
        command: deprecated，请使用 operation（自动赋值给 operation）

    Returns:
        True: 用户确认
        False/其他: 用户拒绝
        str: 当 action=CONFIRM_WITH_INPUT 时，返回用户输入的值
    """
    # 向后兼容：command 自动赋值给 operation
    if command and not operation:
        operation = command

    from langgraph.types import interrupt

    context = {
        "_approval": True,           # 标识这是一个审批中断（区别于其他类型的 interrupt）
        "tool_name": tool_name,
        "title": title,
        "description": description,
        "action": action,
        "operation": operation,      # 统一字段名（兼容旧 command）
        "danger_level": danger_level,
    }
    if input_placeholder:
        context["input_placeholder"] = input_placeholder
    if extra:
        context["extra"] = extra

    logger.info(f"interrupt_for_approval: tool={tool_name}, title={title}, danger={danger_level}, operation={operation[:80]}")

    result = interrupt(context)

    if action == ApprovalAction.CONFIRM_WITH_INPUT:
        # 用户输入模式：返回用户输入的值或 None（取消）
        return result
    else:
        # 确认/取消模式：返回 True/False
        return result is True


def reject_sync_approval(tool_name: str, detail: str = "") -> str:
    """同步模式下无法使用 interrupt，返回统一的拒绝消息

    当工具在 _run（同步）模式下需要审批时，应调用此函数返回拒绝消息，
    而非手写拒绝文本。这样保证所有工具的拒绝消息格式一致。

    Args:
        tool_name: 工具名称
        detail: 操作详情（如命令、文件路径等）
    """
    msg = f"操作需要用户确认，但当前为同步执行模式，无法请求审批。"
    if detail:
        msg += f" 详情: {detail}"
    return msg


def is_approval_interrupt(value: Any) -> bool:
    """判断 interrupt 值是否为审批类型

    用于 chat_service.py 中过滤审批中断事件。
    """
    return isinstance(value, dict) and value.get("_approval") is True
