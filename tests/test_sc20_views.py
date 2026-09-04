from __future__ import annotations

from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.automations.models import (
    AutomationModule,
    AutomationRun,
    CertificateCommunication,
    CertificateStatus,
    CommunicationAttempt,
    CommunicationChannel,
    CommunicationStatus,
    DigitalCertificate,
    RunStatus,
)
from core.identity.models import User

pytestmark = pytest.mark.django_db


def _certificate(*, email: str = "contato@example.test") -> DigitalCertificate:
    return DigitalCertificate.objects.create(
        serial_number=f"VIEW-{DigitalCertificate.objects.count() + 1}",
        client_name="Cliente da Interface",
        client_document="12345678000190",
        responsible_name="Contato da Interface",
        contact_email=email,
        preferred_channel=CommunicationChannel.EMAIL,
        valid_until=timezone.localdate() + timedelta(days=15),
        status=CertificateStatus.ACTIVE,
    )


def _module_url(modules: dict[str, AutomationModule]) -> str:
    return reverse(
        "automations:module-detail",
        kwargs={"slug": modules["SC-20"].slug},
    )


def test_sc20_page_exposes_operational_controls_and_summary(
    client: Client,
    processes_operator: User,
    modules: dict[str, AutomationModule],
) -> None:
    _certificate()
    client.force_login(processes_operator)

    response = client.get(_module_url(modules))

    assert response.status_code == 200
    html = response.content.decode()
    assert "Verificar vencimentos agora" in html
    assert "Cadastrar certificado" in html
    assert "Cliente da Interface" in html
    assert "Próximos do vencimento" in html
    assert "Histórico de avisos" in html


def test_certificate_form_normalizes_identifier_and_document(
    client: Client,
    processes_operator: User,
    modules: dict[str, AutomationModule],
) -> None:
    client.force_login(processes_operator)

    response = client.post(
        _module_url(modules),
        {
            "action": "create_certificate",
            "serial_number": " cert-form-01 ",
            "client_name": "Empresa Formulário",
            "client_document": "12.345.678/0001-90",
            "responsible_name": "Pessoa Responsável",
            "contact_email": "formulario@example.test",
            "contact_phone": "",
            "preferred_channel": CommunicationChannel.EMAIL,
            "valid_until": (timezone.localdate() + timedelta(days=40)).isoformat(),
            "status": CertificateStatus.ACTIVE,
        },
    )

    certificate = DigitalCertificate.objects.get()
    assert response.status_code == 302
    assert response.url == _module_url(modules)
    assert certificate.serial_number == "CERT-FORM-01"
    assert certificate.client_document == "12345678000190"


def test_certificate_form_requires_contact_for_selected_channel(
    client: Client,
    processes_operator: User,
    modules: dict[str, AutomationModule],
) -> None:
    client.force_login(processes_operator)

    response = client.post(
        _module_url(modules),
        {
            "action": "create_certificate",
            "serial_number": "CERT-NO-PHONE",
            "client_name": "Empresa sem Telefone",
            "client_document": "12345678000190",
            "responsible_name": "Pessoa Responsável",
            "contact_email": "",
            "contact_phone": "",
            "preferred_channel": CommunicationChannel.WHATSAPP,
            "valid_until": (timezone.localdate() + timedelta(days=40)).isoformat(),
            "status": CertificateStatus.ACTIVE,
        },
    )

    assert response.status_code == 200
    assert "Informe o telefone usado no aviso simulado" in response.content.decode()
    assert not DigitalCertificate.objects.exists()


def test_manual_execution_runs_the_real_use_case_and_shows_evidence(
    client: Client,
    processes_operator: User,
    modules: dict[str, AutomationModule],
) -> None:
    _certificate()
    client.force_login(processes_operator)

    response = client.post(_module_url(modules), {"action": "execute"})

    run = AutomationRun.objects.get()
    assert response.status_code == 302
    assert response.url == reverse("automations:run-detail", kwargs={"run_id": run.id})
    assert run.status == RunStatus.SUCCEEDED
    assert CommunicationAttempt.objects.count() == 1

    detail = client.get(response.url)
    html = detail.content.decode()
    assert detail.status_code == 200
    assert "Tentativas desta execução" in html
    assert "Cliente da Interface" in html
    assert "Entrega simulada registrada" in html


def test_failed_attempt_can_be_retried_from_the_module_page(
    client: Client,
    processes_operator: User,
    modules: dict[str, AutomationModule],
) -> None:
    certificate = _certificate(email="falha@avisos.invalid")
    client.force_login(processes_operator)
    client.post(_module_url(modules), {"action": "execute"})
    failed_attempt = CommunicationAttempt.objects.get()
    communication = CertificateCommunication.objects.get()
    assert failed_attempt.status == CommunicationStatus.FAILED

    certificate.contact_email = "recuperado@example.test"
    certificate.save(update_fields=("contact_email", "updated_at"))
    response = client.post(
        _module_url(modules),
        {"action": "retry", "attempt_id": str(failed_attempt.id)},
    )

    communication.refresh_from_db()
    retry_run = AutomationRun.objects.exclude(pk=failed_attempt.run_id).get()
    assert response.status_code == 302
    assert response.url == reverse(
        "automations:run-detail",
        kwargs={"run_id": retry_run.id},
    )
    assert communication.status == CommunicationStatus.SENT
    assert retry_run.status == RunStatus.SUCCEEDED
    assert CommunicationAttempt.objects.count() == 2

    page = client.get(_module_url(modules)).content.decode()
    assert "Superado" in page
    assert "Tentar novamente" not in page


