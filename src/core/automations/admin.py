from collections.abc import Callable

from django.contrib import admin
from django.http import HttpRequest

from core.automations.models import (
    AutomationModule,
    AutomationRun,
    BriefingTemplate,
    BriefingTemplateVersion,
    BriefingVersionStatus,
    CertificateCommunication,
    CommunicationAttempt,
    DigitalCertificate,
    DocumentClassificationAttempt,
    DocumentDecision,
    DocumentIntake,
    DocumentReview,
    DocumentRouting,
    DocumentRunItem,
    FiscalClient,
    FiscalDocument,
    SC05Artifact,
    SC05Client,
    SC05Operation,
    SC05PortalStep,
    SC05StepAttempt,
    SocietaryBriefing,
    SocietaryBriefingStatus,
)


@admin.register(FiscalClient)
class FiscalClientAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("code", "name", "document_number", "route_prefix", "is_active")
    list_filter = ("is_active",)
    search_fields = ("code", "name", "document_number")
    readonly_fields = ("created_at", "updated_at")


class SC04EvidenceAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """SC-04 operational evidence is inspected here, never rewritten."""

    list_per_page = 50

    def get_readonly_fields(
        self,
        request: HttpRequest,
        obj: object | None = None,
    ) -> tuple[str, ...]:
        return tuple(field.name for field in self.model._meta.fields)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: object | None = None) -> bool:
        return False


admin.site.register(
    (
        FiscalDocument,
        DocumentIntake,
        DocumentRunItem,
        DocumentClassificationAttempt,
        DocumentReview,
        DocumentDecision,
        DocumentRouting,
    ),
    SC04EvidenceAdmin,
)


@admin.register(SC05Client)
class SC05ClientAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("external_reference", "name", "document", "status", "updated_at")
    list_filter = ("status",)
    search_fields = ("external_reference", "name", "document")
    readonly_fields = ("task_restore_snapshot", "created_at", "updated_at")


class SC05EvidenceAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """SC-05 saga evidence is inspectable but never edited through maintenance UI."""

    list_per_page = 50

    def get_readonly_fields(
        self,
        request: HttpRequest,
        obj: object | None = None,
    ) -> tuple[str, ...]:
        return tuple(field.name for field in self.model._meta.fields)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: object | None = None) -> bool:
        return False


admin.site.register(
    (SC05Operation, SC05PortalStep, SC05StepAttempt, SC05Artifact),
    SC05EvidenceAdmin,
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


@admin.register(BriefingTemplate)
class BriefingTemplateAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("code", "name", "is_active", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("code", "name", "description")
    readonly_fields = ("created_at", "updated_at")


@admin.register(BriefingTemplateVersion)
class BriefingTemplateVersionAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("template", "version", "status", "published_at", "created_by")
    list_filter = ("status", "template")
    search_fields = ("template__code", "template__name")
    readonly_fields = ("id", "published_at", "created_at", "updated_at")

    def get_readonly_fields(
        self,
        request: HttpRequest,
        obj: BriefingTemplateVersion | None = None,
    ) -> tuple[str, ...]:
        fields = tuple(super().get_readonly_fields(request, obj))
        if obj is not None and obj.status == BriefingVersionStatus.PUBLISHED:
            return fields + ("template", "version", "schema", "status", "created_by")
        return fields


@admin.register(SocietaryBriefing)
class SocietaryBriefingAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = (
        "client_name",
        "client_document",
        "status",
        "created_by",
        "completed_by",
        "updated_at",
    )
    list_filter = ("status", "template_version__template")
    search_fields = ("client_name", "client_document", "id", "run__id")
    readonly_fields = (
        "id",
        "template_version",
        "run",
        "client_name",
        "client_document",
        "answers",
        "status",
        "created_by",
        "completed_by",
        "completed_at",
        "created_at",
        "updated_at",
    )

    def get_actions(
        self,
        request: HttpRequest,
    ) -> dict[str, tuple[Callable[..., str], str, str] | None]:
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions

    def has_delete_permission(
        self,
        request: HttpRequest,
        obj: SocietaryBriefing | None = None,
    ) -> bool:
        allowed = super().has_delete_permission(request, obj)
        return allowed and (obj is None or obj.status != SocietaryBriefingStatus.COMPLETED)
