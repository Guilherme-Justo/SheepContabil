from django.urls import path

from core.automations import views

app_name = "automations"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("modulos/<slug:slug>/", views.module_detail, name="module-detail"),
    path("triagem-fiscal/upload/", views.sc04_upload, name="sc04-upload"),
    path(
        "triagem-fiscal/processar-caixa/",
        views.sc04_process_inbox,
        name="sc04-process-inbox",
    ),
    path("triagem-fiscal/fila/", views.sc04_queue_fragment, name="sc04-queue-fragment"),
    path(
        "triagem-fiscal/documentos/<uuid:document_id>/",
        views.sc04_document_detail,
        name="sc04-document-detail",
    ),
    path(
        "triagem-fiscal/documentos/<uuid:document_id>/estado/",
        views.sc04_document_state,
        name="sc04-document-state",
    ),
    path(
        "triagem-fiscal/documentos/<uuid:document_id>/visualizar/",
        views.sc04_document_preview,
        name="sc04-document-preview",
    ),
    path(
        "triagem-fiscal/documentos/<uuid:document_id>/baixar/",
        views.sc04_document_download,
        name="sc04-document-download",
    ),
    path(
        "triagem-fiscal/documentos/<uuid:document_id>/tentar-encaminhamento/",
        views.sc04_retry_route,
        name="sc04-route-retry",
    ),
    path(
        "triagem-fiscal/revisoes/<uuid:review_id>/resolver/",
        views.sc04_resolve_review,
        name="sc04-review-resolve",
    ),
    path(
        "bloqueio-clientes/execucoes/<uuid:run_id>/retomar/",
        views.sc05_resume,
        name="sc05-resume",
    ),
    path(
        "bloqueio-clientes/evidencias/<uuid:artifact_id>/",
        views.sc05_artifact,
        name="sc05-artifact",
    ),
    path(
        "briefings-societarios/<uuid:briefing_id>/",
        views.sc06_briefing_detail,
        name="sc06-briefing-detail",
    ),
    path(
        "briefings-societarios/<uuid:briefing_id>/pdf/",
        views.sc06_briefing_pdf,
        name="sc06-briefing-pdf",
    ),
    path("execucoes/<uuid:run_id>/", views.run_detail, name="run-detail"),
]
