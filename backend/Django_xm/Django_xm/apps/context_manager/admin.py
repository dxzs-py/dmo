"""上下文管理 Admin 注册"""

from django.contrib import admin

from Django_xm.apps.context_manager.models import AutoMemory, ContextRule, PromptCache


@admin.register(ContextRule)
class ContextRuleAdmin(admin.ModelAdmin):
    list_display = ['name', 'scope', 'user', 'project_id', 'priority', 'is_active', 'updated_at']
    list_filter = ['scope', 'is_active']
    search_fields = ['name', 'content']
    list_editable = ['is_active', 'priority']


@admin.register(AutoMemory)
class AutoMemoryAdmin(admin.ModelAdmin):
    list_display = ['id', 'user', 'source', 'project_id', 'access_count', 'last_accessed_at']
    list_filter = ['source']
    search_fields = ['content']
    readonly_fields = ['access_count', 'last_accessed_at', 'created_at']


@admin.register(PromptCache)
class PromptCacheAdmin(admin.ModelAdmin):
    list_display = ['name', 'cache_type', 'user', 'token_count', 'usage_count', 'is_active', 'sort_order', 'updated_at']
    list_filter = ['cache_type', 'is_active']
    search_fields = ['name', 'content']
    list_editable = ['is_active', 'sort_order']
