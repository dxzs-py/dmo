from django.contrib import admin
from .models import ResearchTask


@admin.register(ResearchTask)
class ResearchTaskAdmin(admin.ModelAdmin):
    list_display = ['task_id', 'query_preview', 'status', 'created_by', 
                     'research_depth', 'enable_web_search', 'enable_doc_analysis', 
                     'created_at', 'updated_at']
    list_filter = ['status', 'research_depth', 'enable_web_search', 
                    'enable_doc_analysis', 'created_at']
    search_fields = ['task_id', 'query', 'final_report']
    ordering = ['-created_at']
    readonly_fields = ['task_id', 'created_at', 'updated_at', 'error_message', 'celery_task_id']
    
    fieldsets = (
        ('基本信息', {
            'fields': ('task_id', 'query', 'status', 'research_depth')
        }),
        ('配置选项', {
            'fields': ('enable_web_search', 'enable_doc_analysis')
        }),
        ('执行信息', {
            'fields': ('celery_task_id', 'created_by', 'created_at', 'updated_at')
        }),
        ('结果', {
            'fields': ('final_report', 'error_message')
        }),
    )

    def query_preview(self, obj):
        return obj.query[:50] + '...' if len(obj.query) > 50 else obj.query
    query_preview.short_description = '研究主题'
    
    def has_add_permission(self, request):
        return False
    
    def has_change_permission(self, request, obj=None):
        return False
