"""
附件验证与管理服务层

封装附件上传验证、数据序列化、管理员查询构建等业务逻辑，
视图层只负责请求解析和响应构建。
"""

import os
import logging
import mimetypes
from typing import Dict, List, Optional, Any, Tuple

from django.db import models
from django.core.paginator import Paginator
from django.utils import timezone

from ..models import ChatAttachment, AttachmentStatus, StorageAlert, AttachmentCleanupLog

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {
    'txt', 'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx',
    'png', 'jpg', 'jpeg', 'gif', 'webp', 'svg',
    'csv', 'json', 'xml', 'md', 'py', 'js', 'ts', 'html', 'css',
}

MAX_FILE_SIZE = 10 * 1024 * 1024

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


def validate_upload_file(uploaded_file) -> Tuple[bool, str, Optional[str]]:
    """
    验证上传文件的大小、扩展名和 MIME 类型

    Returns:
        (is_valid, error_message, mime_type)
    """
    if uploaded_file.size > MAX_FILE_SIZE:
        return False, f'文件大小不能超过{MAX_FILE_SIZE // (1024*1024)}MB', None

    ext = os.path.splitext(uploaded_file.name)[1].lower().lstrip('.')
    if ext not in ALLOWED_EXTENSIONS:
        return False, f'不支持的文件类型: .{ext}，支持的类型: {", ".join(sorted(ALLOWED_EXTENSIONS))}', None

    mime_type, _ = mimetypes.guess_type(uploaded_file.name)

    if ext in VALIDATED_MIME_MAP:
        header = uploaded_file.read(512)
        uploaded_file.seek(0)
        if ext in ('png', 'jpg', 'jpeg', 'gif', 'webp'):
            import filetype
            kind = filetype.guess(header)
            if kind is None or kind.mime not in VALIDATED_MIME_MAP[ext]:
                return False, f'文件内容与扩展名 .{ext} 不匹配，可能存在安全风险', None

    return True, '', mime_type or 'application/octet-stream'


def serialize_attachment(att: ChatAttachment, include_index_info: bool = True) -> Dict[str, Any]:
    """将附件对象序列化为 API 响应字典"""
    file_url = att.file.url if hasattr(att.file, 'url') else ''
    data = {
        'id': att.id,
        'original_name': att.original_name,
        'file_size': att.file_size,
        'file_type': att.file_type,
        'mime_type': att.mime_type,
        'url': file_url,
        'message_id': att.message_id,
        'created_at': att.created_at.isoformat(),
    }
    return data


def serialize_attachment_detail(att: ChatAttachment) -> Dict[str, Any]:
    """将附件对象序列化为管理员详情响应字典"""
    data = {
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
    }
    return data


def build_admin_list(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    构建管理员附件列表，包含查询、过滤、分页和统计

    Args:
        params: 包含 page, page_size, status, search, sort_by, trashed 的字典

    Returns:
        包含 items, total, page, page_size, total_pages, stats 的字典
    """
    from .attachment_lifecycle import AttachmentLifecycleService

    page = int(params.get('page', 1))
    page_size = int(params.get('page_size', 20))
    status_filter = params.get('status', '')
    search = params.get('search', '')
    sort_by = params.get('sort_by', '-created_at')
    trashed = params.get('trashed', '').lower() in ('1', 'true', 'yes')

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

    service = AttachmentLifecycleService()
    stats = service.get_storage_stats()

    return {
        'items': items,
        'total': paginator.count,
        'page': page,
        'page_size': page_size,
        'total_pages': paginator.num_pages,
        'stats': stats,
    }


def get_admin_stats() -> Dict[str, Any]:
    """
    获取管理员统计信息，包含存储统计、告警和最近日志

    Returns:
        包含 storage, alert, recent_logs 的字典
    """
    from .attachment_lifecycle import AttachmentLifecycleService

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

    return {
        'storage': stats,
        'alert': alert_data,
        'recent_logs': log_data,
    }


def serialize_storage_alert(alert: StorageAlert) -> Dict[str, Any]:
    """将存储告警对象序列化为 API 响应字典"""
    return {
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
    }


def handle_alert_action(alert_id: int, action: str) -> Tuple[bool, str]:
    """
    处理存储告警操作（确认/解决）

    Returns:
        (success, message)
    """
    try:
        alert = StorageAlert.objects.get(id=alert_id)
    except StorageAlert.DoesNotExist:
        return False, '告警不存在'

    if action == 'acknowledge':
        alert.status = 'acknowledged'
        alert.acknowledged_at = timezone.now()
        alert.save()
        return True, '告警已确认'
    elif action == 'resolve':
        alert.status = 'resolved'
        alert.resolved_at = timezone.now()
        alert.save()
        return True, '告警已解决'

    return False, f'不支持的操作: {action}'
