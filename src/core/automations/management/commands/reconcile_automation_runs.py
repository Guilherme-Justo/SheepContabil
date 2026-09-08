from __future__ import annotations

from typing import Any, cast

from django.core.management.base import BaseCommand, CommandError

from core.automations.reconciliation import reconcile_stale_runs


class Command(BaseCommand):
    help = "Reconcilia execuções assíncronas órfãs usando o estado persistido no PostgreSQL."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Mostra as ações elegíveis sem alterar estado nem publicar tarefas.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=100,
            help="Limite de execuções avaliadas neste pulso (padrão: 100).",
        )

    def handle(self, *args: object, **options: object) -> None:
        dry_run = bool(options["dry_run"])
        try:
            result = reconcile_stale_runs(
                batch_size=cast(int, options["batch_size"]),
                dry_run=dry_run,
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        prefix = "Simulação" if dry_run else "Reconciliação"
        self.stdout.write(
            f"{prefix}: {result.inspected} inspecionada(s), "
            f"{result.requeued} republicada(s), "
            f"{result.quarantined} em quarentena, "
            f"{result.failed} encerrada(s), "
            f"{result.inspection_failed} com erro de inspeção, "
            f"{result.skipped} ignorada(s)."
        )
        if result.publish_failed:
            raise CommandError(
                f"{result.publish_failed} recuperação(ões) falharam ao publicar no broker."
            )
        if result.inspection_failed:
            raise CommandError(
                f"{result.inspection_failed} execução(ões) não puderam ser reconciliadas."
            )
