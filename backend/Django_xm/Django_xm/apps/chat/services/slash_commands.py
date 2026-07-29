"""
斜杠命令系统
参考 claw-code-main 的 commands.rs 实现
支持在聊天中通过 /command 执行特殊操作
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from Django_xm.apps.core.config import get_logger

logger = get_logger(__name__)


class CommandCategory(StrEnum):
    SESSION = "session"
    TOOLS = "tools"
    INFO = "info"
    SETTINGS = "settings"
    AI = "ai"


@dataclass
class SlashCommand:
    name: str
    description: str
    category: CommandCategory
    usage: str
    examples: list[str]
    handler: Callable | None = None
    requires_session: bool = False
    supports_resume: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "category": self.category.value,
            "usage": self.usage,
            "examples": self.examples,
            "requiresSession": self.requires_session,
        }


def _handle_help(context: dict[str, Any]) -> dict[str, Any]:
    commands = context.get("all_commands", {})
    lines = ["📋 **可用命令列表**\n"]
    current_category = None
    category_names = {
        CommandCategory.SESSION: "🔄 会话管理",
        CommandCategory.TOOLS: "🔧 工具操作",
        CommandCategory.INFO: "ℹ️ 信息查询",
        CommandCategory.SETTINGS: "⚙️ 系统设置",
        CommandCategory.AI: "🤖 AI 功能",
    }

    for cmd in sorted(commands.values(), key=lambda c: (c.category.value, c.name)):
        if cmd.category != current_category:
            current_category = cmd.category
            lines.append(f"\n**{category_names.get(current_category, current_category.value)}**")
            lines.append("---")
        lines.append(f"- `/{cmd.name}` — {cmd.description}")
        if cmd.usage:
            lines.append(f"  用法: `{cmd.usage}`")

    lines.append("\n💡 在输入框中输入 `/` 即可查看命令提示")
    return {"type": "info", "content": "\n".join(lines)}


def _handle_status(context: dict[str, Any]) -> dict[str, Any]:
    session = context.get("session")
    if not session:
        return {"type": "info", "content": "❌ 当前没有活跃的会话"}

    messages = context.get("messages", [])
    user_msgs = sum(1 for m in messages if m.get("role") == "user")
    ai_msgs = sum(1 for m in messages if m.get("role") == "assistant")

    lines = [
        "📊 **会话状态**\n",
        f"- 会话ID: `{session.get('session_id', 'N/A')}`",
        f"- 模式: {session.get('mode', 'N/A')}",
        f"- 标题: {session.get('title', 'N/A')}",
        f"- 消息数: {len(messages)} (用户: {user_msgs}, 助手: {ai_msgs})",
    ]

    token_info = context.get("token_info")
    if token_info:
        tokens = token_info.get("tokens", {})
        lines.append(
            f"- Token: 输入={tokens.get('input', 0)}, 输出={tokens.get('output', 0)}, 总计={tokens.get('total', 0)}"
        )

    return {"type": "info", "content": "\n".join(lines)}


def _handle_compact(context: dict[str, Any]) -> dict[str, Any]:
    from Django_xm.apps.context_manager.services.context_pruner import ContextPruner
    from Django_xm.apps.context_manager.services.manager import create_context_manager

    messages = context.get("messages", [])
    if not messages:
        return {"type": "info", "content": "❌ 当前没有消息可以压缩"}

    user_id = context.get("user_id")
    session_id = context.get("session_id")
    context_manager = create_context_manager(user_id=user_id, thread_id=session_id)

    pruner = ContextPruner()
    if not pruner.prune(messages)[1].pruned_count:
        total_tokens = sum(len(m.get("content", "").split()) for m in messages)
        return {"type": "info", "content": f"ℹ️ 当前会话无需压缩 (估算 {total_tokens} tokens)"}

    result = context_manager.build_structured_context(
        messages=messages,
        query="",
        mode="chat",
    )
    metadata = result["metadata"]
    prune_info = metadata["prune_result"]

    if prune_info["pruned_count"] > 0:
        return {
            "type": "success",
            "content": (
                f"✅ 会话已压缩！\n"
                f"- 原始消息: {prune_info['original_count']} 条\n"
                f"- 保留消息: {prune_info['original_count'] - prune_info['pruned_count']} 条\n"
                f"- 去重: {prune_info['deduped_count']} 条, 过滤: {prune_info['filtered_count']} 条"
            ),
        }
    else:
        return {"type": "info", "content": "ℹ️ 会话无需压缩"}


def _handle_model(context: dict[str, Any]) -> dict[str, Any]:
    args = context.get("args", "").strip()
    from Django_xm.apps.ai_engine.services.llm_factory import get_model_string
    from Django_xm.apps.ai_engine.services.registry_service import (
        get_all_provider_ids,
        get_provider_models,
    )

    current_model = get_model_string()

    if not args:
        return {
            "type": "info",
            "content": f"🤖 当前模型: `{current_model}`\n\n使用 `/model <provider>:<model_name>` 切换模型",
        }

    # 动态从 MODEL_REGISTRY 拉取所有启用的 provider + 模型
    supported_models: list[str] = []
    for pid in get_all_provider_ids():
        for mname in get_provider_models(pid):
            # 同时支持 "model_name" 与 "provider:model_name" 两种写法
            supported_models.append(mname)
            supported_models.append(f"{pid}:{mname}")

    if args not in supported_models:
        return {
            "type": "error",
            "content": (
                f"❌ 不支持的模型: `{args}`\n\n"
                f"支持格式: `model_name` 或 `provider:model_name`\n"
                f"当前已注册: {', '.join(f'`{m}`' for m in sorted(set(supported_models)))}"
            ),
        }

    return {
        "type": "success",
        "content": f"✅ 模型已切换为: `{args}` (将在下次对话生效)",
        "model": args,
    }


def _handle_clear(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "action",
        "action": "clear_session",
        "content": "🗑️ 会话已清除，开始新的对话",
    }


def _handle_export(context: dict[str, Any]) -> dict[str, Any]:
    messages = context.get("messages", [])
    session = context.get("session", {})

    if not messages:
        return {"type": "info", "content": "❌ 当前没有消息可导出"}

    export_data = {
        "session": session,
        "messages": messages,
        "exportedAt": context.get("timestamp", ""),
    }

    return {
        "type": "export",
        "content": f"📤 已导出 {len(messages)} 条消息",
        "data": export_data,
    }


def _handle_version(context: dict[str, Any]) -> dict[str, Any]:
    from Django_xm.apps.ai_engine.config import settings

    return {
        "type": "info",
        "content": (
            f"📌 **LC-StudyLab**\n"
            f"- 版本: {getattr(settings, 'app_version', '1.0.0')}\n"
            f"- LangChain: 1.2.13\n"
            f"- Django: 5.2"
        ),
    }


COMMANDS: dict[str, SlashCommand] = {
    "help": SlashCommand(
        name="help",
        description="显示可用命令列表",
        category=CommandCategory.INFO,
        usage="/help",
        examples=["/help"],
        handler=_handle_help,
    ),
    "status": SlashCommand(
        name="status",
        description="查看当前会话状态",
        category=CommandCategory.SESSION,
        usage="/status",
        examples=["/status"],
        handler=_handle_status,
        requires_session=True,
    ),
    "compact": SlashCommand(
        name="compact",
        description="压缩当前会话历史",
        category=CommandCategory.SESSION,
        usage="/compact",
        examples=["/compact"],
        handler=_handle_compact,
        requires_session=True,
    ),
    "model": SlashCommand(
        name="model",
        description="查看或切换AI模型",
        category=CommandCategory.AI,
        usage="/model [provider:model_name]",
        examples=["/model", "/model openai:gpt-4o-mini", "/model deepseek:deepseek-v4-flash"],
        handler=_handle_model,
    ),
    "clear": SlashCommand(
        name="clear",
        description="清除当前会话",
        category=CommandCategory.SESSION,
        usage="/clear",
        examples=["/clear"],
        handler=_handle_clear,
        requires_session=True,
    ),
    "export": SlashCommand(
        name="export",
        description="导出当前对话",
        category=CommandCategory.SESSION,
        usage="/export",
        examples=["/export"],
        handler=_handle_export,
        requires_session=True,
    ),
    "version": SlashCommand(
        name="version",
        description="查看系统版本信息",
        category=CommandCategory.INFO,
        usage="/version",
        examples=["/version"],
        handler=_handle_version,
    ),
}


def parse_command(text: str) -> tuple | None:
    text = text.strip()
    if not text.startswith("/"):
        return None

    parts = text[1:].split(None, 1)
    if not parts:
        return None

    command_name = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""

    return command_name, args


def execute_command(
    command_name: str,
    context: dict[str, Any],
) -> dict[str, Any]:
    command = COMMANDS.get(command_name)
    if not command:
        available = ", ".join(f"`/{name}`" for name in sorted(COMMANDS.keys()))
        return {
            "type": "error",
            "content": f"❌ 未知命令: `/{command_name}`\n\n可用命令: {available}",
        }

    if command.requires_session and not context.get("session"):
        return {"type": "error", "content": "❌ 此命令需要活跃的会话"}

    try:
        context["all_commands"] = COMMANDS
        result = command.handler(context)
        return result
    except Exception as e:
        logger.exception(f"执行命令 /{command_name} 失败")
        return {"type": "error", "content": f"❌ 命令执行失败: {e!s}"}


def get_all_commands() -> list[dict[str, Any]]:
    return [cmd.to_dict() for cmd in COMMANDS.values()]


def get_commands_by_category(category: CommandCategory) -> list[dict[str, Any]]:
    return [cmd.to_dict() for cmd in COMMANDS.values() if cmd.category == category]
