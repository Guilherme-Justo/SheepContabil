from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from core.automations.sc06.rules import validate_template_schema
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
            if self.status == RunStatus.RUNNING or (self.started_at and not self.finished_at):
                return "Em andamento"
            if self.status in {RunStatus.QUEUED, RunStatus.PENDING}:
                return "Aguardando início"
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


class DocumentSource(models.TextChoices):
    MANUAL = "manual", "Upload manual"
    SIMULATED_INBOX = "simulated_inbox", "Caixa simulada"


class DocumentStatus(models.TextChoices):
    QUEUED = "queued", "Na fila"
    PROCESSING = "processing", "Em processamento"
    AWAITING_REVIEW = "awaiting_review", "Aguardando revisão"
    ROUTED = "routed", "Encaminhado"
    FAILED = "failed", "Falhou"


class DocumentIntakeStatus(models.TextChoices):
    QUEUED = "queued", "Na fila"
    DUPLICATE = "duplicate", "Duplicado"
    PROCESSING = "processing", "Em processamento"
    AWAITING_REVIEW = "awaiting_review", "Aguardando revisão"
    ROUTED = "routed", "Encaminhado"
    FAILED = "failed", "Falhou"


class DocumentType(models.TextChoices):
    INVOICE = "invoice", "Nota fiscal"
    TAX_PAYMENT = "tax_payment", "Guia ou tributo"
    BANK_STATEMENT = "bank_statement", "Extrato financeiro"
    PAYROLL = "payroll", "Documento trabalhista"
    CORPORATE_RECORD = "corporate_record", "Documento societário"
    OTHER = "other", "Outro documento"
    UNKNOWN = "unknown", "Não identificado"


class ExtractionMethod(models.TextChoices):
    PLAIN_TEXT = "plain_text", "Texto"
    PDF_TEXT = "pdf_text", "Texto do PDF"
    OCR = "ocr", "OCR"


class ClassificationAttemptStatus(models.TextChoices):
    PROCESSING = "processing", "Em processamento"
    SUCCEEDED = "succeeded", "Concluída"
    INVALID_RESPONSE = "invalid_response", "Resposta inválida"
    FAILED = "failed", "Falhou"


class DocumentReviewStatus(models.TextChoices):
    PENDING = "pending", "Pendente"
    COMPLETED = "completed", "Concluída"


class DocumentReviewReason(models.TextChoices):
    LOW_CONFIDENCE = "low_confidence", "Confiança insuficiente"
    AMBIGUOUS_CLIENT = "ambiguous_client", "Cliente ambíguo"
    UNKNOWN_CLIENT = "unknown_client", "Cliente não identificado"
    UNKNOWN_TYPE = "unknown_type", "Tipo não identificado"
    CLASSIFIER_UNAVAILABLE = "classifier_unavailable", "Classificador indisponível"
    INVALID_RESPONSE = "invalid_response", "Resposta inválida"


class DocumentDecisionOrigin(models.TextChoices):
    AUTOMATIC = "automatic", "Automático"
    HUMAN_REVIEW = "human_review", "Revisão humana"


class DocumentRunOutcome(models.TextChoices):
    NEW = "new", "Novo conteúdo"
    DUPLICATE_SOURCE = "duplicate_source", "Origem já processada"
    DUPLICATE_HASH = "duplicate_hash", "Conteúdo duplicado"


class DocumentRoutingStatus(models.TextChoices):
    PENDING = "pending", "Pendente"
    ROUTED = "routed", "Encaminhado"
    FAILED = "failed", "Falhou"


class FiscalClient(models.Model):
    """Synthetic client catalogue used to ground and route SC-04 predictions."""

    code = models.SlugField("código", max_length=80, primary_key=True)
    name = models.CharField("nome", max_length=180)
    document_number = models.CharField("CPF/CNPJ sintético", max_length=14, unique=True)
    aliases = models.JSONField("aliases", default=list, blank=True)
    route_prefix = models.SlugField("pasta de destino", max_length=100, unique=True)
    is_active = models.BooleanField("ativo", default=True)
    created_at = models.DateTimeField("criado em", auto_now_add=True)
    updated_at = models.DateTimeField("atualizado em", auto_now=True)

    class Meta:
        ordering = ("name", "code")
        verbose_name = "cliente fiscal sintético"
        verbose_name_plural = "clientes fiscais sintéticos"

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        super().clean()
        if not self.document_number.isdigit() or len(self.document_number) not in {11, 14}:
            raise ValidationError({"document_number": "Informe 11 ou 14 dígitos sintéticos."})
        if not isinstance(self.aliases, list) or len(self.aliases) > 20:
            raise ValidationError({"aliases": "Informe uma lista com até 20 aliases."})
        if any(
            not isinstance(alias, str) or not alias.strip() or len(alias) > 120
            for alias in self.aliases
        ):
            raise ValidationError({"aliases": "Cada alias deve conter até 120 caracteres."})


