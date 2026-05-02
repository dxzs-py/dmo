from django.contrib import admin
from .models import WorkflowExecution, WorkflowSession


@admin.register(WorkflowExecution)
class WorkflowExecutionAdmin(admin.ModelAdmin):
    list_display = ['thread_id', 'workflow_type', 'status', 'query_preview', 'created_at']
    list_filter = ['workflow_type', 'status', 'created_at']
    search_fields = ['thread_id', 'query']
    ordering = ['-created_at']
    readonly_fields = ['thread_id', 'created_at', 'updated_at']

    def query_preview(self, obj):
        return obj.query[:50] + '...' if len(obj.query) > 50 else obj.query
    query_preview.short_description = '查询内容'


@admin.register(WorkflowSession)
class WorkflowSessionAdmin(admin.ModelAdmin):
    list_display = ['thread_id', 'status', 'current_step', 'score', 'created_at']
    list_filter = ['status', 'created_at']
    search_fields = ['thread_id', 'user_question']
    ordering = ['-created_at']
    readonly_fields = ['thread_id', 'created_at', 'updated_at']
