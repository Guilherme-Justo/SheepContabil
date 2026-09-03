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
