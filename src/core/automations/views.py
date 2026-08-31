import hashlib
from datetime import timedelta
from io import BytesIO
from typing import Any, cast

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Count, Max, Q
from django.http import (
    FileResponse,
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponseBadRequest,
)
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from core.automations.forms import (
    BriefingStartForm,
    DigitalCertificateForm,
    SC04QueueFilterForm,
    SC04ReviewForm,
    SC04UploadForm,
)
from core.automations.models import (
    AutomationModule,
    AutomationModuleQuerySet,
    AutomationRun,
    CertificateCommunication,
    CommunicationAttempt,
    CommunicationStatus,
    DigitalCertificate,
    DocumentDecision,
    DocumentReview,
    DocumentReviewStatus,
    DocumentRouting,
    DocumentRoutingStatus,
    DocumentRunItem,
    DocumentRunOutcome,
    DocumentStatus,
    FiscalClient,
    FiscalDocument,
    RunStatus,
    RunTrigger,
    SocietaryBriefing,
    SocietaryBriefingStatus,
)
from core.automations.sc04.contracts import SC04Error, StorageOperationError
from core.automations.sc04.services import (
    create_manual_sc04_inbox_run,
    create_manual_sc04_run,
    resolve_document_review,
    retry_document_route,
)
from core.automations.sc04.storage import build_object_storage
from core.automations.sc04.validation import extension_for_media_type
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
from core.automations.tasks import run_sc04_task, run_sc20_task
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
    if module.code == "SC-04":
        return _sc04_detail(request, module)
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
    document_items = (
        run.document_run_items.select_related(
            "intake",
            "intake__document",
            "intake__document__matched_client",
        )
        if run.module_id == "SC-04"
        else DocumentRunItem.objects.none()
    )
    return render(
        request,
        "automations/run_detail.html",
        {
            "run": run,
            "attempts": attempts,
            "briefing": briefing,
            "document_items": document_items,
        },
    )


def _sc04_detail(request: HttpRequest, module: AutomationModule) -> HttpResponse:
    if request.method != "GET":
        return HttpResponseBadRequest("Use uma ação explícita para iniciar a triagem.")
    return render(
        request,
        "automations/sc04_detail.html",
        _sc04_dashboard_context(request, module),
    )


@login_required
@require_POST
def sc04_upload(request: HttpRequest) -> HttpResponse:
    module = _visible_sc04_module(request)
    form = SC04UploadForm(request.POST, request.FILES)
    if not form.is_valid() or form.validated_document is None:
        return render(
            request,
            "automations/sc04_detail.html",
            _sc04_dashboard_context(request, module, upload_form=form),
            status=400,
        )
    validated = form.validated_document
    try:
        run, ingestion, should_dispatch = create_manual_sc04_run(
            triggered_by=cast(User, request.user),
            filename=validated.filename,
            declared_content_type=validated.media_type,
            content=validated.content,
        )
    except (SC04Error, ValidationError) as exc:
        form.add_error(None, str(exc))
        return render(
            request,
            "automations/sc04_detail.html",
            _sc04_dashboard_context(request, module, upload_form=form),
            status=503 if isinstance(exc, SC04Error) else 400,
        )
    if should_dispatch:
        _dispatch_sc04(request, run)
    else:
        messages.info(request, "O conteúdo já existia e foi registrado como duplicado.")
    return redirect("automations:sc04-document-detail", document_id=ingestion.document_id)


@login_required
@require_POST
def sc04_process_inbox(request: HttpRequest) -> HttpResponse:
    _visible_sc04_module(request)
    run = create_manual_sc04_inbox_run(triggered_by=cast(User, request.user))
    _dispatch_sc04(request, run)
    return redirect("automations:run-detail", run_id=run.id)


@login_required
@require_GET
def sc04_queue_fragment(request: HttpRequest) -> HttpResponse:
    module = _visible_sc04_module(request)
    context = _sc04_dashboard_context(request, module)
    response = render(request, "automations/partials/sc04_queue.html", context)
    response["Cache-Control"] = "private, no-store"
    return response


