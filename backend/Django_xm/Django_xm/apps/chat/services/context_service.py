"""
上下文工程服务

从 chat_service.py 拆分出的上下文管理逻辑：
- 上下文压缩
- 上下文工程（结构化上下文构建）
- Checkpointer 模式下的注入检测和预算检查
- 研究上下文加载与压缩
"""

import logging
from typing import Any

from Django_xm.apps.context_manager.services.manager import ContextManager

logger = logging.getLogger(__name__)

# 4.4/4.8：上下文工程降级的用户可见提示文案（degradation 事件 data.message）
_COMPRESSION_DEGRADED_NOTICE = "上下文过长，自动摘要未达质量标准，已降级为关键片段保留（可能丢失部分细节）"
_BUDGET_TRIMMED_NOTICE = "上下文过长，已裁剪 {removed_count} 条最早的会话历史"


def build_context_degradation_events(metadata: dict[str, Any]) -> list[dict[str, str]]:
    """从上下文工程 metadata 构建用户可见降级事件（4.4/4.8，E5-02 同型结构）。

    - compression_degraded：摘要质量门禁不达标，已降级为【原文关键片段】保留；
    - budget_trimmed.trimmed：压缩与相关性筛选后仍超预算，最终裁剪了最旧历史。

    消费方：chat_service._build_stream_agent_and_input（追加进
    _stream_degradation_events）与 stream/fallback.stream_without_tools（直接
    yield degradation 事件），前端按 type=degradation 展示提示。
    """
    events: list[dict[str, str]] = []
    if metadata.get("compression_degraded"):
        events.append({"message": _COMPRESSION_DEGRADED_NOTICE})
    trimmed = metadata.get("budget_trimmed")
    if isinstance(trimmed, dict) and trimmed.get("trimmed"):
        events.append({"message": _BUDGET_TRIMMED_NOTICE.format(removed_count=trimmed.get("removed_count", 0))})
    return events


