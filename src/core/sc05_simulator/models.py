from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from django.core.exceptions import ValidationError
from django.db import models

if TYPE_CHECKING:
    from django_stubs_ext.db.models.manager import RelatedManager


class SimulatorSystem(models.TextChoices):
    FILES = "files", "Arquivos"
    ACCOUNTING = "accounting", "Contábil"


class SimulatorClient(models.Model):
    external_id = models.SlugField("identificador externo", max_length=80, unique=True)
    name = models.CharField("nome", max_length=180)
    document = models.CharField("CPF/CNPJ sintético", max_length=14, unique=True)
    is_active = models.BooleanField("ativo no sistema de tarefas", default=True)

    objects: ClassVar[models.Manager[SimulatorClient]] = models.Manager()

    if TYPE_CHECKING:
        service_accounts: RelatedManager[SimulatorServiceAccount]
        tasks: RelatedManager[SimulatorTask]

    class Meta:
        ordering = ("name", "external_id")
        verbose_name = "cliente do simulador"
        verbose_name_plural = "clientes do simulador"

    def __str__(self) -> str:
        return f"{self.external_id} · {self.name}"

    def clean(self) -> None:
        super().clean()
        if not self.document.isdigit() or len(self.document) not in {11, 14}:
            raise ValidationError({"document": "Informe 11 ou 14 dígitos sintéticos."})


class SimulatorServiceAccount(models.Model):
    client = models.ForeignKey(
        SimulatorClient,
        on_delete=models.CASCADE,
        related_name="service_accounts",
        verbose_name="cliente",
    )
    system = models.CharField("sistema", max_length=20, choices=SimulatorSystem.choices)
    is_blocked = models.BooleanField("bloqueada", default=False)

    objects: ClassVar[models.Manager[SimulatorServiceAccount]] = models.Manager()

    class Meta:
        ordering = ("system", "client__name", "client__external_id")
        constraints = [
            models.UniqueConstraint(
                fields=("client", "system"),
                name="sc05_sim_unique_client_system",
            )
        ]
        verbose_name = "conta de serviço do simulador"
        verbose_name_plural = "contas de serviço do simulador"

    def __str__(self) -> str:
        state = "bloqueada" if self.is_blocked else "ativa"
        return f"{self.client.external_id} · {SimulatorSystem(self.system).label} · {state}"


class SimulatorTask(models.Model):
    reference = models.SlugField("referência", max_length=80, unique=True)
    client = models.ForeignKey(
        SimulatorClient,
        on_delete=models.CASCADE,
        related_name="tasks",
        verbose_name="cliente",
    )
    title = models.CharField("título", max_length=240)
    assignee = models.CharField("responsável", max_length=180)
    previous_assignee = models.CharField("responsável anterior", max_length=180, blank=True)
    is_open = models.BooleanField("aberta", default=True)

    objects: ClassVar[models.Manager[SimulatorTask]] = models.Manager()

    class Meta:
        ordering = ("client__name", "reference")
        verbose_name = "tarefa do simulador"
        verbose_name_plural = "tarefas do simulador"

    def __str__(self) -> str:
        return f"{self.reference} · {self.client.external_id}"
