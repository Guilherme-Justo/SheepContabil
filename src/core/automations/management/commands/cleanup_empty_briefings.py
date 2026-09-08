from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from core.automations.models import SocietaryBriefing, SocietaryBriefingStatus
from core.automations.sc06.services import discard_empty_briefing, is_briefing_empty


class Command(BaseCommand):
    help = (
        "Cancela briefings societários em rascunho que não possuem respostas "
        "preenchidas, preservando a trilha operacional."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simula a limpeza sem alterar nenhum registro do banco de dados.",
        )

    def handle(self, *args: object, **options: object) -> None:
        dry_run = bool(options.get("dry_run"))
        drafts = SocietaryBriefing.objects.filter(
            status=SocietaryBriefingStatus.DRAFT
        ).select_related("run")
        empty_count = 0

        for briefing in drafts:
            if is_briefing_empty(briefing):
                empty_count += 1
                label = f"{briefing.id} ({briefing.client_name})"
                if not dry_run:
                    discard_empty_briefing(briefing.id)
                    self.stdout.write(self.style.SUCCESS(f"Briefing {label} cancelado."))
                else:
                    self.stdout.write(f"[DRY-RUN] Briefing {label} seria cancelado.")

        if empty_count == 0:
            self.stdout.write("Nenhum briefing vazio em rascunho encontrado.")
        elif dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"[DRY-RUN] Total de {empty_count} rascunho(s) vazio(s) elegível(is)."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Total de {empty_count} rascunho(s) vazio(s) "
                    "cancelado(s) com trilha preservada."
                )
            )