class ContextService:
    CHECKPOINTER_ENABLED = True

    def __init__(self, user_id: int | None = None, thread_id: str | None = None):
        self.user_id = user_id
        self._thread_id = thread_id
        self._context_manager: ContextManager | None = None
        self._store = None

        if self.CHECKPOINTER_ENABLED:
            try:
                from Django_xm.apps.ai_engine.services.checkpointer_factory import get_store

                self._store = get_store()
            except Exception:
                # store 初始化失败时回退到 None，不影响 ContextManager 创建
                logger.debug("get_store 初始化失败，使用 None", exc_info=True)

        try:
            from Django_xm.apps.context_manager.services.manager import create_context_manager

            self._context_manager = create_context_manager(
                user_id=self.user_id,
                store=self._store,
                thread_id=self._thread_id,
            )
            logger.info(f"ContextService: ContextManager 已初始化 (user={self.user_id}, thread={self._thread_id})")
        except Exception as e:
            logger.warning(f"ContextService: ContextManager 初始化失败: {e}")

    def apply_context_engineering(
        self,
        chat_history: list[dict[str, Any]],
        query: str,
        mode: str = "agent",
        model_name: str | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if self._context_manager is None:
            return chat_history, {"injection_detected": False, "pruned": False, "budget_over": []}

        try:
            result = self._context_manager.build_structured_context(
                messages=chat_history,
                query=query,
                mode=mode,
                model_name=model_name,
            )
            metadata = result.get("metadata", {})
            pruned_messages = result.get("pruned_messages", chat_history)

            return pruned_messages, {
                "injection_detected": metadata.get("injection_detected", False),
                "pruned": metadata.get("prune_result", {}).get("pruned_count", 0) > 0,
                "budget_over": metadata.get("budget_over_sections", []),
                # 4.4/4.8：质量门禁降级与最终裁剪标记，供 chat 消费端转用户可见提示
                "compression_degraded": bool(metadata.get("compression_degraded", False)),
                "budget_trimmed": metadata.get("budget_trimmed") or {"trimmed": False, "removed_count": 0},
            }
        except Exception as e:
            logger.warning(f"build_structured_context 调用失败，回退到简单修剪: {e}")
            pruned_messages, prune_result = self._context_manager.prune_messages(chat_history)
            return pruned_messages, {
                "injection_detected": False,
                "pruned": prune_result.pruned_count > 0,
                "budget_over": [],
            }

    def apply_context_engineering_for_checkpointer(
        self,
        user_message: str,
        model_name: str | None = None,
        mode: str = "agent",
    ) -> dict[str, Any]:

        metadata = {
            "injection_detected": False,
            "budget_over": [],
            "pruned": False,
        }

        if self._context_manager is None:
            return metadata

        injection_detected = self._context_manager.check_injection(user_message)
        if injection_detected:
            # 1.5 真实语义：检测仅留痕，无隔离动作，用户输入原样进入模型上下文
            # （旧日志"用户输入将被隔离"为假隔离表述）
            logger.warning("检测到指令注入尝试（Checkpointer 模式，已留痕，未隔离）")
            metadata["injection_detected"] = True

        if model_name:
            self._context_manager.allocate_budget(model_name)

        return metadata

    def check_injection(self, user_message: str) -> bool:
        if self._context_manager is None:
            return False
        return self._context_manager.check_injection(user_message)

    def get_injection_context(self, query: str, session_id: str | None = None) -> str:
        """获取记忆注入上下文（A9：checkpointer 主链路接通）。

        委托 ContextManager.get_injection_context：返回知识图谱相关实体上下文
        与跨会话会话摘要的拼接（两段各自内部捕获异常降级为空段，段首自带
        「【知识图谱上下文·来源：对话记忆】」「【跨会话上下文】」标题）。
        manager 未初始化或全部来源不可用时返回空串，调用方按"不注入"处理。

        同步方法且内部含 KG 检索与逐条同步 embed_query（远程 API），
        调用方（chat_service checkpointer 分支）须经 asyncio.to_thread 调度。
        """
        if self._context_manager is None:
            return ""
        return self._context_manager.get_injection_context(query, session_id=session_id)

    def load_research_context(
        self, research_task_id: str, user_id: int | None = None, session_id: str | None = None
    ) -> str | None:
        # 前端未传 research_task_id 时，尝试从 session 历史消息中推断
        if not research_task_id and session_id:
            research_task_id = self._find_research_task_from_session(session_id, user_id)
            if research_task_id:
                logger.info(f"从 session 历史消息推断出 research_task_id: {research_task_id}")

        if not research_task_id:
            logger.debug("load_research_context: research_task_id 为空，跳过")
            return None

        try:
            from Django_xm.apps.research.services.cross_app import get_research_task_for_context

            # 用 all_objects 查询，已软删除的研究任务仍可被聊天引用（只要聊天会话还在）
            task = get_research_task_for_context(research_task_id, user_id=user_id)
            if not task:
                logger.warning(f"研究任务不存在或无权访问: {research_task_id}")
                return None

            task_query = task.get("query", "")
            logger.info(
                f"加载研究上下文: task_id={research_task_id}, query={task_query[:50] if task_query else '(无)'}"
            )

            from Django_xm.apps.core.services.file_manager import get_file_manager

            file_manager = get_file_manager()
            files = file_manager.list_task_files(research_task_id, "research")
            md_files = [f for f in files if f.path.suffix in (".md", ".txt")]

            file_contents = []
            for f in md_files[:8]:
                relative_path = str(f.path.relative_to(f.base_dir))
                content = file_manager.read_file_content(research_task_id, relative_path, "research")
                if content and content.strip():
                    file_contents.append(f"### {relative_path}\n{content.strip()}")

            if not file_contents:
                final_report = task.get("final_report", "")
                if final_report:
                    file_contents.append(f"### 最终报告\n{final_report.strip()}")
                else:
                    return None

            combined = "\n\n".join(file_contents)

            from Django_xm.apps.context_manager.services.compression import TokenEstimator

            token_count = TokenEstimator.estimate(combined)

            # 阈值 8000 tokens：低于此值直接返回原文，高于此值用统一引擎摘要（ARCH-07）
            if token_count <= 8000:
                return combined

            # 摘要统一走引擎能力（模型解析链单源：轻量模型→辅助模型→SystemConfig 默认），
            # 消除旁路自建 get_chat_model 的双摘要实现；异常向上抛由外层 except 返回 None
            from Django_xm.apps.context_manager.services.compression import ContextCompressionEngine

            instruction = (
                "请对以下深度研究的所有文件进行结构化总结，保留关键信息以便后续讨论：\n\n"
                "1. 【研究主题】研究的核心问题\n"
                "2. 【研究计划】研究的方法和步骤\n"
                "3. 【关键发现】每个方面的核心结论和数据\n"
                "4. 【详细内容】各文件的核心要点（保留关键细节，不要过度压缩）\n"
                "5. 【建议与展望】研究提出的建议或后续方向\n\n"
                "请用中文生成详细的结构化总结："
            )
            summary = ContextCompressionEngine().summarize_long_text(combined, instruction)
            return summary if summary else combined[:16000]

        except Exception:
            logger.exception("加载研究上下文失败")
            return None

    def _find_research_task_from_session(self, session_id: str, user_id: int | None = None) -> str | None:
        """从 session 的历史消息中查找关联的 research_task_id（兜底逻辑）"""
        try:
            from Django_xm.apps.chat.models import ChatMessage

            # 用 all_objects，默认 objects 过滤了 is_deleted=True 的消息
            qs = ChatMessage.all_objects.filter(
                session__session_id=session_id,
                role="assistant",
                research_task_id__isnull=False,
            ).exclude(research_task_id="")
            if user_id:
                qs = qs.filter(session__user_id=user_id)
            msg = qs.order_by("-created_at").first()
            if msg and msg.research_task_id:
                return msg.research_task_id
        except Exception as e:
            logger.warning(f"从 session 历史消息推断 research_task_id 失败: {e}")
        return None