class FiscalDocument(models.Model):
    """Canonical, content-addressed document processed by SC-04."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sha256 = models.CharField("SHA-256", max_length=64, unique=True)
    storage_key = models.CharField("chave do original", max_length=500, unique=True)
    media_type = models.CharField("tipo de mídia", max_length=100)
    byte_size = models.PositiveIntegerField("tamanho em bytes")
    status = models.CharField(
        "estado",
        max_length=24,
        choices=DocumentStatus.choices,
        default=DocumentStatus.QUEUED,
        db_index=True,
    )
    extraction_method = models.CharField(
        "método de extração",
        max_length=20,
        choices=ExtractionMethod.choices,
        blank=True,
    )
    extracted_text_sha256 = models.CharField("hash do texto extraído", max_length=64, blank=True)
    extracted_excerpt = models.TextField("trecho extraído", blank=True)
    page_count = models.PositiveSmallIntegerField("páginas", null=True, blank=True)
    classified_type = models.CharField(
        "tipo documental",
        max_length=32,
        choices=DocumentType.choices,
        default=DocumentType.UNKNOWN,
    )
    type_confidence = models.DecimalField(
        "confiança no tipo",
        max_digits=5,
        decimal_places=4,
        default=0,
    )
    matched_client = models.ForeignKey(
        FiscalClient,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="documents",
        verbose_name="cliente identificado",
    )
    client_confidence = models.DecimalField(
        "confiança no cliente",
        max_digits=5,
        decimal_places=4,
        default=0,
    )
    evidence = models.JSONField("evidências", default=list, blank=True)
    last_error = models.TextField("último erro", blank=True)
    created_at = models.DateTimeField("criado em", auto_now_add=True)
    updated_at = models.DateTimeField("atualizado em", auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.CheckConstraint(
                condition=models.Q(byte_size__gt=0),
                name="sc04_doc_byte_size_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(type_confidence__gte=0, type_confidence__lte=1),
                name="sc04_doc_type_confidence_range",
            ),
            models.CheckConstraint(
                condition=models.Q(client_confidence__gte=0, client_confidence__lte=1),
                name="sc04_doc_client_confidence_range",
            ),
        ]
        indexes = [
            models.Index(fields=("status", "-created_at"), name="fiscal_doc_status_created_idx"),
            models.Index(
                fields=("classified_type", "-created_at"), name="fiscal_doc_type_created_idx"
            ),
        ]
        verbose_name = "documento fiscal"
        verbose_name_plural = "documentos fiscais"

    def __str__(self) -> str:
        return f"{self.sha256[:12]} · {self.get_status_display()}"

    @property
    def status_tone(self) -> str:
        if self.status == DocumentStatus.ROUTED:
            return "success"
        if self.status == DocumentStatus.AWAITING_REVIEW:
            return "warning"
        if self.status == DocumentStatus.FAILED:
            return "danger"
        return "active"


class DocumentIntake(models.Model):
    """One observed attachment; duplicates still remain auditable occurrences."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(
        FiscalDocument,
        on_delete=models.PROTECT,
        related_name="intakes",
        verbose_name="documento",
    )
    run = models.ForeignKey(
        AutomationRun,
        on_delete=models.PROTECT,
        related_name="document_intakes",
        verbose_name="execução",
    )
    source = models.CharField("origem", max_length=24, choices=DocumentSource.choices)
    source_reference = models.CharField("identificador na origem", max_length=180, blank=True)
    original_filename = models.CharField("nome original", max_length=255)
    status = models.CharField(
        "estado",
        max_length=24,
        choices=DocumentIntakeStatus.choices,
        default=DocumentIntakeStatus.QUEUED,
        db_index=True,
    )
    is_duplicate = models.BooleanField("duplicado", default=False)
    received_at = models.DateTimeField("recebido em", auto_now_add=True)

    class Meta:
        ordering = ("-received_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("source", "source_reference"),
                condition=~models.Q(source_reference=""),
                name="uniq_sc04_source_reference",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        is_duplicate=True,
                        status=DocumentIntakeStatus.DUPLICATE,
                    )
                    | models.Q(is_duplicate=False)
                    & ~models.Q(status=DocumentIntakeStatus.DUPLICATE)
                ),
                name="sc04_intake_duplicate_consistent",
            ),
        ]
        indexes = [
            models.Index(fields=("run", "status"), name="sc04_intake_run_status_idx"),
        ]
        verbose_name = "entrada documental"
        verbose_name_plural = "entradas documentais"

    def __str__(self) -> str:
        return self.original_filename


