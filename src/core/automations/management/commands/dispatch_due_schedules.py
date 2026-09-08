from __future__ import annotations

from datetime import datetime, time
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.automations.dispatching import dispatch_run
from core.automations.models import (
    AutomationFrequency,
    AutomationModule,
)
from core.automations.reconciliation import reconcile_stale_runs
from core.automations.sc04.services import prepare_scheduled_sc04_run
from core.automations.sc20.services import prepare_scheduled_sc20_run


class Command(BaseCommand):
    help = "Publica as execuções diárias do SC-04 e mensais do SC-20 já vencidas."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--force",
            action="store_true",
            help="Ignora o horário de competência; útil apenas para operação controlada.",
        )

    def handle(self, *args: object, **options: object) -> None:
        now = timezone.now()
        local_now = timezone.localtime(now)
        forced = bool(options.get("force"))
        errors: list[Exception] = []
        reconciliation = reconcile_stale_runs(at=now)
        if reconciliation.inspected:
            self.stdout.write(
                "Reconciliação: "
                f"{reconciliation.requeued} republicada(s), "
                f"{reconciliation.quarantined} em quarentena, "
                f"{reconciliation.failed} encerrada(s), "
                f"{reconciliation.inspection_failed} com erro de inspeção, "
                f"{reconciliation.skipped} ignorada(s)."
            )
        if reconciliation.publish_failed:
            errors.append(
                RuntimeError(
                    f"{reconciliation.publish_failed} recuperação(ões) "
                    "falharam ao publicar no broker."
                )
            )
        if reconciliation.inspection_failed:
            errors.append(
                RuntimeError(
                    f"{reconciliation.inspection_failed} execução(ões) "
                    "não puderam ser reconciliadas."
                )
            )
        if self._sc04_is_enabled() and (forced or local_now.hour >= int(settings.SC04_DAILY_HOUR)):
            run, should_dispatch = prepare_scheduled_sc04_run(base_date=local_now.date())
            if should_dispatch:
                try:
                    dispatch_run(
                        run,
                        failure_summary="Não foi possível publicar a triagem diária.",
                    )
                except Exception as exc:
                    errors.append(exc)
                else:
                    self.stdout.write(
                        self.style.SUCCESS(f"SC-04 publicado para {local_now:%Y-%m-%d}: {run.id}")
                    )
            else:
                self.stdout.write(f"SC-04 já registrado para {local_now:%Y-%m-%d}: {run.id}")
        elif self._sc04_is_enabled():
            self.stdout.write("SC-04 ainda não venceu hoje.")

        if self._sc20_is_enabled():
            due_at = timezone.make_aware(
                datetime.combine(
                    local_now.date().replace(day=1),
                    time(hour=int(settings.SC20_MONTHLY_HOUR)),
                ),
                timezone.get_current_timezone(),
            )
            if local_now < due_at and not forced:
                self.stdout.write("SC-20 ainda não venceu nesta competência.")
            else:
                competence_date = local_now.date().replace(day=1)
                run, should_dispatch = prepare_scheduled_sc20_run(base_date=competence_date)
                if not should_dispatch:
                    self.stdout.write(f"SC-20 já registrado para {local_now:%Y-%m}: {run.id}")
                else:
                    try:
                        dispatch_run(
                            run,
                            failure_summary="Não foi possível publicar a execução mensal.",
                        )
                    except Exception as exc:
                        errors.append(exc)
                    else:
                        self.stdout.write(
                            self.style.SUCCESS(f"SC-20 publicado para {local_now:%Y-%m}: {run.id}")
                        )
        if errors:
            raise errors[0]

    @staticmethod
    def _sc04_is_enabled() -> bool:
        return AutomationModule.objects.filter(
            code="SC-04",
            is_enabled=True,
            frequency=AutomationFrequency.DAILY,
        ).exists()

    @staticmethod
    def _sc20_is_enabled() -> bool:
        return AutomationModule.objects.filter(
            code="SC-20",
            is_enabled=True,
            frequency=AutomationFrequency.MONTHLY,
        ).exists()
