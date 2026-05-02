"""
Chat 服务层 - 提供对话管理的所有服务接口

包含：
- 聊天服务（消息持久化、附件处理、对话模式）
- 斜杠命令（解析、执行、注册）
- 会话压缩（长对话摘要）
- 安全会话缓存
"""

from .chat_service import (
    AttachmentService,
    MessagePersistenceService,
    ChatService,
    ChatModeService,
)
from .slash_commands import (
    CommandCategory,
    SlashCommand,
    parse_command,
    execute_command,
    get_all_commands,
    get_commands_by_category,
)
from .session_compactor import (
    CompactResult,
    SessionCompactor,
    apply_compaction_to_chat_history,
)
from .secure_session_cache import SecureSessionCacheService

__all__ = [
    "AttachmentService",
    "MessagePersistenceService",
    "ChatService",
    "ChatModeService",
    "CommandCategory",
    "SlashCommand",
    "parse_command",
    "execute_command",
    "get_all_commands",
    "get_commands_by_category",
    "CompactResult",
    "SessionCompactor",
    "apply_compaction_to_chat_history",
    "SecureSessionCacheService",
]
