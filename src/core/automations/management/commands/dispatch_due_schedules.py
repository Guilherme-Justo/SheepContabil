from __future__ import annotations

from datetime import datetime, time
from typing import Any

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.automations.models import AutomationRun, RunStatus
from core.automations.sc20.services import prepare_scheduled_sc20_run
from core.automations.tasks import run_sc20_task


class Command(BaseCommand):
    help = "Publica execuções mensais vencidas do SC-20 com idempotência por competência."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--force",
            action="store_true",
            help="Ignora o horário de competência; útil apenas para operação controlada.",
        )

    def handle(self, *args: object, **options: object) -> None:
        local_now = timezone.localtime()
        due_at = timezone.make_aware(
            datetime.combine(local_now.date().replace(day=1), time(hour=8)),
            timezone.get_current_timezone(),
        )
        if local_now < due_at and not bool(options.get("force")):
            self.stdout.write("SC-20 ainda não venceu nesta competência.")
            return

        competence_date = local_now.date().replace(day=1)
        run, should_dispatch = prepare_scheduled_sc20_run(base_date=competence_date)
        if not should_dispatch:
            self.stdout.write(f"SC-20 já registrado para {local_now:%Y-%m}: {run.id}")
            return
        try:
            run_sc20_task.delay(str(run.id))
        except Exception as exc:
            AutomationRun.objects.filter(pk=run.pk).update(
                status=RunStatus.FAILED,
                summary="Não foi possível publicar a execução mensal.",
                error_message="O serviço de execução está temporariamente indisponível.",
                metadata={"dispatch_error": type(exc).__name__},
                finished_at=timezone.now(),
            )
            raise
        self.stdout.write(self.style.SUCCESS(f"SC-20 publicado para {local_now:%Y-%m}: {run.id}"))
