from django.contrib import admin

from core.automations.models import (
    AutomationModule,
    AutomationRun,
    CertificateCommunication,
    CommunicationAttempt,
    DigitalCertificate,
)


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


@admin.register(DigitalCertificate)
class DigitalCertificateAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("serial_number", "client_name", "valid_until", "status", "preferred_channel")
    list_filter = ("status", "preferred_channel", "valid_until")
    search_fields = ("serial_number", "client_name", "client_document", "responsible_name")
    date_hierarchy = "valid_until"


@admin.register(CertificateCommunication)
class CertificateCommunicationAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("certificate", "channel", "recipient", "status", "created_at")
    list_filter = ("status", "channel", "policy_key")
    search_fields = ("certificate__client_name", "recipient", "last_error")
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(CommunicationAttempt)
class CommunicationAttemptAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("communication", "sequence", "status", "recipient", "created_at")
    list_filter = ("status", "communication__channel")
    search_fields = ("communication__certificate__client_name", "recipient", "error_message")
    readonly_fields = (
        "id",
        "communication",
        "run",
        "sequence",
        "status",
        "recipient",
        "provider_message_id",
        "error_message",
        "payload",
        "created_at",
        "finished_at",
    )
