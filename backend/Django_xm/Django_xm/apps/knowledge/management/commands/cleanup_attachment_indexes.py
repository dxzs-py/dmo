"""清理超期的会话附件索引（attach_*）。

实现与 Celery beat 任务 ``chat.cleanup_attachment_indexes`` 共用
``knowledge.services.attachment_index_cleanup``，避免两处口径分叉。

用法（backend/Django_xm 目录）：
    python manage.py cleanup_attachment_indexes --dry-run
    python manage.py cleanup_attachment_indexes
    python manage.py cleanup_attachment_indexes --days 3
"""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "清理超过 TTL 的会话附件索引（attach_* collection）"

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=None, help="覆盖 rag_attachment_ttl_days")
        parser.add_argument("--dry-run", action="store_true", help="只列出，不删除")

    def handle(self, *args, **options):
        from Django_xm.apps.knowledge.services.attachment_index_cleanup import (
            cleanup_expired_attachment_indexes,
        )

        result = cleanup_expired_attachment_indexes(
            days=options["days"], dry_run=options["dry_run"]
        )

        self.stdout.write(
            f"前缀={result['prefix']}，TTL={result['days']} 天，扫描到 {result['scanned']} 个附件索引"
        )
        if result["dry_run"]:
            for name in result["names"]:
                self.stdout.write(f"[dry-run] 将删除: {name}")
            return

        self.stdout.write(f"已删除 {result['deleted']} 个，失败 {result['failed']} 个")

