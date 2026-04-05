from django.contrib import admin
from .models import ResearchTask


@admin.register(ResearchTask)
class ResearchTaskAdmin(admin.ModelAdmin):
    list_display = ['task_id', 'query_preview', 'status', 'enable_web_search', 'enable_doc_analysis', 'created_at']
    list_filter = ['status', 'enable_web_search', 'enable_doc_analysis', 'created_at']
    search_fields = ['task_id', 'query']
    ordering = ['-created_at']
    readonly_fields = ['task_id', 'created_at', 'updated_at']

    def query_preview(self, obj):
        return obj.query[:50] + '...' if len(obj.query) > 50 else obj.query
    query_preview.short_description = '研究主题'
