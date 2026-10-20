"""
附件生命周期管理 Celery 任务
定时清理过期附件、入库旧文件、监控存储空间
"""

from celery import shared_task
from celery.utils.log import get_task_logger

from Django_xm.async_utils import run_async
from Django_xm.tasks.base import TrackedTask

logger = get_task_logger(__name__)


@shared_task(
    bind=True,
    name="chat.cleanup_expired_attachments",
    max_retries=1,
    soft_time_limit=600,
    autoretry_for=(ConnectionError, TimeoutError, OSError),
    retry_backoff=True,
    retry_backoff_max=60,
)
def cleanup_expired_attachments(self):
    from Django_xm.apps.attachments.services.attachment_lifecycle import AttachmentLifecycleService

    tracker = TrackedTask(self)
    tracker.set_task_type("chat_cleanup")

    try:
        tracker.mark_started()
        service = AttachmentLifecycleService()
        log = service.cleanup_expired(triggered_by="celery_beat")

        result = {
            "files_processed": log.files_processed,
            "files_deleted": log.files_deleted,
            "files_indexed": log.files_archived,
            "files_skipped": log.files_skipped,
            "space_freed_mb": round(log.space_freed / 1024 / 1024, 2),
            "space_indexed_mb": round(log.space_archived / 1024 / 1024, 2),
            "errors": log.errors,
        }

        logger.info(
            f"[Celery Chat] 附件清理完成: 处理={log.files_processed}, 删除={log.files_deleted}, "
            f"入库={log.files_archived}, 释放={result['space_freed_mb']}MB"
        )
        tracker.mark_success(result=result)
        return {"status": "success", **result}
    except Exception as e:
        logger.exception("[Celery Chat] 附件清理任务失败")
        tracker.mark_failure(error_message=str(e))
        return {"status": "error", "error": str(e)}


@shared_task(
    bind=True,
    name="chat.index_old_attachments",
    max_retries=1,
    soft_time_limit=600,
    autoretry_for=(ConnectionError, TimeoutError, OSError),
    retry_backoff=True,
    retry_backoff_max=60,
)
def index_old_attachments(self):
    from Django_xm.apps.attachments.services.attachment_lifecycle import AttachmentLifecycleService

    tracker = TrackedTask(self)
    tracker.set_task_type("chat_index")

    try:
        tracker.mark_started()
        service = AttachmentLifecycleService()
        log = service.index_old_attachments(triggered_by="celery_beat")

        result = {
            "files_processed": log.files_processed,
            "files_indexed": log.files_archived,
            "space_indexed_mb": round(log.space_archived / 1024 / 1024, 2),
            "errors": log.errors,
        }

        logger.info(
            f"[Celery Chat] 附件入库完成: 处理={log.files_processed}, 入库={log.files_archived}, "
            f"空间={result['space_indexed_mb']}MB"
        )
        tracker.mark_success(result=result)
        return {"status": "success", **result}
    except Exception as e:
        logger.exception("[Celery Chat] 附件入库任务失败")
        tracker.mark_failure(error_message=str(e))
        return {"status": "error", "error": str(e)}


@shared_task(
    bind=True,
    name="chat.check_storage_alerts",
    max_retries=1,
    soft_time_limit=120,
    autoretry_for=(ConnectionError, TimeoutError, OSError),
    retry_backoff=True,
    retry_backoff_max=60,
)
def check_storage_alerts(self):
    from Django_xm.apps.attachments.services.attachment_lifecycle import AttachmentLifecycleService

    tracker = TrackedTask(self)
    tracker.set_task_type("chat_storage")

    try:
        tracker.mark_started()
        service = AttachmentLifecycleService()
        stats = service.get_storage_stats()
        alert = service.check_storage_alerts()

        result = {
            "disk_usage_percent": stats["disk_usage_percent"],
            "disk_free_gb": round(stats["disk_free_bytes"] / 1024 / 1024 / 1024, 2),
            "total_files": stats["total_files"],
            "total_size_mb": stats["total_size_mb"],
            "alert_triggered": alert is not None,
            "alert_level": alert.level if alert else None,
            "alert_message": alert.message if alert else None,
        }

        if alert:
            logger.warning(f"[Celery Chat] 存储告警: {alert.message}")
        else:
            logger.info(f"[Celery Chat] 存储正常: 使用率={stats['disk_usage_percent']}%")

        tracker.mark_success(result=result)
        return {"status": "success", **result}
    except Exception as e:
        logger.exception("[Celery Chat] 存储检查任务失败")
        tracker.mark_failure(error_message=str(e))
        return {"status": "error", "error": str(e)}


