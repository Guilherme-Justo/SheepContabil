from __future__ import annotations

import importlib

import pytest
from django.db import connection, migrations
from django.db.migrations.executor import MigrationExecutor

DELIVERY_TRACKING_FROM = ("automations", "0008_alter_automationmodule_options")
DELIVERY_TRACKING_TO = ("automations", "0009_automationrun_delivery_tracking")


def test_delivery_tracking_indexes_precede_the_data_backfill() -> None:
    migration_module = importlib.import_module(
        "core.automations.migrations.0009_automationrun_delivery_tracking"
    )
    operations = migration_module.Migration.operations

    backfill_position = next(
        index
        for index, operation in enumerate(operations)
        if isinstance(operation, migrations.RunPython)
        and operation.code is migration_module.backfill_active_run_timestamps
    )
    index_positions = [
        index
        for index, operation in enumerate(operations)
        if isinstance(operation, migrations.AddIndex)
    ]

    assert index_positions
    assert max(index_positions) < backfill_position


@pytest.mark.django_db(transaction=True)
def test_delivery_tracking_migration_handles_existing_active_run_on_postgresql() -> None:
    if connection.vendor != "postgresql":
        pytest.skip("PostgreSQL migration contract")

    executor = MigrationExecutor(connection)
    executor.migrate([DELIVERY_TRACKING_FROM])
    old_apps = executor.loader.project_state([DELIVERY_TRACKING_FROM]).apps

    area = old_apps.get_model("identity", "Area").objects.create(
        code="migration-contract",
        name="Migration contract",
    )
    module = old_apps.get_model("automations", "AutomationModule").objects.create(
        code="MG-09",
        slug="migration-contract",
        name="Migration contract",
        short_description="Validates migration 0009 with existing data.",
        nature="control",
        complexity="low",
        frequency="on_demand",
        area=area,
    )
    old_run = old_apps.get_model("automations", "AutomationRun").objects.create(
        module=module,
        trigger="manual",
        status="queued",
    )

    executor = MigrationExecutor(connection)
    executor.migrate([DELIVERY_TRACKING_TO])
    new_apps = executor.loader.project_state([DELIVERY_TRACKING_TO]).apps
    migrated_run = new_apps.get_model("automations", "AutomationRun").objects.get(pk=old_run.pk)

    assert migrated_run.queued_at == migrated_run.created_at
    assert migrated_run.dispatch_started_at == migrated_run.created_at
    assert migrated_run.broker_published_at == migrated_run.created_at