@login_required
@require_GET
def sc04_document_detail(request: HttpRequest, document_id: str) -> HttpResponse:
    module = _visible_sc04_module(request)
    document = _visible_sc04_document(request, document_id)
    return render(
        request,
        "automations/sc04_document_detail.html",
        _sc04_document_context(module, document),
    )


@login_required
@require_GET
def sc04_document_state(request: HttpRequest, document_id: str) -> HttpResponse:
    module = _visible_sc04_module(request)
    document = _visible_sc04_document(request, document_id)
    response = render(
        request,
        "automations/partials/sc04_document_state.html",
        _sc04_document_context(module, document),
    )
    response["Cache-Control"] = "private, no-store"
    return response


@login_required
@require_POST
def sc04_resolve_review(request: HttpRequest, review_id: str) -> HttpResponse:
    module = _visible_sc04_module(request)
    review = _visible_sc04_review(request, review_id)
    form = SC04ReviewForm(request.POST, attempt=review.suggested_attempt)
    document = review.document
    if not form.is_valid():
        return render(
            request,
            "automations/sc04_document_detail.html",
            _sc04_document_context(module, document, review_form=form),
            status=400,
        )
    try:
        resolve_document_review(
            review.id,
            document_type=str(form.cleaned_data["document_type"]),
            client=cast(FiscalClient, form.cleaned_data["client"]),
            reviewed_by=cast(User, request.user),
            notes=str(form.cleaned_data.get("notes") or ""),
        )
    except StorageOperationError:
        messages.warning(
            request,
            "A decisão foi preservada, mas o encaminhamento falhou. Tente novamente no documento.",
        )
    except ValidationError as exc:
        form.add_error(
            None, exc.messages[0] if exc.messages else "A revisão não pôde ser concluída."
        )
        document.refresh_from_db()
        return render(
            request,
            "automations/sc04_document_detail.html",
            _sc04_document_context(module, document, review_form=form),
            status=409,
        )
    else:
        messages.success(request, "Revisão concluída e decisão auditável registrada.")
    return redirect("automations:sc04-document-detail", document_id=document.id)


@login_required
@require_POST
def sc04_retry_route(request: HttpRequest, document_id: str) -> HttpResponse:
    _visible_sc04_module(request)
    document = _visible_sc04_document(request, document_id)
    try:
        retry_document_route(document.id)
    except SC04Error as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Encaminhamento concluído sem alterar a decisão registrada.")
    return redirect("automations:sc04-document-detail", document_id=document.id)


@login_required
@require_GET
@xframe_options_sameorigin
def sc04_document_preview(request: HttpRequest, document_id: str) -> HttpResponse:
    document = _visible_sc04_document(request, document_id)
    response = _sc04_file_response(document, attachment=False)
    response["Content-Security-Policy"] = "sandbox; default-src 'none'; frame-ancestors 'self'"
    return response


@login_required
@require_GET
def sc04_document_download(request: HttpRequest, document_id: str) -> HttpResponse:
    document = _visible_sc04_document(request, document_id)
    return _sc04_file_response(document, attachment=True)


def _visible_sc04_module(request: HttpRequest) -> AutomationModule:
    try:
        return _visible_modules(request).get(code="SC-04")
    except AutomationModule.DoesNotExist as exc:
        raise Http404("Módulo não encontrado.") from exc


def _visible_sc04_document(request: HttpRequest, document_id: str) -> FiscalDocument:
    module = _visible_sc04_module(request)
    try:
        return (
            FiscalDocument.objects.select_related("matched_client")
            .filter(intakes__run__module=module)
            .distinct()
            .get(pk=document_id)
        )
    except (FiscalDocument.DoesNotExist, ValueError) as exc:
        raise Http404("Documento não encontrado.") from exc


