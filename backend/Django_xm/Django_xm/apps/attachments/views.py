import os
import logging

from django.db import models
from django.utils import timezone
from django.core.paginator import Paginator
from rest_framework.views import APIView
from rest_framework import status
from rest_framework.permissions import IsAuthenticated

from Django_xm.apps.common.responses import success_response, error_response
from Django_xm.apps.common.error_codes import ErrorCode

from .models import ChatAttachment, AttachmentStatus, AttachmentCleanupLog, StorageAlert

logger = logging.getLogger(__name__)


class ChatAttachmentUploadView(APIView):
    permission_classes = [IsAuthenticated]

    ALLOWED_EXTENSIONS = {
        'txt', 'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx',
        'png', 'jpg', 'jpeg', 'gif', 'webp', 'svg',
        'csv', 'json', 'xml', 'md', 'py', 'js', 'ts', 'html', 'css',
    }
    MAX_FILE_SIZE = 10 * 1024 * 1024

    def post(self, request, session_id):
        from Django_xm.apps.chat.models import ChatSession
        try:
            session = ChatSession.objects.get(session_id=session_id, user=request.user, is_deleted=False)
        except ChatSession.DoesNotExist:
            return error_response(
                code=ErrorCode.NOT_FOUND,
                message='会话不存在',
                http_status=status.HTTP_404_NOT_FOUND
            )

        uploaded_file = request.FILES.get('file')
        if not uploaded_file:
            return error_response(
                code=ErrorCode.INVALID_PARAMS,
                message='请选择要上传的文件'
            )

        if uploaded_file.size > self.MAX_FILE_SIZE:
            return error_response(
                code=ErrorCode.INVALID_PARAMS,
                message=f'文件大小不能超过{self.MAX_FILE_SIZE // (1024*1024)}MB'
            )

        ext = os.path.splitext(uploaded_file.name)[1].lower().lstrip('.')
        if ext not in self.ALLOWED_EXTENSIONS:
            return error_response(
                code=ErrorCode.INVALID_PARAMS,
                message=f'不支持的文件类型: .{ext}，支持的类型: {", ".join(sorted(self.ALLOWED_EXTENSIONS))}'
            )

        import mimetypes
        mime_type, _ = mimetypes.guess_type(uploaded_file.name)

        VALIDATED_MIME_MAP = {
            'pdf': {'application/pdf'},
            'txt': {'text/plain'},
            'md': {'text/plain', 'text/markdown'},
            'csv': {'text/csv', 'text/plain', 'application/vnd.ms-excel'},
            'json': {'application/json', 'text/plain'},
            'py': {'text/x-python', 'text/plain'},
            'js': {'text/javascript', 'application/javascript', 'text/plain'},
            'html': {'text/html', 'text/plain'},
            'css': {'text/css', 'text/plain'},
            'png': {'image/png'},
            'jpg': {'image/jpeg'},
            'jpeg': {'image/jpeg'},
            'gif': {'image/gif'},
            'webp': {'image/webp'},
            'svg': {'image/svg+xml'},
            'docx': {'application/vnd.openxmlformats-officedocument.wordprocessingml.document'},
            'xlsx': {'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'},
        }

        if ext in VALIDATED_MIME_MAP:
            header = uploaded_file.read(512)
            uploaded_file.seek(0)
            import imghdr
            if ext in ('png', 'jpg', 'jpeg', 'gif', 'webp') and imghdr.what(None, header) is None:
                detected_mime = mimetypes.guess_type(uploaded_file.name)[0] or 'application/octet-stream'
                if detected_mime not in VALIDATED_MIME_MAP[ext]:
                    return error_response(
                        code=ErrorCode.INVALID_PARAMS,
                        message=f'文件内容与扩展名 .{ext} 不匹配，可能存在安全风险'
                    )

        attachment = ChatAttachment.objects.create(
            session=session,
            file=uploaded_file,
            original_name=uploaded_file.name,
            file_size=uploaded_file.size,
            file_type=ext,
            mime_type=mime_type or 'application/octet-stream',
        )

        from Django_xm.apps.attachments.services.attachment_lifecycle import AttachmentLifecycleService
        lifecycle = AttachmentLifecycleService()
        lifecycle.record_file_hash(attachment)

        file_url = attachment.file.url if hasattr(attachment.file, 'url') else ''

        return success_response(
            data={
                'id': attachment.id,
                'original_name': attachment.original_name,
                'file_size': attachment.file_size,
                'file_type': attachment.file_type,
                'mime_type': attachment.mime_type,
                'url': file_url,
                'created_at': attachment.created_at.isoformat(),
            },
            message='文件上传成功',
            status_code=status.HTTP_201_CREATED
        )


class ChatAttachmentListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, session_id):
        from Django_xm.apps.chat.models import ChatSession
        try:
            session = ChatSession.objects.get(session_id=session_id, user=request.user, is_deleted=False)
        except ChatSession.DoesNotExist:
            return error_response(
                code=ErrorCode.NOT_FOUND,
                message='会话不存在',
                http_status=status.HTTP_404_NOT_FOUND
            )

        attachments = ChatAttachment.objects.filter(
            session=session
        ).order_by('-created_at')

        data = []
        for att in attachments:
            file_url = att.file.url if hasattr(att.file, 'url') else ''
            data.append({
                'id': att.id,
                'original_name': att.original_name,
                'file_size': att.file_size,
                'file_type': att.file_type,
                'mime_type': att.mime_type,
                'url': file_url,
                'message_id': att.message_id,
                'created_at': att.created_at.isoformat(),
            })

        return success_response(data=data)


class ChatAttachmentDeleteView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, attachment_id):
        try:
            attachment = ChatAttachment.objects.filter(
                id=attachment_id,
                session__user=request.user,
                is_deleted=False
            ).first()
            if not attachment:
                return error_response(
                    code=ErrorCode.NOT_FOUND,
                    message='附件不存在',
                    http_status=status.HTTP_404_NOT_FOUND
                )

            from Django_xm.apps.attachments.services.attachment_lifecycle import AttachmentLifecycleService
            lifecycle = AttachmentLifecycleService()
            success, message = lifecycle.manual_delete(attachment_id, triggered_by='user')
            if not success:
                return error_response(
                    code=ErrorCode.INVALID_PARAMS,
                    message=message,
                    http_status=status.HTTP_400_BAD_REQUEST
                )
            return success_response(message=message)
        except Exception as e:
            logger.error(f"删除附件失败: {e}", exc_info=True)
            return error_response(
                code=ErrorCode.SERVER_ERROR,
                message='删除附件失败',
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class AttachmentAdminListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not request.user.is_staff:
            return error_response(code=ErrorCode.PERMISSION_DENIED, message='无管理员权限', http_status=status.HTTP_403_FORBIDDEN)

        from Django_xm.apps.attachments.services.attachment_lifecycle import AttachmentLifecycleService
        service = AttachmentLifecycleService()

        page = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 20))
        status_filter = request.query_params.get('status', '')
        search = request.query_params.get('search', '')
        sort_by = request.query_params.get('sort_by', '-created_at')
        trashed = request.query_params.get('trashed', '').lower() in ('1', 'true', 'yes')

        if trashed:
            queryset = ChatAttachment.all_objects.select_related('session', 'session__user').filter(
                models.Q(is_deleted=True) | models.Q(status=AttachmentStatus.DELETED)
            )
        else:
            queryset = ChatAttachment.all_objects.select_related('session', 'session__user').filter(
                is_deleted=False
            ).exclude(
                status=AttachmentStatus.DELETED
            )
            if status_filter:
                queryset = queryset.filter(status=status_filter)

        if search:
            queryset = queryset.filter(
                models.Q(original_name__icontains=search)
                | models.Q(session__session_id__icontains=search)
                | models.Q(session__title__icontains=search)
            )

        valid_sorts = ['created_at', '-created_at', 'file_size', '-file_size', 'last_accessed_at', '-last_accessed_at']
        if sort_by not in valid_sorts:
            sort_by = '-created_at'
        queryset = queryset.order_by(sort_by)

        paginator = Paginator(queryset, page_size)
        page_obj = paginator.get_page(page)

        items = []
        for att in page_obj:
            item = {
                'id': att.id,
                'original_name': att.original_name,
                'file_size': att.file_size,
                'file_type': att.file_type,
                'mime_type': att.mime_type,
                'status': att.status,
                'reference_count': att.reference_count,
                'last_accessed_at': att.last_accessed_at.isoformat() if att.last_accessed_at else None,
                'retention_days': att.retention_days,
                'file_hash': att.file_hash,
                'session_id': att.session.session_id if att.session else None,
                'session_title': att.session.title if att.session else None,
                'username': att.session.user.username if att.session and att.session.user else None,
                'created_at': att.created_at.isoformat(),
                'is_deleted': att.is_deleted,
                'deleted_at': att.deleted_at.isoformat() if att.deleted_at else None,
            }
            if not trashed:
                item['indexed_path'] = att.indexed_path
                item['indexed_at'] = att.indexed_at.isoformat() if att.indexed_at else None
            items.append(item)

        stats = service.get_storage_stats()
        return success_response(data={
            'items': items,
            'total': paginator.count,
            'page': page,
            'page_size': page_size,
            'total_pages': paginator.num_pages,
            'stats': stats,
        })


class AttachmentAdminDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, attachment_id):
        if not request.user.is_staff:
            return error_response(code=ErrorCode.PERMISSION_DENIED, message='无管理员权限', http_status=status.HTTP_403_FORBIDDEN)

        try:
            att = ChatAttachment.all_objects.select_related('session', 'session__user').get(id=attachment_id)
            if att.is_deleted:
                return error_response(code=ErrorCode.NOT_FOUND, message='附件不存在', http_status=status.HTTP_404_NOT_FOUND)
        except ChatAttachment.DoesNotExist:
            return error_response(code=ErrorCode.NOT_FOUND, message='附件不存在', http_status=status.HTTP_404_NOT_FOUND)

        return success_response(data={
            'id': att.id,
            'original_name': att.original_name,
            'file_size': att.file_size,
            'file_type': att.file_type,
            'mime_type': att.mime_type,
            'status': att.status,
            'reference_count': att.reference_count,
            'last_accessed_at': att.last_accessed_at.isoformat() if att.last_accessed_at else None,
            'retention_days': att.retention_days,
            'indexed_path': att.indexed_path,
            'indexed_at': att.indexed_at.isoformat() if att.indexed_at else None,
            'file_hash': att.file_hash,
            'session_id': att.session.session_id if att.session else None,
            'session_title': att.session.title if att.session else None,
            'username': att.session.user.username if att.session and att.session.user else None,
            'created_at': att.created_at.isoformat(),
            'updated_at': att.updated_at.isoformat(),
            'is_deleted': att.is_deleted,
            'is_expired': att.is_expired(),
            'can_safely_delete': att.can_safely_delete(),
        })

    def delete(self, request, attachment_id):
        if not request.user.is_staff:
            return error_response(code=ErrorCode.PERMISSION_DENIED, message='无管理员权限', http_status=status.HTTP_403_FORBIDDEN)

        from Django_xm.apps.attachments.services.attachment_lifecycle import AttachmentLifecycleService
        service = AttachmentLifecycleService()
        success, message = service.manual_delete(attachment_id, triggered_by=f'admin:{request.user.username}')
        if success:
            return success_response(message=message)
        if '不存在' in message or '已删除' in message:
            return error_response(code=ErrorCode.NOT_FOUND, message=message)
        return error_response(code=ErrorCode.SERVER_ERROR, message=message)


