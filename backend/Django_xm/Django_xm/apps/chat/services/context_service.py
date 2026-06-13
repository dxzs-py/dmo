"""
上下文工程服务

从 chat_service.py 拆分出的上下文管理逻辑：
- 上下文压缩
- 上下文工程（结构化上下文构建）
- Checkpointer 模式下的注入检测和预算检查
- 研究上下文加载与压缩
"""
import logging
from typing import Optional, Dict, Any, List, Tuple

from Django_xm.apps.context_manager.services.manager import ContextManager

logger = logging.getLogger(__name__)


class ContextService:

    CHECKPOINTER_ENABLED = True

    def __init__(self, user_id: Optional[int] = None, thread_id: Optional[str] = None):
        self.user_id = user_id
        self._thread_id = thread_id
        self._context_manager: Optional[ContextManager] = None
        self._store = None

        if self.CHECKPOINTER_ENABLED:
            try:
                from Django_xm.apps.ai_engine.services.checkpointer_factory import get_store
                self._store = get_store()
            except Exception:
                pass

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

    def apply_compaction(self, chat_history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if self._context_manager is not None:
            try:
                processed, metadata = self._context_manager.process_messages(chat_history)
                if metadata.get("compression", {}).get("compressed"):
                    logger.info(
                        f"上下文压缩: {metadata['compression']['original_tokens']} -> "
                        f"{metadata['compression']['compressed_tokens']} tokens, "
                        f"压缩率 {metadata['compression']['ratio']:.1%}"
                    )
                return processed
            except Exception as e:
                logger.warning(f"ContextManager 压缩失败，返回原始消息: {e}")

        return chat_history

    def apply_context_engineering(
        self,
        chat_history: List[Dict[str, Any]],
        query: str,
        mode: str = "agent",
        model_name: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
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
        model_name: Optional[str] = None,
        mode: str = "agent",
    ) -> Dict[str, Any]:
        from Django_xm.apps.context_manager.services.compression import TokenEstimator

        metadata = {
            "injection_detected": False,
            "budget_over": [],
            "pruned": False,
        }

        if self._context_manager is None:
            return metadata

        injection_detected = self._context_manager.check_injection(user_message)
        if injection_detected:
            logger.warning("检测到指令注入尝试（Checkpointer 模式），用户输入将被隔离")
            metadata["injection_detected"] = True

        if model_name:
            self._context_manager.allocate_budget(model_name)

        return metadata

    def check_injection(self, user_message: str) -> bool:
        if self._context_manager is None:
            return False
        return self._context_manager.check_injection(user_message)

    def load_research_context(self, research_task_id: str, user_id: Optional[int] = None,
                              session_id: Optional[str] = None) -> Optional[str]:
        # 前端未传 research_task_id 时，尝试从 session 历史消息中推断
        if not research_task_id and session_id:
            research_task_id = self._find_research_task_from_session(session_id, user_id)
            if research_task_id:
                logger.info(f"从 session 历史消息推断出 research_task_id: {research_task_id}")

        if not research_task_id:
            logger.debug(f"load_research_context: research_task_id 为空，跳过")
            return None

        try:
            from Django_xm.apps.research.models import ResearchTask
            # 用 all_objects 查询，已软删除的研究任务仍可被聊天引用（只要聊天会话还在）
            qs = ResearchTask.all_objects.filter(task_id=research_task_id)
            if user_id:
                qs = qs.filter(created_by_id=user_id)
            task = qs.first()
            if not task:
                logger.warning(f"研究任务不存在或无权访问: {research_task_id}")
                return None

            logger.info(f"加载研究上下文: task_id={research_task_id}, query={task.query[:50] if task.query else '(无)'}")

            from Django_xm.apps.core.services.file_manager import get_file_manager
            file_manager = get_file_manager()
            files = file_manager.list_task_files(research_task_id, 'research')
            md_files = [f for f in files if f.path.suffix in ('.md', '.txt')]

            file_contents = []
            for f in md_files[:8]:
                relative_path = str(f.path.relative_to(f.base_dir))
                content = file_manager.read_file_content(research_task_id, relative_path, 'research')
                if content and content.strip():
                    file_contents.append(f"### {relative_path}\n{content.strip()}")

            if not file_contents:
                if task.final_report:
                    file_contents.append(f"### 最终报告\n{task.final_report.strip()}")
                else:
                    return None

            combined = "\n\n".join(file_contents)

            from Django_xm.apps.context_manager.services.compression import TokenEstimator
            token_count = TokenEstimator.estimate(combined)

            # 阈值 8000 tokens：低于此值直接返回原文，高于此值用 LLM 压缩
            if token_count <= 8000:
                return combined

            from Django_xm.apps.ai_engine.services.llm_factory import get_chat_model
            model = get_chat_model(temperature=0.3, max_tokens=4000)
            prompt = (
                "请对以下深度研究的所有文件进行结构化总结，保留关键信息以便后续讨论：\n\n"
                "1. 【研究主题】研究的核心问题\n"
                "2. 【研究计划】研究的方法和步骤\n"
                "3. 【关键发现】每个方面的核心结论和数据\n"
                "4. 【详细内容】各文件的核心要点（保留关键细节，不要过度压缩）\n"
                "5. 【建议与展望】研究提出的建议或后续方向\n\n"
                f"研究文件内容：\n{combined}\n\n"
                "请用中文生成详细的结构化总结："
            )
            response = model.invoke([{"role": "user", "content": prompt}])
            summary = getattr(response, "content", "")
            return summary if summary else combined[:16000]

        except Exception as e:
            logger.error(f"加载研究上下文失败: {e}", exc_info=True)
            return None

    def _find_research_task_from_session(self, session_id: str, user_id: Optional[int] = None) -> Optional[str]:
        """从 session 的历史消息中查找关联的 research_task_id（兜底逻辑）"""
        try:
            from Django_xm.apps.chat.models import ChatMessage
            # 用 all_objects，默认 objects 过滤了 is_deleted=True 的消息
            qs = ChatMessage.all_objects.filter(
                session__session_id=session_id,
                role='assistant',
                research_task_id__isnull=False,
            ).exclude(research_task_id='')
            if user_id:
                qs = qs.filter(session__user_id=user_id)
            msg = qs.order_by('-created_at').first()
            if msg and msg.research_task_id:
                return msg.research_task_id
        except Exception as e:
            logger.warning(f"从 session 历史消息推断 research_task_id 失败: {e}")
        return None
