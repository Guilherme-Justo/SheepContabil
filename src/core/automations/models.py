from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import TYPE_CHECKING

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.db import models
from django.utils import timezone

from core.identity.models import Area

if TYPE_CHECKING:
    from core.identity.models import User


class AutomationNature(models.TextChoices):
    AI_AGENT = "ai_agent", "Agente de IA"
    RPA = "rpa", "RPA"
    CONTROL = "control", "Controle sistematizado"


class AutomationComplexity(models.TextChoices):
    LOW = "low", "Baixa"
    MEDIUM = "medium", "Média"
    HIGH = "high", "Alta"


class AutomationFrequency(models.TextChoices):
    DAILY = "daily", "Diário"
    MONTHLY = "monthly", "Mensal"
    ON_DEMAND = "on_demand", "Sob demanda"


class AutomationModuleQuerySet(models.QuerySet["AutomationModule"]):
    def enabled(self) -> AutomationModuleQuerySet:
        return self.filter(is_enabled=True)

    def visible_to(self, user: User | AnonymousUser) -> AutomationModuleQuerySet:
        if isinstance(user, AnonymousUser):
            return self.none()
        if user.is_business_administrator:
            return self.enabled()
        return self.enabled().filter(area__memberships__user=user).distinct()


class AutomationModule(models.Model):
    code = models.CharField("código", max_length=5, primary_key=True)
    slug = models.SlugField("slug", max_length=80, unique=True)
    name = models.CharField("nome", max_length=140)
    short_description = models.CharField("descrição", max_length=320)
    nature = models.CharField("natureza", max_length=20, choices=AutomationNature.choices)
    complexity = models.CharField(
        "complexidade",
        max_length=10,
        choices=AutomationComplexity.choices,
    )
    frequency = models.CharField(
        "frequência",
        max_length=20,
        choices=AutomationFrequency.choices,
    )
    area = models.ForeignKey(
        Area,
        on_delete=models.PROTECT,
        related_name="automation_modules",
        verbose_name="área",
    )
    is_enabled = models.BooleanField("habilitado", default=True)
    sort_order = models.PositiveSmallIntegerField("ordem", default=0)
    created_at = models.DateTimeField("criado em", auto_now_add=True)
    updated_at = models.DateTimeField("atualizado em", auto_now=True)

    objects = AutomationModuleQuerySet.as_manager()

    class Meta:
        ordering = ("sort_order", "code")
        verbose_name = "módulo de automação"
        verbose_name_plural = "módulos de automação"

    def __str__(self) -> str:
        return f"{self.code} · {self.name}"


class RunTrigger(models.TextChoices):
    MANUAL = "manual", "Manual"
    SCHEDULED = "scheduled", "Agendado"


class RunStatus(models.TextChoices):
    PENDING = "pending", "Pendente"
    QUEUED = "queued", "Na fila"
    RUNNING = "running", "Em execução"
    AWAITING_REVIEW = "awaiting_review", "Aguardando revisão"
    SUCCEEDED = "succeeded", "Concluída"
    SUCCEEDED_WITH_WARNINGS = "succeeded_with_warnings", "Concluída com alertas"
    PARTIALLY_FAILED = "partially_failed", "Falha parcial"
    FAILED = "failed", "Falhou"
    CANCELLED = "cancelled", "Cancelada"