class DocumentRunItem(models.Model):
    """Occurrence of an intake in a run, including source redelivery."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(
        AutomationRun,
        on_delete=models.PROTECT,
        related_name="document_run_items",
        verbose_name="execução",
    )
    intake = models.ForeignKey(
        DocumentIntake,
        on_delete=models.PROTECT,
        related_name="run_items",
        verbose_name="entrada",
    )
    outcome = models.CharField(
        "resultado da ingestão", max_length=24, choices=DocumentRunOutcome.choices
    )
    created_at = models.DateTimeField("criado em", auto_now_add=True)

    class Meta:
        ordering = ("created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("run", "intake"),
                name="uniq_sc04_run_intake",
            ),
        ]
        indexes = [
            models.Index(fields=("run", "outcome"), name="sc04_run_item_outcome_idx"),
        ]
        verbose_name = "item de execução documental"
        verbose_name_plural = "itens de execução documental"

    def __str__(self) -> str:
        return f"{self.run_id} · {self.get_outcome_display()}"


class DocumentClassificationAttempt(models.Model):
    """Append-only provider attempt with only validated/minimized output."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(
        FiscalDocument,
        on_delete=models.PROTECT,
        related_name="classification_attempts",
        verbose_name="documento",
    )
    run = models.ForeignKey(
        AutomationRun,
        on_delete=models.PROTECT,
        related_name="document_classification_attempts",
        verbose_name="execução",
    )
    sequence = models.PositiveSmallIntegerField("tentativa")
    status = models.CharField(
        "estado",
        max_length=24,
        choices=ClassificationAttemptStatus.choices,
        default=ClassificationAttemptStatus.PROCESSING,
    )
    provider = models.CharField("provedor", max_length=40, default="openai")
    model = models.CharField("modelo", max_length=120, blank=True)
    prompt_version = models.CharField("versão do prompt", max_length=80)
    schema_version = models.CharField("versão do schema", max_length=80)
    input_sha256 = models.CharField("hash da entrada minimizada", max_length=64)
    input_char_count = models.PositiveIntegerField("caracteres enviados", default=0)
    provider_response_id = models.CharField("resposta do provedor", max_length=120, blank=True)
    predicted_document_type = models.CharField(
        "tipo previsto",
        max_length=32,
        choices=DocumentType.choices,
        default=DocumentType.UNKNOWN,
    )
    predicted_client = models.ForeignKey(
        FiscalClient,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="classification_attempts",
        verbose_name="cliente previsto",
    )
    type_confidence = models.DecimalField(
        "confiança no tipo",
        max_digits=5,
        decimal_places=4,
        default=0,
    )
    client_confidence = models.DecimalField(
        "confiança no cliente",
        max_digits=5,
        decimal_places=4,
        default=0,
    )
    is_ambiguous = models.BooleanField("resultado ambíguo", default=False)
    evidence = models.JSONField("evidências", default=list, blank=True)
    prediction = models.JSONField("snapshot de candidatos", default=dict, blank=True)
    error_code = models.CharField("código de erro", max_length=80, blank=True)
    error_message = models.TextField("erro operacional", blank=True)
    created_at = models.DateTimeField("criada em", auto_now_add=True)
    finished_at = models.DateTimeField("finalizada em", null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("document", "sequence"),
                name="uniq_sc04_document_attempt_sequence",
            ),
            models.CheckConstraint(
                condition=models.Q(type_confidence__gte=0, type_confidence__lte=1),
                name="sc04_attempt_type_confidence_range",
            ),
            models.CheckConstraint(
                condition=models.Q(client_confidence__gte=0, client_confidence__lte=1),
                name="sc04_attempt_client_confidence_range",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status=ClassificationAttemptStatus.PROCESSING,
                        finished_at__isnull=True,
                    )
                    | models.Q(
                        status__in=(
                            ClassificationAttemptStatus.SUCCEEDED,
                            ClassificationAttemptStatus.INVALID_RESPONSE,
                            ClassificationAttemptStatus.FAILED,
                        ),
                        finished_at__isnull=False,
                    )
                ),
                name="sc04_attempt_finished_consistent",
            ),
        ]
        indexes = [
            models.Index(fields=("run", "status"), name="sc04_attempt_run_status_idx"),
        ]
        verbose_name = "tentativa de classificação"
        verbose_name_plural = "tentativas de classificação"

    def __str__(self) -> str:
        return f"{self.document.sha256[:12]} · tentativa {self.sequence}"


