"""
斜杠命令系统
参考 claw-code-main 的 commands.rs 实现
支持在聊天中通过 /command 执行特殊操作
"""

import json
import logging
from typing import Dict, Any, Optional, List, Callable
from dataclasses import dataclass
from enum import Enum

from Django_xm.apps.config_center.config import get_logger

logger = get_logger(__name__)


class CommandCategory(str, Enum):
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
    examples: List[str]
    handler: Optional[Callable] = None
    requires_session: bool = False
    supports_resume: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "category": self.category.value,
            "usage": self.usage,
            "examples": self.examples,
            "requiresSession": self.requires_session,
        }


def _handle_help(context: Dict[str, Any]) -> Dict[str, Any]:
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


def _handle_status(context: Dict[str, Any]) -> Dict[str, Any]:
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
        lines.append(f"- Token使用: {token_info.get('usedTokens', 'N/A')}/{token_info.get('maxTokens', 'N/A')}")

    cost_info = context.get("cost_info")
    if cost_info:
        lines.append(f"- 估算成本: {cost_info.get('totalCostFormatted', 'N/A')}")

    return {"type": "info", "content": "\n".join(lines)}


def _handle_compact(context: Dict[str, Any]) -> Dict[str, Any]:
    from Django_xm.apps.context_manager.services.session_compactor import SessionCompactor, apply_compaction_to_chat_history

    messages = context.get("messages", [])
    if not messages:
        return {"type": "info", "content": "❌ 当前没有消息可以压缩"}

    compactor = SessionCompactor()
    if not compactor.should_compact(messages):
        total_tokens = sum(
            compactor.estimate_tokens(m.get('content', ''))
            for m in messages
        )
        return {
            "type": "info",
            "content": f"ℹ️ 当前会话无需压缩 (估算 {total_tokens} tokens，阈值 {compactor.token_threshold})"
        }

    compacted, result = apply_compaction_to_chat_history(messages)
    if result.compressed:
        return {
            "type": "success",
            "content": (
                f"✅ 会话已压缩！\n"
                f"- 原始消息: {result.original_message_count} 条\n"
                f"- 保留消息: {result.kept_message_count} 条\n"
                f"- 摘要估算: {result.summary_token_estimate} tokens"
            ),
            "compacted_messages": compacted,
        }
    else:
        return {"type": "info", "content": "ℹ️ 会话无需压缩"}


def _handle_model(context: Dict[str, Any]) -> Dict[str, Any]:
    args = context.get("args", "").strip()
    from Django_xm.apps.ai_engine.config import settings

    current_model = getattr(settings, 'openai_model', 'gpt-4o')

    if not args:
        return {
            "type": "info",
            "content": f"🤖 当前模型: `{current_model}`\n\n使用 `/model <模型名>` 切换模型",
        }

    supported_models = [
        "gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo",
        "claude-opus-4-20250514", "claude-sonnet-4-20250514",
        "deepseek-chat", "deepseek-reasoner",
    ]

    if args not in supported_models:
        return {
            "type": "error",
            "content": f"❌ 不支持的模型: `{args}`\n\n支持的模型: {', '.join(f'`{m}`' for m in supported_models)}",
        }

    return {
        "type": "success",
        "content": f"✅ 模型已切换为: `{args}` (将在下次对话生效)",
        "model": args,
    }


def _handle_cost(context: Dict[str, Any]) -> Dict[str, Any]:
    cost_info = context.get("cost_info")
    if not cost_info:
        return {"type": "info", "content": "📊 暂无成本数据"}

    tokens = cost_info.get("tokens", {})
    lines = [
        "💰 **Token 使用与成本统计**\n",
        f"- 总成本: {cost_info.get('totalCostFormatted', '$0.0000')}",
        f"- 输入 Token: {tokens.get('input', 0):,}",
        f"- 输出 Token: {tokens.get('output', 0):,}",
        f"- 推理 Token: {tokens.get('reasoning', 0):,}",
        f"- 缓存命中 Token: {tokens.get('cachedInput', 0):,}",
        f"- 总 Token: {tokens.get('total', 0):,}",
        f"- API 调用次数: {cost_info.get('recordCount', 0)}",
    ]

    models = cost_info.get("models", [])
    if models:
        lines.append(f"- 使用模型: {', '.join(models)}")

    return {"type": "info", "content": "\n".join(lines)}


def _handle_permissions(context: Dict[str, Any]) -> Dict[str, Any]:
    args = context.get("args", "").strip()
    user_id = context.get("user_id")

    if not user_id:
        return {"type": "error", "content": "❌ 需要登录才能查看权限"}

    from Django_xm.apps.core.permissions import PermissionService

    if not args:
        info = PermissionService.get_permission_info(user_id)
        policy = info["policy"]
        lines = [
            "🔒 **当前权限设置**\n",
            f"- 会话模式: `{policy['sessionMode']}`",
            f"- 默认权限: `{policy['defaultMode']}`",
            f"- 允许的工具数: {len(policy['allowedTools'])}",
        ]
        lines.append("\n使用 `/permissions <模式>` 切换模式:")
        for mode, details in info["sessionModes"].items():
            lines.append(f"  - `{mode}`: {details['description']}")
        return {"type": "info", "content": "\n".join(lines)}

    try:
        policy = PermissionService.update_session_mode(user_id, args)
        return {
            "type": "success",
            "content": f"✅ 权限模式已更新为: `{policy.session_mode.value}`",
        }
    except ValueError as e:
        return {"type": "error", "content": f"❌ {str(e)}"}


def _handle_clear(context: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "action",
        "action": "clear_session",
        "content": "🗑️ 会话已清除，开始新的对话",
    }


def _handle_export(context: Dict[str, Any]) -> Dict[str, Any]:
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


def _handle_version(context: Dict[str, Any]) -> Dict[str, Any]:
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


COMMANDS: Dict[str, SlashCommand] = {
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
        usage="/model [模型名]",
        examples=["/model", "/model gpt-4o-mini"],
        handler=_handle_model,
    ),
    "cost": SlashCommand(
        name="cost",
        description="查看Token使用量和成本",
        category=CommandCategory.INFO,
        usage="/cost",
        examples=["/cost"],
        handler=_handle_cost,
    ),
    "permissions": SlashCommand(
        name="permissions",
        description="查看或设置工具权限",
        category=CommandCategory.SETTINGS,
        usage="/permissions [模式]",
        examples=["/permissions", "/permissions read-only"],
        handler=_handle_permissions,
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


def parse_command(text: str) -> Optional[tuple]:
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
    context: Dict[str, Any],
) -> Dict[str, Any]:
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
        logger.error(f"执行命令 /{command_name} 失败: {e}")
        return {"type": "error", "content": f"❌ 命令执行失败: {str(e)}"}


def get_all_commands() -> List[Dict[str, Any]]:
    return [cmd.to_dict() for cmd in COMMANDS.values()]


def get_commands_by_category(category: CommandCategory) -> List[Dict[str, Any]]:
    return [cmd.to_dict() for cmd in COMMANDS.values() if cmd.category == category]
