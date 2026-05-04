from django.contrib import admin
from .models import ChatSession, ChatMessage, ChatAttachment, AttachmentCleanupLog, StorageAlert


@admin.register(ChatSession)
class ChatSessionAdmin(admin.ModelAdmin):
    list_display = ['session_id', 'user', 'title', 'mode', 'created_at', 'updated_at']
    list_filter = ['mode', 'created_at']
    search_fields = ['session_id', 'title', 'user__username']
    ordering = ['-created_at']
    readonly_fields = ['session_id', 'created_at', 'updated_at']


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ['id', 'session', 'role', 'content_preview', 'created_at']
    list_filter = ['role', 'created_at']
    search_fields = ['content', 'session__session_id']
    ordering = ['-created_at']
    readonly_fields = ['created_at']

    def content_preview(self, obj):
        return obj.content[:50] + '...' if len(obj.content) > 50 else obj.content
    content_preview.short_description = '内容预览'


@admin.register(ChatAttachment)
class ChatAttachmentAdmin(admin.ModelAdmin):
    list_display = ['id', 'original_name', 'session', 'file_type', 'file_size', 'status', 'reference_count', 'retention_days', 'created_at']
    list_filter = ['file_type', 'status', 'created_at']
    search_fields = ['original_name', 'session__session_id']
    ordering = ['-created_at']
    readonly_fields = ['created_at', 'updated_at', 'file_hash', 'last_accessed_at']
    list_editable = ['status', 'retention_days']
    actions = ['action_archive', 'action_delete', 'action_restore']

    @admin.action(description='归档选中的附件')
    def action_archive(self, request, queryset):
        from .services.attachment_lifecycle import AttachmentLifecycleService
        service = AttachmentLifecycleService()
        count = 0
        for att in queryset.filter(status='active'):
            try:
                service._archive_attachment(att)
                count += 1
            except Exception:
                pass
        self.message_user(request, f'成功归档 {count} 个附件')

    @admin.action(description='删除选中的附件')
    def action_delete(self, request, queryset):
        from .services.attachment_lifecycle import AttachmentLifecycleService
        service = AttachmentLifecycleService()
        count = 0
        for att in queryset:
            try:
                service._delete_attachment(att)
                count += 1
            except Exception:
                pass
        self.message_user(request, f'成功删除 {count} 个附件')

    @admin.action(description='恢复选中的归档附件')
    def action_restore(self, request, queryset):
        from .services.attachment_lifecycle import AttachmentLifecycleService
        service = AttachmentLifecycleService()
        count = 0
        for att in queryset.filter(status='archived'):
            if service.restore_attachment(att.id):
                count += 1
        self.message_user(request, f'成功恢复 {count} 个附件')


@admin.register(AttachmentCleanupLog)
class AttachmentCleanupLogAdmin(admin.ModelAdmin):
    list_display = ['id', 'action', 'started_at', 'files_processed', 'files_deleted', 'files_archived', 'space_freed_mb', 'triggered_by']
    list_filter = ['action', 'triggered_by']
    ordering = ['-started_at']
    readonly_fields = ['started_at', 'finished_at', 'errors', 'details']

    def space_freed_mb(self, obj):
        return f'{obj.space_freed / 1024 / 1024:.2f} MB'
    space_freed_mb.short_description = '释放空间'


@admin.register(StorageAlert)
class StorageAlertAdmin(admin.ModelAdmin):
    list_display = ['id', 'level', 'status', 'usage_percent', 'threshold_percent', 'created_at']
    list_filter = ['level', 'status']
    ordering = ['-created_at']
    readonly_fields = ['created_at']
    actions = ['action_acknowledge', 'action_resolve']

    @admin.action(description='确认选中的告警')
    def action_acknowledge(self, request, queryset):
        from django.utils import timezone
        queryset.filter(status='active').update(status='acknowledged', acknowledged_at=timezone.now())

    @admin.action(description='解决选中的告警')
    def action_resolve(self, request, queryset):
        from django.utils import timezone
        queryset.filter(status__in=['active', 'acknowledged']).update(status='resolved', resolved_at=timezone.now())