class DocumentReview(models.Model):
    """Human decision required when the routing policy cannot accept a prediction."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.OneToOneField(
        FiscalDocument,
        on_delete=models.PROTECT,
        related_name="review",
        verbose_name="documento",
    )
    run = models.ForeignKey(
        AutomationRun,
        on_delete=models.PROTECT,
        related_name="document_reviews",
        verbose_name="execução",
    )
    suggested_attempt = models.ForeignKey(
        DocumentClassificationAttempt,
        on_delete=models.PROTECT,
        related_name="reviews",
        verbose_name="predição sugerida",
    )
    status = models.CharField(
        "estado",
        max_length=16,
        choices=DocumentReviewStatus.choices,
        default=DocumentReviewStatus.PENDING,
        db_index=True,
    )
    reason = models.CharField(
        "motivo",
        max_length=32,
        choices=DocumentReviewReason.choices,
    )
    policy_version = models.CharField("versão da política", max_length=80)
    resolved_document_type = models.CharField(
        "tipo confirmado",
        max_length=32,
        choices=DocumentType.choices,
        default=DocumentType.UNKNOWN,
    )
    resolved_client = models.ForeignKey(
        FiscalClient,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="document_reviews",
        verbose_name="cliente confirmado",
    )
    notes = models.TextField("observações", blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="document_reviews",
        verbose_name="revisada por",
    )
    created_at = models.DateTimeField("criada em", auto_now_add=True)
    resolved_at = models.DateTimeField("resolvida em", null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status=DocumentReviewStatus.PENDING,
                        resolved_document_type=DocumentType.UNKNOWN,
                        resolved_client__isnull=True,
                        reviewed_by__isnull=True,
                        resolved_at__isnull=True,
                    )
                    | models.Q(
                        status=DocumentReviewStatus.COMPLETED,
                        resolved_client__isnull=False,
                        reviewed_by__isnull=False,
                        resolved_at__isnull=False,
                    )
                    & ~models.Q(resolved_document_type=DocumentType.UNKNOWN)
                ),
                name="sc04_review_status_consistent",
            ),
        ]
        verbose_name = "revisão documental"
        verbose_name_plural = "revisões documentais"

    def __str__(self) -> str:
        return f"{self.document.sha256[:12]} · {self.get_status_display()}"


class DocumentDecision(models.Model):
    """Immutable final business decision, distinct from the model prediction."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.OneToOneField(
        FiscalDocument,
        on_delete=models.PROTECT,
        related_name="decision",
        verbose_name="documento",
    )
    classification_attempt = models.ForeignKey(
        DocumentClassificationAttempt,
        on_delete=models.PROTECT,
        related_name="decisions",
        verbose_name="tentativa de origem",
    )
    review = models.OneToOneField(
        DocumentReview,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="decision",
        verbose_name="revisão",
    )
    document_type = models.CharField(
        "tipo documental",
        max_length=32,
        choices=DocumentType.choices,
    )
    client = models.ForeignKey(
        FiscalClient,
        on_delete=models.PROTECT,
        related_name="document_decisions",
        verbose_name="cliente",
    )
    origin = models.CharField(
        "origem da decisão", max_length=20, choices=DocumentDecisionOrigin.choices
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="document_decisions",
        verbose_name="decidido por",
    )
    policy_version = models.CharField("versão da política", max_length=80)
    decided_at = models.DateTimeField("decidido em", auto_now_add=True)

    class Meta:
        ordering = ("-decided_at",)
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(
                        origin=DocumentDecisionOrigin.AUTOMATIC,
                        review__isnull=True,
                        decided_by__isnull=True,
                    )
                    | models.Q(
                        origin=DocumentDecisionOrigin.HUMAN_REVIEW,
                        review__isnull=False,
                        decided_by__isnull=False,
                    )
                ),
                name="sc04_decision_origin_consistent",
            ),
            models.CheckConstraint(
                condition=~models.Q(document_type=DocumentType.UNKNOWN),
                name="sc04_decision_type_known",
            ),
        ]
        verbose_name = "decisão documental"
        verbose_name_plural = "decisões documentais"

    def __str__(self) -> str:
        return f"{self.client.name} · {self.get_document_type_display()}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("Uma decisão documental concluída é imutável.")
        self.full_clean()
        super().save(*args, **kwargs)

    def clean(self) -> None:
        super().clean()
        if (
            self.classification_attempt_id
            and self.document_id
            and self.classification_attempt.document_id != self.document_id
        ):
            raise ValidationError("A tentativa precisa pertencer ao documento decidido.")
        selected_review = self.review if self.review_id else None
        if selected_review is not None and self.document_id:
            if selected_review.document_id != self.document_id:
                raise ValidationError("A revisão precisa pertencer ao documento decidido.")
            if selected_review.status != DocumentReviewStatus.COMPLETED:
                raise ValidationError("A revisão precisa estar concluída antes da decisão.")


