import hashlib
import re
from collections import defaultdict
from datetime import timedelta
from io import BytesIO
from typing import Any, cast
from urllib.parse import urlencode
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Max, Q
from django.http import (
    FileResponse,
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponseBadRequest,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from core.automations.forms import (
    BriefingStartForm,
    DashboardRunFilterForm,
    DigitalCertificateForm,
    SC04QueueFilterForm,
    SC04ReviewForm,
    SC04UploadForm,
    SC05ClientFilterForm,
    SC05OperationFilterForm,
    SC05OperationForm,
    SC20CertificateFilterForm,
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
    DocumentIntake,
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
    SC05Artifact,
    SC05Client,
    SC05Operation,
    SC05PortalStep,
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
from core.automations.sc05.artifacts import build_screenshot_storage
from core.automations.sc05.contracts import ArtifactStorageError
from core.automations.sc05.services import create_sc05_run_result, resume_sc05_run
from core.automations.sc06.forms import BriefingAnswersForm, SC06CasesFilterForm
from core.automations.sc06.pdf import build_briefing_pdf
from core.automations.sc06.rules import build_frontend_config
from core.automations.sc06.services import (
    DEFAULT_TEMPLATE_CODE,
    PublishedTemplateUnavailable,
    cancel_briefing,
    complete_briefing,
    create_briefing,
    discard_empty_briefing,
    get_latest_published_version,
    is_briefing_empty,
    save_briefing_draft,
)
from core.automations.sc20.services import create_sc20_run
from core.automations.tasks import run_sc04_task, run_sc05_task, run_sc20_task
from core.identity.models import User

DEFAULT_PAGE_SIZE = 7


def _extract_sort_and_query_params(
    request: HttpRequest,
    sort_key: str = "sort",
    page_key: str = "page",
) -> tuple[str, str, str]:
    """Extract current sort parameter and build query strings for pagination and sorting.

    Returns:
        tuple of (current_sort, pagination_query_params, sort_query_params)
        - current_sort: e.g. "created_at", "-created_at", or ""
        - pagination_query_params: query string preserving sort and filters, excluding 'page'
        - sort_query_params: query string preserving filters, excluding 'page' and 'sort'
    """
    current_sort = request.GET.get(sort_key, "").strip()

    pagination_dict = request.GET.copy()
    pagination_dict.pop(page_key, None)
    pagination_query_params = f"&{pagination_dict.urlencode()}" if pagination_dict else ""

    sort_dict = request.GET.copy()
    sort_dict.pop(page_key, None)
    sort_dict.pop(sort_key, None)
    sort_query_params = f"&{sort_dict.urlencode()}" if sort_dict else ""

    return current_sort, pagination_query_params, sort_query_params


def _apply_sorting(
    queryset: Any,
    sort_param: str,
    whitelist: dict[str, str],
    default_order: str | tuple[str, ...],
) -> tuple[Any, str]:
    """Apply safe sorting to a queryset using a strict whitelist.

    Whitelist maps user-facing column keys to database model field names.
    Supports ascending ("field") and descending ("-field").
    Falls back gracefully to default_order on absent or unauthorized params.

    Returns:
        tuple of (sorted_queryset, valid_active_sort)
    """
    if sort_param:
        is_desc = sort_param.startswith("-")
        clean_key = sort_param[1:] if is_desc else sort_param
        if clean_key in whitelist:
            db_field = whitelist[clean_key]
            order_expr = f"-{db_field}" if is_desc else db_field
            return queryset.order_by(order_expr), sort_param

    if isinstance(default_order, (tuple, list)):
        return queryset.order_by(*default_order), ""
    return queryset.order_by(default_order), ""


def _visible_modules(request: HttpRequest) -> AutomationModuleQuerySet:
    return AutomationModule.objects.visible_to(request.user).select_related("area").order_by("code")


@login_required
def dashboard(request: HttpRequest) -> HttpResponse:
    modules = (
        _visible_modules(request)
        .annotate(
            run_count=Count("runs"),
            last_run_at=Max("runs__created_at"),
        )
        .order_by("code")
    )

    runs = (
        AutomationRun.objects.filter(module__in=modules)
        .select_related("module", "triggered_by")
    )

    filter_form = DashboardRunFilterForm(
        request.GET if request.GET else None,
        modules=modules,
    )
    has_active_filters = False
    if request.GET and filter_form.is_valid():
        selected_module = filter_form.cleaned_data.get("module")
        if selected_module:
            runs = runs.filter(module__code=selected_module)
            has_active_filters = True
        selected_status = filter_form.cleaned_data.get("status")
        if selected_status:
            runs = runs.filter(status=selected_status)
            has_active_filters = True
        selected_trigger = filter_form.cleaned_data.get("trigger")
        if selected_trigger:
            runs = runs.filter(trigger=selected_trigger)
            has_active_filters = True

    DASHBOARD_SORT_FIELDS = {
        "module": "module__code",
        "created_at": "created_at",
        "trigger": "trigger",
        "status": "status",
    }
    current_sort, query_params, sort_query_params = _extract_sort_and_query_params(request)
    runs, valid_sort = _apply_sorting(
        runs,
        current_sort,
        DASHBOARD_SORT_FIELDS,
        default_order="-created_at",
    )

    paginator = Paginator(runs, per_page=DEFAULT_PAGE_SIZE)
    page_number = request.GET.get("page", 1)
    page_obj = paginator.get_page(page_number)

    return render(
        request,
        "automations/dashboard.html",
        {
            "modules": modules,
            "recent_runs": page_obj,
            "page_obj": page_obj,
            "paginator": paginator,
            "filter_form": filter_form,
            "has_active_filters": has_active_filters,
            "query_params": query_params,
            "current_sort": valid_sort,
            "sort_query_params": sort_query_params,
        },
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
    if module.code == "SC-05":
        return _sc05_detail(request, module)
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
    sc05_operation = (
        SC05Operation.objects.select_related("client").filter(run=run).first()
        if run.module_id == "SC-05"
        else None
    )
    sc05_steps = (
        sc05_operation.steps.prefetch_related("attempts__artifacts").all()
        if sc05_operation is not None
        else SC05PortalStep.objects.none()
    )
    return render(
        request,
        "automations/run_detail.html",
        {
            "run": run,
            "attempts": attempts,
            "briefing": briefing,
            "document_items": document_items,
            "sc05_operation": sc05_operation,
            "sc05_steps": sc05_steps,
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
    context = _sc04_document_context(module, document)
    context["is_partial"] = True
    response = render(
        request,
        "automations/partials/sc04_document_state.html",
        context,
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
    current_sort, query_params_formatted, sort_query_params = (
        _extract_sort_and_query_params(request)
    )
    queue, valid_sort = _sc04_queue_queryset(module, filter_form, sort_param=current_sort)
    paginator = Paginator(queue, per_page=DEFAULT_PAGE_SIZE)
    page_number = request.GET.get("page", 1)
    page_obj = paginator.get_page(page_number)
    queue_rows = []
    document_ids = [item.intake.document_id for item in page_obj.object_list]
    intakes_by_doc: dict[UUID, list[DocumentIntake]] = defaultdict(list)
    if document_ids:
        intakes = (
            DocumentIntake.objects.filter(document_id__in=document_ids)
            .select_related("run")
            .prefetch_related("run_items")
            .order_by("received_at")
        )
        for intake in intakes:
            intakes_by_doc[intake.document_id].append(intake)

    for item in page_obj.object_list:
        document = item.intake.document
        history = intakes_by_doc.get(document.id) or [item.intake]
        total_occurrences = len(history)
        is_multi_intake = total_occurrences > 1
        latest_intake = history[-1] if history else item.intake
        first_intake = history[0] if history else item.intake
        display_filename = latest_intake.original_filename or item.intake.original_filename
        display_source = (
            latest_intake.get_source_display()
            if latest_intake
            else item.intake.get_source_display()
        )
        display_received_at = latest_intake.received_at if latest_intake else item.created_at
        queue_rows.append(
            {
                "item": item,
                "document": document,
                "latest_intake": latest_intake,
                "first_intake": first_intake,
                "intakes_history": history,
                "total_occurrences": total_occurrences,
                "is_multi_intake": is_multi_intake,
                "display_filename": display_filename,
                "display_source": display_source,
                "display_received_at": display_received_at,
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
    query_params = request.GET.copy()
    query_params.pop("page", None)
    filter_querystring = query_params.urlencode()
    has_active_filters = bool(
        filter_form.is_valid()
        and (
            filter_form.cleaned_data.get("status")
            or filter_form.cleaned_data.get("source")
            or filter_form.cleaned_data.get("outcome")
            or (filter_form.cleaned_data.get("q") or "").strip()
        )
    )
    query = request.GET.urlencode()
    queue_refresh_url = reverse("automations:sc04-queue-fragment")
    if query:
        queue_refresh_url = f"{queue_refresh_url}?{query}"
    runs_paginator = Paginator(
        module.runs.select_related("triggered_by").all(), per_page=DEFAULT_PAGE_SIZE
    )
    runs_page_number = request.GET.get("runs_page", 1)
    runs_page_obj = runs_paginator.get_page(runs_page_number)
    return {
        "module": module,
        "upload_form": upload_form or SC04UploadForm(),
        "queue_filter_form": filter_form,
        "has_active_filters": has_active_filters,
        "summary": summary,
        "queue_rows": queue_rows,
        "page_obj": page_obj,
        "paginator": paginator,
        "is_paginated": page_obj.has_other_pages(),
        "filter_querystring": filter_querystring,
        "query_params": query_params_formatted,
        "current_sort": valid_sort,
        "sort_query_params": sort_query_params,
        "queue_refresh_url": queue_refresh_url,
        "has_active_queue": documents.filter(
            status__in=(DocumentStatus.QUEUED, DocumentStatus.PROCESSING)
        ).exists(),
        "runs": runs_page_obj,
        "runs_page_obj": runs_page_obj,
        "runs_paginator": runs_paginator,
    }


SC04_QUEUE_SORT_FIELDS = {
    "file": "intake__original_filename",
    "source": "intake__source",
    "client": "intake__document__matched_client__name",
    "status": "intake__document__status",
    "received_at": "created_at",
}


def _sc04_queue_queryset(
    module: AutomationModule,
    filter_form: SC04QueueFilterForm,
    sort_param: str = "",
) -> tuple[Any, str]:
    queue = DocumentRunItem.objects.filter(
        run__module=module,
        intake__is_duplicate=False,
    ).select_related(
        "run",
        "intake",
        "intake__document",
        "intake__document__matched_client",
    )
    if not filter_form.is_valid():
        return _apply_sorting(
            queue,
            sort_param,
            SC04_QUEUE_SORT_FIELDS,
            default_order="-created_at",
        )
    status = str(filter_form.cleaned_data.get("status") or "")
    source = str(filter_form.cleaned_data.get("source") or "")
    outcome = str(filter_form.cleaned_data.get("outcome") or "")
    query = str(filter_form.cleaned_data.get("q") or "").strip()
    if status:
        queue = queue.filter(intake__document__status=status)
    if source:
        matching_doc_ids = FiscalDocument.objects.filter(
            intakes__run__module=module,
            intakes__source=source,
        ).values_list("id", flat=True)
        queue = queue.filter(intake__document_id__in=matching_doc_ids)
    if outcome == DocumentRunOutcome.NEW:
        queue = queue.filter(outcome=DocumentRunOutcome.NEW)
    elif outcome:
        matching_doc_ids = FiscalDocument.objects.filter(
            intakes__run__module=module,
            intakes__run_items__outcome=outcome,
        ).values_list("id", flat=True)
        queue = queue.filter(intake__document_id__in=matching_doc_ids)
    if query:
        matching_doc_ids = FiscalDocument.objects.filter(
            intakes__run__module=module
        ).filter(
            Q(intakes__original_filename__icontains=query)
            | Q(matched_client__name__icontains=query)
        ).values_list("id", flat=True)
        queue = queue.filter(intake__document_id__in=matching_doc_ids)
    return _apply_sorting(
        queue,
        sort_param,
        SC04_QUEUE_SORT_FIELDS,
        default_order="-created_at",
    )


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
            metadata={**run.metadata, "dispatch_error": type(exc).__name__},
            finished_at=timezone.now(),
        )
        messages.error(request, "A triagem não pôde ser iniciada. Tente novamente mais tarde.")
        return
    messages.success(request, "Triagem adicionada à fila com rastreabilidade.")


def _sc05_detail(request: HttpRequest, module: AutomationModule) -> HttpResponse:
    allow_failure_scenarios = cast(User, request.user).is_business_administrator
    form = SC05OperationForm(allow_failure_scenarios=allow_failure_scenarios)
    if request.method == "POST":
        form = SC05OperationForm(
            request.POST,
            allow_failure_scenarios=allow_failure_scenarios,
        )
        if form.is_valid():
            try:
                creation = create_sc05_run_result(
                    module=module,
                    client=cast(SC05Client, form.cleaned_data["client"]),
                    action=str(form.cleaned_data["action"]),
                    scenario=str(form.cleaned_data["scenario"]),
                    triggered_by=cast(User, request.user),
                    request_key=form.cleaned_data["request_key"],
                )
            except ValidationError as exc:
                for message in exc.messages:
                    form.add_error(None, message)
            else:
                if creation.created:
                    _dispatch_sc05(request, creation.run)
                else:
                    messages.info(
                        request,
                        "Esta solicitação já havia sido registrada; "
                        "nenhuma execução foi duplicada.",
                    )
                return redirect("automations:run-detail", run_id=creation.run.id)

    clients = SC05Client.objects.all()

    clients_filter_form = SC05ClientFilterForm(request.GET if request.GET else None)
    filtered_clients = clients
    has_active_client_filters = False
    if clients_filter_form.is_valid():
        clients_q = str(clients_filter_form.cleaned_data.get("clients_q") or "").strip()
        clients_status = str(clients_filter_form.cleaned_data.get("clients_status") or "")
        if clients_q:
            clean_digits = re.sub(r"\D", "", clients_q)
            client_q_filter = Q(name__icontains=clients_q) | Q(
                external_reference__icontains=clients_q
            )
            if clean_digits:
                client_q_filter |= Q(document__icontains=clean_digits)
            filtered_clients = filtered_clients.filter(client_q_filter)
            has_active_client_filters = True
        if clients_status:
            filtered_clients = filtered_clients.filter(status=clients_status)
            has_active_client_filters = True

    SC05_CLIENTS_SORT_FIELDS = {
        "client": "name",
        "name": "name",
        "document": "document",
        "status": "status",
    }
    clients_current_sort, clients_query_params_formatted, clients_sort_query_params = (
        _extract_sort_and_query_params(request, sort_key="clients_sort", page_key="clients_page")
    )
    filtered_clients, valid_clients_sort = _apply_sorting(
        filtered_clients,
        clients_current_sort,
        SC05_CLIENTS_SORT_FIELDS,
        default_order="name",
    )

    clients_clear_dict = request.GET.copy()
    clients_clear_dict.pop("clients_page", None)
    clients_clear_dict.pop("clients_sort", None)
    clients_clear_dict.pop("clients_q", None)
    clients_clear_dict.pop("clients_status", None)
    clients_clear_qs = clients_clear_dict.urlencode()
    clients_clear_query_params = f"?{clients_clear_qs}" if clients_clear_qs else ""

    clients_paginator = Paginator(filtered_clients, per_page=DEFAULT_PAGE_SIZE)
    clients_page_number = request.GET.get("clients_page", 1)
    clients_page_obj = clients_paginator.get_page(clients_page_number)

    filter_form = SC05OperationFilterForm(request.GET if request.GET else None)
    operations = (
        SC05Operation.objects.select_related("client", "run", "run__triggered_by")
        .prefetch_related("steps")
        .all()
    )
    if filter_form.is_valid():
        q_val = str(filter_form.cleaned_data.get("q") or "").strip()
        action_val = str(filter_form.cleaned_data.get("action") or "")
        status_val = str(filter_form.cleaned_data.get("status") or "")

        if q_val:
            clean_digits = re.sub(r"\D", "", q_val)
            query_filter = Q(client__name__icontains=q_val)
            if clean_digits:
                query_filter |= Q(client__document__icontains=clean_digits)
            operations = operations.filter(query_filter)
        if action_val:
            operations = operations.filter(action=action_val)
        if status_val:
            operations = operations.filter(run__status=status_val)

    SC05_OPERATIONS_SORT_FIELDS = {
        "created_at": "created_at",
        "client": "client__name",
        "action": "action",
        "status": "run__status",
    }
    current_sort, query_params_formatted, sort_query_params = (
        _extract_sort_and_query_params(request)
    )
    operations, valid_sort = _apply_sorting(
        operations,
        current_sort,
        SC05_OPERATIONS_SORT_FIELDS,
        default_order="-created_at",
    )

    query_params = request.GET.copy()
    query_params.pop("page", None)
    filter_querystring = query_params.urlencode()
    has_active_filters = bool(
        filter_form.is_valid()
        and (
            filter_form.cleaned_data.get("action")
            or filter_form.cleaned_data.get("status")
            or (filter_form.cleaned_data.get("q") or "").strip()
        )
    )

    operations_clear_dict = request.GET.copy()
    operations_clear_dict.pop("page", None)
    operations_clear_dict.pop("sort", None)
    operations_clear_dict.pop("q", None)
    operations_clear_dict.pop("action", None)
    operations_clear_dict.pop("status", None)
    operations_clear_qs = operations_clear_dict.urlencode()
    operations_clear_query_params = f"?{operations_clear_qs}" if operations_clear_qs else ""

    paginator = Paginator(operations, per_page=DEFAULT_PAGE_SIZE)
    page_number = request.GET.get("page", 1)
    page_obj = paginator.get_page(page_number)

    summary = {
        "total": clients.count(),
        "active": clients.filter(status="active").count(),
        "blocked": clients.filter(status="blocked").count(),
        "partial": clients.filter(status="partial").count(),
    }
    return render(
        request,
        "automations/sc05_detail.html",
        {
            "module": module,
            "form": form,
            "filter_form": filter_form,
            "has_active_filters": has_active_filters,
            "query_params": query_params_formatted,
            "current_sort": valid_sort,
            "sort_query_params": sort_query_params,
            "filter_querystring": filter_querystring,
            "operations_clear_query_params": operations_clear_query_params,
            "clients_filter_form": clients_filter_form,
            "has_active_client_filters": has_active_client_filters,
            "clients_query_params": clients_query_params_formatted,
            "clients_current_sort": valid_clients_sort,
            "clients_sort_query_params": clients_sort_query_params,
            "clients_clear_query_params": clients_clear_query_params,
            "clients": clients_page_obj,
            "clients_page_obj": clients_page_obj,
            "clients_paginator": clients_paginator,
            "operations": page_obj,
            "page_obj": page_obj,
            "paginator": paginator,
            "summary": summary,
            "allow_failure_scenarios": allow_failure_scenarios,
        },
    )


@login_required
@require_POST
def sc05_resume(request: HttpRequest, run_id: str) -> HttpResponse:
    operation = _visible_sc05_operation(request, run_id=run_id)
    try:
        run = resume_sc05_run(operation.run_id)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect("automations:run-detail", run_id=operation.run_id)
    _dispatch_sc05(request, run, resumed=True)
    return redirect("automations:run-detail", run_id=run.id)


@login_required
@require_GET
def sc05_artifact(request: HttpRequest, artifact_id: str) -> HttpResponse:
    try:
        artifact = (
            SC05Artifact.objects.select_related(
                "attempt__step__operation__run__module__area",
            )
            .filter(attempt__step__operation__run__module__in=_visible_modules(request))
            .get(pk=artifact_id)
        )
    except (SC05Artifact.DoesNotExist, ValueError) as exc:
        raise Http404("Evidência não encontrada.") from exc
    try:
        content = build_screenshot_storage().get(key=artifact.storage_key)
        if (
            len(content) != artifact.byte_size
            or hashlib.sha256(content).hexdigest() != artifact.sha256
        ):
            raise ArtifactStorageError("A evidência visual falhou na verificação de integridade.")
    except ArtifactStorageError:
        return HttpResponse(
            "A evidência visual está temporariamente indisponível.",
            status=503,
            content_type="text/plain; charset=utf-8",
        )
    response = FileResponse(
        BytesIO(content),
        as_attachment=False,
        filename=f"evidencia-sc05-{artifact.id}.png",
        content_type="image/png",
    )
    response["Cache-Control"] = "private, no-store"
    response["Cross-Origin-Resource-Policy"] = "same-origin"
    response["X-Content-Type-Options"] = "nosniff"
    return cast(HttpResponse, response)


def _visible_sc05_operation(request: HttpRequest, *, run_id: str) -> SC05Operation:
    try:
        return (
            SC05Operation.objects.select_related("run", "client", "run__module__area")
            .filter(run__module__in=_visible_modules(request))
            .get(run_id=UUID(str(run_id)))
        )
    except (SC05Operation.DoesNotExist, ValueError) as exc:
        raise Http404("Operação SC-05 não encontrada.") from exc


def _dispatch_sc05(
    request: HttpRequest,
    run: AutomationRun,
    *,
    resumed: bool = False,
) -> None:
    try:
        run_sc05_task.delay(str(run.id))
    except Exception as exc:
        AutomationRun.objects.filter(pk=run.pk).update(
            status=(RunStatus.PARTIALLY_FAILED if resumed else RunStatus.FAILED),
            summary=(
                "A retomada não entrou na fila; o estado residual foi preservado "
                "para nova tentativa."
                if resumed
                else "Não foi possível adicionar o robô ao processamento."
            ),
            error_message="O serviço de execução está temporariamente indisponível.",
            metadata={**run.metadata, "dispatch_error": type(exc).__name__},
            finished_at=timezone.now(),
        )
        messages.error(request, "A operação não pôde ser iniciada. Tente novamente mais tarde.")
        return
    label = "Retomada" if resumed else "Operação"
    messages.success(request, f"{label} adicionada à fila RPA com rastreabilidade.")


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
        if action not in {"save", "complete", "cancel"}:
            return HttpResponseBadRequest("Ação inválida.")
        if action == "cancel":
            module_slug = briefing.run.module.slug
            if is_briefing_empty(briefing):
                discard_empty_briefing(briefing.id)
                messages.info(request, "Rascunho vazio descartado sem resíduos.")
            else:
                cancel_briefing(briefing.id, cancelled_by=cast(User, request.user))
                messages.info(request, "Briefing societário cancelado e arquivado.")
            return redirect("automations:module-detail", slug=module_slug)
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
                next_url = request.POST.get("next")
                if action == "save" and next_url and next_url.startswith("/"):
                    return redirect(next_url)
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
            "is_new": False,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def sc06_briefing_new(request: HttpRequest) -> HttpResponse:
    module = get_object_or_404(
        AutomationModule.objects.filter(pk__in=_visible_modules(request)),
        pk="SC-06",
    )
    try:
        template_version = get_latest_published_version(DEFAULT_TEMPLATE_CODE)
    except PublishedTemplateUnavailable:
        messages.error(
            request,
            "O template publicado está indisponível. Solicite a configuração ao administrador.",
        )
        return redirect("automations:module-detail", slug=module.slug)

    data_source = request.POST if request.method == "POST" else request.GET
    start_form = BriefingStartForm(
        {
            "client_name": data_source.get("client_name", ""),
            "client_document": data_source.get("client_document", ""),
        }
    )
    if not start_form.is_valid():
        messages.error(request, "Informe os dados válidos do cliente para iniciar o briefing.")
        return redirect("automations:module-detail", slug=module.slug)

    client_name = start_form.cleaned_data["client_name"]
    client_document = start_form.cleaned_data["client_document"]
    schema = template_version.schema
    form = BriefingAnswersForm(
        schema=schema,
        data=request.POST if request.method == "POST" else None,
    )
    submitted_answers: dict[str, object] = {}

    if request.method == "POST":
        action = request.POST.get("action", "")
        if action not in {"save", "complete", "cancel"}:
            return HttpResponseBadRequest("Ação inválida.")
        if action == "cancel":
            messages.info(request, "Atendimento descartado sem resíduos.")
            return redirect("automations:module-detail", slug=module.slug)

        submitted_answers = {
            field_name: request.POST.get(field_name, "") for field_name in form.fields
        }
        if form.is_valid():
            submitted_answers = form.answer_payload
            try:
                with transaction.atomic():
                    briefing = create_briefing(
                        client_name=client_name,
                        client_document=client_document,
                        created_by=cast(User, request.user),
                    )
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
                next_url = request.POST.get("next")
                if action == "save" and next_url and next_url.startswith("/"):
                    return redirect(next_url)
                return redirect(
                    "automations:sc06-briefing-detail",
                    briefing_id=briefing.id,
                )

    transient_briefing = SocietaryBriefing(
        template_version=template_version,
        client_name=client_name,
        client_document=client_document,
        created_by=cast(User, request.user),
        status=SocietaryBriefingStatus.DRAFT,
        answers=submitted_answers,
    )

    return render(
        request,
        "automations/sc06_form.html",
        {
            "module": module,
            "briefing": transient_briefing,
            "form": form,
            "sections": form.sections,
            "frontend_config": build_frontend_config(schema, submitted_answers),
            "is_new": True,
            "client_name": client_name,
            "client_document": client_document,
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
                get_latest_published_version(DEFAULT_TEMPLATE_CODE)
            except PublishedTemplateUnavailable:
                start_form.add_error(
                    None,
                    "O template publicado está indisponível. "
                    "Solicite a configuração ao administrador.",
                )
            else:
                params = urlencode(
                    {
                        "client_name": start_form.cleaned_data["client_name"],
                        "client_document": start_form.cleaned_data["client_document"],
                    }
                )
                return redirect(f"{reverse('automations:sc06-briefing-new')}?{params}")

    briefings = SocietaryBriefing.objects.filter(run__module=module).select_related(
        "template_version", "created_by", "completed_by", "run"
    )
    summary = briefings.aggregate(
        total=Count("id"),
        draft=Count("id", filter=Q(status=SocietaryBriefingStatus.DRAFT)),
        completed=Count("id", filter=Q(status=SocietaryBriefingStatus.COMPLETED)),
        cancelled=Count("id", filter=Q(status=SocietaryBriefingStatus.CANCELLED)),
    )
    filter_form = SC06CasesFilterForm(request.GET if request.GET else None)
    has_active_filters = False
    filtered_briefings = briefings
    if request.GET and filter_form.is_valid():
        q = filter_form.cleaned_data.get("q")
        if q:
            clean_digits = re.sub(r"\D", "", q)
            query_q = Q(client_name__icontains=q)
            if clean_digits:
                query_q |= Q(client_document__icontains=clean_digits)
            filtered_briefings = filtered_briefings.filter(query_q)
            has_active_filters = True
        status = filter_form.cleaned_data.get("status")
        if status:
            filtered_briefings = filtered_briefings.filter(status=status)
            has_active_filters = True

    paginator = Paginator(filtered_briefings, per_page=DEFAULT_PAGE_SIZE)
    page_number = request.GET.get("page", 1)
    page_obj = paginator.get_page(page_number)

    query_dict = request.GET.copy()
    query_dict.pop("page", None)
    query_params = f"&{query_dict.urlencode()}" if query_dict else ""

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
            "briefings": page_obj,
            "page_obj": page_obj,
            "paginator": paginator,
            "summary": summary,
            "active_template": active_template,
            "filter_form": filter_form,
            "has_active_filters": has_active_filters,
            "query_params": query_params,
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
    all_certificates = DigitalCertificate.objects.all()
    summary = {
        "total": all_certificates.count(),
        "expiring": DigitalCertificate.objects.expiring_between(
            start_date=today,
            end_date=window_end,
        ).count(),
        "expired": DigitalCertificate.objects.active().filter(valid_until__lt=today).count(),
        "failed": CertificateCommunication.objects.filter(
            status=CommunicationStatus.FAILED
        ).count(),
    }

    filter_form = SC20CertificateFilterForm(request.GET if request.GET else None)
    certificates = all_certificates
    if filter_form.is_valid():
        q_val = str(filter_form.cleaned_data.get("q") or "").strip()
        status_val = str(filter_form.cleaned_data.get("status") or "")

        if q_val:
            clean_digits = re.sub(r"\D", "", q_val)
            query_filter = Q(client_name__icontains=q_val)
            if clean_digits:
                query_filter |= Q(client_document__icontains=clean_digits)
            certificates = certificates.filter(query_filter)
        if status_val == "expiring":
            certificates = certificates.expiring_between(start_date=today, end_date=window_end)
        elif status_val:
            certificates = certificates.filter(status=status_val)

    SC20_CERTIFICATES_SORT_FIELDS = {
        "client": "client_name",
        "document": "client_document",
        "expires_on": "valid_until",
        "status": "status",
    }
    current_sort, query_params_formatted, sort_query_params = (
        _extract_sort_and_query_params(request)
    )
    certificates, valid_sort = _apply_sorting(
        certificates,
        current_sort,
        SC20_CERTIFICATES_SORT_FIELDS,
        default_order=("valid_until", "client_name", "serial_number"),
    )

    query_params_dict = request.GET.copy()
    query_params_dict.pop("page", None)
    filter_querystring = query_params_dict.urlencode()
    has_active_filters = bool(
        filter_form.is_valid()
        and (
            filter_form.cleaned_data.get("status")
            or (filter_form.cleaned_data.get("q") or "").strip()
        )
    )

    certificates_paginator = Paginator(certificates, per_page=DEFAULT_PAGE_SIZE)
    certificates_page = certificates_paginator.get_page(request.GET.get("page", 1))

    attempts_paginator = Paginator(
        CommunicationAttempt.objects.select_related("communication__certificate", "run").all(),
        per_page=DEFAULT_PAGE_SIZE,
    )
    attempts_page = attempts_paginator.get_page(request.GET.get("attempts_page", 1))

    runs_paginator = Paginator(
        module.runs.select_related("triggered_by").all(),
        per_page=DEFAULT_PAGE_SIZE,
    )
    runs_page = runs_paginator.get_page(request.GET.get("runs_page", 1))

    return render(
        request,
        "automations/sc20_detail.html",
        {
            "module": module,
            "filter_form": filter_form,
            "has_active_filters": has_active_filters,
            "query_params": query_params_formatted,
            "current_sort": valid_sort,
            "sort_query_params": sort_query_params,
            "filter_querystring": filter_querystring,
            "certificates": certificates_page,
            "certificates_paginator": certificates_paginator,
            "attempts": attempts_page,
            "attempts_paginator": attempts_paginator,
            "summary": summary,
            "form": form,
            "runs": runs_page,
            "runs_paginator": runs_paginator,
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
