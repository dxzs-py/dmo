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

from Django_xm.apps.attachments.models import (
    ChatAttachment,
    AttachmentStatus,
    AttachmentCleanupLog,
    StorageAlert,
)

logger = logging.getLogger(__name__)


class AttachmentLifecycleService:
    def __init__(self):
        self.media_root = Path(settings.MEDIA_ROOT)
        self.default_retention_days = getattr(settings, 'ATTACHMENT_DEFAULT_RETENTION_DAYS', 30)
        self.index_after_days = getattr(settings, 'ATTACHMENT_INDEX_AFTER_DAYS', 60)
        self.auto_index_enabled = getattr(settings, 'ATTACHMENT_AUTO_INDEX_ENABLED', True)
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
            status__in=[AttachmentStatus.ACTIVE, AttachmentStatus.INDEXED]
        )
        total_size = attachments.aggregate(total=models.Sum('file_size'))['total'] or 0
        total_count = attachments.count()
        active_count = attachments.filter(status=AttachmentStatus.ACTIVE).count()
        indexed_count = attachments.filter(status=AttachmentStatus.INDEXED).count()

        trashed_count = ChatAttachment.all_objects.filter(is_deleted=True).count()
        trashed_size = ChatAttachment.all_objects.filter(is_deleted=True).aggregate(
            total=models.Sum('file_size')
        )['total'] or 0

        disk_usage = self._get_disk_usage()

        return {
            'total_files': total_count,
            'active_files': active_count,
            'indexed_files': indexed_count,
            'trashed_files': trashed_count,
            'trashed_size_bytes': trashed_size,
            'trashed_size_mb': round(trashed_size / (1024 * 1024), 2),
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

    def find_index_candidates(self) -> models.QuerySet:
        cutoff = timezone.now() - timedelta(days=self.index_after_days)
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
        files_indexed = 0
        files_skipped = 0
        space_freed = 0
        space_indexed = 0
        errors = []

        for attachment in expired.iterator():
            files_processed += 1
            try:
                if self.auto_index_enabled and not attachment.is_expired():
                    indexed = self._index_attachment(attachment, dry_run)
                    if indexed:
                        files_indexed += 1
                        space_indexed += attachment.file_size
                    else:
                        files_skipped += 1
                    continue

                if attachment.reference_count > 0 and not attachment.is_expired():
                    files_skipped += 1
                    continue

                if not dry_run:
                    self._delete_attachment(attachment, delete_files=True)
                files_deleted += 1
                space_freed += attachment.file_size
            except Exception as e:
                error_msg = f"附件 {attachment.id} ({attachment.original_name}) 处理失败: {str(e)}"
                logger.error(error_msg)
                errors.append(error_msg)

        log.finished_at = timezone.now()
        log.files_processed = files_processed
        log.files_deleted = files_deleted
        log.files_archived = files_indexed
        log.files_skipped = files_skipped
        log.space_freed = space_freed
        log.space_archived = space_indexed
        log.errors = errors
        log.details = {
            'dry_run': dry_run,
            'retention_days': self.default_retention_days,
            'auto_index_enabled': self.auto_index_enabled,
        }
        log.save()

        logger.info(
            f"附件清理完成: 处理={files_processed}, 删除={files_deleted}, "
            f"入库={files_indexed}, 跳过={files_skipped}, "
            f"释放={space_freed / 1024 / 1024:.2f}MB"
        )
        return log

    def _get_user_index_name(self, user_id) -> str:
        return f"user_{user_id}_chat_attachments"

    def _index_attachment(self, attachment: ChatAttachment, dry_run: bool = False) -> bool:
        if dry_run:
            return False

        try:
            from Django_xm.apps.tools.file.reader import read_attachment_as_documents
            from Django_xm.apps.knowledge.services.embedding_service import get_embeddings
            from Django_xm.apps.knowledge.services.index_service import IndexManager

            docs = read_attachment_as_documents(attachment.id)
            if not docs:
                logger.warning(f"附件 {attachment.id} 无法提取文档内容，跳过入库")
                return False

            user_id = attachment.session.user_id
            index_name = self._get_user_index_name(user_id)
            embeddings = get_embeddings()
            manager = IndexManager()

            if not manager.index_exists(index_name):
                manager.create_index(
                    name=index_name,
                    documents=docs,
                    embeddings=embeddings,
                    description='聊天附件自动入库索引',
                )
                logger.info(f"创建用户附件索引: {index_name}")
            else:
                manager.add_documents(index_name, docs, embeddings)
                logger.info(f"向用户附件索引添加文档: {index_name}")

            attachment.status = AttachmentStatus.INDEXED
            attachment.indexed_path = index_name
            attachment.indexed_at = timezone.now()
            attachment.save(update_fields=['status', 'indexed_path', 'indexed_at'])

            logger.info(f"附件已向量化入库: {attachment.original_name} (id={attachment.id})")
            return True
        except Exception as e:
            logger.error(f"附件向量化入库失败 (id={attachment.id}): {e}", exc_info=True)
            raise

    def _unindex_attachment(self, attachment: ChatAttachment) -> bool:
        try:
            if not attachment.indexed_path:
                logger.warning(f"附件 {attachment.id} 无入库索引信息")
                return True

            from Django_xm.apps.knowledge.services.embedding_service import get_embeddings
            from Django_xm.apps.knowledge.services.index_service import IndexManager

            user_id = attachment.session.user_id
            index_name = self._get_user_index_name(user_id)
            manager = IndexManager()

            if not manager.index_exists(index_name):
                logger.warning(f"索引不存在: {index_name}")
                return True

            embeddings = get_embeddings()
            removed = manager.remove_documents_by_filename(
                index_name, embeddings, attachment.original_name
            )
            logger.info(f"从向量索引移除 {removed} 个文档块: {attachment.original_name}")
            return True
        except Exception as e:
            logger.error(f"从向量索引移除失败 (id={attachment.id}): {e}", exc_info=True)
            return False

    def _delete_attachment(self, attachment: ChatAttachment, delete_files: bool = False):
        if delete_files:
            try:
                file_path = attachment.file.path
                if os.path.exists(file_path):
                    os.remove(file_path)
                    parent = os.path.dirname(file_path)
                    if os.path.isdir(parent) and not os.listdir(parent):
                        os.rmdir(parent)
                    logger.info(f"磁盘文件已删除: {file_path}")
            except Exception as e:
                logger.warning(f"删除磁盘文件失败: {e}")

        if attachment.status == AttachmentStatus.INDEXED:
            try:
                self._unindex_attachment(attachment)
            except Exception as e:
                logger.warning(f"删除时从向量索引移除失败: {e}")

        attachment.status = AttachmentStatus.DELETED
        attachment.is_deleted = True
        attachment.deleted_at = timezone.now()
        attachment.save(update_fields=['status', 'is_deleted', 'deleted_at'])

        logger.info(f"附件已软删除: {attachment.original_name} (id={attachment.id})")

    @transaction.atomic
    def index_old_attachments(self, dry_run: bool = False, triggered_by: str = 'system') -> AttachmentCleanupLog:
        started_at = timezone.now()
        log = AttachmentCleanupLog.objects.create(
            action='index',
            started_at=started_at,
            triggered_by=triggered_by,
        )

        candidates = self.find_index_candidates()
        files_processed = 0
        files_indexed = 0
        files_skipped = 0
        space_indexed = 0
        errors = []

        for attachment in candidates.iterator():
            files_processed += 1
            try:
                indexed = self._index_attachment(attachment, dry_run)
                if indexed:
                    files_indexed += 1
                    space_indexed += attachment.file_size
                else:
                    files_skipped += 1
            except Exception as e:
                error_msg = f"附件 {attachment.id} 入库失败: {str(e)}"
                logger.error(error_msg)
                errors.append(error_msg)

        log.finished_at = timezone.now()
        log.files_processed = files_processed
        log.files_archived = files_indexed
        log.files_skipped = files_skipped
        log.space_archived = space_indexed
        log.errors = errors
        log.details = {'dry_run': dry_run, 'index_after_days': self.index_after_days}
        log.save()

        logger.info(f"附件入库完成: 处理={files_processed}, 入库={files_indexed}, 跳过={files_skipped}, 空间={space_indexed / 1024 / 1024:.2f}MB")
        return log

    @transaction.atomic
    def index_attachment_by_id(self, attachment_id: int, triggered_by: str = 'user') -> Tuple[bool, str]:
        try:
            attachment = ChatAttachment.objects.get(id=attachment_id, is_deleted=False)
        except ChatAttachment.DoesNotExist:
            return False, f"附件不存在: id={attachment_id}"

        if attachment.status == AttachmentStatus.INDEXED:
            return False, "附件已入库，无需重复操作"

        if attachment.status != AttachmentStatus.ACTIVE:
            return False, f"附件状态为 {attachment.status}，无法入库"

        try:
            self._index_attachment(attachment)
            AttachmentCleanupLog.objects.create(
                action='index',
                started_at=timezone.now(),
                finished_at=timezone.now(),
                files_processed=1,
                files_archived=1,
                space_archived=attachment.file_size,
                details={'attachment_id': attachment_id, 'original_name': attachment.original_name},
                triggered_by=triggered_by,
            )
            return True, f"附件已向量化入库: {attachment.original_name}"
        except Exception as e:
            return False, f"入库失败: {str(e)}"

    @transaction.atomic
    def unindex_attachment_by_id(self, attachment_id: int, triggered_by: str = 'user') -> Tuple[bool, str]:
        try:
            attachment = ChatAttachment.objects.get(id=attachment_id, is_deleted=False)
        except ChatAttachment.DoesNotExist:
            return False, f"附件不存在: id={attachment_id}"

        if attachment.status != AttachmentStatus.INDEXED:
            return False, "附件未入库，无需移除"

        try:
            self._unindex_attachment(attachment)

            attachment.status = AttachmentStatus.ACTIVE
            attachment.indexed_path = ''
            attachment.indexed_at = None
            attachment.save(update_fields=['status', 'indexed_path', 'indexed_at'])

            AttachmentCleanupLog.objects.create(
                action='unindex',
                started_at=timezone.now(),
                finished_at=timezone.now(),
                files_processed=1,
                details={'attachment_id': attachment_id, 'original_name': attachment.original_name},
                triggered_by=triggered_by,
            )
            return True, f"附件已从向量库移除: {attachment.original_name}"
        except Exception as e:
            return False, f"移除失败: {str(e)}"

    def batch_index(self, attachment_ids: List[int], triggered_by: str = 'user') -> Dict:
        results = {'success': [], 'failed': []}
        for att_id in attachment_ids:
            try:
                success, message = self.index_attachment_by_id(att_id, triggered_by)
                if success:
                    results['success'].append({'id': att_id, 'message': message})
                else:
                    results['failed'].append({'id': att_id, 'message': message})
            except Exception as e:
                results['failed'].append({'id': att_id, 'message': str(e)})
        return results

    def batch_unindex(self, attachment_ids: List[int], triggered_by: str = 'user') -> Dict:
        results = {'success': [], 'failed': []}
        for att_id in attachment_ids:
            try:
                success, message = self.unindex_attachment_by_id(att_id, triggered_by)
                if success:
                    results['success'].append({'id': att_id, 'message': message})
                else:
                    results['failed'].append({'id': att_id, 'message': message})
            except Exception as e:
                results['failed'].append({'id': att_id, 'message': str(e)})
        return results

    def batch_delete(self, attachment_ids: List[int], triggered_by: str = 'user') -> Dict:
        results = {'success': [], 'failed': []}
        for att_id in attachment_ids:
            try:
                success, message = self.manual_delete(att_id, triggered_by)
                if success:
                    results['success'].append({'id': att_id, 'message': message})
                else:
                    results['failed'].append({'id': att_id, 'message': message})
            except Exception as e:
                results['failed'].append({'id': att_id, 'message': str(e)})
        return results

    @transaction.atomic
    def manual_delete(self, attachment_id: int, triggered_by: str = 'admin') -> Tuple[bool, str]:
        try:
            attachment = ChatAttachment.all_objects.get(id=attachment_id)
        except ChatAttachment.DoesNotExist:
            return False, f"附件不存在: id={attachment_id}"

        if attachment.is_deleted or attachment.status == AttachmentStatus.DELETED:
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

    @transaction.atomic
    def permanent_delete(self, attachment_id: int, triggered_by: str = 'admin') -> Tuple[bool, str]:
        try:
            attachment = ChatAttachment.all_objects.get(id=attachment_id)
        except ChatAttachment.DoesNotExist:
            return False, f"附件不存在: id={attachment_id}"

        if not attachment.is_deleted:
            return False, "附件未在回收站中，无法永久删除"

        file_name = attachment.original_name
        file_size = attachment.file_size

        if attachment.status == AttachmentStatus.INDEXED:
            try:
                self._unindex_attachment(attachment)
            except Exception as e:
                logger.warning(f"永久删除时从向量索引移除失败: {e}")

        try:
            file_path = attachment.file.path
            if os.path.exists(file_path):
                os.remove(file_path)
                parent = os.path.dirname(file_path)
                if os.path.isdir(parent) and not os.listdir(parent):
                    os.rmdir(parent)
        except Exception as e:
            logger.warning(f"永久删除文件时清理磁盘文件失败: {e}")

        attachment.hard_delete()

        AttachmentCleanupLog.objects.create(
            action='permanent_delete',
            started_at=timezone.now(),
            finished_at=timezone.now(),
            files_processed=1,
            files_deleted=1,
            space_freed=file_size,
            details={'attachment_id': attachment_id, 'original_name': file_name},
            triggered_by=triggered_by,
        )

        logger.info(f"附件已永久删除: {file_name} (id={attachment_id})")
        return True, f"附件已永久删除: {file_name}"

    @transaction.atomic
    def restore_from_trash(self, attachment_id: int) -> Tuple[bool, str]:
        try:
            attachment = ChatAttachment.all_objects.get(id=attachment_id)
        except ChatAttachment.DoesNotExist:
            return False, f"附件不存在: id={attachment_id}"

        if not attachment.is_deleted:
            return False, "附件未在回收站中"

        try:
            file_path = attachment.file.path
            file_exists = os.path.exists(file_path)

            if not file_exists:
                logger.error(f"恢复失败，磁盘文件不存在: {file_path}")
                return False, f"恢复失败：磁盘文件已丢失，无法恢复 ({attachment.original_name})"
        except Exception as e:
            logger.error(f"恢复失败，无法访问文件路径: {e}")
            return False, f"恢复失败：无法访问文件 ({attachment.original_name})"

        attachment.is_deleted = False
        attachment.deleted_at = None
        attachment.status = AttachmentStatus.ACTIVE
        attachment.save(update_fields=['is_deleted', 'deleted_at', 'status'])

        AttachmentCleanupLog.objects.create(
            action='restore_from_trash',
            started_at=timezone.now(),
            finished_at=timezone.now(),
            files_processed=1,
            details={'attachment_id': attachment_id, 'original_name': attachment.original_name},
            triggered_by='manual',
        )

        logger.info(f"附件已从回收站恢复: {attachment.original_name} (id={attachment_id})")
        return True, f"附件已恢复: {attachment.original_name}"
