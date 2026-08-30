from datetime import timedelta
from typing import cast

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Max
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from core.automations.forms import DigitalCertificateForm
from core.automations.models import (
    AutomationModule,
    AutomationModuleQuerySet,
    AutomationRun,
    CertificateCommunication,
    CommunicationAttempt,
    CommunicationStatus,
    DigitalCertificate,
    RunStatus,
    RunTrigger,
)
from core.automations.sc20.services import create_sc20_run
from core.automations.tasks import run_sc20_task
from core.identity.models import User


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
@require_http_methods(["GET", "POST"])
def module_detail(request: HttpRequest, slug: str) -> HttpResponse:
    try:
        module = _visible_modules(request).get(slug=slug)
    except AutomationModule.DoesNotExist as exc:
        raise Http404("Módulo não encontrado.") from exc
    if module.code == "SC-20":
        return _sc20_detail(request, module)
    if request.method == "POST":
        return HttpResponseBadRequest("Este módulo ainda não aceita execução manual.")
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
    attempts = (
        run.communication_attempts.select_related("communication__certificate")
        if run.module_id == "SC-20"
        else CommunicationAttempt.objects.none()
    )
    return render(
        request,
        "automations/run_detail.html",
        {"run": run, "attempts": attempts},
    )


def _sc20_detail(request: HttpRequest, module: AutomationModule) -> HttpResponse:
    form = DigitalCertificateForm()
    if request.method == "POST":
        action = request.POST.get("action", "")
        if action == "create_certificate":
            form = DigitalCertificateForm(request.POST)
            if form.is_valid():
                certificate = form.save()
                messages.success(
                    request,
                    f"Certificado sintético de {certificate.client_name} cadastrado.",
                )
                return redirect("automations:module-detail", slug=module.slug)
        elif action == "execute":
            run = create_sc20_run(
                triggered_by=cast(User, request.user),
                trigger=RunTrigger.MANUAL,
            )
            _dispatch_sc20(request, run)
            return redirect("automations:run-detail", run_id=run.id)
        elif action == "retry":
            attempt_id = request.POST.get("attempt_id", "")
            try:
                attempt = (
                    CommunicationAttempt.objects.select_related("communication__certificate")
                    .filter(
                        status=CommunicationStatus.FAILED,
                        communication__status=CommunicationStatus.FAILED,
                    )
                    .get(pk=attempt_id)
                )
            except (CommunicationAttempt.DoesNotExist, ValueError) as exc:
                raise Http404("Tentativa não encontrada.") from exc
            run = create_sc20_run(
                triggered_by=cast(User, request.user),
                trigger=RunTrigger.MANUAL,
                retry_communication=attempt.communication,
            )
            _dispatch_sc20(request, run)
            return redirect("automations:run-detail", run_id=run.id)
        else:
            return HttpResponseBadRequest("Ação inválida.")

    today = timezone.localdate()
    window_end = today + timedelta(days=60)
    certificates = DigitalCertificate.objects.all()
    attempts = CommunicationAttempt.objects.select_related(
        "communication__certificate",
        "run",
    )[:30]
    summary = {
        "total": certificates.count(),
        "expiring": DigitalCertificate.objects.expiring_between(
            start_date=today,
            end_date=window_end,
        ).count(),
        "expired": DigitalCertificate.objects.active().filter(valid_until__lt=today).count(),
        "failed": CertificateCommunication.objects.filter(
            status=CommunicationStatus.FAILED
        ).count(),
    }
    runs = module.runs.select_related("triggered_by").all()[:20]
    return render(
        request,
        "automations/sc20_detail.html",
        {
            "module": module,
            "certificates": certificates,
            "attempts": attempts,
            "summary": summary,
            "form": form,
            "runs": runs,
        },
    )


def _dispatch_sc20(request: HttpRequest, run: AutomationRun) -> None:
    try:
        run_sc20_task.delay(str(run.id))
    except Exception as exc:
        AutomationRun.objects.filter(pk=run.pk).update(
            status=RunStatus.FAILED,
            summary="Não foi possível adicionar a execução ao processamento.",
            error_message="O serviço de execução está temporariamente indisponível.",
            metadata={"dispatch_error": type(exc).__name__},
            finished_at=timezone.now(),
        )
        messages.error(request, "A execução não pôde ser iniciada. Tente novamente mais tarde.")
        return
    messages.success(request, "Verificação adicionada à fila com rastreabilidade.")