@shared_task(
    bind=True,
    name="chat.cleanup_checkpoints",
    max_retries=1,
    soft_time_limit=300,
    autoretry_for=(ConnectionError, TimeoutError, OSError),
    retry_backoff=True,
    retry_backoff_max=60,
)
def cleanup_checkpoints(self, user_id=None, session_id=None):
    """
    清理 AI checkpoint 和 Store 数据的 Celery 任务

    根据参数执行不同级别的清理：
    - session_id 不为 None：清理该 session 的 checkpoint 和 Store 数据
    - session_id 为 None 且 user_id 不为 None：清理该用户的所有数据

    Args:
        user_id: 用户 ID（int, 可选）
        session_id: 会话 ID（str, 可选）
    """
    from Django_xm.apps.ai_engine.services.checkpointer_factory import (
        delete_thread_checkpoints,
        delete_thread_store_data,
        delete_user_all_data,
    )

    tracker = TrackedTask(self)
    tracker.set_task_type("chat_cleanup")

    try:
        tracker.mark_started()

        if session_id is not None:
            # 清理指定 session 的 checkpoint 和 Store 数据
            try:
                run_async(delete_thread_checkpoints(str(session_id)))
                logger.info(f"[Celery Chat] 已清理 session={session_id} 的 checkpoint 数据")
            except Exception:
                logger.exception(f"[Celery Chat] 清理 session={session_id} 的 checkpoint 失败")

            if user_id is not None:
                try:
                    run_async(delete_thread_store_data(user_id, str(session_id)))
                    logger.info(f"[Celery Chat] 已清理 user={user_id} session={session_id} 的 Store 数据")
                except Exception:
                    logger.exception(
                        f"[Celery Chat] 清理 user={user_id} session={session_id} 的 Store 数据失败",
                    )

        elif user_id is not None:
            # session_id 为 None，清理该用户的所有数据
            try:
                run_async(delete_user_all_data(user_id))
                logger.info(f"[Celery Chat] 已清理 user={user_id} 的所有数据")
            except Exception:
                logger.exception(f"[Celery Chat] 清理 user={user_id} 的所有数据失败")

        else:
            logger.warning("[Celery Chat] cleanup_checkpoints 未提供 user_id 或 session_id，跳过清理")

        result = {"user_id": user_id, "session_id": session_id}
        tracker.mark_success(result=result)
        return {"status": "success", **result}
    except Exception as e:
        logger.exception("[Celery Chat] checkpoint 清理任务失败")
        tracker.mark_failure(error_message=str(e))
        return {"status": "error", "error": str(e)}


