from __future__ import annotations

import uuid
from datetime import timedelta
from typing import TYPE_CHECKING

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.db import models

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