def _visible_sc04_review(request: HttpRequest, review_id: str) -> DocumentReview:
    module = _visible_sc04_module(request)
    try:
        return (
            DocumentReview.objects.select_related(
                "document",
                "document__matched_client",
                "suggested_attempt",
                "suggested_attempt__predicted_client",
                "suggested_attempt__document__matched_client",
                "run",
            )
            .filter(document__intakes__run__module=module)
            .distinct()
            .get(pk=review_id)
        )
    except (DocumentReview.DoesNotExist, ValueError) as exc:
        raise Http404("Revisão não encontrada.") from exc


def _sc04_dashboard_context(
    request: HttpRequest,
    module: AutomationModule,
    *,
    upload_form: SC04UploadForm | None = None,
) -> dict[str, Any]:
    filter_form = SC04QueueFilterForm(request.GET or None)
    queue = _sc04_queue_queryset(module, filter_form)
    queue_rows = []
    for item in queue[:30]:
        document = item.intake.document
        queue_rows.append(
            {
                "item": item,
                "document": document,
                "type_confidence_percent": int(document.type_confidence * 100),
                "client_confidence_percent": int(document.client_confidence * 100),
            }
        )
    today = timezone.localdate()
    documents = FiscalDocument.objects.filter(intakes__run__module=module).distinct()
    summary = {
        "received_today": DocumentRunItem.objects.filter(
            run__module=module,
            created_at__date=today,
        ).count(),
        "processing": documents.filter(
            status__in=(DocumentStatus.QUEUED, DocumentStatus.PROCESSING)
        ).count(),
        "awaiting_review": documents.filter(status=DocumentStatus.AWAITING_REVIEW).count(),
        "routed_today": DocumentRouting.objects.filter(
            run__module=module,
            routed_at__date=today,
        ).count(),
        "failed": documents.filter(status=DocumentStatus.FAILED).count(),
        "duplicates_today": DocumentRunItem.objects.filter(
            run__module=module,
            created_at__date=today,
        )
        .exclude(outcome=DocumentRunOutcome.NEW)
        .count(),
    }
    query = request.GET.urlencode()
    queue_refresh_url = reverse("automations:sc04-queue-fragment")
    if query:
        queue_refresh_url = f"{queue_refresh_url}?{query}"
    return {
        "module": module,
        "upload_form": upload_form or SC04UploadForm(),
        "queue_filter_form": filter_form,
        "summary": summary,
        "queue_rows": queue_rows,
        "queue_refresh_url": queue_refresh_url,
        "has_active_queue": documents.filter(
            status__in=(DocumentStatus.QUEUED, DocumentStatus.PROCESSING)
        ).exists(),
        "runs": module.runs.select_related("triggered_by")[:20],
    }


def _sc04_queue_queryset(
    module: AutomationModule,
    filter_form: SC04QueueFilterForm,
) -> Any:
    queue = DocumentRunItem.objects.filter(run__module=module).select_related(
        "run",
        "intake",
        "intake__document",
        "intake__document__matched_client",
    )
    if not filter_form.is_valid():
        return queue.order_by("-created_at")
    status = str(filter_form.cleaned_data.get("status") or "")
    source = str(filter_form.cleaned_data.get("source") or "")
    outcome = str(filter_form.cleaned_data.get("outcome") or "")
    query = str(filter_form.cleaned_data.get("q") or "").strip()
    if status:
        queue = queue.filter(intake__document__status=status)
    if source:
        queue = queue.filter(intake__source=source)
    if outcome:
        queue = queue.filter(outcome=outcome)
    if query:
        queue = queue.filter(
            Q(intake__original_filename__icontains=query)
            | Q(intake__document__matched_client__name__icontains=query)
        )
    return queue.order_by("-created_at")


