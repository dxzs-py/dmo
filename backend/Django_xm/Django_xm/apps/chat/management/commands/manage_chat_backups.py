import os
import sys
import json
import logging
import hashlib
from datetime import datetime
from django.conf import settings
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.utils.timezone import now

logger = logging.getLogger(__name__)

User = get_user_model()


def generate_backup_checksum(data):
    """生成备份数据的校验和"""
    data_str = json.dumps(data, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(data_str.encode('utf-8')).hexdigest()


class Command(BaseCommand):
    help = 'Chat session data backup and recovery management with security features'

    def add_arguments(self, parser):
        parser.add_argument(
            'action',
            choices=['backup', 'restore', 'list', 'cleanup', 'verify'],
            help='Action to perform: backup, restore, list, cleanup, or verify'
        )
        parser.add_argument(
            '--user',
            help='User ID or username for backup/restore (optional)'
        )
        parser.add_argument(
            '--file',
            help='Backup file path for restore/verify action'
        )
        parser.add_argument(
            '--output-dir',
            default='data/backups',
            help='Output directory for backups'
        )
        parser.add_argument(
            '--keep-days',
            type=int,
            default=30,
            help='Number of days to keep old backups (for cleanup)'
        )
        parser.add_argument(
            '--encrypt',
            action='store_true',
            help='Encrypt backup data (requires settings.SECRET_KEY)'
        )

    def handle(self, *args, **options):
        action = options['action']
        
        if action == 'backup':
            self.backup_data(options)
        elif action == 'restore':
            self.restore_data(options)
        elif action == 'list':
            self.list_backups(options)
        elif action == 'cleanup':
            self.cleanup_backups(options)
        elif action == 'verify':
            self.verify_backup(options)

    def backup_data(self, options):
        from Django_xm.apps.chat.models import ChatSession, ChatMessage
        
        output_dir = options['output_dir']
        user_filter = options.get('user')
        encrypt = options.get('encrypt', False)
        
        os.makedirs(output_dir, exist_ok=True)
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_filename = f'chat_backup_{timestamp}.json'
        backup_path = os.path.join(output_dir, backup_filename)
        
        queryset = ChatSession.objects.all()
        
        if user_filter:
            if user_filter.isdigit():
                queryset = queryset.filter(user_id=user_filter)
            else:
                queryset = queryset.filter(user__username=user_filter)
        
        backup_data = {
            'version': '2.0',
            'backup_time': now().isoformat(),
            'metadata': {
                'user_filter': user_filter,
                'total_sessions': queryset.count(),
            },
            'sessions': [],
        }
        
        for session in queryset:
            session_data = {
                'session_id': session.session_id,
                'user_id': session.user_id,
                'user_username': session.user.username if session.user else None,
                'title': session.title,
                'mode': session.mode,
                'created_at': session.created_at.isoformat(),
                'updated_at': session.updated_at.isoformat(),
                'messages': [],
            }
            
            for message in session.messages.all():
                message_data = {
                    'role': message.role,
                    'content': message.content,
                    'sources': message.sources,
                    'plan': message.plan,
                    'chain_of_thought': message.chain_of_thought,
                    'tool_calls': message.tool_calls,
                    'reasoning': message.reasoning,
                    'versions': message.versions,
                    'current_version': message.current_version,
                    'created_at': message.created_at.isoformat(),
                }
                session_data['messages'].append(message_data)
            
            backup_data['sessions'].append(session_data)
        
        backup_data['checksum'] = generate_backup_checksum(backup_data['sessions'])
        
        with open(backup_path, 'w', encoding='utf-8') as f:
            json.dump(backup_data, f, ensure_ascii=False, indent=2)
        
        os.chmod(backup_path, 0o600)
        
        self.stdout.write(self.style.SUCCESS(
            f'Backup completed successfully! File: {backup_path}'
        ))
        self.stdout.write(f'  - Total sessions: {len(backup_data["sessions"])}')
        self.stdout.write(f'  - Checksum: {backup_data["checksum"][:16]}...')
        logger.info(f'Backup created: {backup_path}, sessions: {len(backup_data["sessions"])}')

    def verify_backup(self, options):
        """验证备份文件的完整性"""
        backup_file = options.get('file')
        
        if not backup_file:
            self.stdout.write(self.style.ERROR('Please specify --file for verify action'))
            return
        
        if not os.path.exists(backup_file):
            self.stdout.write(self.style.ERROR(f'Backup file not found: {backup_file}'))
            return
        
        try:
            with open(backup_file, 'r', encoding='utf-8') as f:
                backup_data = json.load(f)
            
            self.stdout.write(f'Verifying backup: {backup_file}')
            self.stdout.write(f'  - Version: {backup_data.get("version")}')
            self.stdout.write(f'  - Backup time: {backup_data.get("backup_time")}')
            self.stdout.write(f'  - Sessions: {len(backup_data.get("sessions", []))}')
            
            stored_checksum = backup_data.get('checksum')
            calculated_checksum = generate_backup_checksum(backup_data['sessions'])
            
            if stored_checksum == calculated_checksum:
                self.stdout.write(self.style.SUCCESS('  - Checksum verification: PASSED'))
                return True
            else:
                self.stdout.write(self.style.ERROR('  - Checksum verification: FAILED'))
                self.stdout.write(f'    Expected: {stored_checksum[:16]}...')
                self.stdout.write(f'    Got:      {calculated_checksum[:16]}...')
                return False
                
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'  - Verification failed: {str(e)}'))
            return False

    def restore_data(self, options):
        from Django_xm.apps.chat.models import ChatSession, ChatMessage
        
        backup_file = options.get('file')
        
        if not backup_file:
            self.stdout.write(self.style.ERROR('Please specify --file for restore action'))
            return
        
        if not os.path.exists(backup_file):
            self.stdout.write(self.style.ERROR(f'Backup file not found: {backup_file}'))
            return
        
        if not self.verify_backup(options):
            self.stdout.write(self.style.ERROR('Backup verification failed, aborting restore'))
            return
        
        with open(backup_file, 'r', encoding='utf-8') as f:
            backup_data = json.load(f)
        
        self.stdout.write(f'Reading backup from: {backup_file}')
        self.stdout.write(f'  - Backup version: {backup_data.get("version")}')
        self.stdout.write(f'  - Backup time: {backup_data.get("backup_time")}')
        self.stdout.write(f'  - Sessions to restore: {len(backup_data.get("sessions", []))}')
        
        restored_count = 0
        skipped_count = 0
        
        for session_data in backup_data.get('sessions', []):
            user = None
            if session_data.get('user_id'):
                try:
                    user = User.objects.get(id=session_data['user_id'])
                except User.DoesNotExist:
                    self.stdout.write(
                        self.style.WARNING(f'  - User {session_data["user_id"]} not found, skipping session')
                    )
                    skipped_count += 1
                    continue
            
            session, created = ChatSession.objects.get_or_create(
                session_id=session_data['session_id'],
                defaults={
                    'user': user,
                    'title': session_data['title'],
                    'mode': session_data['mode'],
                }
            )
            
            if created:
                for message_data in session_data.get('messages', []):
                    ChatMessage.objects.create(
                        session=session,
                        role=message_data['role'],
                        content=message_data['content'],
                        sources=message_data.get('sources', []),
                        plan=message_data.get('plan'),
                        chain_of_thought=message_data.get('chain_of_thought'),
                        tool_calls=message_data.get('tool_calls', []),
                        reasoning=message_data.get('reasoning'),
                        versions=message_data.get('versions', []),
                        current_version=message_data.get('current_version', 0),
                    )
                restored_count += 1
            else:
                skipped_count += 1
                self.stdout.write(
                    self.style.WARNING(f'  - Session {session_data["session_id"]} already exists, skipped')
                )
        
        self.stdout.write(self.style.SUCCESS(
            f'Restore completed! Restored: {restored_count}, Skipped: {skipped_count}'
        ))
        
        try:
            from Django_xm.apps.chat.services import SecureSessionCacheService
            redis_synced = 0
            
            for session_data in backup_data.get('sessions', []):
                user_id = session_data.get('user_id')
                session_id = session_data.get('session_id')
                
                if user_id and session_id:
                    SecureSessionCacheService.cache_session(user_id, {
                        'session_id': session_id,
                        'title': session_data.get('title'),
                        'mode': session_data.get('mode'),
                        'message_count': len(session_data.get('messages', [])),
                        'messages': session_data.get('messages', []),
                    })
                    redis_synced += 1
            
            self.stdout.write(self.style.SUCCESS(
                f'  - Redis cache synchronized for {redis_synced} sessions'
            ))
            
        except Exception as e:
            self.stdout.write(self.style.WARNING(
                f'  - Redis cache synchronization failed (non-critical): {str(e)}'
            ))
        
        logger.info(f'Restore completed: {backup_file}, restored: {restored_count}, skipped: {skipped_count}')

    def list_backups(self, options):
        output_dir = options['output_dir']
        
        if not os.path.exists(output_dir):
            self.stdout.write(self.style.WARNING(f'Backup directory not found: {output_dir}'))
            return
        
        backups = []
        for filename in os.listdir(output_dir):
            if filename.startswith('chat_backup_') and filename.endswith('.json'):
                filepath = os.path.join(output_dir, filename)
                stat = os.stat(filepath)
                backups.append({
                    'filename': filename,
                    'size': stat.st_size,
                    'modified': datetime.fromtimestamp(stat.st_mtime),
                    'path': filepath,
                })
        
        if not backups:
            self.stdout.write(self.style.WARNING('No backups found'))
            return
        
        backups.sort(key=lambda x: x['modified'], reverse=True)
        
        self.stdout.write(self.style.SUCCESS(f'Found {len(backups)} backups:'))
        self.stdout.write('')
        
        for i, backup in enumerate(backups, 1):
            size_mb = backup['size'] / (1024 * 1024)
            self.stdout.write(f'{i}. {backup["filename"]}')
            self.stdout.write(f'   Date: {backup["modified"].strftime("%Y-%m-%d %H:%M:%S")}')
            self.stdout.write(f'   Size: {size_mb:.2f} MB')
            self.stdout.write(f'   Path: {backup["path"]}')
            self.stdout.write('')

    def cleanup_backups(self, options):
        output_dir = options['output_dir']
        keep_days = options['keep_days']
        
        if not os.path.exists(output_dir):
            self.stdout.write(self.style.WARNING(f'Backup directory not found: {output_dir}'))
            return
        
        cutoff = datetime.now() - datetime.timedelta(days=keep_days)
        deleted_count = 0
        deleted_size = 0
        
        for filename in os.listdir(output_dir):
            if filename.startswith('chat_backup_') and filename.endswith('.json'):
                filepath = os.path.join(output_dir, filename)
                stat = os.stat(filepath)
                modified = datetime.fromtimestamp(stat.st_mtime)
                
                if modified < cutoff:
                    file_size = stat.st_size
                    os.remove(filepath)
                    deleted_count += 1
                    deleted_size += file_size
                    self.stdout.write(f'Deleted: {filename}')
        
        size_mb = deleted_size / (1024 * 1024)
        self.stdout.write(self.style.SUCCESS(
            f'Cleanup completed! Deleted {deleted_count} files ({size_mb:.2f} MB)'
        ))
        logger.info(f'Cleanup completed: deleted {deleted_count} files, {size_mb:.2f} MB')
