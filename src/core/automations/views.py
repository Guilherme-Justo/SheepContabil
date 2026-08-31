from datetime import timedelta
from typing import cast

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Count, Max, Q
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from core.automations.forms import BriefingStartForm, DigitalCertificateForm
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
    SocietaryBriefing,
    SocietaryBriefingStatus,
)
from core.automations.sc06.forms import BriefingAnswersForm
from core.automations.sc06.pdf import build_briefing_pdf
from core.automations.sc06.rules import build_frontend_config
from core.automations.sc06.services import (
    PublishedTemplateUnavailable,
    complete_briefing,
    create_briefing,
    get_latest_published_version,
    save_briefing_draft,
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
    if module.code == "SC-06":
        return _sc06_detail(request, module)
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
    briefing = (
        SocietaryBriefing.objects.select_related("template_version", "created_by", "completed_by")
        .filter(run=run)
        .first()
        if run.module_id == "SC-06"
        else None
    )
    return render(
        request,
        "automations/run_detail.html",
        {"run": run, "attempts": attempts, "briefing": briefing},
    )


@login_required
@require_http_methods(["GET", "POST"])
def sc06_briefing_detail(request: HttpRequest, briefing_id: str) -> HttpResponse:
    briefing = _visible_briefing(request, briefing_id)
    schema = briefing.template_version.schema
    form = BriefingAnswersForm(
        schema=schema,
        answers=briefing.answers,
        data=request.POST if request.method == "POST" else None,
    )
    submitted_answers: dict[str, object] = dict(briefing.answers)

    if request.method == "POST":
        if briefing.status != SocietaryBriefingStatus.DRAFT:
            return HttpResponseBadRequest("Este briefing já foi concluído e não aceita alterações.")
        action = request.POST.get("action", "")
        if action not in {"save", "complete"}:
            return HttpResponseBadRequest("Ação inválida.")
        submitted_answers = {
            field_name: request.POST.get(field_name, "") for field_name in form.fields
        }
        if form.is_valid():
            submitted_answers = form.answer_payload
            try:
                if action == "save":
                    briefing = save_briefing_draft(briefing.id, form.answer_payload)
                    messages.success(
                        request, "Rascunho salvo com as regras condicionais aplicadas."
                    )
                else:
                    briefing = complete_briefing(
                        briefing.id,
                        form.answer_payload,
                        completed_by=cast(User, request.user),
                    )
                    messages.success(request, "Briefing concluído e resultado consolidado.")
            except ValidationError as exc:
                _apply_validation_error(form, exc)
            else:
                return redirect(
                    "automations:sc06-briefing-detail",
                    briefing_id=briefing.id,
                )

    return render(
        request,
        "automations/sc06_form.html",
        {
            "module": briefing.run.module,
            "briefing": briefing,
            "form": form,
            "sections": form.sections,
            "frontend_config": build_frontend_config(schema, submitted_answers),
        },
    )


@login_required
def sc06_briefing_pdf(request: HttpRequest, briefing_id: str) -> HttpResponse:
    briefing = _visible_briefing(request, briefing_id)
    if briefing.status != SocietaryBriefingStatus.COMPLETED:
        return HttpResponseBadRequest("Conclua o briefing antes de gerar o PDF.")
    response = HttpResponse(build_briefing_pdf(briefing), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="briefing-sc06-{briefing.id}.pdf"'
    response["X-Content-Type-Options"] = "nosniff"
    return response


def _sc06_detail(request: HttpRequest, module: AutomationModule) -> HttpResponse:
    start_form = BriefingStartForm(request.POST if request.method == "POST" else None)
    if request.method == "POST":
        if request.POST.get("action", "") != "start":
            return HttpResponseBadRequest("Ação inválida.")
        if start_form.is_valid():
            try:
                briefing = create_briefing(
                    client_name=start_form.cleaned_data["client_name"],
                    client_document=start_form.cleaned_data["client_document"],
                    created_by=cast(User, request.user),
                )
            except PublishedTemplateUnavailable:
                start_form.add_error(
                    None,
                    "O template publicado está indisponível. "
                    "Solicite a configuração ao administrador.",
                )
            else:
                messages.success(request, "Briefing criado; responda apenas o caminho aplicável.")
                return redirect(
                    "automations:sc06-briefing-detail",
                    briefing_id=briefing.id,
                )

    briefings = SocietaryBriefing.objects.filter(run__module=module).select_related(
        "template_version", "created_by", "completed_by", "run"
    )
    summary = briefings.aggregate(
        total=Count("id"),
        draft=Count("id", filter=Q(status=SocietaryBriefingStatus.DRAFT)),
        completed=Count("id", filter=Q(status=SocietaryBriefingStatus.COMPLETED)),
    )
    try:
        active_template = get_latest_published_version()
    except PublishedTemplateUnavailable:
        active_template = None
    return render(
        request,
        "automations/sc06_detail.html",
        {
            "module": module,
            "start_form": start_form,
            "briefings": briefings,
            "summary": summary,
            "active_template": active_template,
        },
    )


def _visible_briefing(request: HttpRequest, briefing_id: str) -> SocietaryBriefing:
    try:
        return (
            SocietaryBriefing.objects.select_related(
                "template_version",
                "template_version__template",
                "run",
                "run__module",
                "run__module__area",
                "created_by",
                "completed_by",
            )
            .filter(
                run__module_id="SC-06",
                run__module__in=_visible_modules(request),
            )
            .get(pk=briefing_id)
        )
    except (SocietaryBriefing.DoesNotExist, ValueError) as exc:
        raise Http404("Briefing não encontrado.") from exc


def _apply_validation_error(form: BriefingAnswersForm, exc: ValidationError) -> None:
    if hasattr(exc, "error_dict"):
        for field_name, errors in exc.error_dict.items():
            target = field_name if field_name in form.fields else None
            for error in errors:
                form.add_error(target, error)
        return
    for message in exc.messages:
        form.add_error(None, message)


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
