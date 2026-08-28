from django.db import connections
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET


@require_GET
def live(_request: HttpRequest) -> JsonResponse:
    return JsonResponse({"status": "ok"})


@require_GET
def ready(_request: HttpRequest) -> JsonResponse:
    try:
        with connections["default"].cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        return JsonResponse({"status": "not-ready", "database": "unavailable"}, status=503)
    return JsonResponse({"status": "ready", "database": "ok"})
