from django.contrib import admin

from Django_xm.apps.tools.models import CustomTool, McpServerConfig, SkillConfig, SkillPackage, ToolCategory


@admin.register(ToolCategory)
class ToolCategoryAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "sort_order", "is_active")
    list_filter = ("is_active",)
    search_fields = ("code", "name", "description")
    ordering = ("sort_order",)


@admin.register(CustomTool)
class CustomToolAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "tool_type", "category", "source", "status", "created_at", "updated_at")
    list_filter = ("status", "tool_type", "category", "source")
    search_fields = ("name", "description")
    readonly_fields = ("created_at", "updated_at")
    raw_id_fields = ("user",)


@admin.register(McpServerConfig)
class McpServerConfigAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "user",
        "tool_type",
        "category",
        "source",
        "transport",
        "status",
        "created_at",
        "updated_at",
    )
    list_filter = ("status", "tool_type", "transport", "source")
    search_fields = ("name", "description")
    readonly_fields = ("created_at", "updated_at")
    raw_id_fields = ("user",)


@admin.register(SkillConfig)
class SkillConfigAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "tool_type", "category", "source", "status", "version", "created_at", "updated_at")
    list_filter = ("status", "tool_type", "category", "source")
    search_fields = ("name", "description")
    readonly_fields = ("created_at", "updated_at")
    raw_id_fields = ("user",)


@admin.register(SkillPackage)
class SkillPackageAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "tool_type", "category", "source", "status", "version", "created_at", "updated_at")
    list_filter = ("status", "tool_type", "category", "source")
    search_fields = ("name", "description")
    readonly_fields = ("created_at", "updated_at")
    raw_id_fields = ("user",)