class DocumentRouting(models.Model):
    """Immutable routing evidence; the original object is never removed."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    decision = models.OneToOneField(
        DocumentDecision,
        on_delete=models.PROTECT,
        related_name="routing",
        verbose_name="decisão",
    )
    run = models.ForeignKey(
        AutomationRun,
        on_delete=models.PROTECT,
        related_name="document_routings",
        verbose_name="execução",
    )
    storage_key = models.CharField("chave encaminhada", max_length=500, unique=True)
    status = models.CharField(
        "estado",
        max_length=16,
        choices=DocumentRoutingStatus.choices,
        default=DocumentRoutingStatus.PENDING,
    )
    attempt_count = models.PositiveSmallIntegerField("tentativas", default=0)
    last_error = models.TextField("último erro", blank=True)
    routed_at = models.DateTimeField("encaminhado em", null=True, blank=True)
    created_at = models.DateTimeField("criado em", auto_now_add=True)
    updated_at = models.DateTimeField("atualizado em", auto_now=True)

    class Meta:
        ordering = ("-routed_at",)
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status=DocumentRoutingStatus.PENDING,
                        routed_at__isnull=True,
                        last_error="",
                    )
                    | models.Q(
                        status=DocumentRoutingStatus.FAILED,
                        routed_at__isnull=True,
                    )
                    & ~models.Q(last_error="")
                    | models.Q(
                        status=DocumentRoutingStatus.ROUTED,
                        routed_at__isnull=False,
                        last_error="",
                        attempt_count__gte=1,
                    )
                ),
                name="sc04_routing_status_consistent",
            ),
        ]
        verbose_name = "encaminhamento documental"
        verbose_name_plural = "encaminhamentos documentais"

    def __str__(self) -> str:
        return self.storage_key


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


class BriefingTemplate(models.Model):
    """Stable identity of a configurable SC-06 briefing template."""

    code = models.SlugField("código", max_length=80, primary_key=True)
    name = models.CharField("nome", max_length=160)
    description = models.TextField("descrição", blank=True)
    is_active = models.BooleanField("ativo", default=True)
    created_at = models.DateTimeField("criado em", auto_now_add=True)
    updated_at = models.DateTimeField("atualizado em", auto_now=True)

    class Meta:
        ordering = ("name", "code")
        verbose_name = "template de briefing"
        verbose_name_plural = "templates de briefing"

    def __str__(self) -> str:
        return f"{self.code} · {self.name}"


class BriefingVersionStatus(models.TextChoices):
    DRAFT = "draft", "Rascunho"
    PUBLISHED = "published", "Publicada"


class BriefingTemplateVersion(models.Model):
    """Validated and immutable-after-publication schema for one template."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    template = models.ForeignKey(
        BriefingTemplate,
        on_delete=models.PROTECT,
        related_name="versions",
        verbose_name="template",
    )
    version = models.PositiveIntegerField("versão")
    schema = models.JSONField(
        "schema",
        default=dict,
        validators=(validate_template_schema,),
    )
    status = models.CharField(
        "estado",
        max_length=16,
        choices=BriefingVersionStatus.choices,
        default=BriefingVersionStatus.DRAFT,
    )
    published_at = models.DateTimeField("publicada em", null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_briefing_template_versions",
        verbose_name="criada por",
    )
    created_at = models.DateTimeField("criada em", auto_now_add=True)
    updated_at = models.DateTimeField("atualizada em", auto_now=True)

    class Meta:
        ordering = ("template", "-version")
        constraints = [
            models.UniqueConstraint(
                fields=("template", "version"),
                name="uniq_briefing_template_version",
            ),
            models.CheckConstraint(
                condition=models.Q(version__gte=1),
                name="briefing_version_gte_one",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status=BriefingVersionStatus.DRAFT, published_at__isnull=True)
                    | models.Q(status=BriefingVersionStatus.PUBLISHED, published_at__isnull=False)
                ),
                name="briefing_publication_timestamp_consistent",
            ),
        ]
        verbose_name = "versão de template de briefing"
        verbose_name_plural = "versões de templates de briefing"

    def __str__(self) -> str:
        return f"{self.template.name} · v{self.version}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self.status == BriefingVersionStatus.PUBLISHED and self.published_at is None:
            self.published_at = timezone.now()
        self._ensure_published_version_is_immutable()
        self.full_clean()
        super().save(*args, **kwargs)

    def clean(self) -> None:
        super().clean()
        validate_template_schema(self.schema)
        if self.status == BriefingVersionStatus.PUBLISHED and self.published_at is None:
            raise ValidationError({"published_at": "Informe quando esta versão foi publicada."})
        if self.status == BriefingVersionStatus.DRAFT and self.published_at is not None:
            raise ValidationError(
                {"published_at": "Uma versão em rascunho não pode ter data de publicação."}
            )

    def _ensure_published_version_is_immutable(self) -> None:
        if self._state.adding:
            return
        previous = type(self).objects.filter(pk=self.pk).first()
        if previous is None or previous.status != BriefingVersionStatus.PUBLISHED:
            return
        protected_fields = (
            "template_id",
            "version",
            "schema",
            "status",
            "published_at",
            "created_by_id",
        )
        if any(getattr(previous, field) != getattr(self, field) for field in protected_fields):
            raise ValidationError("Uma versão publicada é imutável; crie uma nova versão.")