class AttachmentAdminActionView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, attachment_id):
        if not request.user.is_staff:
            return error_response(code=ErrorCode.PERMISSION_DENIED, message='无管理员权限', http_status=status.HTTP_403_FORBIDDEN)

        action = request.data.get('action', '')

        from Django_xm.apps.attachments.services.attachment_lifecycle import AttachmentLifecycleService
        service = AttachmentLifecycleService()

        try:
            att = ChatAttachment.all_objects.get(id=attachment_id)
        except ChatAttachment.DoesNotExist:
            return error_response(code=ErrorCode.NOT_FOUND, message='附件不存在')

        if action in ('index', 'unindex', 'update_retention'):
            if att.is_deleted or att.status == AttachmentStatus.DELETED:
                return error_response(code=ErrorCode.NOT_FOUND, message='附件已删除，请在回收站中操作')

        if action == 'index':
            success, message = service.index_attachment_by_id(attachment_id, triggered_by=f'admin:{request.user.username}')
            if success:
                return success_response(message=message)
            return error_response(code=ErrorCode.INVALID_PARAMS, message=message)

        elif action == 'unindex':
            success, message = service.unindex_attachment_by_id(attachment_id, triggered_by=f'admin:{request.user.username}')
            if success:
                return success_response(message=message)
            return error_response(code=ErrorCode.INVALID_PARAMS, message=message)

        elif action == 'update_retention':
            days = request.data.get('retention_days')
            if days is None or days < 1:
                return error_response(code=ErrorCode.INVALID_PARAMS, message='保留天数必须大于0')
            ChatAttachment.objects.filter(pk=attachment_id, is_deleted=False).update(retention_days=days)
            return success_response(message=f'保留天数已更新为 {days} 天')

        elif action == 'permanent_delete':
            success, message = service.permanent_delete(attachment_id, triggered_by=f'admin:{request.user.username}')
            if success:
                return success_response(message=message)
            return error_response(code=ErrorCode.SERVER_ERROR, message=message)

        elif action == 'restore_from_trash':
            success, message = service.restore_from_trash(attachment_id)
            if success:
                return success_response(message=message)
            return error_response(code=ErrorCode.SERVER_ERROR, message=message)

        else:
            return error_response(code=ErrorCode.INVALID_PARAMS, message=f'不支持的操作: {action}')


class AttachmentAdminCleanupView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if not request.user.is_staff:
            return error_response(code=ErrorCode.PERMISSION_DENIED, message='无管理员权限', http_status=status.HTTP_403_FORBIDDEN)

        action = request.data.get('action', 'cleanup')
        dry_run = request.data.get('dry_run', False)

        from Django_xm.apps.attachments.services.attachment_lifecycle import AttachmentLifecycleService
        service = AttachmentLifecycleService()

        if action == 'cleanup':
            log = service.cleanup_expired(dry_run=dry_run, triggered_by=f'admin:{request.user.username}')
        elif action == 'index':
            log = service.index_old_attachments(dry_run=dry_run, triggered_by=f'admin:{request.user.username}')
        else:
            return error_response(code=ErrorCode.INVALID_PARAMS, message=f'不支持的操作: {action}')

        return success_response(data={
            'action': log.action,
            'files_processed': log.files_processed,
            'files_deleted': log.files_deleted,
            'files_archived': log.files_archived,
            'files_skipped': log.files_skipped,
            'space_freed_mb': round(log.space_freed / 1024 / 1024, 2),
            'space_archived_mb': round(log.space_archived / 1024 / 1024, 2),
            'errors': log.errors,
            'dry_run': dry_run,
        })


class AttachmentAdminStatsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not request.user.is_staff:
            return error_response(code=ErrorCode.PERMISSION_DENIED, message='无管理员权限', http_status=status.HTTP_403_FORBIDDEN)

        from Django_xm.apps.attachments.services.attachment_lifecycle import AttachmentLifecycleService
        service = AttachmentLifecycleService()
        stats = service.get_storage_stats()

        alert = service.check_storage_alerts()

        recent_logs = AttachmentCleanupLog.objects.all()[:10]
        log_data = []
        for log in recent_logs:
            log_data.append({
                'id': log.id,
                'action': log.action,
                'started_at': log.started_at.isoformat(),
                'finished_at': log.finished_at.isoformat() if log.finished_at else None,
                'files_processed': log.files_processed,
                'files_deleted': log.files_deleted,
                'files_archived': log.files_archived,
                'space_freed_mb': round(log.space_freed / 1024 / 1024, 2),
                'triggered_by': log.triggered_by,
            })

        active_alerts = StorageAlert.objects.filter(status='active')
        alert_data = []
        for a in active_alerts:
            alert_data.append({
                'id': a.id,
                'level': a.level,
                'usage_percent': a.usage_percent,
                'message': a.message,
                'created_at': a.created_at.isoformat(),
            })

        return success_response(data={
            'storage': stats,
            'alert': alert_data,
            'recent_logs': log_data,
        })