class AutomationRun(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    module = models.ForeignKey(
        AutomationModule,
        on_delete=models.PROTECT,
        related_name="runs",
        verbose_name="módulo",
    )
    trigger = models.CharField("origem", max_length=12, choices=RunTrigger.choices)
    status = models.CharField(
        "estado",
        max_length=32,
        choices=RunStatus.choices,
        default=RunStatus.PENDING,
    )
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="automation_runs",
        verbose_name="disparada por",
    )
    parameters = models.JSONField("parâmetros", default=dict, blank=True)
    idempotency_key = models.CharField(
        "chave de idempotência",
        max_length=180,
        unique=True,
        null=True,
        blank=True,
    )
    summary = models.TextField("resumo", blank=True)
    error_message = models.TextField("erro apresentado", blank=True)
    metadata = models.JSONField("metadados", default=dict, blank=True)
    created_at = models.DateTimeField("criada em", auto_now_add=True)
    started_at = models.DateTimeField("iniciada em", null=True, blank=True)
    finished_at = models.DateTimeField("finalizada em", null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("module", "-created_at"), name="run_module_created_idx"),
            models.Index(fields=("status", "-created_at"), name="run_status_created_idx"),
        ]
        verbose_name = "execução"
        verbose_name_plural = "execuções"

    def __str__(self) -> str:
        return f"{self.module.code} · {self.get_status_display()} · {self.created_at:%d/%m/%Y}"

    @property
    def duration(self) -> timedelta | None:
        if not self.started_at or not self.finished_at:
            return None
        return self.finished_at - self.started_at

    @property
    def duration_label(self) -> str:
        duration = self.duration
        if duration is None:
            return "—"
        total_seconds = max(0, int(duration.total_seconds()))
        minutes, seconds = divmod(total_seconds, 60)
        if minutes:
            return f"{minutes} min {seconds:02d} s"
        return f"{seconds} s"

    @property
    def status_tone(self) -> str:
        if self.status == RunStatus.SUCCEEDED:
            return "success"
        if self.status in {
            RunStatus.AWAITING_REVIEW,
            RunStatus.SUCCEEDED_WITH_WARNINGS,
            RunStatus.PENDING,
        }:
            return "warning"
        if self.status in {RunStatus.FAILED, RunStatus.PARTIALLY_FAILED}:
            return "danger"
        if self.status == RunStatus.CANCELLED:
            return "neutral"
        return "active"


class CertificateStatus(models.TextChoices):
    ACTIVE = "active", "Ativo"
    REVOKED = "revoked", "Revogado"
    REPLACED = "replaced", "Substituído"


class CommunicationChannel(models.TextChoices):
    EMAIL = "email", "E-mail"
    WHATSAPP = "whatsapp", "WhatsApp"


class CommunicationStatus(models.TextChoices):
    PENDING = "pending", "Pendente"
    SENT = "sent", "Enviada"
    FAILED = "failed", "Falhou"


class DigitalCertificateQuerySet(models.QuerySet["DigitalCertificate"]):
    def active(self) -> DigitalCertificateQuerySet:
        return self.filter(status=CertificateStatus.ACTIVE)

    def expiring_between(
        self,
        *,
        start_date: date,
        end_date: date,
    ) -> DigitalCertificateQuerySet:
        """Return active certificates in an inclusive expiration window."""
        return self.active().filter(valid_until__gte=start_date, valid_until__lte=end_date)