def test_sc20_certificates_filters_and_pagination(
    client: Client,
    processes_operator: User,
    modules: dict[str, AutomationModule],
) -> None:
    for i in range(10):
        DigitalCertificate.objects.create(
            serial_number=f"CERT-PAGE-{i + 1:02d}",
            client_name="Cliente Exclusivo" if i == 0 else f"Cliente Cert {i + 1:02d}",
            client_document=f"900000000{i:02d}",
            responsible_name=f"Responsavel {i + 1}",
            contact_email=f"cert{i}@example.test",
            preferred_channel=CommunicationChannel.EMAIL,
            valid_until=timezone.localdate() + timedelta(days=20 if i < 5 else 120),
            status=CertificateStatus.ACTIVE if i < 8 else CertificateStatus.REVOKED,
        )

    client.force_login(processes_operator)
    url = _module_url(modules)

    # 1. Sem filtros: 7 itens na página 1, 2 páginas no total
    response = client.get(url)
    assert response.status_code == 200
    assert response.context["certificates_paginator"].num_pages == 2
    assert len(response.context["certificates"]) == 7
    assert 'id="sc20-certificates-region"' in response.content.decode()
    assert "Limpar" not in response.content.decode()

    # 2. Página 2: 3 itens restantes
    response_p2 = client.get(f"{url}?page=2")
    assert response_p2.status_code == 200
    assert len(response_p2.context["certificates"]) == 3

    # 3. Filtrar por busca textual de nome único
    response_search = client.get(f"{url}?q=Exclusivo")
    assert response_search.status_code == 200
    assert len(response_search.context["certificates"]) == 1
    assert "Limpar" in response_search.content.decode()

    # 4. Filtrar por status vencendo em 60 dias
    response_expiring = client.get(f"{url}?status=expiring")
    assert response_expiring.status_code == 200
    assert len(response_expiring.context["certificates"]) == 5
    assert "Limpar" in response_expiring.content.decode()


def test_sc20_certificates_sorting_and_whitelist_security(
    client: Client,
    processes_operator: User,
    modules: dict[str, AutomationModule],
) -> None:
    client.force_login(processes_operator)
    url = _module_url(modules)

    DigitalCertificate.objects.create(
        serial_number="CERT-SORT-01",
        client_name="Zeta Transportes",
        client_document="11111111000101",
        responsible_name="Responsavel Zeta",
        contact_email="zeta@example.test",
        preferred_channel=CommunicationChannel.EMAIL,
        valid_until=timezone.localdate() + timedelta(days=50),
        status=CertificateStatus.ACTIVE,
    )
    DigitalCertificate.objects.create(
        serial_number="CERT-SORT-02",
        client_name="Alpha Tecnologia",
        client_document="22222222000102",
        responsible_name="Responsavel Alpha",
        contact_email="alpha@example.test",
        preferred_channel=CommunicationChannel.EMAIL,
        valid_until=timezone.localdate() + timedelta(days=10),
        status=CertificateStatus.ACTIVE,
    )

    # 1. Sem sort: natural, cabeçalhos neutros com aria-sort="none"
    resp_nat = client.get(url)
    assert resp_nat.status_code == 200
    html_nat = resp_nat.content.decode()
    assert 'aria-sort="none"' in html_nat
    assert 'hx-target="#sc20-certificates-region"' in html_nat

    # 2. Sort por cliente ASC: Alpha antes de Zeta
    resp_asc = client.get(f"{url}?sort=client")
    assert resp_asc.status_code == 200
    html_asc = resp_asc.content.decode()
    assert 'aria-sort="ascending"' in html_asc
    pos_alpha = html_asc.find("Alpha Tecnologia")
    pos_zeta = html_asc.find("Zeta Transportes")
    assert pos_alpha != -1 and pos_zeta != -1
    assert pos_alpha < pos_zeta

    # 3. Sort por cliente DESC: Zeta antes de Alpha
    resp_desc = client.get(f"{url}?sort=-client")
    assert resp_desc.status_code == 200
    html_desc = resp_desc.content.decode()
    assert 'aria-sort="descending"' in html_desc
    pos_alpha = html_desc.find("Alpha Tecnologia")
    pos_zeta = html_desc.find("Zeta Transportes")
    assert pos_zeta != -1 and pos_alpha != -1
    assert pos_zeta < pos_alpha

    # 4. Whitelist fallback: campo inválido não quebra a página
    resp_invalid = client.get(f"{url}?sort=invalid_column")
    assert resp_invalid.status_code == 200
    assert resp_invalid.context["current_sort"] == ""


def test_sc20_filter_toolbar_alignment_structure(
    client: Client,
    processes_operator: User,
    modules: dict[str, AutomationModule],
) -> None:
    _certificate()
    client.force_login(processes_operator)
    url = _module_url(modules)

    # 1. Sem filtro ativo: formulário com sm:flex-nowrap, shrink-0 e título com min-w-0 flex-1
    response = client.get(url)
    assert response.status_code == 200
    html = response.content.decode()
    assert 'class="min-w-0 flex-1"' in html
    assert "sm:flex-nowrap" in html
    assert "shrink-0" in html
    assert '<div class="flex items-center gap-1.5 shrink-0">' in html
    assert "novalidate" in html

    # 2. Com filtro ativo: botão Limpar agrupado junto a Filtrar
    resp_filtered = client.get(f"{url}?q=Cliente")
    assert resp_filtered.status_code == 200
    html_filtered = resp_filtered.content.decode()
    assert "Limpar" in html_filtered
    assert "Filtrar" in html_filtered
    assert '<div class="flex items-center gap-1.5 shrink-0">' in html_filtered
