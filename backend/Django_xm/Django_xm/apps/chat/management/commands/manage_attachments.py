import logging
from django.core.management.base import BaseCommand
from django.utils import timezone

from Django_xm.apps.chat.services.attachment_lifecycle import AttachmentLifecycleService

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = '附件生命周期管理：清理过期文件、入库旧文件、监控存储空间'

    def add_arguments(self, parser):
        parser.add_argument(
            'action',
            choices=['cleanup', 'index', 'check-storage', 'stats', 'full', 'fix-data'],
            help='操作: cleanup=清理过期, index=入库旧文件, check-storage=检查存储空间, stats=统计信息, full=完整流程, fix-data=修复数据一致性',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='仅模拟运行，不实际删除或入库文件',
        )
        parser.add_argument(
            '--retention-days',
            type=int,
            help='覆盖默认保留天数',
        )

    def handle(self, *args, **options):
        action = options['action']
        dry_run = options['dry_run']
        service = AttachmentLifecycleService()

        if options['retention_days']:
            service.default_retention_days = options['retention_days']

        self.stdout.write(f"[{timezone.now():%Y-%m-%d %H:%M:%S}] 开始执行: {action}")

        if action == 'cleanup':
            self._run_cleanup(service, dry_run)
        elif action == 'index':
            self._run_index(service, dry_run)
        elif action == 'check-storage':
            self._run_storage_check(service)
        elif action == 'stats':
            self._run_stats(service)
        elif action == 'full':
            self._run_full(service, dry_run)
        elif action == 'fix-data':
            self._run_fix_data(service, dry_run)

    def _run_cleanup(self, service: AttachmentLifecycleService, dry_run: bool):
        if dry_run:
            expired = service.find_expired_attachments()
            count = expired.count()
            total_size = sum(a.file_size for a in expired)
            self.stdout.write(f"[DRY RUN] 将清理 {count} 个过期附件，释放 {total_size / 1024 / 1024:.2f} MB")
            return

        log = service.cleanup_expired(triggered_by='cron')
        self.stdout.write(self.style.SUCCESS(
            f"清理完成: 处理={log.files_processed}, 删除={log.files_deleted}, "
            f"入库={log.files_archived}, 跳过={log.files_skipped}, "
            f"释放={log.space_freed / 1024 / 1024:.2f}MB"
        ))
        if log.errors:
            for err in log.errors:
                self.stdout.write(self.style.ERROR(f"  错误: {err}"))

    def _run_index(self, service: AttachmentLifecycleService, dry_run: bool):
        if dry_run:
            candidates = service.find_index_candidates()
            count = candidates.count()
            total_size = sum(a.file_size for a in candidates)
            self.stdout.write(f"[DRY RUN] 将入库 {count} 个附件，空间 {total_size / 1024 / 1024:.2f} MB")
            return

        log = service.index_old_attachments(triggered_by='cron')
        self.stdout.write(self.style.SUCCESS(
            f"入库完成: 处理={log.files_processed}, 入库={log.files_archived}, "
            f"空间={log.space_archived / 1024 / 1024:.2f}MB"
        ))

    def _run_storage_check(self, service: AttachmentLifecycleService):
        stats = service.get_storage_stats()
        self.stdout.write(f"存储统计:")
        self.stdout.write(f"  总文件数: {stats['total_files']}")
        self.stdout.write(f"  活跃文件: {stats['active_files']}")
        self.stdout.write(f"  已入库: {stats['indexed_files']}")
        self.stdout.write(f"  附件总大小: {stats['total_size_mb']:.2f} MB")
        self.stdout.write(f"  磁盘使用率: {stats['disk_usage_percent']:.1f}%")
        self.stdout.write(f"  磁盘剩余: {stats['disk_free_bytes'] / 1024 / 1024 / 1024:.2f} GB")

        alert = service.check_storage_alerts()
        if alert:
            self.stdout.write(self.style.WARNING(f"⚠ 存储告警: {alert.message}"))
        else:
            self.stdout.write(self.style.SUCCESS("✓ 存储空间正常"))

    def _run_stats(self, service: AttachmentLifecycleService):
        stats = service.get_storage_stats()
        self.stdout.write(f"附件存储统计:")
        self.stdout.write(f"  总文件数: {stats['total_files']}")
        self.stdout.write(f"  活跃文件: {stats['active_files']}")
        self.stdout.write(f"  已入库: {stats['indexed_files']}")
        self.stdout.write(f"  附件总大小: {stats['total_size_mb']:.2f} MB")
        self.stdout.write(f"  磁盘总空间: {stats['disk_total_bytes'] / 1024 / 1024 / 1024:.2f} GB")
        self.stdout.write(f"  磁盘已用: {stats['disk_used_bytes'] / 1024 / 1024 / 1024:.2f} GB")
        self.stdout.write(f"  磁盘剩余: {stats['disk_free_bytes'] / 1024 / 1024 / 1024:.2f} GB")
        self.stdout.write(f"  磁盘使用率: {stats['disk_usage_percent']:.1f}%")

    def _run_full(self, service: AttachmentLifecycleService, dry_run: bool):
        self.stdout.write("=== 完整生命周期管理流程 ===")
        self.stdout.write("")

        self.stdout.write("1. 检查存储空间...")
        self._run_storage_check(service)
        self.stdout.write("")

        self.stdout.write("2. 入库旧文件...")
        self._run_index(service, dry_run)
        self.stdout.write("")

        self.stdout.write("3. 清理过期文件...")
        self._run_cleanup(service, dry_run)
        self.stdout.write("")

        self.stdout.write("4. 再次检查存储空间...")
        self._run_storage_check(service)

    def _run_fix_data(self, service: AttachmentLifecycleService, dry_run: bool):
        from Django_xm.apps.chat.models import ChatAttachment, AttachmentStatus
        from django.utils import timezone

        self.stdout.write("=== 修复附件数据一致性 ===")

        inconsistent_1 = ChatAttachment.all_objects.filter(
            status=AttachmentStatus.DELETED,
            is_deleted=False
        )
        count_1 = inconsistent_1.count()
        self.stdout.write(f"1. status=deleted 但 is_deleted=False: {count_1} 条")

        inconsistent_2 = ChatAttachment.all_objects.filter(
            is_deleted=True
        ).exclude(status=AttachmentStatus.DELETED)
        count_2 = inconsistent_2.count()
        self.stdout.write(f"2. is_deleted=True 但 status != deleted: {count_2} 条")

        inconsistent_3 = ChatAttachment.all_objects.filter(
            is_deleted=True,
            deleted_at__isnull=True
        )
        count_3 = inconsistent_3.count()
        self.stdout.write(f"3. is_deleted=True 但 deleted_at=None: {count_3} 条")

        if dry_run:
            self.stdout.write(self.style.WARNING("[DRY RUN] 仅检查，不修改"))
            return

        fixed_count = 0

        if count_1 > 0:
            for att in inconsistent_1:
                att.is_deleted = True
                if not att.deleted_at:
                    att.deleted_at = att.updated_at or att.created_at or timezone.now()
                att.save(update_fields=['is_deleted', 'deleted_at'])
                fixed_count += 1
            self.stdout.write(self.style.SUCCESS(f"✓ 已修复 {count_1} 条第 1 类记录"))

        if count_2 > 0:
            for att in inconsistent_2:
                att.status = AttachmentStatus.DELETED
                if not att.deleted_at:
                    att.deleted_at = att.updated_at or att.created_at or timezone.now()
                att.save(update_fields=['status', 'deleted_at'])
                fixed_count += 1
            self.stdout.write(self.style.SUCCESS(f"✓ 已修复 {count_2} 条第 2 类记录"))

        if count_3 > 0:
            for att in inconsistent_3:
                if not att.deleted_at:
                    att.deleted_at = att.updated_at or att.created_at or timezone.now()
                att.save(update_fields=['deleted_at'])
                fixed_count += 1
            self.stdout.write(self.style.SUCCESS(f"✓ 已修复 {count_3} 条第 3 类记录"))

        if fixed_count == 0:
            self.stdout.write(self.style.SUCCESS("✓ 没有需要修复的记录，数据一致"))