def _sc04_document_context(
    module: AutomationModule,
    document: FiscalDocument,
    *,
    review_form: SC04ReviewForm | None = None,
) -> dict[str, Any]:
    intakes = list(document.intakes.select_related("run").order_by("received_at")[:20])
    attempts = list(
        document.classification_attempts.select_related("predicted_client", "run").all()
    )
    latest_attempt = attempts[0] if attempts else None
    review = (
        DocumentReview.objects.select_related(
            "suggested_attempt",
            "suggested_attempt__document",
            "suggested_attempt__document__matched_client",
            "suggested_attempt__predicted_client",
            "reviewed_by",
            "resolved_client",
        )
        .filter(document=document)
        .first()
    )
    decision = (
        DocumentDecision.objects.select_related("client", "decided_by", "review")
        .filter(document=document)
        .first()
    )
    routing = (
        DocumentRouting.objects.filter(decision=decision).first() if decision is not None else None
    )
    if review is not None and review.status == DocumentReviewStatus.PENDING:
        review_form = review_form or SC04ReviewForm(attempt=review.suggested_attempt)
    else:
        review_form = None
    is_polling = document.status in {DocumentStatus.QUEUED, DocumentStatus.PROCESSING} or (
        routing is not None and routing.status == DocumentRoutingStatus.PENDING
    )
    stage = _sc04_stage(document, routing)
    preview_kind = {
        "application/pdf": "pdf",
        "image/png": "image",
        "image/jpeg": "image",
        "text/plain": "text",
    }.get(document.media_type, "text")
    return {
        "module": module,
        "document": document,
        "filename": intakes[0].original_filename if intakes else _sc04_original_filename(document),
        "intakes": intakes,
        "attempts": attempts,
        "latest_attempt": latest_attempt,
        "review": review,
        "review_form": review_form,
        "decision": decision,
        "routing": routing,
        "is_polling": is_polling,
        "stage": stage,
        "type_confidence_percent": int(document.type_confidence * 100),
        "client_confidence_percent": int(document.client_confidence * 100),
        "preview_kind": preview_kind,
        "route_destination": (
            f"{decision.client.name} / {decision.get_document_type_display()}"
            if decision is not None
            else ""
        ),
        "can_retry_route": (routing is not None and routing.status == DocumentRoutingStatus.FAILED),
    }


def _sc04_stage(
    document: FiscalDocument,
    routing: DocumentRouting | None,
) -> str:
    if document.status == DocumentStatus.FAILED:
        return "Falha"
    if document.status == DocumentStatus.ROUTED:
        return "Concluído"
    if document.status == DocumentStatus.AWAITING_REVIEW:
        return "Revisão"
    if routing is not None:
        return "Encaminhamento"
    if document.status == DocumentStatus.PROCESSING:
        return "Classificação" if document.extraction_method else "Extração / OCR"
    return "Na fila"


def _sc04_original_filename(document: FiscalDocument) -> str:
    extension = extension_for_media_type(document.media_type)
    return f"documento-{document.id}{extension}"


def _sc04_file_response(document: FiscalDocument, *, attachment: bool) -> HttpResponse:
    try:
        content = build_object_storage().get_bytes(document.storage_key)
        if hashlib.sha256(content).hexdigest() != document.sha256:
            raise StorageOperationError("O original falhou na verificação de integridade.")
    except SC04Error as exc:
        return HttpResponse(str(exc), status=503, content_type="text/plain; charset=utf-8")
    intake = document.intakes.order_by("received_at").first()
    filename = intake.original_filename if intake is not None else _sc04_original_filename(document)
    response = FileResponse(
        BytesIO(content),
        as_attachment=attachment,
        filename=filename,
        content_type=document.media_type,
    )
    response["Cache-Control"] = "private, no-store"
    response["Cross-Origin-Resource-Policy"] = "same-origin"
    response["X-Content-Type-Options"] = "nosniff"
    return cast(HttpResponse, response)


def _dispatch_sc04(request: HttpRequest, run: AutomationRun) -> None:
    try:
        run_sc04_task.delay(str(run.id))
    except Exception as exc:
        AutomationRun.objects.filter(pk=run.pk).update(
            status=RunStatus.FAILED,
            summary="Não foi possível adicionar a triagem ao processamento.",
            error_message="O serviço de execução está temporariamente indisponível.",
            metadata={"dispatch_error": type(exc).__name__},
            finished_at=timezone.now(),
        )
        messages.error(request, "A triagem não pôde ser iniciada. Tente novamente mais tarde.")
        return
    messages.success(request, "Triagem adicionada à fila com rastreabilidade.")


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
