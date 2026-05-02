from django.contrib import admin
from .models import ChatSession, ChatMessage, ChatAttachment


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
    list_display = ['id', 'original_name', 'session', 'file_type', 'file_size', 'created_at']
    list_filter = ['file_type', 'created_at']
    search_fields = ['original_name', 'session__session_id']
    ordering = ['-created_at']
    readonly_fields = ['created_at', 'updated_at']
