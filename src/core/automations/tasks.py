from __future__ import annotations

from celery import shared_task

from core.automations.sc20.services import execute_sc20


@shared_task(name="automations.sc20.execute")  # type: ignore[untyped-decorator]
def run_sc20_task(run_id: str) -> dict[str, int]:
    result = execute_sc20(run_id)
    return {
        "selected": result.selected,
        "sent": result.sent,
        "failed": result.failed,
        "deduplicated": result.deduplicated,
    }
