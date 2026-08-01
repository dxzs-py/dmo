"""
附件管理视图

提供附件上传、列表、删除、管理员操作等接口。
视图层只负责请求解析、服务调用、响应构建。
"""

import logging
import os

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from Django_xm.apps.core.throttling import KnowledgeRateThrottle
from Django_xm.common.error_codes import ErrorCode
from Django_xm.common.permissions import IsAdmin
from Django_xm.common.responses import error_response, success_response
from Django_xm.common.serializers import EmptySerializer

from .models import AttachmentStatus, ChatAttachment, StorageAlert
from .services.attachment_validation import (
    build_admin_list,
    check_user_storage_quota,
    get_admin_stats,
    handle_alert_action,
    serialize_attachment,
    serialize_attachment_detail,
    serialize_storage_alert,
    validate_upload_file,
)

logger = logging.getLogger(__name__)


class ChatAttachmentUploadView(APIView):
    """聊天附件上传视图

    校验链：
        1. 会话归属
        2. 文件大小 / 扩展名 / magic bytes
        3. 用户存储配额（settings.ATTACHMENT_MAX_TOTAL_SIZE_MB）

    限流：附件上传涉及文件 IO 与存储配额校验，按用户限流（KnowledgeRateThrottle, 60/min），
    防止单用户高频上传耗尽存储与 IO。
    """

    permission_classes = [IsAuthenticated]
    throttle_classes = [KnowledgeRateThrottle]

    @extend_schema(exclude=True)
    def post(self, request, session_id):
        try:
            from Django_xm.apps.chat.services.cross_app import get_chat_session_strict

            try:
                session = get_chat_session_strict(session_id, request.user)
            except Exception:
                return error_response(
                    code=ErrorCode.NOT_FOUND, message="会话不存在", http_status=status.HTTP_404_NOT_FOUND
                )

            uploaded_file = request.FILES.get("file")
            if not uploaded_file:
                return error_response(code=ErrorCode.INVALID_PARAMS, message="请选择要上传的文件")

            # 用户存储配额校验（先于文件内容校验，提前拦截超额上传）
            is_ok, quota_message, current_total = check_user_storage_quota(
                request.user,
                uploaded_file.size,
            )
            if not is_ok:
                logger.info(f"用户 {request.user.id} 上传超额: current={current_total}B, file={uploaded_file.size}B")
                return error_response(
                    code=ErrorCode.PERMISSION_DENIED,
                    message=quota_message,
                    http_status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                )

            is_valid, error_message, mime_type = validate_upload_file(uploaded_file)
            if not is_valid:
                return error_response(code=ErrorCode.INVALID_PARAMS, message=error_message)

            ext = os.path.splitext(uploaded_file.name)[1].lower().lstrip(".")

            attachment = ChatAttachment.objects.create(
                session=session,
                file=uploaded_file,
                original_name=uploaded_file.name,
                file_size=uploaded_file.size,
                file_type=ext,
                mime_type=mime_type,
            )

            from Django_xm.apps.attachments.services.attachment_lifecycle import AttachmentLifecycleService

            lifecycle = AttachmentLifecycleService()
            lifecycle.record_file_hash(attachment)

            data = serialize_attachment(attachment)
            data.update(
                {
                    "id": attachment.id,
                    "mime_type": attachment.mime_type,
                }
            )

            return success_response(data=data, message="文件上传成功", http_status=status.HTTP_201_CREATED)
        except Exception:
            logger.exception("上传附件失败")
            return error_response(ErrorCode.SERVER_ERROR, message="操作失败，请稍后重试")


class ChatAttachmentListView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(exclude=True)
    def get(self, request, session_id):
        try:
            from Django_xm.apps.chat.services.cross_app import get_chat_session_strict

            try:
                session = get_chat_session_strict(session_id, request.user)
            except Exception:
                return error_response(
                    code=ErrorCode.NOT_FOUND, message="会话不存在", http_status=status.HTTP_404_NOT_FOUND
                )

            attachments = ChatAttachment.objects.filter(session=session).order_by("-created_at")

            data = [serialize_attachment(att) for att in attachments]
            return success_response(data=data)
        except Exception:
            logger.exception("获取附件列表失败")
            return error_response(ErrorCode.SERVER_ERROR, message="操作失败，请稍后重试")