class SocietaryBriefingStatus(models.TextChoices):
    DRAFT = "draft", "Rascunho"
    COMPLETED = "completed", "Concluído"
    CANCELLED = "cancelled", "Cancelado"


class SocietaryBriefing(models.Model):
    """SC-06 answers bound permanently to the schema version used."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    template_version = models.ForeignKey(
        BriefingTemplateVersion,
        on_delete=models.PROTECT,
        related_name="briefings",
        verbose_name="versão do template",
    )
    run = models.OneToOneField(
        AutomationRun,
        on_delete=models.PROTECT,
        related_name="societary_briefing",
        verbose_name="execução",
    )
    client_name = models.CharField("cliente", max_length=180)
    client_document = models.CharField("número documental sintético", max_length=40)
    answers = models.JSONField("respostas", default=dict, blank=True)
    status = models.CharField(
        "estado",
        max_length=16,
        choices=SocietaryBriefingStatus.choices,
        default=SocietaryBriefingStatus.DRAFT,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_societary_briefings",
        verbose_name="criado por",
    )
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="completed_societary_briefings",
        verbose_name="concluído por",
    )
    completed_at = models.DateTimeField("concluído em", null=True, blank=True)
    created_at = models.DateTimeField("criado em", auto_now_add=True)
    updated_at = models.DateTimeField("atualizado em", auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status=SocietaryBriefingStatus.COMPLETED,
                        completed_at__isnull=False,
                        completed_by__isnull=False,
                    )
                    | models.Q(
                        status__in=(
                            SocietaryBriefingStatus.DRAFT,
                            SocietaryBriefingStatus.CANCELLED,
                        ),
                        completed_at__isnull=True,
                        completed_by__isnull=True,
                    )
                ),
                name="briefing_completion_audit_consistent",
            ),
        ]
        indexes = [
            models.Index(fields=("status", "-created_at"), name="briefing_status_created_idx"),
        ]
        verbose_name = "briefing societário"
        verbose_name_plural = "briefings societários"

    def __str__(self) -> str:
        return f"{self.client_name} · {self.template_version}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            previous = type(self).objects.filter(pk=self.pk).first()
            if previous is not None and previous.status == SocietaryBriefingStatus.COMPLETED:
                protected_fields = (
                    "template_version_id",
                    "run_id",
                    "client_name",
                    "client_document",
                    "answers",
                    "status",
                    "created_by_id",
                    "completed_by_id",
                    "completed_at",
                )
                if any(
                    getattr(previous, field) != getattr(self, field) for field in protected_fields
                ):
                    raise ValidationError(
                        "Um briefing concluído é imutável; preserve a evidência histórica."
                    )
        super().save(*args, **kwargs)

    @property
    def status_tone(self) -> str:
        if self.status == SocietaryBriefingStatus.COMPLETED:
            return "success"
        if self.status == SocietaryBriefingStatus.CANCELLED:
            return "neutral"
        return "warning"


class SC05ClientStatus(models.TextChoices):
    ACTIVE = "active", "Ativo"
    BLOCKED = "blocked", "Bloqueado"
    PARTIAL = "partial", "Estado parcial"
    UNKNOWN = "unknown", "A reconciliar"


class SC05Action(models.TextChoices):
    BLOCK = "block", "Bloquear"
    UNBLOCK = "unblock", "Desbloquear"


class SC05Scenario(models.TextChoices):
    HAPPY_PATH = "happy_path", "Fluxo normal"
    FAIL_TASKS_APPLY = "fail_tasks_apply", "Falha ao bloquear tarefas"
    TIMEOUT_ACCOUNTING_APPLY = (
        "timeout_accounting_apply",
        "Timeout no sistema contábil",
    )
    FAIL_TASKS_AND_FILES_COMPENSATION = (
        "fail_tasks_apply_and_files_compensation",
        "Falha em tarefas e na compensação de arquivos",
    )


class SC05Portal(models.TextChoices):
    FILES = "files", "Portal de arquivos"
    ACCOUNTING = "accounting", "Sistema contábil"
    TASKS = "tasks", "Sistema de tarefas"


class SC05StepStatus(models.TextChoices):
    PENDING = "pending", "Pendente"
    RUNNING = "running", "Em execução"
    APPLIED = "applied", "Aplicado"
    UNCHANGED = "unchanged", "Já estava correto"
    FAILED = "failed", "Falhou"
    COMPENSATED = "compensated", "Compensado"
    COMPENSATION_FAILED = "compensation_failed", "Compensação falhou"


class SC05AttemptOperation(models.TextChoices):
    INSPECT = "inspect", "Capturar estado"
    APPLY = "apply", "Aplicar"
    COMPENSATE = "compensate", "Compensar"


class SC05AttemptStatus(models.TextChoices):
    RUNNING = "running", "Em execução"
    SUCCEEDED = "succeeded", "Concluída"
    FAILED = "failed", "Falhou"


class SC05ArtifactKind(models.TextChoices):
    SCREENSHOT = "screenshot", "Captura de tela"


class SC05Client(models.Model):
    """Synthetic client projection owned by the SC-05 orchestration module."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    external_reference = models.SlugField("referência externa", max_length=80, unique=True)
    name = models.CharField("cliente", max_length=180)
    document = models.CharField("documento sintético", max_length=24, unique=True)
    status = models.CharField(
        "estado operacional",
        max_length=16,
        choices=SC05ClientStatus.choices,
        default=SC05ClientStatus.ACTIVE,
        db_index=True,
    )
    task_restore_snapshot = models.JSONField(
        "responsáveis anteriores das tarefas",
        default=dict,
        blank=True,
    )
    created_at = models.DateTimeField("criado em", auto_now_add=True)
    updated_at = models.DateTimeField("atualizado em", auto_now=True)

    class Meta:
        ordering = ("name",)
        verbose_name = "cliente do SC-05"
        verbose_name_plural = "clientes do SC-05"

    def __str__(self) -> str:
        return self.name

    @property
    def status_tone(self) -> str:
        if self.status == SC05ClientStatus.ACTIVE:
            return "success"
        if self.status == SC05ClientStatus.BLOCKED:
            return "warning"
        if self.status == SC05ClientStatus.PARTIAL:
            return "danger"
        return "neutral"


