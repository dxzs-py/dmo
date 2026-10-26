import contextvars
import logging
from collections.abc import Callable
from typing import Any, Literal

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ── 会话存在性校验 provider（阶段 7 票②依赖倒置）───────────────────────────────
# tools 不得直连 chat.models（.importlinter research-chat-via-facade-only 传递性
# 豁免台账清零）：ChatSession 存在性校验实现归位 chat 业务层
# （chat/services/cross_app.is_chat_session_exists），经运行期 provider 注入，
# 组合根接线在 chat AppConfig.ready()（先例 context_manager/services/
# runtime_providers.py）。工具为模块级单例（C-1 ContextVar 身份先例），构造注入
# 不便，故取注册式。未注册时按既有降级路径处理（返回"未找到会话"，跳过检索）。
ChatSessionExistsProvider = Callable[[str], bool]

_chat_session_exists_provider: ChatSessionExistsProvider | None = None


def set_chat_session_exists_provider(provider: ChatSessionExistsProvider | None) -> None:
    """注册会话存在性校验实现（chat AppConfig.ready() 组合根调用）。"""
    global _chat_session_exists_provider  # noqa: PLW0603 - 运行期 provider 注册点（先例 runtime_providers）
    _chat_session_exists_provider = provider


def get_chat_session_exists_provider() -> ChatSessionExistsProvider | None:
    return _chat_session_exists_provider


# ── 身份传递（C-1 多租户安全，B1 同款身份传递模式）───────────────────────────────
# thread_id 不落单例实例字段：单例被全部会话共享，实例字段在并发下会被后一个
# 会话的捕获覆写，导致会话 A 以会话 B 的 session 检索附件 → 跨用户附件内容泄漏
# （审批策略 SAFE 自动通过，无人工闸门拦截）。
# 传递链：invoke/ainvoke 边界从本次调用的 config.configurable 捕获 → ContextVar
# （任务级上下文；asgiref sync_to_async 复制上下文进工作线程）→ _run 读取；
# 图内直接 _run 调用回退 langgraph.config.get_config()（运行期 config，与
# termination_judge 身份取值同口径）。
_current_thread_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "attachment_rag_thread_id", default=""
)


# ── 输入模型 ────────────────────────────────────────────────────────────────────


class AttachmentRagSearchInput(BaseModel):
    query: str = Field(description="检索查询，用于在用户上传的附件中搜索相关内容")
    intent: Literal["auto", "precise", "comprehensive"] = Field(
        default="auto",
        description=(
            "检索意图：precise=定位具体事实/数据/步骤；"
            "comprehensive=整体性/总结性提问（如“这份附件主要写了什么”“全文结构”“有哪些章节”），"
            "会放大召回数量以覆盖更多原文；auto=按关键词自动判断"
        ),
    )


# ── 工具类 ──────────────────────────────────────────────────────────────────────


