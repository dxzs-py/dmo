from django.contrib import admin
from .models import DocumentIndex, Document


@admin.register(DocumentIndex)
class DocumentIndexAdmin(admin.ModelAdmin):
    list_display = ['index_name', 'description_preview', 'document_count', 'created_at', 'updated_at']
    search_fields = ['index_name', 'description']
    ordering = ['-created_at']

    def description_preview(self, obj):
        return obj.description[:50] + '...' if len(obj.description) > 50 else obj.description
    description_preview.short_description = '描述'


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ['filename', 'index', 'file_type', 'file_size_display', 'chunk_count', 'created_at']
    list_filter = ['file_type', 'created_at']
    search_fields = ['filename', 'file_path']
    ordering = ['-created_at']
    readonly_fields = ['created_at']

    def file_size_display(self, obj):
        size = obj.file_size
        if size < 1024:
            return f"{size} B"
        elif size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        else:
            return f"{size / (1024 * 1024):.1f} MB"
    file_size_display.short_description = '文件大小'
