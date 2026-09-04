from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.automations.models import (
    AutomationModule,
    AutomationRun,
    ClassificationAttemptStatus,
    DocumentClassificationAttempt,
    DocumentIntake,
    DocumentReview,
    DocumentReviewReason,
    DocumentRunItem,
    DocumentRunOutcome,
    DocumentSource,
    DocumentStatus,
    DocumentType,
    FiscalClient,
    FiscalDocument,
    RunStatus,
    RunTrigger,
)
from core.automations.sc04.contracts import StoredObject
from core.identity.models import User

pytestmark = pytest.mark.django_db


@dataclass
class MemoryStorage:
    objects: dict[str, tuple[bytes, str]] = field(default_factory=dict)

    def put_bytes(self, *, key: str, content: bytes, content_type: str) -> StoredObject:
        self.objects.setdefault(key, (content, content_type))
        return StoredObject(key=key, byte_size=len(content), content_type=content_type)

    def get_bytes(self, key: str) -> bytes:
        return self.objects[key][0]

    def copy_if_absent(
        self,
        *,
        source_key: str,
        destination_key: str,
        content_type: str,
    ) -> StoredObject:
        content = self.objects[source_key][0]
        self.objects.setdefault(destination_key, (content, content_type))
        return StoredObject(
            key=destination_key,
            byte_size=len(content),
            content_type=content_type,
        )


def _document_case(
    module: AutomationModule,
    *,
    status: str = DocumentStatus.QUEUED,
    suffix: str = "case",
) -> tuple[AutomationRun, FiscalDocument, DocumentRunItem, bytes]:
    content = f"DOCUMENTO FISCAL SINTETICO {suffix}".encode()
    digest = hashlib.sha256(content).hexdigest()
    run = AutomationRun.objects.create(
        module=module,
        trigger=RunTrigger.MANUAL,
        status=RunStatus.RUNNING,
        idempotency_key=f"view-sc04:{suffix}",
    )
    document = FiscalDocument.objects.create(
        sha256=digest,
        storage_key=f"private/sc04/{digest}.txt",
        media_type="text/plain",
        byte_size=len(content),
        status=status,
    )
    intake = DocumentIntake.objects.create(
        document=document,
        run=run,
        source=DocumentSource.MANUAL,
        source_reference=f"manual:view:{suffix}",
        original_filename=f"documento-{suffix}.txt",
    )
    item = DocumentRunItem.objects.create(
        run=run,
        intake=intake,
        outcome=DocumentRunOutcome.NEW,
    )
    return run, document, item, content


def test_sc04_dashboard_obeys_area_rbac_and_renders_operational_actions(
    client: Client,
    modules: dict[str, AutomationModule],
    fiscal_operator: User,
    processes_operator: User,
) -> None:
    url = reverse("automations:module-detail", kwargs={"slug": modules["SC-04"].slug})

    client.force_login(fiscal_operator)
    allowed = client.get(url)
    client.force_login(processes_operator)
    denied = client.get(url)

    assert allowed.status_code == 200
    page = allowed.content.decode()
    assert "Processar caixa agora" in page
    assert "Enviar documento" in page
    assert "Arquivos recebidos" in page
    assert denied.status_code == 404