class SC05Operation(models.Model):
    """One auditable block or unblock saga bound to the common run."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.OneToOneField(
        AutomationRun,
        on_delete=models.PROTECT,
        related_name="sc05_operation",
        verbose_name="execução",
    )
    client = models.ForeignKey(
        SC05Client,
        on_delete=models.PROTECT,
        related_name="operations",
        verbose_name="cliente",
    )
    action = models.CharField("ação", max_length=12, choices=SC05Action.choices)
    scenario = models.CharField(
        "cenário demonstrativo",
        max_length=64,
        choices=SC05Scenario.choices,
        default=SC05Scenario.HAPPY_PATH,
    )
    resume_count = models.PositiveSmallIntegerField("retomadas", default=0)
    created_at = models.DateTimeField("criada em", auto_now_add=True)
    updated_at = models.DateTimeField("atualizada em", auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("client", "-created_at"), name="sc05_op_client_created_idx"),
        ]
        verbose_name = "operação do SC-05"
        verbose_name_plural = "operações do SC-05"

    def __str__(self) -> str:
        return f"{self.client.name} · {self.get_action_display()}"


class SC05PortalStep(models.Model):
    """Current projection of one logical saga step; attempts remain append-only."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    operation = models.ForeignKey(
        SC05Operation,
        on_delete=models.PROTECT,
        related_name="steps",
        verbose_name="operação",
    )
    portal = models.CharField("portal", max_length=20, choices=SC05Portal.choices)
    position = models.PositiveSmallIntegerField("ordem")
    status = models.CharField(
        "estado",
        max_length=32,
        choices=SC05StepStatus.choices,
        default=SC05StepStatus.PENDING,
    )
    before_state = models.JSONField("estado anterior", default=dict, blank=True)
    desired_state = models.JSONField("estado desejado", default=dict, blank=True)
    after_state = models.JSONField("estado confirmado", default=dict, blank=True)
    error_message = models.TextField("erro operacional", blank=True)
    started_at = models.DateTimeField("iniciado em", null=True, blank=True)
    finished_at = models.DateTimeField("finalizado em", null=True, blank=True)
    updated_at = models.DateTimeField("atualizado em", auto_now=True)

    class Meta:
        ordering = ("position",)
        constraints = [
            models.UniqueConstraint(
                fields=("operation", "portal"),
                name="uniq_sc05_operation_portal",
            ),
            models.UniqueConstraint(
                fields=("operation", "position"),
                name="uniq_sc05_operation_position",
            ),
        ]
        indexes = [
            models.Index(fields=("operation", "status"), name="sc05_step_operation_status_idx"),
        ]
        verbose_name = "etapa de portal do SC-05"
        verbose_name_plural = "etapas de portal do SC-05"

    def __str__(self) -> str:
        return f"{self.operation} · {self.get_portal_display()}"

    @property
    def status_tone(self) -> str:
        if self.status in {SC05StepStatus.APPLIED, SC05StepStatus.UNCHANGED}:
            return "success"
        if self.status == SC05StepStatus.COMPENSATED:
            return "neutral"
        if self.status in {SC05StepStatus.FAILED, SC05StepStatus.COMPENSATION_FAILED}:
            return "danger"
        return "warning"

    @property
    def before_state_label(self) -> str:
        return self._state_label(self.before_state)

    @property
    def desired_state_label(self) -> str:
        return self._state_label(self.desired_state)

    @property
    def after_state_label(self) -> str:
        return self._state_label(self.after_state)

    def _state_label(self, state: dict[str, Any]) -> str:
        if not state:
            return "Não capturado"
        if self.portal in {SC05Portal.FILES, SC05Portal.ACCOUNTING}:
            blocked = state.get("blocked")
            if blocked is True:
                return "Bloqueado"
            if blocked is False:
                return "Ativo"
            return "Estado inválido"
        tasks = state.get("tasks")
        if state.get("client_active") is not True or not isinstance(tasks, list):
            return "Estado inválido"
        open_tasks = [item for item in tasks if isinstance(item, dict) and item.get("is_open")]
        blocked_tasks = [
            item for item in open_tasks if item.get("assignee") == "BLOQUEADO_INADIMPLENCIA"
        ]
        return (
            f"Cliente ativo · {len(blocked_tasks)} de {len(open_tasks)} tarefa(s) "
            "aberta(s) com marcador"
        )


