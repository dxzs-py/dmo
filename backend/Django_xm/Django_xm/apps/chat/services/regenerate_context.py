"""重新生成 — 上下文构建与版本归档（Task 15.5 拆分）。

从 regenerate_service.py 抽离的纯上下文逻辑：
- thread_id 生成与识别
- prepare_context: 构建重新生成所需的对话上下文（前一条 user + 历史）
- resolve_tool_config: 解析工具配置（优先 session，回退 extended tier）
- archive_current_version: 归档当前版本到 versions 数组，创建新空版本

依赖方向：独立底层模块，被 stream/resume/persist 引用。
"""

import logging
from typing import Any

from django.utils import timezone

from Django_xm.apps.chat.models import ChatMessage, MessageRole

logger = logging.getLogger(__name__)

_REGEN_THREAD_PREFIX = "regen_"


def _make_regen_thread_id(session_id: str, message_id: int) -> str:
    return f"{_REGEN_THREAD_PREFIX}{session_id}_{message_id}"


def is_regen_thread_id(thread_id: str | None) -> bool:
    return bool(thread_id and thread_id.startswith(_REGEN_THREAD_PREFIX))


def prepare_context(session, message_id: int) -> dict[str, Any]:
    """构建重新生成的对话上下文。

    流程：
    1. 取 message_id 对应的 assistant 消息
    2. 找到该 assistant 消息的前一条 user 消息（按 created_at 排序）
    3. 查询前一条 user 消息关联的附件，重新构建含附件全文的 user_content
    4. 构建对话上下文：前一条 user 消息（含附件内容）+ 之前的历史消息

    返回：
        {
            'context': List[Dict[str, Any]],  # 对话历史消息列表
            'preloaded_attachment_content': Optional[Any],  # 附件内容（str 或 list）
            'preloaded_attachment_type': Optional[str],  # 'text' 或 'multimodal'
        }
        若未找到消息，返回空字典 {}。

    附件处理与 ChatStreamView.post 的预处理逻辑完全对齐：
        - text + RAG: 构造复合 content 作为 multimodal 传递
        - text 无 RAG: 使用 content 作为消息内容
        - multimodal: 使用原始 content（列表，保留图片信息）
    """
    assistant_msg = ChatMessage.objects.filter(
        id=message_id,
        session=session,
        role=MessageRole.ASSISTANT,
        is_deleted=False,
    ).first()
    if not assistant_msg:
        logger.warning(f"[Regenerate] 未找到 assistant 消息: message_id={message_id}")
        return {}

    prev_user_msg = (
        ChatMessage.objects.filter(
            session=session,
            role=MessageRole.USER,
            is_deleted=False,
            created_at__lt=assistant_msg.created_at,
        )
        .order_by("-created_at")
        .first()
    )
    if not prev_user_msg:
        logger.warning(f"[Regenerate] 未找到 assistant 消息前的 user 消息: message_id={message_id}")
        return {}

    # 查询前一条 user 消息关联的附件，重新构建含附件全文的 user_content
    # 与 ChatStreamView.post 的附件预处理逻辑（views_chat.py:370-387）完全对齐
    user_content = prev_user_msg.content or ""
    preloaded_attachment_content: Any | None = None
    preloaded_attachment_type: str | None = None
    try:
        attachment_ids = list(prev_user_msg.attachments.values_list("id", flat=True))
        if attachment_ids:
            from Django_xm.apps.attachments.services.attachment_content_service import AttachmentService

            att_svc = AttachmentService()
            built_content = att_svc.build_user_content(user_content, attachment_ids)
            if built_content["type"] == "text":
                rag_context = built_content.get("_rag_context")
                if rag_context:
                    # RAG 上下文存在：构造复合 content 作为 multimodal 传递，避免 preloaded text 路径丢弃 RAG
                    preloaded_attachment_content = [
                        {"type": "text", "text": built_content["content"]},
                        {"type": "text", "text": f"\n\n---\n{rag_context}\n---\n"},
                    ]
                    preloaded_attachment_type = "multimodal"
                else:
                    # 无 RAG 上下文：使用 content 作为消息内容
                    user_content = built_content["content"]
                    preloaded_attachment_type = "text"
            else:
                # multimodal 类型：使用原始 content（列表，保留图片信息）
                preloaded_attachment_content = built_content["content"]
                preloaded_attachment_type = "multimodal"
            logger.info(
                f"[Regenerate] 恢复附件内容: msg={prev_user_msg.id}, "
                f"attachment_ids={attachment_ids}, type={preloaded_attachment_type}"
            )
    except Exception as e:
        logger.warning(f"[Regenerate] 恢复附件内容失败: {e}", exc_info=True)

    history_msgs = ChatMessage.objects.filter(
        session=session,
        is_deleted=False,
        created_at__lt=prev_user_msg.created_at,
        role__in=[MessageRole.USER, MessageRole.ASSISTANT],
    ).order_by("created_at")

    context: list[dict[str, Any]] = []
    for msg in history_msgs:
        context.append(
            {
                "role": msg.role,
                "content": msg.content or "",
            }
        )
    context.append(
        {
            "role": MessageRole.USER,
            "content": user_content,
        }
    )
    return {
        "context": context,
        "preloaded_attachment_content": preloaded_attachment_content,
        "preloaded_attachment_type": preloaded_attachment_type,
    }


