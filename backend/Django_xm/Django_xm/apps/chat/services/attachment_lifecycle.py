import os
import shutil
import hashlib
import logging
from pathlib import Path
from datetime import timedelta
from typing import Dict, List, Optional, Tuple
from django.db import transaction, models
from django.conf import settings
from django.utils import timezone

from Django_xm.apps.chat.models import (
    ChatAttachment,
    AttachmentStatus,
    AttachmentCleanupLog,
    StorageAlert,
)

logger = logging.getLogger(__name__)


class AttachmentLifecycleService:
    def __init__(self):
        self.media_root = Path(settings.MEDIA_ROOT)
        self.archive_dir = Path(getattr(settings, 'ATTACHMENT_ARCHIVE_DIR', self.media_root.parent / 'archives'))
        self.default_retention_days = getattr(settings, 'ATTACHMENT_DEFAULT_RETENTION_DAYS', 30)
        self.archive_after_days = getattr(settings, 'ATTACHMENT_ARCHIVE_AFTER_DAYS', 60)
        self.archive_enabled = getattr(settings, 'ATTACHMENT_ARCHIVE_ENABLED', True)
        self.dedup_enabled = getattr(settings, 'ATTACHMENT_DEDUP_ENABLED', True)
        self.warning_threshold = getattr(settings, 'ATTACHMENT_STORAGE_WARNING_THRESHOLD', 80)
        self.critical_threshold = getattr(settings, 'ATTACHMENT_STORAGE_CRITICAL_THRESHOLD', 95)

    def compute_file_hash(self, file_path: str) -> str:
        h = hashlib.sha256()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                h.update(chunk)
        return h.hexdigest()

    def get_storage_stats(self) -> Dict:
        attachments = ChatAttachment.objects.filter(
            status__in=[AttachmentStatus.ACTIVE, AttachmentStatus.ARCHIVED]
        )
        total_size = attachments.aggregate(total=models.Sum('file_size'))['total'] or 0
        total_count = attachments.count()
        active_count = attachments.filter(status=AttachmentStatus.ACTIVE).count()
        archived_count = attachments.filter(status=AttachmentStatus.ARCHIVED).count()

        disk_usage = self._get_disk_usage()

        return {
            'total_files': total_count,
            'active_files': active_count,
            'archived_files': archived_count,
            'total_size_bytes': total_size,
            'total_size_mb': round(total_size / (1024 * 1024), 2),
            'disk_total_bytes': disk_usage['total'],
            'disk_used_bytes': disk_usage['used'],
            'disk_free_bytes': disk_usage['free'],
            'disk_usage_percent': disk_usage['percent'],
        }

    def _get_disk_usage(self) -> Dict:
        try:
            disk_usage = shutil.disk_usage(str(self.media_root))
            percent = (disk_usage.used / disk_usage.total * 100) if disk_usage.total > 0 else 0
            return {
                'total': disk_usage.total,
                'used': disk_usage.used,
                'free': disk_usage.free,
                'percent': round(percent, 2),
            }
        except Exception as e:
            logger.error(f"获取磁盘使用信息失败: {e}")
            return {'total': 0, 'used': 0, 'free': 0, 'percent': 0}

    def check_storage_alerts(self) -> Optional[StorageAlert]:
        disk_usage = self._get_disk_usage()
        usage_percent = disk_usage['percent']

        if usage_percent >= self.critical_threshold:
            level = 'critical'
        elif usage_percent >= self.warning_threshold:
            level = 'warning'
        else:
            return None

        existing = StorageAlert.objects.filter(
            status='active',
            level=level,
        ).first()
        if existing:
            return None

        alert = StorageAlert.objects.create(
            level=level,
            storage_path=str(self.media_root),
            total_space=disk_usage['total'],
            used_space=disk_usage['used'],
            usage_percent=usage_percent,
            threshold_percent=self.critical_threshold if level == 'critical' else self.warning_threshold,
            message=f"存储空间使用率达到 {usage_percent:.1f}%，已超过{'严重' if level == 'critical' else '警告'}阈值",
        )
        logger.warning(f"存储空间告警: {alert.message}")
        return alert

    def find_expired_attachments(self) -> models.QuerySet:
        retention_days = self.default_retention_days
        cutoff = timezone.now() - timedelta(days=retention_days)
        return ChatAttachment.objects.filter(
            status=AttachmentStatus.ACTIVE,
            created_at__lt=cutoff,
        )

    def find_archive_candidates(self) -> models.QuerySet:
        cutoff = timezone.now() - timedelta(days=self.archive_after_days)
        return ChatAttachment.objects.filter(
            status=AttachmentStatus.ACTIVE,
            created_at__lt=cutoff,
            reference_count__gt=0,
        )

    def find_duplicates(self) -> Dict[str, List[int]]:
        if not self.dedup_enabled:
            return {}

        hashes = {}
        attachments = ChatAttachment.objects.filter(
            status=AttachmentStatus.ACTIVE,
            file_hash__gt='',
        ).values_list('id', 'file_hash')

        for att_id, file_hash in attachments:
            if file_hash in hashes:
                hashes[file_hash].append(att_id)
            else:
                hashes[file_hash] = [att_id]

        return {h: ids for h, ids in hashes.items() if len(ids) > 1}

    @transaction.atomic
    def cleanup_expired(self, dry_run: bool = False, triggered_by: str = 'system') -> AttachmentCleanupLog:
        started_at = timezone.now()
        log = AttachmentCleanupLog.objects.create(
            action='cleanup',
            started_at=started_at,
            triggered_by=triggered_by,
        )

        expired = self.find_expired_attachments()
        files_processed = 0
        files_deleted = 0
        files_archived = 0
        files_skipped = 0
        space_freed = 0
        space_archived = 0
        errors = []

        for attachment in expired.iterator():
            files_processed += 1
            try:
                if self.archive_enabled and not attachment.is_expired():
                    self._archive_attachment(attachment, dry_run)
                    files_archived += 1
                    space_archived += attachment.file_size
                    continue

                if attachment.reference_count > 0 and not attachment.is_expired():
                    files_skipped += 1
                    continue

                if not dry_run:
                    self._delete_attachment(attachment)
                files_deleted += 1
                space_freed += attachment.file_size
            except Exception as e:
                error_msg = f"附件 {attachment.id} ({attachment.original_name}) 处理失败: {str(e)}"
                logger.error(error_msg)
                errors.append(error_msg)

        log.finished_at = timezone.now()
        log.files_processed = files_processed
        log.files_deleted = files_deleted
        log.files_archived = files_archived
        log.files_skipped = files_skipped
        log.space_freed = space_freed
        log.space_archived = space_archived
        log.errors = errors
        log.details = {
            'dry_run': dry_run,
            'retention_days': self.default_retention_days,
            'archive_enabled': self.archive_enabled,
        }
        log.save()

        logger.info(
            f"附件清理完成: 处理={files_processed}, 删除={files_deleted}, "
            f"归档={files_archived}, 跳过={files_skipped}, "
            f"释放={space_freed / 1024 / 1024:.2f}MB"
        )
        return log

    def _archive_attachment(self, attachment: ChatAttachment, dry_run: bool = False):
        if dry_run:
            return

        source_path = attachment.file.path
        if not os.path.exists(source_path):
            return

        session_id = attachment.session.session_id
        archive_subdir = self.archive_dir / session_id
        archive_subdir.mkdir(parents=True, exist_ok=True)

        dest_path = archive_subdir / os.path.basename(source_path)
        shutil.move(source_path, dest_path)

        attachment.status = AttachmentStatus.ARCHIVED
        attachment.archived_path = str(dest_path)
        attachment.archived_at = timezone.now()
        attachment.save(update_fields=['status', 'archived_path', 'archived_at'])

        logger.info(f"附件已归档: {attachment.original_name} -> {dest_path}")

    def _delete_attachment(self, attachment: ChatAttachment):
        file_path = attachment.file.path
        if os.path.exists(file_path):
            os.remove(file_path)
            parent = os.path.dirname(file_path)
            if os.path.isdir(parent) and not os.listdir(parent):
                os.rmdir(parent)

        attachment.status = AttachmentStatus.DELETED
        attachment.save(update_fields=['status'])

        logger.info(f"附件已删除: {attachment.original_name} (id={attachment.id})")

    @transaction.atomic
    def archive_old_attachments(self, dry_run: bool = False, triggered_by: str = 'system') -> AttachmentCleanupLog:
        started_at = timezone.now()
        log = AttachmentCleanupLog.objects.create(
            action='archive',
            started_at=started_at,
            triggered_by=triggered_by,
        )

        candidates = self.find_archive_candidates()
        files_processed = 0
        files_archived = 0
        space_archived = 0
        errors = []

        for attachment in candidates.iterator():
            files_processed += 1
            try:
                self._archive_attachment(attachment, dry_run)
                files_archived += 1
                space_archived += attachment.file_size
            except Exception as e:
                error_msg = f"附件 {attachment.id} 归档失败: {str(e)}"
                logger.error(error_msg)
                errors.append(error_msg)

        log.finished_at = timezone.now()
        log.files_processed = files_processed
        log.files_archived = files_archived
        log.space_archived = space_archived
        log.errors = errors
        log.details = {'dry_run': dry_run, 'archive_after_days': self.archive_after_days}
        log.save()

        logger.info(f"附件归档完成: 处理={files_processed}, 归档={files_archived}, 空间={space_archived / 1024 / 1024:.2f}MB")
        return log

    @transaction.atomic
    def restore_attachment(self, attachment_id: int) -> bool:
        try:
            attachment = ChatAttachment.objects.get(id=attachment_id, status=AttachmentStatus.ARCHIVED)
        except ChatAttachment.DoesNotExist:
            logger.error(f"归档附件不存在: id={attachment_id}")
            return False

        if not attachment.archived_path or not os.path.exists(attachment.archived_path):
            logger.error(f"归档文件不存在: {attachment.archived_path}")
            return False

        original_dir = os.path.dirname(attachment.file.path)
        os.makedirs(original_dir, exist_ok=True)

        shutil.move(attachment.archived_path, attachment.file.path)

        attachment.status = AttachmentStatus.ACTIVE
        attachment.archived_path = ''
        attachment.archived_at = None
        attachment.save(update_fields=['status', 'archived_path', 'archived_at'])

        AttachmentCleanupLog.objects.create(
            action='restore',
            started_at=timezone.now(),
            finished_at=timezone.now(),
            files_processed=1,
            details={'attachment_id': attachment_id, 'original_name': attachment.original_name},
            triggered_by='manual',
        )

        logger.info(f"附件已恢复: {attachment.original_name}")
        return True

    @transaction.atomic
    def manual_delete(self, attachment_id: int, triggered_by: str = 'admin') -> Tuple[bool, str]:
        try:
            attachment = ChatAttachment.objects.get(id=attachment_id)
        except ChatAttachment.DoesNotExist:
            return False, f"附件不存在: id={attachment_id}"

        if attachment.status == AttachmentStatus.DELETED:
            return False, "附件已被删除"

        started_at = timezone.now()
        try:
            self._delete_attachment(attachment)
            AttachmentCleanupLog.objects.create(
                action='manual_delete',
                started_at=started_at,
                finished_at=timezone.now(),
                files_processed=1,
                files_deleted=1,
                space_freed=attachment.file_size,
                details={'attachment_id': attachment_id, 'original_name': attachment.original_name},
                triggered_by=triggered_by,
            )
            return True, f"附件已删除: {attachment.original_name}"
        except Exception as e:
            return False, f"删除失败: {str(e)}"

    def record_file_hash(self, attachment: ChatAttachment):
        if not self.dedup_enabled:
            return
        try:
            file_path = attachment.file.path
            if os.path.exists(file_path):
                file_hash = self.compute_file_hash(file_path)
                ChatAttachment.objects.filter(pk=attachment.pk).update(file_hash=file_hash)
        except Exception as e:
            logger.warning(f"计算文件哈希失败 (id={attachment.id}): {e}")

    def touch_access(self, attachment_id: int):
        ChatAttachment.objects.filter(pk=attachment_id).update(last_accessed_at=timezone.now())
