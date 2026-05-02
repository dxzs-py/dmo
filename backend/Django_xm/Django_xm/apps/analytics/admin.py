from django.contrib import admin
from .models import UserEvent, DailyAggregation


@admin.register(UserEvent)
class UserEventAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'event_type', 'event_category', 'is_success', 'created_at')
    list_filter = ('event_type', 'event_category', 'is_success')
    search_fields = ('user__username', 'event_type')
    readonly_fields = ('created_at', 'updated_at')
    date_hierarchy = 'created_at'


@admin.register(DailyAggregation)
class DailyAggregationAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'date', 'chat_sessions', 'chat_messages', 'api_requests')
    list_filter = ('date',)
    search_fields = ('user__username',)
    date_hierarchy = 'date'
