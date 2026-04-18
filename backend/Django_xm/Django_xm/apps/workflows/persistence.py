"""
工作流状态持久化服务
确保工作流状态在重启后能够恢复
"""
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any

from django.conf import settings

from Django_xm.apps.core.services.file_manager import get_file_manager

logger = logging.getLogger(__name__)
file_manager = get_file_manager()


class WorkflowPersistenceService:
    """工作流持久化服务"""

    def __init__(self):
        self.workflow_data_dir = Path(settings.DATA_DIR) / "workflow"
        self.workflow_data_dir.mkdir(parents=True, exist_ok=True)

    def save_workflow_state(
        self,
        thread_id: str,
        state: Dict[str, Any],
        user_id: Optional[int] = None,
    ) -> None:
        """
        保存工作流状态

        Args:
            thread_id: 线程ID
            state: 工作流状态字典
            user_id: 用户ID（可选）
        """
        try:
            serializable_state = self._make_serializable(state)
            self._save_to_database(thread_id, serializable_state, user_id)
            self._save_to_file(thread_id, serializable_state)
            logger.info(f"[Persistence] 工作流状态已保存: thread_id={thread_id}")
        except Exception as e:
            logger.error(f"[Persistence] 保存工作流状态失败: {e}", exc_info=True)

    def _make_serializable(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """将状态转换为可JSON序列化的格式"""
        serializable = {}
        for key, value in state.items():
            if key == "messages" and isinstance(value, list):
                serializable[key] = [self._serialize_message(msg) for msg in value]
            elif isinstance(value, dict):
                serializable[key] = self._make_serializable(value)
            else:
                serializable[key] = value
        return serializable

    def _serialize_message(self, msg: Any) -> Dict[str, Any]:
        """序列化LangChain消息对象"""
        try:
            from langchain_core.messages import BaseMessage
            if isinstance(msg, BaseMessage):
                return {
                    "type": msg.type,
                    "content": msg.content,
                    "additional_kwargs": getattr(msg, 'additional_kwargs', {}),
                    "response_metadata": getattr(msg, 'response_metadata', {}),
                    "id": getattr(msg, 'id', None),
                }
        except ImportError:
            pass
        
        if hasattr(msg, '__dict__'):
            return {"type": type(msg).__name__, "data": str(msg)}
        
        return msg

    def _deserialize_messages(self, messages_data: list) -> list:
        """反序列化消息列表"""
        try:
            from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
            
            messages = []
            for msg_data in messages_data:
                if isinstance(msg_data, dict):
                    msg_type = msg_data.get("type", "")
                    content = msg_data.get("content", "")
                    
                    if msg_type == "ai":
                        messages.append(AIMessage(content=content))
                    elif msg_type == "human":
                        messages.append(HumanMessage(content=content))
                    elif msg_type == "system":
                        messages.append(SystemMessage(content=content))
                    else:
                        messages.append(msg_data)
                else:
                    messages.append(msg_data)
            return messages
        except ImportError:
            return messages_data

    def load_workflow_state(self, thread_id: str) -> Optional[Dict[str, Any]]:
        """
        加载工作流状态

        Args:
            thread_id: 线程ID

        Returns:
            工作流状态字典，如果不存在则返回None
        """
        try:
            state = self._load_from_database(thread_id)
            if state is None:
                state = self._load_from_file(thread_id)
            if state:
                state = self._deserialize_state(state)
                logger.info(f"[Persistence] 工作流状态已加载: thread_id={thread_id}")
            return state
        except Exception as e:
            logger.error(f"[Persistence] 加载工作流状态失败: {e}", exc_info=True)
            return None

    def _deserialize_state(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """反序列化状态，恢复消息对象"""
        if "messages" in state and isinstance(state["messages"], list):
            state["messages"] = self._deserialize_messages(state["messages"])
        return state

    def _save_to_database(
        self,
        thread_id: str,
        state: Dict[str, Any],
        user_id: Optional[int] = None,
    ) -> None:
        """保存到数据库"""
        from django.contrib.auth import get_user_model
        from .models import WorkflowSession

        User = get_user_model()

        user = None
        if user_id:
            try:
                user = User.objects.get(id=user_id)
            except User.DoesNotExist:
                pass

        current_step = state.get("current_step", "start")
        
        if current_step in ["completed", "failed", "end", "feedback_completed"]:
            status = "completed" if current_step in ["completed", "end", "feedback_completed"] else "failed"
        elif current_step == "waiting_for_answers":
            status = "waiting_for_answers"
        elif current_step == "retry":
            status = "retry"
        else:
            status = "running"

        session, created = WorkflowSession.objects.update_or_create(
            thread_id=thread_id,
            defaults={
                "created_by": user,
                "user_question": state.get("user_question", ""),
                "status": status,
                "current_step": current_step,
                "learning_plan": state.get("learning_plan"),
                "quiz": state.get("quiz"),
                "user_answers": state.get("user_answers"),
                "score": state.get("score"),
                "score_details": state.get("score_details"),
                "feedback": state.get("feedback"),
                "should_retry": state.get("should_retry", False),
                "error_message": state.get("error") or state.get("error_message", ""),
            },
        )

        if created:
            logger.info(f"[Persistence] 创建新的工作流会话: thread_id={thread_id}")
        else:
            logger.info(f"[Persistence] 更新工作流会话: thread_id={thread_id}")

    def _load_from_database(self, thread_id: str) -> Optional[Dict[str, Any]]:
        """从数据库加载"""
        from .models import WorkflowSession

        try:
            session = WorkflowSession.objects.get(thread_id=thread_id, is_deleted=False)
            
            state = {
                "thread_id": session.thread_id,
                "user_question": session.user_question,
                "current_step": session.current_step or "start",
                "status": session.status,
                "learning_plan": session.learning_plan,
                "quiz": session.quiz,
                "user_answers": session.user_answers,
                "score": session.score,
                "score_details": session.score_details,
                "feedback": session.feedback,
                "should_retry": session.should_retry,
                "error": session.error_message,
                "created_at": session.created_at.isoformat() if session.created_at else "",
                "updated_at": session.updated_at.isoformat() if session.updated_at else "",
            }
            
            return {k: v for k, v in state.items() if v is not None}
            
        except WorkflowSession.DoesNotExist:
            pass
        return None

    def _save_to_file(self, thread_id: str, state: Dict[str, Any]) -> None:
        """保存到文件系统"""
        thread_dir = self.workflow_data_dir / thread_id
        thread_dir.mkdir(parents=True, exist_ok=True)

        state_file = thread_dir / "state.json"
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

        if state.get("learning_plan"):
            plan_file = thread_dir / "learning_plan.md"
            with open(plan_file, "w", encoding="utf-8") as f:
                f.write(str(state["learning_plan"]))

        if state.get("final_report") or state.get("feedback"):
            report_file = thread_dir / "report.md"
            content = []
            if state.get("feedback"):
                content.append(f"# 反馈报告\n\n{state['feedback']}\n\n")
            if state.get("score") is not None:
                content.append(f"## 得分\n\n{state['score']} 分\n\n")
            if state.get("final_report"):
                content.append(f"## 最终报告\n\n{state['final_report']}\n\n")
            with open(report_file, "w", encoding="utf-8") as f:
                f.write("".join(content))

    def _load_from_file(self, thread_id: str) -> Optional[Dict[str, Any]]:
        """从文件系统加载"""
        state_file = self.workflow_data_dir / thread_id / "state.json"
        if state_file.exists():
            with open(state_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return None

    def list_user_sessions(
        self,
        user_id: int,
        status: Optional[str] = None,
        search: Optional[str] = None,
    ):
        """列出用户的所有工作流会话"""
        from .models import WorkflowSession

        queryset = WorkflowSession.objects.filter(
            created_by_id=user_id,
            is_deleted=False
        ).select_related("created_by")

        if status:
            # 支持按 status 或 current_step 筛选
            from django.db.models import Q
            queryset = queryset.filter(Q(status=status) | Q(current_step=status))

        if search:
            queryset = queryset.filter(user_question__icontains=search)

        return queryset.order_by("-created_at")


_persistence_instance: Optional[WorkflowPersistenceService] = None


def get_persistence_service() -> WorkflowPersistenceService:
    """获取持久化服务单例"""
    global _persistence_instance
    if _persistence_instance is None:
        _persistence_instance = WorkflowPersistenceService()
    return _persistence_instance
