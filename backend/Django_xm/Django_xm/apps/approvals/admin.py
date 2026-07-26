from django.contrib import admin

from Django_xm.apps.approvals.models import Approval


@admin.register(Approval)
class ApprovalAdmin(admin.ModelAdmin):
    list_display = ('interrupt_id', 'source', 'source_id', 'tool_name', 'state', 'created_at', 'resolved_at')
    list_filter = ('source', 'state', 'danger_level')
    search_fields = ('interrupt_id', 'source_id', 'tool_name', 'chat_session_id')
    readonly_fields = ('created_at', 'resolved_at')
    ordering = ['-created_at']