def resolve_tool_config(session, original_message) -> dict[str, Any]:
    """解析重新生成所需的工具配置。

    配置来源（与 ChatStreamView.post 持久化的字段一致）：
    - selected_tools: 从 session.selected_tools 读取（用户在 UI 选择的工具列表）
    - selected_knowledge_bases: 从 session.selected_knowledge_bases 读取
    - tool_tier: 当 selected_tools 为空时决定返回的工具集范围

    设计说明：
    - 不从原消息 tool_calls 反推 selected_tools 或 tool_tier，因为 tool_calls 只记录
      实际被调用过的工具，不是原始可用工具集，反推会缩窄工具范围
    - 当 selected_tools 为空时（历史会话或用户未显式选择），使用 extended tier
      返回完整的工具集，避免工具集异常缩减影响 agent 能力
    - original_message 参数保留为了未来可能的扩展（如从消息恢复模型配置），
      当前仅用于日志记录
    """
    from Django_xm.apps.tools import TOOL_TIER_EXTENDED

    use_tools = True
    use_web_search = False
    use_mcp = False
    selected_mcp_servers: list[str] | None = None
    agent_type = getattr(session, "mode", None) or "agent"

    # 直接从 session 字段读取（ChatSession 无 metadata 字段）
    selected_tools = list(getattr(session, "selected_tools", None) or []) or None
    selected_knowledge_bases = list(getattr(session, "selected_knowledge_bases", None) or [])
    use_knowledge_base = bool(selected_knowledge_bases)

    # 当 selected_tools 为空时，使用 extended tier 返回完整的工具集
    # 避免历史会话（session.selected_tools 为空）工具集异常缩减为 4 个
    tool_tier = TOOL_TIER_EXTENDED

    if original_message is not None:
        logger.debug(
            f"[Regenerate] 工具配置: selected_tools={'自定义' if selected_tools else '全部'}, "
            f"knowledge_bases={selected_knowledge_bases}, tool_tier={tool_tier}"
        )

    return {
        "selected_tools": selected_tools,
        "selected_mcp_servers": selected_mcp_servers,
        "use_tools": use_tools,
        "use_web_search": use_web_search,
        "use_mcp": use_mcp,
        "use_knowledge_base": use_knowledge_base,
        "selected_knowledge_bases": selected_knowledge_bases,
        "agent_type": agent_type,
        "mode": agent_type,
        "tool_tier": tool_tier,
    }


def archive_current_version(message) -> None:
    """将当前版本归档到 versions 数组，并创建新空版本。"""
    if message.versions is None:
        message.versions = []

    archived = {
        "content": message.content or "",
        "tool_calls": list(message.tool_calls or []),
        "sources": list(message.sources or []),
        "reasoning": message.reasoning if message.reasoning else {},
        "created_at": message.created_at.isoformat() if message.created_at else "",
        "model": message.model or "",
    }
    message.versions.append(archived)

    message.versions.append(
        {
            "content": "",
            "tool_calls": [],
            "sources": [],
            "reasoning": "",
            "created_at": timezone.now().isoformat(),
            "model": "",
        }
    )

    message.current_version = len(message.versions) - 1

    message.content = ""
    message.tool_calls = []
    message.sources = []
    message.reasoning = {}
    message.approval = {}

    message.save()