class SC05StepAttempt(models.Model):
    """Append-only evidence of an inspect, mutation, or compensation call."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    step = models.ForeignKey(
        SC05PortalStep,
        on_delete=models.PROTECT,
        related_name="attempts",
        verbose_name="etapa",
    )
    sequence = models.PositiveSmallIntegerField("tentativa")
    operation = models.CharField(
        "operação",
        max_length=16,
        choices=SC05AttemptOperation.choices,
    )
    status = models.CharField(
        "estado",
        max_length=16,
        choices=SC05AttemptStatus.choices,
        default=SC05AttemptStatus.RUNNING,
    )
    state_before = models.JSONField("estado antes", default=dict, blank=True)
    state_after = models.JSONField("estado depois", default=dict, blank=True)
    error_code = models.CharField("código de erro", max_length=80, blank=True)
    error_message = models.TextField("erro operacional", blank=True)
    created_at = models.DateTimeField("criada em", auto_now_add=True)
    finished_at = models.DateTimeField("finalizada em", null=True, blank=True)

    class Meta:
        ordering = ("sequence",)
        constraints = [
            models.UniqueConstraint(
                fields=("step", "sequence"),
                name="uniq_sc05_step_attempt_sequence",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status=SC05AttemptStatus.RUNNING,
                        finished_at__isnull=True,
                        error_code="",
                        error_message="",
                    )
                    | models.Q(
                        status=SC05AttemptStatus.SUCCEEDED,
                        finished_at__isnull=False,
                        error_code="",
                        error_message="",
                    )
                    | models.Q(
                        status=SC05AttemptStatus.FAILED,
                        finished_at__isnull=False,
                    )
                    & ~models.Q(error_code="")
                    & ~models.Q(error_message="")
                ),
                name="sc05_attempt_status_consistent",
            ),
        ]
        indexes = [
            models.Index(fields=("step", "status"), name="sc05_attempt_step_status_idx"),
        ]
        verbose_name = "tentativa de etapa do SC-05"
        verbose_name_plural = "tentativas de etapa do SC-05"

    def __str__(self) -> str:
        return f"{self.step} · tentativa {self.sequence}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            previous = type(self).objects.filter(pk=self.pk).first()
            if previous is not None and previous.status != SC05AttemptStatus.RUNNING:
                raise ValidationError("Uma tentativa finalizada do SC-05 é imutável.")
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ValidationError("Tentativas do SC-05 são evidências append-only.")

    @property
    def status_tone(self) -> str:
        if self.status == SC05AttemptStatus.SUCCEEDED:
            return "success"
        if self.status == SC05AttemptStatus.FAILED:
            return "danger"
        return "warning"


class SC05Artifact(models.Model):
    """Immutable private RPA artifact linked to exactly one browser attempt."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    attempt = models.ForeignKey(
        SC05StepAttempt,
        on_delete=models.PROTECT,
        related_name="artifacts",
        verbose_name="tentativa",
    )
    kind = models.CharField(
        "tipo",
        max_length=16,
        choices=SC05ArtifactKind.choices,
        default=SC05ArtifactKind.SCREENSHOT,
    )
    storage_key = models.CharField("chave privada", max_length=500, unique=True)
    sha256 = models.CharField("SHA-256", max_length=64)
    content_type = models.CharField("tipo de mídia", max_length=100, default="image/png")
    byte_size = models.PositiveIntegerField("tamanho em bytes")
    created_at = models.DateTimeField("criado em", auto_now_add=True)

    class Meta:
        ordering = ("created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("attempt", "kind"),
                name="uniq_sc05_attempt_artifact_kind",
            ),
        ]
        verbose_name = "artefato RPA do SC-05"
        verbose_name_plural = "artefatos RPA do SC-05"

    def __str__(self) -> str:
        return self.storage_key

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("Um artefato RPA concluído é imutável.")
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ValidationError("Artefatos RPA são evidências append-only.")