class DigitalCertificate(models.Model):
    """Certificate monitored by SC-20, with only synthetic business data."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    serial_number = models.CharField("identificador", max_length=120, unique=True)
    client_name = models.CharField("cliente", max_length=180)
    client_document = models.CharField("CPF/CNPJ", max_length=18, db_index=True)
    responsible_name = models.CharField("responsável", max_length=180)
    contact_email = models.EmailField("e-mail de contato", blank=True)
    contact_phone = models.CharField("telefone de contato", max_length=24, blank=True)
    preferred_channel = models.CharField(
        "canal preferencial",
        max_length=16,
        choices=CommunicationChannel.choices,
        default=CommunicationChannel.EMAIL,
    )
    valid_until = models.DateField("válido até", db_index=True)
    status = models.CharField(
        "estado",
        max_length=16,
        choices=CertificateStatus.choices,
        default=CertificateStatus.ACTIVE,
        db_index=True,
    )
    created_at = models.DateTimeField("criado em", auto_now_add=True)
    updated_at = models.DateTimeField("atualizado em", auto_now=True)

    objects = DigitalCertificateQuerySet.as_manager()

    class Meta:
        ordering = ("valid_until", "client_name", "serial_number")
        indexes = [
            models.Index(
                fields=("status", "valid_until"),
                name="cert_status_expiry_idx",
            ),
        ]
        verbose_name = "certificado digital"
        verbose_name_plural = "certificados digitais"

    def __str__(self) -> str:
        return f"{self.client_name} · {self.valid_until:%d/%m/%Y}"

    def recipient_for(self, channel: str) -> str:
        if channel == CommunicationChannel.EMAIL:
            return self.contact_email
        if channel == CommunicationChannel.WHATSAPP:
            return self.contact_phone
        return ""

    @property
    def document(self) -> str:
        return self.client_document

    @property
    def expires_on(self) -> date:
        return self.valid_until

    @property
    def contact_name(self) -> str:
        return self.responsible_name

    @property
    def days_remaining(self) -> int:
        return (self.valid_until - timezone.localdate()).days

    @property
    def days_remaining_abs(self) -> int:
        return abs(self.days_remaining)

    @property
    def status_tone(self) -> str:
        if self.status == CertificateStatus.ACTIVE:
            return "success"
        if self.status == CertificateStatus.REVOKED:
            return "danger"
        return "neutral"


class CertificateCommunication(models.Model):
    """Logical, deduplicated notification for one certificate expiration."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    certificate = models.ForeignKey(
        DigitalCertificate,
        on_delete=models.PROTECT,
        related_name="communications",
        verbose_name="certificado",
    )
    certificate_valid_until = models.DateField("validade considerada")
    channel = models.CharField(
        "canal",
        max_length=16,
        choices=CommunicationChannel.choices,
    )
    policy_key = models.CharField("política", max_length=80)
    recipient = models.CharField("destinatário", max_length=254)
    status = models.CharField(
        "estado",
        max_length=16,
        choices=CommunicationStatus.choices,
        default=CommunicationStatus.PENDING,
    )
    first_run = models.ForeignKey(
        AutomationRun,
        on_delete=models.PROTECT,
        related_name="initiated_communications",
        verbose_name="primeira execução",
    )
    latest_run = models.ForeignKey(
        AutomationRun,
        on_delete=models.PROTECT,
        related_name="latest_communications",
        verbose_name="execução mais recente",
    )
    sent_at = models.DateTimeField("enviada em", null=True, blank=True)
    last_error = models.TextField("último erro", blank=True)
    created_at = models.DateTimeField("criada em", auto_now_add=True)
    updated_at = models.DateTimeField("atualizada em", auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("certificate", "certificate_valid_until", "channel", "policy_key"),
                name="uniq_cert_expiry_channel_policy",
            ),
        ]
        indexes = [
            models.Index(fields=("status", "-created_at"), name="comm_status_created_idx"),
        ]
        verbose_name = "comunicação de certificado"
        verbose_name_plural = "comunicações de certificados"

    def __str__(self) -> str:
        return f"{self.certificate.client_name} · {self.get_channel_display()}"

    @property
    def status_tone(self) -> str:
        if self.status == CommunicationStatus.SENT:
            return "success"
        if self.status == CommunicationStatus.FAILED:
            return "danger"
        return "warning"


class CommunicationAttempt(models.Model):
    """Immutable audit record of a delivery attempt for a communication."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    communication = models.ForeignKey(
        CertificateCommunication,
        on_delete=models.PROTECT,
        related_name="attempts",
        verbose_name="comunicação",
    )
    run = models.ForeignKey(
        AutomationRun,
        on_delete=models.PROTECT,
        related_name="communication_attempts",
        verbose_name="execução",
    )
    sequence = models.PositiveSmallIntegerField("tentativa")
    status = models.CharField(
        "estado",
        max_length=16,
        choices=CommunicationStatus.choices,
        default=CommunicationStatus.PENDING,
    )
    recipient = models.CharField("destinatário", max_length=254)
    provider_message_id = models.CharField("identificador no provedor", max_length=120, blank=True)
    error_message = models.TextField("erro", blank=True)
    payload = models.JSONField("conteúdo auditável", default=dict, blank=True)
    created_at = models.DateTimeField("criada em", auto_now_add=True)
    finished_at = models.DateTimeField("finalizada em", null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("communication", "sequence"),
                name="uniq_communication_attempt_sequence",
            ),
            models.UniqueConstraint(
                fields=("communication", "run"),
                name="uniq_communication_attempt_run",
            ),
        ]
        indexes = [
            models.Index(fields=("run", "status"), name="attempt_run_status_idx"),
        ]
        verbose_name = "tentativa de comunicação"
        verbose_name_plural = "tentativas de comunicação"

    def __str__(self) -> str:
        return f"{self.communication} · tentativa {self.sequence}"

    @property
    def certificate(self) -> DigitalCertificate:
        return self.communication.certificate

    @property
    def channel(self) -> str:
        return self.communication.channel

    def get_channel_display(self) -> str:
        return self.communication.get_channel_display()

    @property
    def status_tone(self) -> str:
        if self.status == CommunicationStatus.SENT:
            return "success"
        if self.status == CommunicationStatus.FAILED:
            return "danger"
        return "warning"
