from __future__ import annotations

import django.db.models.functions
from django.db import migrations, models
from django.db.models import F


def backfill_active_run_timestamps(apps, schema_editor) -> None:
    automation_run = apps.get_model("automations", "AutomationRun")
    runs = automation_run.objects.using(schema_editor.connection.alias)
    runs.filter(
        status__in=("pending", "queued"),
        queued_at__isnull=True,
    ).update(queued_at=F("created_at"))
    runs.filter(status="running", heartbeat_at__isnull=True).update(
        heartbeat_at=django.db.models.functions.Coalesce("started_at", "created_at")
    )
    runs.filter(status__in=("queued", "running"), broker_published_at__isnull=True).update(
        dispatch_started_at=django.db.models.functions.Coalesce(
            "queued_at",
            "started_at",
            "created_at",
        ),
        broker_published_at=django.db.models.functions.Coalesce(
            "queued_at",
            "started_at",
            "created_at",
        ),
    )


class Migration(migrations.Migration):
    dependencies = [("automations", "0008_alter_automationmodule_options")]

    operations = [
        migrations.AddField(
            model_name="automationrun",
            name="broker_published_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                verbose_name="publicação confirmada em",
            ),
        ),
        migrations.AddField(
            model_name="automationrun",
            name="dispatch_started_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                verbose_name="publicação iniciada em",
            ),
        ),
        migrations.AddField(
            model_name="automationrun",
            name="heartbeat_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                verbose_name="último sinal do worker",
            ),
        ),
        migrations.AddField(
            model_name="automationrun",
            name="queued_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="enfileirada em"),
        ),
        migrations.AddField(
            model_name="automationrun",
            name="reconciliation_attempts",
            field=models.PositiveSmallIntegerField(
                default=0,
                verbose_name="tentativas de reconciliação",
            ),
        ),
        migrations.AddField(
            model_name="automationrun",
            name="task_id",
            field=models.UUIDField(
                blank=True,
                editable=False,
                null=True,
                unique=True,
                verbose_name="identificador da entrega",
            ),
        ),
        migrations.RunPython(backfill_active_run_timestamps, migrations.RunPython.noop),
        migrations.AddIndex(
            model_name="automationrun",
            index=models.Index(
                fields=["status", "queued_at"],
                name="run_status_queued_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="automationrun",
            index=models.Index(
                fields=["status", "heartbeat_at"],
                name="run_status_heartbeat_idx",
            ),
        ),
    ]
