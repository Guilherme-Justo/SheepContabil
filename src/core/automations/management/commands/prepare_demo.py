from __future__ import annotations

from datetime import timedelta
from io import BytesIO
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count
from django.utils import timezone
from pypdf import PdfReader

from core.automations.models import (
    AutomationModule,
    AutomationRun,
    CertificateStatus,
    CommunicationChannel,
    DigitalCertificate,
    DocumentRoutingStatus,
    RunStatus,
    SC05Action,
    SC05Client,
    SC05ClientStatus,
    SC05Operation,
    SC05Scenario,
    SocietaryBriefing,
    SocietaryBriefingStatus,
)
from core.automations.sc06.pdf import build_briefing_pdf
from core.sc05_simulator.models import (
    SimulatorClient,
    SimulatorServiceAccount,
    SimulatorSystem,
    SimulatorTask,
)

DEMO_MODULE_CODES = {"SC-04", "SC-05", "SC-06", "SC-20"}
SC05_DEMO_CLIENT_REFERENCE = "aurora-demo"
SC05_TASK_BASELINE: dict[str, tuple[str, str, bool]] = {
    "AURORA-FISCAL-01": ("Conferir fechamento fiscal", "maria.fiscal", True),
    "AURORA-CONTABIL-02": ("Revisar conciliação", "joao.contabil", True),
    "AURORA-ARQUIVO-03": ("Arquivo histórico concluído", "ana.arquivos", False),
}
ACTIVE_RUN_STATUSES = {RunStatus.PENDING, RunStatus.QUEUED, RunStatus.RUNNING}