class ChatAttachmentDeleteView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(exclude=True)
    def delete(self, request, attachment_id):
        try:
            attachment = ChatAttachment.objects.filter(
                id=attachment_id, session__user=request.user, is_deleted=False
            ).first()
            if not attachment:
                return error_response(
                    code=ErrorCode.NOT_FOUND, message="附件不存在", http_status=status.HTTP_404_NOT_FOUND
                )

            from Django_xm.apps.attachments.services.attachment_lifecycle import AttachmentLifecycleService

            lifecycle = AttachmentLifecycleService()
            success, message = lifecycle.manual_delete(attachment_id, triggered_by="user")
            if not success:
                return error_response(
                    code=ErrorCode.INVALID_PARAMS, message=message, http_status=status.HTTP_400_BAD_REQUEST
                )
            return success_response(message=message)
        except Exception:
            logger.exception("删除附件失败")
            return error_response(
                code=ErrorCode.SERVER_ERROR, message="删除附件失败", http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class AttachmentAdminListView(APIView):
    permission_classes = [IsAdmin]

    @extend_schema(operation_id="attachments_admin_list", responses={200: None})
    def get(self, request):
        try:
            params = {
                "page": request.query_params.get("page", 1),
                "page_size": request.query_params.get("page_size", 20),
                "status": request.query_params.get("status", ""),
                "search": request.query_params.get("search", ""),
                "sort_by": request.query_params.get("sort_by", "-created_at"),
                "trashed": request.query_params.get("trashed", ""),
            }

            result = build_admin_list(params)
            return success_response(data=result)
        except Exception:
            logger.exception("获取附件管理列表失败")
            return error_response(ErrorCode.SERVER_ERROR, message="操作失败，请稍后重试")


class AttachmentAdminDetailView(APIView):
    permission_classes = [IsAdmin]

    @extend_schema(exclude=True)
    def get(self, request, attachment_id):
        try:
            try:
                att = ChatAttachment.all_objects.select_related("session", "session__user").get(id=attachment_id)
                if att.is_deleted:
                    return error_response(
                        code=ErrorCode.NOT_FOUND, message="附件不存在", http_status=status.HTTP_404_NOT_FOUND
                    )
            except ChatAttachment.DoesNotExist:
                return error_response(
                    code=ErrorCode.NOT_FOUND, message="附件不存在", http_status=status.HTTP_404_NOT_FOUND
                )

            data = serialize_attachment_detail(att)
            return success_response(data=data)
        except Exception:
            logger.exception("获取附件详情失败")
            return error_response(ErrorCode.SERVER_ERROR, message="操作失败，请稍后重试")

    @extend_schema(exclude=True)
    def delete(self, request, attachment_id):
        try:
            from Django_xm.apps.attachments.services.attachment_lifecycle import AttachmentLifecycleService

            service = AttachmentLifecycleService()
            success, message = service.manual_delete(attachment_id, triggered_by=f"admin:{request.user.username}")
            if success:
                return success_response(message=message)
            if "不存在" in message or "已删除" in message:
                return error_response(code=ErrorCode.NOT_FOUND, message=message)
            return error_response(code=ErrorCode.SERVER_ERROR, message=message)
        except Exception:
            logger.exception("删除附件失败")
            return error_response(ErrorCode.SERVER_ERROR, message="操作失败，请稍后重试")


class AttachmentAdminActionView(APIView):
    permission_classes = [IsAdmin]

    @extend_schema(exclude=True)
    def post(self, request, attachment_id):
        try:
            action = request.data.get("action", "")

            from Django_xm.apps.attachments.services.attachment_lifecycle import AttachmentLifecycleService

            service = AttachmentLifecycleService()

            try:
                att = ChatAttachment.all_objects.get(id=attachment_id)
            except ChatAttachment.DoesNotExist:
                return error_response(code=ErrorCode.NOT_FOUND, message="附件不存在")

            if action in ("index", "unindex", "update_retention"):
                if att.is_deleted or att.status == AttachmentStatus.DELETED:
                    return error_response(code=ErrorCode.NOT_FOUND, message="附件已删除，请在回收站中操作")

            if action == "index":
                success, message = service.index_attachment_by_id(
                    attachment_id, triggered_by=f"admin:{request.user.username}"
                )
                if success:
                    return success_response(message=message)
                return error_response(code=ErrorCode.INVALID_PARAMS, message=message)

            elif action == "unindex":
                success, message = service.unindex_attachment_by_id(
                    attachment_id, triggered_by=f"admin:{request.user.username}"
                )
                if success:
                    return success_response(message=message)
                return error_response(code=ErrorCode.INVALID_PARAMS, message=message)

            elif action == "update_retention":
                days = request.data.get("retention_days")
                if days is None or days < 1:
                    return error_response(code=ErrorCode.INVALID_PARAMS, message="保留天数必须大于0")
                ChatAttachment.objects.filter(pk=attachment_id, is_deleted=False).update(retention_days=days)
                return success_response(message=f"保留天数已更新为 {days} 天")

            elif action == "permanent_delete":
                success, message = service.permanent_delete(
                    attachment_id, triggered_by=f"admin:{request.user.username}"
                )
                if success:
                    return success_response(message=message)
                return error_response(code=ErrorCode.SERVER_ERROR, message=message)

            elif action == "restore_from_trash":
                success, message = service.restore_from_trash(attachment_id)
                if success:
                    return success_response(message=message)
                return error_response(code=ErrorCode.SERVER_ERROR, message=message)

            else:
                return error_response(code=ErrorCode.INVALID_PARAMS, message=f"不支持的操作: {action}")
        except Exception:
            logger.exception("附件管理操作失败")
            return error_response(ErrorCode.SERVER_ERROR, message="操作失败，请稍后重试")


class AttachmentAdminCleanupView(APIView):
    permission_classes = [IsAdmin]

    @extend_schema(exclude=True)
    def post(self, request):
        try:
            action = request.data.get("action", "cleanup")
            dry_run = request.data.get("dry_run", False)

            from Django_xm.apps.attachments.services.attachment_lifecycle import AttachmentLifecycleService

            service = AttachmentLifecycleService()

            if action == "cleanup":
                log = service.cleanup_expired(dry_run=dry_run, triggered_by=f"admin:{request.user.username}")
            elif action == "index":
                log = service.index_old_attachments(dry_run=dry_run, triggered_by=f"admin:{request.user.username}")
            else:
                return error_response(code=ErrorCode.INVALID_PARAMS, message=f"不支持的操作: {action}")

            return success_response(
                data={
                    "action": log.action,
                    "files_processed": log.files_processed,
                    "files_deleted": log.files_deleted,
                    "files_archived": log.files_archived,
                    "files_skipped": log.files_skipped,
                    "space_freed_mb": round(log.space_freed / 1024 / 1024, 2),
                    "space_archived_mb": round(log.space_archived / 1024 / 1024, 2),
                    "errors": log.errors,
                    "dry_run": dry_run,
                }
            )
        except Exception:
            logger.exception("附件清理操作失败")
            return error_response(ErrorCode.SERVER_ERROR, message="操作失败，请稍后重试")


class AttachmentAdminStatsView(APIView):
    permission_classes = [IsAdmin]

    @extend_schema(exclude=True)
    def get(self, request):
        try:
            data = get_admin_stats()
            return success_response(data=data)
        except Exception:
            logger.exception("获取附件统计失败")
            return error_response(ErrorCode.SERVER_ERROR, message="操作失败，请稍后重试")


class AttachmentAdminBatchView(APIView):
    permission_classes = [IsAdmin]

    @extend_schema(exclude=True)
    def post(self, request):
        try:
            action = request.data.get("action", "")
            attachment_ids = request.data.get("attachment_ids", [])

            if not attachment_ids or not isinstance(attachment_ids, list):
                return error_response(code=ErrorCode.INVALID_PARAMS, message="请提供附件ID列表")

            from Django_xm.apps.attachments.services.attachment_lifecycle import AttachmentLifecycleService

            service = AttachmentLifecycleService()

            if action == "index":
                results = service.batch_index(attachment_ids, triggered_by=f"admin:{request.user.username}")
            elif action == "unindex":
                results = service.batch_unindex(attachment_ids, triggered_by=f"admin:{request.user.username}")
            elif action == "delete":
                results = service.batch_delete(attachment_ids, triggered_by=f"admin:{request.user.username}")
            else:
                return error_response(code=ErrorCode.INVALID_PARAMS, message=f"不支持的操作: {action}")

            return success_response(data=results)
        except Exception:
            logger.exception("附件批量操作失败")
            return error_response(ErrorCode.SERVER_ERROR, message="操作失败，请稍后重试")


class StorageAlertView(APIView):
    """存储告警视图基类。

    历史上同时承载 list / detail / action 三类语义（通过 ``alert_id`` 区分）。
    为修复 drf_spectacular W001 operationId 冲突，拆分为 ``StorageAlertListView``
    与 ``StorageAlertDetailView`` 两个子类，分别挂载到不同 URL。本基类保留所有逻辑，
    子类只覆盖 ``@extend_schema`` 装饰器，不改变任何运行时行为。
    """

    permission_classes = [IsAdmin]

    def get(self, request, alert_id=None):
        try:
            if alert_id:
                try:
                    alert = StorageAlert.objects.get(id=alert_id)
                    data = serialize_storage_alert(alert)
                    return success_response(data=data)
                except StorageAlert.DoesNotExist:
                    return error_response(code=ErrorCode.NOT_FOUND, message="告警不存在")

            alerts = StorageAlert.objects.all().order_by("-created_at")[:500]
            data = [serialize_storage_alert(a) for a in alerts]
            return success_response(data=data)
        except Exception:
            logger.exception("获取存储告警失败")
            return error_response(ErrorCode.SERVER_ERROR, message="操作失败，请稍后重试")

    def post(self, request, alert_id=None):
        try:
            if not alert_id:
                return error_response(code=ErrorCode.INVALID_PARAMS, message="请指定告警ID")

            action = request.data.get("action", "acknowledge")
            success, message = handle_alert_action(alert_id, action)

            if not success:
                if "不存在" in message:
                    return error_response(code=ErrorCode.NOT_FOUND, message=message)
                return error_response(code=ErrorCode.INVALID_PARAMS, message=message)

            return success_response(message=message)
        except Exception:
            logger.exception("处理存储告警失败")
            return error_response(ErrorCode.SERVER_ERROR, message="操作失败，请稍后重试")


class StorageAlertListView(StorageAlertView):
    """存储告警列表端点：GET /api/v1/attachments/admin/storage-alerts/"""

    @extend_schema(operation_id="attachments_admin_storage_alerts_list", responses={200: None})
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @extend_schema(exclude=True)
    def post(self, request, *args, **kwargs):
        # POST 在 list 端点无意义（无 alert_id），保留以维持向后兼容
        return super().post(request, *args, **kwargs)


class StorageAlertDetailView(StorageAlertView):
    """存储告警详情/操作端点：GET/POST /api/v1/attachments/admin/storage-alerts/{alert_id}/"""

    @extend_schema(operation_id="attachments_admin_storage_alerts_retrieve", responses={200: EmptySerializer})
    def get(self, request, alert_id, *args, **kwargs):
        return super().get(request, alert_id=alert_id, *args, **kwargs)

    @extend_schema(operation_id="attachments_admin_storage_alerts_action", request=EmptySerializer, responses={200: EmptySerializer})
    def post(self, request, alert_id, *args, **kwargs):
        return super().post(request, alert_id=alert_id, *args, **kwargs)
