from django.contrib import admin

from core.automations.models import AutomationModule, AutomationRun


@admin.register(AutomationModule)
class AutomationModuleAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("code", "name", "nature", "frequency", "area", "is_enabled")
    list_filter = ("nature", "frequency", "complexity", "area", "is_enabled")
    list_editable = ("is_enabled",)
    search_fields = ("code", "name")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(AutomationRun)
class AutomationRunAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("id", "module", "status", "trigger", "triggered_by", "created_at")
    list_filter = ("module", "status", "trigger")
    search_fields = ("id", "summary", "error_message", "idempotency_key")
    readonly_fields = ("id", "created_at")
    date_hierarchy = "created_at"