class AttachmentAdminBatchView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if not request.user.is_staff:
            return error_response(code=ErrorCode.PERMISSION_DENIED, message='无管理员权限', http_status=status.HTTP_403_FORBIDDEN)

        action = request.data.get('action', '')
        attachment_ids = request.data.get('attachment_ids', [])

        if not attachment_ids or not isinstance(attachment_ids, list):
            return error_response(code=ErrorCode.INVALID_PARAMS, message='请提供附件ID列表')

        from Django_xm.apps.attachments.services.attachment_lifecycle import AttachmentLifecycleService
        service = AttachmentLifecycleService()

        if action == 'index':
            results = service.batch_index(attachment_ids, triggered_by=f'admin:{request.user.username}')
        elif action == 'unindex':
            results = service.batch_unindex(attachment_ids, triggered_by=f'admin:{request.user.username}')
        elif action == 'delete':
            results = service.batch_delete(attachment_ids, triggered_by=f'admin:{request.user.username}')
        else:
            return error_response(code=ErrorCode.INVALID_PARAMS, message=f'不支持的操作: {action}')

        return success_response(data=results)


class StorageAlertView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, alert_id=None):
        if not request.user.is_staff:
            return error_response(code=ErrorCode.PERMISSION_DENIED, message='无管理员权限', http_status=status.HTTP_403_FORBIDDEN)

        if alert_id:
            try:
                alert = StorageAlert.objects.get(id=alert_id)
                return success_response(data={
                    'id': alert.id,
                    'level': alert.level,
                    'status': alert.status,
                    'storage_path': alert.storage_path,
                    'usage_percent': alert.usage_percent,
                    'threshold_percent': alert.threshold_percent,
                    'message': alert.message,
                    'created_at': alert.created_at.isoformat(),
                    'acknowledged_at': alert.acknowledged_at.isoformat() if alert.acknowledged_at else None,
                    'resolved_at': alert.resolved_at.isoformat() if alert.resolved_at else None,
                })
            except StorageAlert.DoesNotExist:
                return error_response(code=ErrorCode.NOT_FOUND, message='告警不存在')

        alerts = StorageAlert.objects.all().order_by('-created_at')
        data = []
        for a in alerts:
            data.append({
                'id': a.id,
                'level': a.level,
                'status': a.status,
                'storage_path': a.storage_path,
                'usage_percent': a.usage_percent,
                'threshold_percent': a.threshold_percent,
                'message': a.message,
                'created_at': a.created_at.isoformat(),
                'acknowledged_at': a.acknowledged_at.isoformat() if a.acknowledged_at else None,
                'resolved_at': a.resolved_at.isoformat() if a.resolved_at else None,
            })
        return success_response(data=data)

    def post(self, request, alert_id=None):
        if not request.user.is_staff:
            return error_response(code=ErrorCode.PERMISSION_DENIED, message='无管理员权限', http_status=status.HTTP_403_FORBIDDEN)

        if not alert_id:
            return error_response(code=ErrorCode.INVALID_PARAMS, message='请指定告警ID')

        action = request.data.get('action', 'acknowledge')
        try:
            alert = StorageAlert.objects.get(id=alert_id)
        except StorageAlert.DoesNotExist:
            return error_response(code=ErrorCode.NOT_FOUND, message='告警不存在')

        if action == 'acknowledge':
            alert.status = 'acknowledged'
            alert.acknowledged_at = timezone.now()
            alert.save()
            return success_response(message='告警已确认')
        elif action == 'resolve':
            alert.status = 'resolved'
            alert.resolved_at = timezone.now()
            alert.save()
            return success_response(message='告警已解决')

        return error_response(code=ErrorCode.INVALID_PARAMS, message=f'不支持的操作: {action}')