def test_valid_upload_is_persisted_dispatched_and_redirected_to_document(
    client: Client,
    modules: dict[str, AutomationModule],
    fiscal_operator: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del modules
    storage = MemoryStorage()
    dispatched: list[str] = []
    monkeypatch.setattr(
        "core.automations.sc04.services.build_object_storage",
        lambda: storage,
    )
    monkeypatch.setattr(
        "core.automations.views.run_sc04_task.delay",
        lambda run_id: dispatched.append(run_id),
    )
    client.force_login(fiscal_operator)

    response = client.post(
        reverse("automations:sc04-upload"),
        {
            "attachment": SimpleUploadedFile(
                "nota-demo.txt",
                b"NOTA FISCAL SINTETICA PARA UPLOAD",
                content_type="text/plain",
            ),
            "confirm_synthetic": "on",
        },
    )

    document = FiscalDocument.objects.get()
    run = AutomationRun.objects.get(module_id="SC-04")
    assert response.status_code == 302
    assert response.url == reverse(
        "automations:sc04-document-detail",
        kwargs={"document_id": document.id},
    )
    assert dispatched == [str(run.id)]
    assert run.status == RunStatus.QUEUED
    assert document.storage_key in storage.objects


def test_invalid_upload_shows_field_error_without_persistence(
    client: Client,
    modules: dict[str, AutomationModule],
    fiscal_operator: User,
) -> None:
    del modules
    client.force_login(fiscal_operator)

    response = client.post(
        reverse("automations:sc04-upload"),
        {
            "attachment": SimpleUploadedFile(
                "binario.exe",
                b"\x00\x01\x02",
                content_type="application/octet-stream",
            ),
            "confirm_synthetic": "on",
        },
    )

    assert response.status_code == 400
    assert "PDF, PNG, JPEG ou TXT" in response.content.decode()
    assert not FiscalDocument.objects.exists()
    assert not AutomationRun.objects.exists()


def test_queue_and_document_poll_only_while_work_is_active(
    client: Client,
    modules: dict[str, AutomationModule],
    fiscal_operator: User,
) -> None:
    run, document, _, _ = _document_case(modules["SC-04"], suffix="poll")
    client.force_login(fiscal_operator)

    queue = client.get(reverse("automations:sc04-queue-fragment"))
    state = client.get(
        reverse("automations:sc04-document-state", kwargs={"document_id": document.id})
    )
    detail = client.get(
        reverse("automations:sc04-document-detail", kwargs={"document_id": document.id})
    )
    assert 'hx-trigger="every 5s"' in queue.content.decode()
    assert 'hx-trigger="every 2s"' in state.content.decode()
    assert 'id="sc04-file-metadata"' in state.content.decode()
    assert 'hx-swap-oob="true"' in state.content.decode()
    assert 'id="sc04-file-metadata"' in detail.content.decode()
    assert 'hx-swap-oob="true"' not in detail.content.decode()

    FiscalDocument.objects.filter(pk=document.pk).update(status=DocumentStatus.AWAITING_REVIEW)
    attempt = DocumentClassificationAttempt.objects.create(
        document=document,
        run=run,
        sequence=1,
        status=ClassificationAttemptStatus.FAILED,
        provider="openai",
        error_code="classifier_unavailable",
        error_message="A classificação por IA ainda não está configurada.",
        finished_at=timezone.now(),
    )
    DocumentReview.objects.create(
        document=document,
        run=run,
        suggested_attempt=attempt,
        reason=DocumentReviewReason.CLASSIFIER_UNAVAILABLE,
        policy_version="v1",
    )
    queue = client.get(reverse("automations:sc04-queue-fragment"))
    state = client.get(
        reverse("automations:sc04-document-state", kwargs={"document_id": document.id})
    )
    assert 'hx-trigger="every 5s"' not in queue.content.decode()
    assert 'hx-trigger="every 2s"' not in state.content.decode()
    assert 'id="sc04-file-metadata"' in state.content.decode()
    assert 'hx-swap-oob="true"' in state.content.decode()
    assert "Classificador indisponível" in state.content.decode()
    assert "dark:text-white font-semibold" in state.content.decode()


def test_preview_and_download_stream_only_to_authorized_area(
    client: Client,
    modules: dict[str, AutomationModule],
    fiscal_operator: User,
    processes_operator: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, document, _, content = _document_case(modules["SC-04"], suffix="stream")
    storage = MemoryStorage(objects={document.storage_key: (content, document.media_type)})
    monkeypatch.setattr("core.automations.views.build_object_storage", lambda: storage)
    preview_url = reverse(
        "automations:sc04-document-preview",
        kwargs={"document_id": document.id},
    )
    download_url = reverse(
        "automations:sc04-document-download",
        kwargs={"document_id": document.id},
    )

    client.force_login(fiscal_operator)
    preview = client.get(preview_url)
    download = client.get(download_url)
    client.force_login(processes_operator)
    denied = client.get(download_url)

    assert preview.status_code == 200
    assert b"".join(preview.streaming_content) == content
    assert preview.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert preview.headers["Cache-Control"] == "private, no-store"
    assert download.status_code == 200
    assert "attachment" in download.headers["Content-Disposition"]
    assert download.headers["X-Content-Type-Options"] == "nosniff"
    assert denied.status_code == 404


def test_review_prefills_deterministic_client_instead_of_model_prediction(
    client: Client,
    modules: dict[str, AutomationModule],
    fiscal_operator: User,
) -> None:
    run, document, _, _ = _document_case(
        modules["SC-04"],
        status=DocumentStatus.AWAITING_REVIEW,
        suffix="review",
    )
    exact_client = FiscalClient.objects.create(
        code="exact-client",
        name="Cliente Exato",
        document_number="12345678000190",
        route_prefix="exact-client",
    )
    model_client = FiscalClient.objects.create(
        code="model-client",
        name="Cliente do Modelo",
        document_number="98765432000110",
        route_prefix="model-client",
    )
    FiscalDocument.objects.filter(pk=document.pk).update(matched_client=exact_client)
    document.refresh_from_db()
    attempt = DocumentClassificationAttempt.objects.create(
        document=document,
        run=run,
        sequence=1,
        status=ClassificationAttemptStatus.SUCCEEDED,
        provider="fake",
        model="fake-v1",
        prompt_version="prompt-v1",
        schema_version="schema-v1",
        input_sha256="a" * 64,
        input_char_count=100,
        predicted_document_type=DocumentType.INVOICE,
        predicted_client=model_client,
        type_confidence=0.7,
        client_confidence=0.7,
        evidence=["Evidência sintética."],
        finished_at=timezone.now(),
    )
    DocumentReview.objects.create(
        document=document,
        run=run,
        suggested_attempt=attempt,
        reason=DocumentReviewReason.LOW_CONFIDENCE,
        policy_version="policy-v1",
    )
    client.force_login(fiscal_operator)

    response = client.get(
        reverse("automations:sc04-document-detail", kwargs={"document_id": document.id})
    )

    assert response.status_code == 200
    page = response.content.decode()
    assert f'<option value="{exact_client.pk}" selected>' in page
    assert f'<option value="{model_client.pk}" selected>' not in page
    assert document.storage_key not in page


def test_sc04_run_detail_lists_new_and_duplicate_items(
    client: Client,
    modules: dict[str, AutomationModule],
    fiscal_operator: User,
) -> None:
    run, document, _, _ = _document_case(modules["SC-04"], suffix="run-detail")
    duplicate_intake = DocumentIntake.objects.create(
        document=document,
        run=run,
        source=DocumentSource.SIMULATED_INBOX,
        source_reference="inbox:view:run-detail",
        original_filename="documento-repetido.txt",
        status="duplicate",
        is_duplicate=True,
    )
    DocumentRunItem.objects.create(
        run=run,
        intake=duplicate_intake,
        outcome=DocumentRunOutcome.DUPLICATE_HASH,
    )
    client.force_login(fiscal_operator)

    response = client.get(reverse("automations:run-detail", kwargs={"run_id": run.id}))

    assert response.status_code == 200
    page = response.content.decode()
    assert "documento-run-detail.txt" in page
    assert "documento-repetido.txt" in page
    assert "Conteúdo duplicado" in page


def test_sc04_queue_is_paginated_and_preserves_filters(
    client: Client,
    modules: dict[str, AutomationModule],
    fiscal_operator: User,
) -> None:
    sc04 = modules["SC-04"]
    for i in range(15):
        _document_case(sc04, suffix=f"paginated-{i:02d}")

    client.force_login(fiscal_operator)

    # Page 1
    resp_p1 = client.get(reverse("automations:module-detail", kwargs={"slug": sc04.slug}))
    assert resp_p1.status_code == 200
    page1 = resp_p1.content.decode()
    assert "Mostrando" in page1
    assert "1</strong> a" in page1
    assert "7</strong> de" in page1
    assert "15</strong> arquivos" in page1
    assert ">1</strong> de" in page1
    assert "page=2" in page1
    assert 'aria-label="Primeira página"' not in page1  # disabled span on page 1
    assert 'aria-label="Última página"' in page1  # active link to page 3
    assert "page=3" in page1

    # Page 2
    resp_p2 = client.get(
        reverse("automations:module-detail", kwargs={"slug": sc04.slug}),
        {"page": "2"},
    )
    assert resp_p2.status_code == 200
    page2 = resp_p2.content.decode()
    assert "8</strong> a" in page2
    assert "14</strong> de" in page2
    assert ">2</strong> de" in page2
    assert "page=1" in page2
    assert 'aria-label="Primeira página"' in page2  # active link to page 1
    assert 'aria-label="Última página"' in page2  # active link to page 3

    # Page 3 (last page of queue)
    resp_p3 = client.get(
        reverse("automations:module-detail", kwargs={"slug": sc04.slug}),
        {"page": "3"},
    )
    assert resp_p3.status_code == 200
    page3 = resp_p3.content.decode()
    assert "15</strong> a" in page3
    assert 'aria-label="Primeira página"' in page3  # active link to page 1

    # Queue fragment on last page (Page 3) - isolated from other paginators
    resp_frag_p3 = client.get(
        reverse("automations:sc04-queue-fragment"),
        {"page": "3"},
    )
    assert resp_frag_p3.status_code == 200
    frag_p3 = resp_frag_p3.content.decode()
    assert "15</strong> a" in frag_p3
    assert 'aria-label="Primeira página"' in frag_p3  # active link to page 1
    assert 'aria-label="Última página"' not in frag_p3  # disabled span on last page

    # Queue fragment with filter + page 2
    resp_fragment = client.get(
        reverse("automations:sc04-queue-fragment"),
        {"q": "paginated", "page": "2"},
    )
    assert resp_fragment.status_code == 200
    frag = resp_fragment.content.decode()
    assert "8</strong> a" in frag
    assert "q=paginated" in frag
    assert "page=2" in frag
    assert 'aria-label="Primeira página"' in frag
    assert 'aria-label="Última página"' in frag
    assert 'show:#sc04-queue-region:top' in frag


def test_sc04_filter_button_and_compact_empty_table(
    client: Client,
    modules: dict[str, AutomationModule],
    fiscal_operator: User,
) -> None:
    sc04 = modules["SC-04"]
    client.force_login(fiscal_operator)

    # Clean queue without filters
    url = reverse("automations:module-detail", kwargs={"slug": sc04.slug})
    resp_clean = client.get(url)
    assert resp_clean.status_code == 200
    page_clean = resp_clean.content.decode()
    assert 'id="sc04-queue-region"' in page_clean
    assert 'hx-target="#sc04-queue-region"' in page_clean
    assert "min-h-[26rem]" not in page_clean
    assert "Limpar" not in page_clean
    assert "Nenhum arquivo recebido na fila operacional." in page_clean

    # Create a document
    _document_case(sc04, suffix="sample-doc")

    # Filter with non-matching query
    resp_filtered = client.get(url, {"q": "termo-inexistente"})
    assert resp_filtered.status_code == 200
    page_filtered = resp_filtered.content.decode()
    assert 'id="sc04-queue-region"' in page_filtered
    assert "Limpar" in page_filtered
    assert 'hx-target="#sc04-queue-region"' in page_filtered
    assert 'hx-select="#sc04-queue-region"' in page_filtered
    assert "Nenhum arquivo localizado com os filtros selecionados." in page_filtered
    assert "Limpar filtros" not in page_filtered


def test_sc04_queue_sorting_and_whitelist_security(
    client: Client,
    modules: dict[str, AutomationModule],
    fiscal_operator: User,
) -> None:
    sc04 = modules["SC-04"]
    client.force_login(fiscal_operator)

    client_a = FiscalClient.objects.create(
        code="CLI-ALPHA",
        route_prefix="alpha",
        name="Alpha Consultoria",
        document_number="11111111000101",
    )
    client_b = FiscalClient.objects.create(
        code="CLI-BETA",
        route_prefix="beta",
        name="Beta Logistica",
        document_number="22222222000102",
    )

    _, doc_a, item_a, _ = _document_case(sc04, suffix="doc-zebra")
    doc_a.matched_client = client_a
    doc_a.save(update_fields=["matched_client"])
    item_a.intake.original_filename = "zebra_invoice.xml"
    item_a.intake.save(update_fields=["original_filename"])

    _, doc_b, item_b, _ = _document_case(sc04, suffix="doc-alpha")
    doc_b.matched_client = client_b
    doc_b.save(update_fields=["matched_client"])
    item_b.intake.original_filename = "alpha_nfse.xml"
    item_b.intake.save(update_fields=["original_filename"])

    url = reverse("automations:module-detail", kwargs={"slug": sc04.slug})

    # 1. Sem sort: natural, cabeçalhos neutros com aria-sort="none"
    resp_nat = client.get(url)
    assert resp_nat.status_code == 200
    html_nat = resp_nat.content.decode()
    assert 'aria-sort="none"' in html_nat
    assert 'hx-target="#sc04-queue-region"' in html_nat

    # 2. Sort ascendente por arquivo: alpha_nfse antes de zebra_invoice
    resp_file_asc = client.get(f"{url}?sort=file")
    assert resp_file_asc.status_code == 200
    html_file_asc = resp_file_asc.content.decode()
    assert 'aria-sort="ascending"' in html_file_asc
    assert "sort=-file" in html_file_asc
    pos_alpha = html_file_asc.find("alpha_nfse.xml")
    pos_zebra = html_file_asc.find("zebra_invoice.xml")
    assert pos_alpha != -1 and pos_zebra != -1
    assert pos_alpha < pos_zebra

    # 3. Sort descendente por arquivo: zebra_invoice antes de alpha_nfse
    resp_file_desc = client.get(f"{url}?sort=-file")
    assert resp_file_desc.status_code == 200
    html_file_desc = resp_file_desc.content.decode()
    assert 'aria-sort="descending"' in html_file_desc
    pos_alpha = html_file_desc.find("alpha_nfse.xml")
    pos_zebra = html_file_desc.find("zebra_invoice.xml")
    assert pos_alpha != -1 and pos_zebra != -1
    assert pos_zebra < pos_alpha

    # 4. Sort por cliente ascendente: Alpha antes de Beta
    resp_client_asc = client.get(f"{url}?sort=client")
    assert resp_client_asc.status_code == 200
    html_client_asc = resp_client_asc.content.decode()
    assert 'aria-sort="ascending"' in html_client_asc
    pos_a = html_client_asc.find("Alpha Consultoria")
    pos_b = html_client_asc.find("Beta Logistica")
    assert pos_a != -1 and pos_b != -1
    assert pos_a < pos_b

    # 5. Whitelist / Proteção contra SQL injection: campo arbitrário cai no padrão
    resp_malicious = client.get(f"{url}?sort=__injection_attempt")
    assert resp_malicious.status_code == 200
    assert resp_malicious.context["current_sort"] == ""

