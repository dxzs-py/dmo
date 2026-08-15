"""工具公共基类和混入

提供：
- AsyncToolMixin: 自动将同步 _run 包装为异步 _arun（使用 asyncio.to_thread）
- SafeConfigMixin: 安全获取 AI 引擎配置（降级到环境变量）
- is_approval_interrupt: 判断 interrupt 值是否为审批类型（供流处理检测用）

审批机制说明：
    工具层不参与审批判断。所有审批由 ApprovalMiddleware 在 after_model 钩子
    统一处理（批量拦截 AIMessage.tool_calls 中需要审批的工具调用）。
    到达工具 _run/_arun 的命令已通过审批或无需审批。
"""

import asyncio
import logging
import os
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


class AsyncToolMixin:
    # _run 由混入目标类（BaseTool 子类）提供，这里仅声明类型供 mypy 检查
    _run: Callable[..., Any]

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
    def get_config(key: str, default: Any = None, env_key: str = "") -> Any:
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
VALID_TOOL_CATEGORIES = {
    "basic",
    "web_search",
    "web_fetch",
    "file",
    "weather",
    "agent",
    "todo",
    "translation",
    "system",
    "other",
}


def validate_tool_metadata(metadata: dict) -> dict:
    merged = {**TOOL_METADATA_DEFAULTS, **(metadata or {})}
    if merged["tier"] not in VALID_TOOL_TIERS:
        raise ValueError(f"Invalid tool tier: {merged['tier']}")
    if merged["visibility"] not in VALID_TOOL_VISIBILITIES:
        raise ValueError(f"Invalid tool visibility: {merged['visibility']}")
    if merged["category"] not in VALID_TOOL_CATEGORIES:
        raise ValueError(f"Invalid tool category: {merged['category']}")
    return merged


# ── 审批中断检测 ──────────────────────────────────────────────────
# 工具层审批已统一收敛到 ApprovalMiddleware（apps/agent_hub/approval/middleware.py），
# 此处仅保留 is_approval_interrupt 供流处理（stream_helpers / adapter / deep_chat_service
# / regenerate_service / views_chat）检测 interrupt 值是否为审批类型。


def is_approval_interrupt(value: Any) -> bool:
    """判断 interrupt 值是否为审批类型

    用于流处理中过滤审批中断事件。ApprovalMiddleware 的 interrupt 值格式为：
        {"_approval": True, "requests": [...], "_meta": {...}}
    """
    return isinstance(value, dict) and value.get("_approval") is True


def is_subagent_wait_interrupt(value: Any) -> bool:
    """判断 interrupt 值是否为子代理业务等待类型（非审批，仅流程暂停）。

    ``wait_for_subagent`` 工具的 interrupt 值格式为：
        {"_subagent_wait": True, "subagent_thread_id": "..."}

    与审批中断完全隔离：不产出审批 UI，仅由调度器在子代理终态时
    以 Command(resume) 唤醒父 Graph。
    """
    return isinstance(value, dict) and value.get("_subagent_wait") is True


# 工具元数据默认规则（替代原 chat_service.py 中硬编码的 weather_tools / raw_content_tools / knowledge_base_ 前缀）
_RAW_CONTENT_TOOL_NAMES = frozenset(
    {
        "web_fetch",
        "web_search",
        "skill_web_research",
    }
)
_RAW_CONTENT_TOOL_PREFIXES = ("knowledge_base_",)

_DEFAULT_TOOL_META: dict[str, Any] = {
    "output_to_chat": True,
    "raw_content": False,
}


def get_tool_metadata(tool_name: str, tools: list | None = None) -> dict[str, Any]:
    """获取工具元数据，驱动工具结果补发与聊天输出行为

    返回字段：
    - output_to_chat: bool — 工具结果是否适合作为聊天文本补发（默认 True）
    - raw_content: bool — 工具是否返回大量原始内容（默认 False），
      此类结果不应直接作为聊天文本输出

    优先级：
    1. 工具实例的 metadata 属性（若包含 output_to_chat / raw_content）
    2. 工具名匹配的默认规则（raw_content_tools / knowledge_base_ 前缀）
    3. 默认值 {output_to_chat: True, raw_content: False}

    替代原 chat_service.py 中硬编码的：
    - weather_tools = ["get_daily_weather", "get_weather_forecast", "get_weather"]
    - raw_content_tools = {"web_fetch", "web_search", "skill_web_research"}
    - tool_name.startswith("knowledge_base_")
    """
    meta = dict(_DEFAULT_TOOL_META)

    # 1. 工具实例 metadata 属性覆盖
    if tools:
        for tool in tools:
            tool_obj_name = getattr(tool, "name", None) or getattr(tool, "__name__", None)
            if tool_obj_name == tool_name:
                instance_meta = getattr(tool, "metadata", None) or {}
                if isinstance(instance_meta, dict):
                    if "output_to_chat" in instance_meta:
                        meta["output_to_chat"] = bool(instance_meta["output_to_chat"])
                    if "raw_content" in instance_meta:
                        meta["raw_content"] = bool(instance_meta["raw_content"])
                break

    # 2. 工具名匹配的默认规则
    if tool_name in _RAW_CONTENT_TOOL_NAMES or any(
        tool_name.startswith(prefix) for prefix in _RAW_CONTENT_TOOL_PREFIXES
    ):
        meta["raw_content"] = True

    return meta
