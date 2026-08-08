import logging
from typing import Any

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ── 输入模型 ────────────────────────────────────────────────────────────────────


class AttachmentRagSearchInput(BaseModel):
    query: str = Field(description="检索查询，用于在用户上传的附件中搜索相关内容")


# ── 工具类 ──────────────────────────────────────────────────────────────────────


class AttachmentRagSearchTool(BaseTool):
    """附件 RAG 检索工具

    自动检索当前会话中用户最近一次上传的非图片附件内容。
    thread_id 由 invoke/ainvoke 从 LangGraph ToolNode 传入的 RunnableConfig 中提取，
    无需用户手动指定附件 ID。

    检索流程:
        1. 通过 self.thread_id 查找 ChatSession
        2. 找到最近一次包含非图片附件的用户消息
        3. 加载附件文档分块 → UnifiedRagPipeline 检索
    """

    name: str = "attachment_rag_search"
    metadata: dict = Field(
        default_factory=lambda: {"tier": "extended", "visibility": "selectable", "category": "file"}
    )
    description: str = (
        "搜索用户在当前会话中上传的文件内容。当用户上传了文件并基于文件内容提问时使用此工具。"
        "参数：query - 搜索查询字符串（必填）"
        "注意：此工具自动检索当前会话中用户最近一次上传的附件，无需指定附件 ID。"
    )
    args_schema: type[BaseModel] = AttachmentRagSearchInput

    # thread_id 由 invoke/ainvoke 从 RunnableConfig 中提取，每次工具调用前更新
    thread_id: str = Field(default="", description="当前会话 ID，由 invoke/ainvoke 注入")

    # ── config 注入 ─────────────────────────────────────────────────────────────

    def invoke(self, input: str | dict | BaseModel, config=None, **kwargs) -> Any:
        """从 config 提取 thread_id，再委托父类执行。"""
        self._capture_thread_id(config)
        logger.info("DIAG: invoke thread_id=%s, has_config=%s",
                    self.thread_id, bool(config))
        try:
            result = super().invoke(input, config=config, **kwargs)
            logger.info("DIAG: invoke 完成 result_type=%s", type(result).__name__)
            return result
        except Exception:
            logger.exception("DIAG: invoke 执行异常")
            raise

    async def ainvoke(self, input: str | dict | BaseModel, config=None, **kwargs) -> Any:
        """从 config 提取 thread_id，再委托父类异步执行。"""
        self._capture_thread_id(config)
        logger.info("DIAG: ainvoke thread_id=%s, has_config=%s",
                    self.thread_id, bool(config))
        try:
            result = await super().ainvoke(input, config=config, **kwargs)
            logger.info("DIAG: ainvoke 完成 result_type=%s content_preview=%s",
                        type(result).__name__, str(result)[:200])
            return result
        except Exception:
            logger.exception("DIAG: ainvoke 执行异常")
            raise

    def _capture_thread_id(self, config) -> None:
        """从 RunnableConfig 提取 thread_id 并设置到 self。

        LangGraph 的 ToolNode 通过 tool.ainvoke(input, config) 调用工具，
        config 中包含 configurable.thread_id（即 chat_session_id）。
        """
        if config and isinstance(config, dict):
            configurable = config.get("configurable", {})
            if isinstance(configurable, dict):
                tid = configurable.get("thread_id", "")
                if tid:
                    self.thread_id = tid

    # ── 核心逻辑 ────────────────────────────────────────────────────────────────

    def _run(self, query: str) -> str:
        """同步执行 RAG 检索。

        Returns:
            格式化后的检索结果字符串
        """
        logger.info("AttachmentRAGSearch: query=%s, thread_id=%s", query[:80] if query else "", self.thread_id)

        thread_id = self.thread_id
        if not thread_id:
            return "无法确定当前会话，请重新发送消息。"

        # ── 1. 查找 ChatSession ────────────────────────────────────────────
        try:
            from Django_xm.apps.chat.models import ChatSession

            ChatSession.objects.get(session_id=thread_id)
        except Exception as e:
            logger.warning("查找会话失败 (session_id=%s): %s", thread_id, e)
            return f"未找到会话 (session_id={thread_id})"

        # ── 2. 查找最近一次包含非图片附件的用户消息 ─────────────────────────
        try:
            from Django_xm.apps.attachments.models import ChatAttachment
            from Django_xm.apps.tools.langchain.file_reader import get_attachment_info

            attachments = list(
                ChatAttachment.objects.filter(
                    session__session_id=thread_id,
                    message__role="user",
                )
                .select_related("message")
                .order_by("-message__created_at", "-created_at")
            )
        except Exception as e:
            logger.exception("查询附件列表失败")
            return f"查询附件列表时出错: {e}"

        if not attachments:
            return "当前会话没有上传附件。请先上传文件后再使用此工具检索。"

        # 遍历附件（按 message__created_at 降序），找到最近一条包含非图片附件的消息
        text_attachment_ids: list[int] = []
        last_message_id: int | None = None
        found_text = False

        for att in attachments:
            try:
                info = get_attachment_info(att.id)
                if not info.get("is_image"):
                    if last_message_id is None:
                        last_message_id = att.message_id
                    if att.message_id == last_message_id:
                        text_attachment_ids.append(att.id)
                        found_text = True
                    elif found_text:
                        break
            except Exception:
                logger.debug("无法获取附件 %s 信息，按文本处理", att.id)
                if last_message_id is None:
                    last_message_id = att.message_id
                if att.message_id == last_message_id:
                    text_attachment_ids.append(att.id)
                    found_text = True

        if not text_attachment_ids:
            return "当前会话的附件均为图片格式，请使用 attachment_reader 工具读取图片内容。"

        logger.info(
            "AttachmentRAGSearch: 找到 %d 个文本附件 (message_id=%s)",
            len(text_attachment_ids),
            last_message_id,
        )

        # ── 3. 加载文档并检索 ──────────────────────────────────────────────
        try:
            from Django_xm.apps.knowledge.services.rag_retrieval import (
                UnifiedRagPipeline,
                load_attachment_documents,
            )

            documents, total_tokens = load_attachment_documents(text_attachment_ids)
            pipeline = UnifiedRagPipeline()
            result = pipeline.search(query, documents, total_tokens)
            logger.info("AttachmentRAGSearch: 检索完成, 结果长度=%d", len(result))
            return result
        except Exception as e:
            logger.exception("AttachmentRAGSearch 检索失败")
            return f"附件检索过程中发生错误: {e}"

    async def _arun(self, query: str) -> str:
        """异步执行 RAG 检索，委托给同步 _run。"""
        from asgiref.sync import sync_to_async

        return await sync_to_async(self._run)(query=query)


# ── 单例 ────────────────────────────────────────────────────────────────────────

attachment_rag_search = AttachmentRagSearchTool()


def get_attachment_rag_tools() -> list[BaseTool]:
    """返回附件 RAG 检索工具单例列表。"""
    return [attachment_rag_search]


ATTACHMENT_RAG_TOOLS = [attachment_rag_search]
