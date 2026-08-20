from django.contrib import admin

from .models import Document, IndexMetadata


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ["filename", "index", "file_type", "file_size_display", "chunk_count", "created_at"]
    list_filter = ["file_type", "created_at"]
    search_fields = ["filename", "file_path"]
    ordering = ["-created_at"]
    readonly_fields = ["created_at"]

    @admin.display(description="文件大小")
    def file_size_display(self, obj):
        size = obj.file_size
        if size < 1024:
            return f"{size} B"
        elif size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        else:
            return f"{size / (1024 * 1024):.1f} MB"


@admin.register(IndexMetadata)
class IndexMetadataAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "user",
        "status",
        "store_type",
        "embedding_model",
        "num_documents",
        "is_deleted",
        "created_at",
        "updated_at",
    ]
    list_filter = ["status", "store_type", "is_deleted", "created_at"]
    search_fields = ["name", "description", "embedding_model", "user__username"]
    ordering = ["-created_at"]
    raw_id_fields = ["user"]
    readonly_fields = ["created_at", "updated_at"]