class AttachmentRagSearchTool(BaseTool):
    """附件 RAG 检索工具

    自动检索当前会话中用户最近一次上传的非图片附件内容。
    thread_id 由 invoke/ainvoke 从本次调用的 RunnableConfig.configurable 按次
    捕获传递（不存实例状态），无需用户手动指定附件 ID。

    检索流程:
        1. 以本次调用的 thread_id 查找 ChatSession
        2. 找到最近一次包含非图片附件的用户消息
        3. 加载附件文档分块 → UnifiedRagPipeline 检索
    """

    name: str = "attachment_search"
    response_format: str = "content_and_artifact"
    metadata: dict = Field(
        default_factory=lambda: {
            "tier": "extended",
            "visibility": "selectable",
            "category": "file",
            "output_to_chat": False,  # 检索输出是中间数据（ToolCallCard 展示），不作为聊天文本补发
        }
    )
    description: str = (
        "搜索用户在当前会话中上传的文件内容。当用户上传了文件并基于文件内容提问时使用此工具。"
        "参数：query - 搜索查询字符串（必填）"
        "intent - 可选：整体/总结/概述类提问传 comprehensive，定位具体事实传 precise，不传则自动判断。"
        "注意：此工具自动检索当前会话中用户最近一次上传的附件，无需指定附件 ID。"
    )
    args_schema: type[BaseModel] = AttachmentRagSearchInput

    # ── config 注入 ─────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_thread_id(config) -> str:
        """从 RunnableConfig.configurable 提取 thread_id（即 chat_session_id）。"""
        if config and isinstance(config, dict):
            configurable = config.get("configurable") or {}
            if isinstance(configurable, dict):
                return configurable.get("thread_id") or ""
        return ""

    def invoke(self, input: str | dict | BaseModel, config=None, **kwargs) -> Any:
        """从本次调用的 config 捕获 thread_id 进当前任务上下文，再委托父类执行。"""
        token = _current_thread_id.set(self._extract_thread_id(config))
        try:
            return super().invoke(input, config=config, **kwargs)
        finally:
            _current_thread_id.reset(token)

    async def ainvoke(self, input: str | dict | BaseModel, config=None, **kwargs) -> Any:
        """从本次调用的 config 捕获 thread_id 进当前任务上下文，再委托父类执行。"""
        token = _current_thread_id.set(self._extract_thread_id(config))
        try:
            return await super().ainvoke(input, config=config, **kwargs)
        finally:
            _current_thread_id.reset(token)

    def _resolve_thread_id(self) -> str:
        """解析当前调用身份：invoke/ainvoke 边界捕获值 → LangGraph 运行期 config → 空。

        并发会话下各调用方持有各自上下文中的 thread_id，单例不携带任何跨调用状态。
        """
        thread_id = _current_thread_id.get()
        if thread_id:
            return thread_id
        try:
            from langgraph.config import get_config

            return self._extract_thread_id(get_config())
        except Exception:
            # 图外调用无运行期上下文，属正常；返回空由调用方给出明确文案
            return ""

    def _attachment_scope(self, thread_id: str):
        """附件使用会话级 PG collection（名字服务端生成，不接受 LLM 传参）。"""
        from Django_xm.apps.knowledge.services.rag_service import attachment_scope

        return attachment_scope(thread_id)

    def _ensure_index(self, scope, attachment_ids: list[int]) -> None:
        """首次检索或附件集合变化时重建 PG 索引（lazy indexing）。"""
        from Django_xm.apps.knowledge.services.index_service import IndexManager
        from Django_xm.apps.knowledge.services.rag_retrieval import load_attachment_documents
        from Django_xm.apps.knowledge.services.rag_service import RagService

        marker = "attachment_ids:" + ",".join(str(i) for i in attachment_ids)
        manager = IndexManager()
        metadata = manager.load_metadata(scope.name) or {}
        if manager.index_exists(scope.name) and metadata.get("description") == marker:
            return

        documents, _ = load_attachment_documents(attachment_ids)
        for idx, doc in enumerate(documents):
            doc.metadata.setdefault("source", f"attachment_{idx}")
            doc.metadata.setdefault("chunk_index", idx)
            doc.metadata.setdefault("total_chunks", len(documents))
        RagService().index_scope(scope, documents, description=marker)

    # ── 核心逻辑 ────────────────────────────────────────────────────────────────

    def _run(self, query: str, intent: str = "auto") -> tuple[str, dict]:
        """同步执行 RAG 检索。

        Returns:
            格式化后的检索结果字符串
        """
        thread_id = self._resolve_thread_id()
        logger.info("AttachmentRAGSearch: query=%s, thread_id=%s", query[:80] if query else "", thread_id)

        if not thread_id:
            return "无法确定当前会话，请重新发送消息。", {"sources": []}

        # ── 1. 会话存在性校验（provider 注入，E5-10：异常细节只进日志）────────
        resolver = get_chat_session_exists_provider()
        if resolver is None:
            logger.warning("ChatSession 校验 provider 未注册（按未找到会话处理）: session_id=%s", thread_id)
            return f"未找到会话 (session_id={thread_id})", {"sources": []}
        try:
            if not resolver(thread_id):
                return f"未找到会话 (session_id={thread_id})", {"sources": []}
        except Exception:
            logger.warning("查找会话失败 (session_id=%s)", thread_id, exc_info=True)
            return f"未找到会话 (session_id={thread_id})", {"sources": []}

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
            # E5-10：异常文本可能携带 SQL/路径等内部细节，不进 LLM 上下文
            # （ToolCallCard 展示位）；全栈经上方 logger.exception 留档。
            return f"错误：查询会话附件列表失败（{type(e).__name__}），请稍后重试。", {"sources": []}

        if not attachments:
            return "当前会话没有上传附件。请先上传文件后再使用此工具检索。", {"sources": []}

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
            return "当前会话的附件均为图片格式，请使用 attachment_reader 工具读取图片内容。", {"sources": []}

        logger.info(
            "AttachmentRAGSearch: 找到 %d 个文本附件 (message_id=%s)",
            len(text_attachment_ids),
            last_message_id,
        )

        # ── 3. 会话级 PG 索引检索 ──────────────────────────────────────────────
        try:
            from Django_xm.apps.knowledge.services.kb_search_tool import _format_result
            from Django_xm.apps.knowledge.services.rag_service import RagRequest, RagService

            scope = self._attachment_scope(thread_id)
            session = getattr(attachments[0], "session", None) if attachments else None
            user_id = getattr(session, "user_id", None)
            if user_id:
                from dataclasses import replace

                scope = replace(scope, user_id=user_id)
            self._ensure_index(scope, text_attachment_ids)
            result = RagService().retrieve_for_scope(
                RagRequest(
                    query=query,
                    scope=scope,
                    k=5,
                    metrics_kind="agent_attachment",
                    intent=intent,
                )
            )
            logger.info("AttachmentSearch: 检索完成, hits=%d", len(result.docs))
            from Django_xm.apps.knowledge.rag_settings import get_rag_runtime_settings
            from Django_xm.apps.tools.langchain.external_content import wrap_external_content

            include_sources = get_rag_runtime_settings()["tool_include_sources"]
            return (
                wrap_external_content(
                    f"会话附件检索结果 (query={query[:80]})", _format_result(result)
                ),
                {
                    "sources": result.sources if include_sources else [],
                    "metrics": result.metrics,
                },
            )
        except Exception as e:
            logger.exception("AttachmentSearch 检索失败")
            return f"错误：附件检索过程中发生错误（{type(e).__name__}），请稍后重试。", {"sources": []}

    async def _arun(self, query: str, intent: str = "auto") -> tuple[str, dict]:
        """异步执行 RAG 检索，委托给同步 _run（sync_to_async 复制上下文，身份随行）。"""
        from asgiref.sync import sync_to_async

        return await sync_to_async(self._run)(query=query, intent=intent)


# ── 单例 ────────────────────────────────────────────────────────────────────────

attachment_search = AttachmentRagSearchTool()


def get_attachment_rag_tools() -> list[BaseTool]:
    """返回附件 RAG 检索工具单例列表。"""
    return [attachment_search]


ATTACHMENT_RAG_TOOLS = [attachment_search]