@shared_task(
    bind=True,
    name="chat.save_document_memory",
    max_retries=1,
    soft_time_limit=300,
    autoretry_for=(ConnectionError, TimeoutError, OSError),
    retry_backoff=True,
    retry_backoff_max=60,
)
def save_document_memory(self, attachment_id: int):
    """附件上传成功后写入文档记忆 Store（D-006 接线写侧）。

    读侧 = DocumentMemoryService.build_document_context（Agent 上下文注入）。
    LLM 摘要在 _generate_summary 内部兜底（失败降级为前 200 字截取），
    本任务自身失败仅记日志——文档记忆是增强能力，不影响上传主流程。
    """
    from Django_xm.apps.attachments.models import ChatAttachment
    from Django_xm.apps.attachments.services.document_memory_service import DocumentMemoryService
    from Django_xm.apps.tools.langchain.file_reader import read_file_content

    tracker = TrackedTask(self)
    tracker.set_task_type("chat_cleanup")

    try:
        tracker.mark_started()
        attachment = ChatAttachment.objects.filter(id=attachment_id, is_deleted=False).first()
        if attachment is None:
            logger.warning(f"[Celery Chat] 文档记忆任务：附件不存在或已删除 id={attachment_id}")
            tracker.mark_success(result={"skipped": "attachment_not_found"})
            return {"status": "success", "skipped": "attachment_not_found"}
        if (attachment.mime_type or "").lower().startswith("image/"):
            tracker.mark_success(result={"skipped": "image"})
            return {"status": "success", "skipped": "image"}

        try:
            content = read_file_content(attachment.file.path)
        except Exception as e:
            logger.warning(f"[Celery Chat] 文档记忆任务：附件内容读取失败 id={attachment_id}: {e}")
            tracker.mark_success(result={"skipped": f"read_failed: {e}"})
            return {"status": "success", "skipped": "read_failed"}

        ok = DocumentMemoryService().save_document(
            user_id=attachment.session.user_id,
            attachment_id=attachment.id,
            doc_name=attachment.original_name,
            content=content,
            metadata={"mime_type": attachment.mime_type, "file_size": attachment.file_size},
        )
        result = {"attachment_id": attachment_id, "saved": ok}
        tracker.mark_success(result=result)
        return {"status": "success", **result}
    except Exception as e:
        logger.exception(f"[Celery Chat] 文档记忆写入任务失败 id={attachment_id}")
        tracker.mark_failure(error_message=str(e))
        return {"status": "error", "error": str(e)}


@shared_task(
    bind=True,
    name="chat.save_cross_session_memory",
    max_retries=1,
    soft_time_limit=600,
    autoretry_for=(ConnectionError, TimeoutError, OSError),
    retry_backoff=True,
    retry_backoff_max=120,
)
def save_cross_session_memory(self, user_id: int, session_id: str, user_message: str, assistant_content: str):
    """会话终态跨会话记忆写入（摘要合并 + 知识图谱抽取）。

    从 chat_executor_core._save_cross_session_memory 移入后台：本地辅助模型
    （ollama）做摘要合并/实体抽取耗时可达分钟级，同步执行会把会话执行器
    占住到记忆写完——期间该会话的新消息 start 信令被 SessionManager 幂等
    忽略，用户端表现为"发消息一直等待"。移入 Celery 后会话在主回复完成
    时立即释放，记忆写入不再阻塞交互。

    内容契约与原同步实现一致（round2 1.4 + round3 2.8）：prepare 锁外 LLM、
    commit 行锁内二次校验写回 + KG 规则抽取；失败静默降级（记忆是增强
    能力，不影响主链路）。
    """
    tracker = TrackedTask(self)
    tracker.set_task_type("chat_cleanup")

    try:
        tracker.mark_started()
        if not str(user_message or "").strip() or not str(assistant_content or "").strip():
            tracker.mark_success(result={"skipped": "empty_content"})
            return {"status": "success", "skipped": "empty_content"}

        from django.db import transaction

        from Django_xm.apps.chat.models import ChatSession
        from Django_xm.apps.context_manager.services.manager import create_context_manager

        manager = create_context_manager(user_id=user_id)

        prepared = manager.prepare_turn_summary(
            session_id=session_id,
            user_message=str(user_message),
            assistant_content=str(assistant_content),
        )
        if prepared is None:
            tracker.mark_success(result={"saved": False, "reason": "disabled_or_skipped"})
            return {"status": "success", "saved": False}

        saved = False
        with transaction.atomic():
            ChatSession.objects.select_for_update().filter(session_id=session_id).first()
            saved = manager.commit_turn_summary(session_id, prepared)
            manager.process_conversation(
                user_id=user_id,
                messages=[
                    {"role": "user", "content": str(user_message)},
                    {"role": "assistant", "content": str(assistant_content)},
                ],
            )

        result = {"session_id": session_id, "saved": saved}
        tracker.mark_success(result=result)
        return {"status": "success", **result}
    except Exception as e:
        logger.exception(f"[Celery Chat] 跨会话记忆写入失败 session={session_id}")
        tracker.mark_failure(error_message=str(e))
        return {"status": "error", "error": str(e)}
