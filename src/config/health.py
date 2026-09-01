from pathlib import Path

from django.conf import settings
from django.db import connections
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET


@require_GET
def live(_request: HttpRequest) -> JsonResponse:
    return JsonResponse({"status": "ok"})


@require_GET
def ready(_request: HttpRequest) -> JsonResponse:
    worker_ready_file = str(getattr(settings, "SC05_SIMULATOR_READY_FILE", "")).strip()
    if worker_ready_file and not Path(worker_ready_file).is_file():
        return JsonResponse({"status": "not-ready", "worker": "starting"}, status=503)
    try:
        with connections["default"].cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        return JsonResponse({"status": "not-ready", "database": "unavailable"}, status=503)
    return JsonResponse({"status": "ready", "database": "ok"})
