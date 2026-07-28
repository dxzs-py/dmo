"""
Chat 服务层 - 提供对话管理的所有服务接口

包含：
- 聊天服务（对话模式）
- 消息持久化服务
- 斜杠命令（解析、执行、注册）
- Agent 管理服务
- 消息构建服务
- 上下文工程服务
- 工具管理服务
"""

from .agent_service import AgentService
from .chat_message_builder import ChatMessageBuilder
from .chat_service import (
    ChatModeService,
    ChatService,
)
from .context_service import ContextService
from .message_service import MessagePersistenceService
from .slash_commands import (
    CommandCategory,
    SlashCommand,
    execute_command,
    get_all_commands,
    get_commands_by_category,
    parse_command,
)
from .tool_service import ToolService

__all__ = [
    "AgentService",
    "ChatMessageBuilder",
    "ChatModeService",
    "ChatService",
    "CommandCategory",
    "ContextService",
    "MessagePersistenceService",
    "SlashCommand",
    "ToolService",
    "execute_command",
    "get_all_commands",
    "get_commands_by_category",
    "parse_command",
]
