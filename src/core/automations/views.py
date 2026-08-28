from django.contrib.auth.decorators import login_required
from django.db.models import Count, Max
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render

from core.automations.models import (
    AutomationModule,
    AutomationModuleQuerySet,
    AutomationRun,
)


def _visible_modules(request: HttpRequest) -> AutomationModuleQuerySet:
    return AutomationModule.objects.visible_to(request.user).select_related("area")


@login_required
def dashboard(request: HttpRequest) -> HttpResponse:
    modules = _visible_modules(request).annotate(
        run_count=Count("runs"),
        last_run_at=Max("runs__created_at"),
    )
    recent_runs = (
        AutomationRun.objects.filter(module__in=modules)
        .select_related("module", "triggered_by")
        .order_by("-created_at")[:6]
    )
    return render(
        request,
        "automations/dashboard.html",
        {"modules": modules, "recent_runs": recent_runs},
    )


@login_required
def module_detail(request: HttpRequest, slug: str) -> HttpResponse:
    try:
        module = _visible_modules(request).get(slug=slug)
    except AutomationModule.DoesNotExist as exc:
        raise Http404("Módulo não encontrado.") from exc
    runs = module.runs.select_related("triggered_by").all()[:20]
    return render(
        request,
        "automations/module_detail.html",
        {"module": module, "runs": runs},
    )


@login_required
def run_detail(request: HttpRequest, run_id: str) -> HttpResponse:
    try:
        run = (
            AutomationRun.objects.select_related("module", "triggered_by", "module__area")
            .filter(module__in=_visible_modules(request))
            .get(pk=run_id)
        )
    except (AutomationRun.DoesNotExist, ValueError) as exc:
        raise Http404("Execução não encontrada.") from exc
    return render(request, "automations/run_detail.html", {"run": run})