class Command(BaseCommand):
    help = (
        "Verifica e, com --apply, restaura somente a projeção sintética do SC-05 "
        "usada no ensaio. Evidências históricas nunca são apagadas ou reescritas."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--apply",
            action="store_true",
            help=(
                "Aplica a restauração allowlisted da Aurora no SC-05. Sem esta opção, "
                "o comando é uma simulação somente leitura."
            ),
        )

    @transaction.atomic
    def handle(self, *args: object, **options: object) -> None:
        apply_changes = bool(options["apply"])
        mode = "APLICAÇÃO" if apply_changes else "SIMULAÇÃO"
        self.stdout.write(f"Preparação da demonstração · {mode}")

        self._require_enabled_modules()
        self._require_simulated_sc20()
        sc05_client, simulator_client, accounts, tasks = self._load_sc05_fixture()
        changes = self._planned_sc05_changes(
            client=sc05_client,
            simulator_client=simulator_client,
            accounts=accounts,
            tasks=tasks,
        )

        if apply_changes:
            self._restore_sc05_projection(
                client=sc05_client,
                simulator_client=simulator_client,
                accounts=accounts,
                tasks=tasks,
            )

        if changes:
            verb = "ajustada(s)" if apply_changes else "seria(m) ajustada(s)"
            self.stdout.write(f"SC-05: {len(changes)} projeção(ões) {verb}.")
            for change in changes:
                self.stdout.write(f"- {change}")
        else:
            self.stdout.write("SC-05: Aurora já está no estado inicial seguro.")

        briefing = self._require_completed_briefing()
        pdf_bytes = build_briefing_pdf(briefing)
        if not pdf_bytes.startswith(b"%PDF-"):
            raise CommandError("O resumo do SC-06 não possui uma assinatura PDF válida.")
        page_count = len(PdfReader(BytesIO(pdf_bytes)).pages)
        if page_count < 1:
            raise CommandError("O resumo do SC-06 não contém páginas.")
        self.stdout.write(f"SC-06: PDF válido ({len(pdf_bytes)} bytes; {page_count} página(s)).")

        evidence = self._evidence_candidates(sc05_client=sc05_client, briefing=briefing)
        self.stdout.write("Evidências funcionais recomendadas:")
        for label, path in evidence.items():
            self.stdout.write(f"- {label}: {path or 'AUSENTE'}")

        baseline_ready = apply_changes or not changes
        evidence_ready = all(path is not None for path in evidence.values())
        readiness = "READY" if baseline_ready and evidence_ready else "NOT READY"
        style = self.style.SUCCESS if readiness == "READY" else self.style.WARNING
        self.stdout.write(style(f"Resultado: {readiness}"))
        if not baseline_ready:
            self.stdout.write("Execute novamente com --apply para restaurar a Aurora sintética.")
        if not evidence_ready:
            self.stdout.write(
                "Produza as evidências ausentes pelos fluxos reais; cabeçalhos demo-* não contam."
            )

    def _require_enabled_modules(self) -> None:
        enabled = set(
            AutomationModule.objects.filter(
                code__in=DEMO_MODULE_CODES, is_enabled=True
            ).values_list("code", flat=True)
        )
        missing = sorted(DEMO_MODULE_CODES - enabled)
        if missing:
            raise CommandError(
                "Módulos demonstrativos ausentes ou desabilitados: " + ", ".join(missing)
            )

    def _require_simulated_sc20(self) -> None:
        backend = str(getattr(settings, "SC20_NOTIFICATION_BACKEND", "simulated")).strip().lower()
        if backend != "simulated":
            raise CommandError(
                "SC-20 não está em modo simulated; a preparação foi interrompida "
                "antes de alterar dados."
            )
        certificates = {
            certificate.serial_number: certificate
            for certificate in DigitalCertificate.objects.filter(
                serial_number__in=("DEMO-CERT-003", "DEMO-CERT-008")
            )
        }
        controlled_failure = certificates.get("DEMO-CERT-003")
        dual_channel = certificates.get("DEMO-CERT-008")
        if (
            controlled_failure is None
            or controlled_failure.preferred_channel != CommunicationChannel.EMAIL
            or "falha" not in controlled_failure.contact_email.lower()
            or dual_channel is None
            or not dual_channel.contact_email
            or not dual_channel.contact_phone
        ):
            raise CommandError(
                "Os certificados controlados DEMO-CERT-003 e DEMO-CERT-008 estão "
                "ausentes ou divergentes; execute seed_demo."
            )
        today = timezone.localdate()
        end_date = today + timedelta(days=60)
        unavailable = sorted(
            certificate.serial_number
            for certificate in (controlled_failure, dual_channel)
            if certificate.status != CertificateStatus.ACTIVE
            or not today <= certificate.valid_until <= end_date
        )
        if unavailable:
            raise CommandError(
                "Certificado(s) controlado(s) fora da janela ativa do SC-20: "
                + ", ".join(unavailable)
                + ". Execute seed_demo --refresh-certificate-dates somente se o "
                "reposicionamento das validades for intencional."
            )
        self.stdout.write(
            "SC-20: entrega simulada e certificados controlados dentro da janela de 60 dias."
        )

    def _load_sc05_fixture(
        self,
    ) -> tuple[
        SC05Client,
        SimulatorClient,
        dict[str, SimulatorServiceAccount],
        dict[str, SimulatorTask],
    ]:
        try:
            client = SC05Client.objects.select_for_update().get(
                external_reference=SC05_DEMO_CLIENT_REFERENCE
            )
            simulator_client = SimulatorClient.objects.select_for_update().get(
                external_id=SC05_DEMO_CLIENT_REFERENCE
            )
        except (SC05Client.DoesNotExist, SimulatorClient.DoesNotExist) as exc:
            raise CommandError("A massa da Aurora não existe; execute seed_demo primeiro.") from exc

        if SC05Operation.objects.filter(
            client=client,
            run__status__in=ACTIVE_RUN_STATUSES,
        ).exists():
            raise CommandError(
                "A Aurora possui uma operação SC-05 ativa; aguarde ou reconcilie "
                "antes da preparação."
            )
        if client.status in {SC05ClientStatus.PARTIAL, SC05ClientStatus.UNKNOWN}:
            raise CommandError(
                "A Aurora está parcial ou a reconciliar; retome a saga em vez de "
                "ocultar a divergência."
            )

        accounts = {
            account.system: account
            for account in SimulatorServiceAccount.objects.select_for_update().filter(
                client=simulator_client
            )
        }
        expected_systems = set(SimulatorSystem.values)
        if set(accounts) != expected_systems:
            raise CommandError(
                "As contas sintéticas da Aurora estão incompletas; execute seed_demo."
            )

        tasks = {
            task.reference: task
            for task in SimulatorTask.objects.select_for_update().filter(
                reference__in=SC05_TASK_BASELINE
            )
        }
        if set(tasks) != set(SC05_TASK_BASELINE) or any(
            task.client_id != simulator_client.pk for task in tasks.values()
        ):
            raise CommandError("As tarefas sintéticas da Aurora estão incompletas ou divergentes.")
        return client, simulator_client, accounts, tasks

    def _planned_sc05_changes(
        self,
        *,
        client: SC05Client,
        simulator_client: SimulatorClient,
        accounts: dict[str, SimulatorServiceAccount],
        tasks: dict[str, SimulatorTask],
    ) -> list[str]:
        changes: list[str] = []
        if client.status != SC05ClientStatus.ACTIVE or client.task_restore_snapshot:
            changes.append("projeção SheepContabil da Aurora")
        if not simulator_client.is_active:
            changes.append("estado do cliente no portal de tarefas")
        changes.extend(
            f"conta {SimulatorSystem(system).label}"
            for system, account in accounts.items()
            if account.is_blocked
        )
        for reference, task in tasks.items():
            title, assignee, is_open = SC05_TASK_BASELINE[reference]
            if (
                task.title != title
                or task.assignee != assignee
                or task.previous_assignee
                or task.is_open != is_open
            ):
                changes.append(f"tarefa {reference}")
        return changes

    def _restore_sc05_projection(
        self,
        *,
        client: SC05Client,
        simulator_client: SimulatorClient,
        accounts: dict[str, SimulatorServiceAccount],
        tasks: dict[str, SimulatorTask],
    ) -> None:
        client.status = SC05ClientStatus.ACTIVE
        client.task_restore_snapshot = {}
        client.save(update_fields=("status", "task_restore_snapshot", "updated_at"))

        simulator_client.is_active = True
        simulator_client.save(update_fields=("is_active",))
        for account in accounts.values():
            account.is_blocked = False
            account.save(update_fields=("is_blocked",))
        for reference, task in tasks.items():
            title, assignee, is_open = SC05_TASK_BASELINE[reference]
            task.title = title
            task.assignee = assignee
            task.previous_assignee = ""
            task.is_open = is_open
            task.save(update_fields=("title", "assignee", "previous_assignee", "is_open"))

    def _require_completed_briefing(self) -> SocietaryBriefing:
        briefing = (
            SocietaryBriefing.objects.select_related("run", "template_version__template")
            .filter(
                client_name="Aurora Participações Demo",
                status=SocietaryBriefingStatus.COMPLETED,
            )
            .order_by("-completed_at")
            .first()
        )
        if briefing is None:
            raise CommandError("O briefing concluído da Aurora não existe; execute seed_demo.")
        return briefing

    def _evidence_candidates(
        self,
        *,
        sc05_client: SC05Client,
        briefing: SocietaryBriefing,
    ) -> dict[str, str | None]:
        sc04 = (
            AutomationRun.objects.filter(
                module_id="SC-04",
                status=RunStatus.SUCCEEDED,
                document_routings__status=DocumentRoutingStatus.ROUTED,
                events__isnull=False,
            )
            .exclude(idempotency_key__startswith="demo-")
            .order_by("-created_at")
            .distinct()
            .first()
        )
        sc05_block = self._latest_sc05_run(client=sc05_client, action=SC05Action.BLOCK)
        sc05_unblock = self._latest_sc05_run(client=sc05_client, action=SC05Action.UNBLOCK)
        sc20_warning = self._latest_sc20_run(
            statuses={RunStatus.SUCCEEDED_WITH_WARNINGS},
            require_sent=True,
            require_deduplicated=False,
        )
        sc20_dedup = self._latest_sc20_run(
            statuses={RunStatus.SUCCEEDED},
            require_sent=False,
            require_deduplicated=True,
        )
        return {
            "SC-04 roteamento real": self._run_path(sc04),
            "SC-05 bloqueio com evidências": self._run_path(sc05_block),
            "SC-05 desbloqueio com evidências": self._run_path(sc05_unblock),
            "SC-06 briefing concluído": f"/briefings-societarios/{briefing.id}/",
            "SC-20 primeira comunicação": self._run_path(sc20_warning),
            "SC-20 reexecução deduplicada": self._run_path(sc20_dedup),
        }

    def _latest_sc05_run(self, *, client: SC05Client, action: str) -> AutomationRun | None:
        operation = (
            SC05Operation.objects.select_related("run")
            .annotate(artifact_count=Count("steps__attempts__artifacts", distinct=True))
            .filter(
                client=client,
                action=action,
                scenario=SC05Scenario.HAPPY_PATH,
                run__status=RunStatus.SUCCEEDED,
                run__events__isnull=False,
                artifact_count__gte=6,
            )
            .exclude(run__idempotency_key__startswith="demo-")
            .order_by("-created_at")
            .distinct()
            .first()
        )
        return operation.run if operation is not None else None

    def _latest_sc20_run(
        self,
        *,
        statuses: set[RunStatus],
        require_sent: bool,
        require_deduplicated: bool,
    ) -> AutomationRun | None:
        runs = (
            AutomationRun.objects.filter(module_id="SC-20", status__in=statuses)
            .exclude(idempotency_key__startswith="demo-")
            .order_by("-created_at")
        )
        for run in runs:
            result = run.metadata.get("result")
            if not isinstance(result, dict):
                continue
            sent = self._metadata_count(result, "sent")
            deduplicated = self._metadata_count(result, "deduplicated")
            if require_sent and sent <= 0:
                continue
            if not require_sent and sent != 0:
                continue
            if require_deduplicated and deduplicated <= 0:
                continue
            return run
        return None

    @staticmethod
    def _run_path(run: AutomationRun | None) -> str | None:
        return f"/execucoes/{run.id}/" if run is not None else None

    @staticmethod
    def _metadata_count(result: dict[str, object], key: str) -> int:
        value = result.get(key, 0)
        return value if isinstance(value, int) else 0
