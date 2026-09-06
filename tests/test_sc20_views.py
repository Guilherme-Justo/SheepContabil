from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

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
    assert 'data-mask="phone"' in html
    assert 'inputmode="tel"' in html


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
    assert "sc20-email-link" in page
    assert "mailto:recuperado@example.test?" in page


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

    # 1. Sem filtros: 5 itens na página 1, 2 páginas no total
    response = client.get(url)
    assert response.status_code == 200
    assert response.context["certificates_paginator"].num_pages == 2
    assert len(response.context["certificates"]) == 5
    assert 'id="sc20-certificates-region"' in response.content.decode()
    assert "Limpar" not in response.content.decode()

    # 2. Página 2: 5 itens restantes
    response_p2 = client.get(f"{url}?page=2")
    assert response_p2.status_code == 200
    assert len(response_p2.context["certificates"]) == 5

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

    # 4. Sort por contato (responsible_name) ASC e DESC
    resp_contact_asc = client.get(f"{url}?sort=contact")
    assert resp_contact_asc.status_code == 200
    html_c_asc = resp_contact_asc.content.decode()
    assert 'aria-sort="ascending"' in html_c_asc
    pos_c_alpha = html_c_asc.find("Responsavel Alpha")
    pos_c_zeta = html_c_asc.find("Responsavel Zeta")
    assert pos_c_alpha != -1 and pos_c_zeta != -1
    assert pos_c_alpha < pos_c_zeta

    resp_contact_desc = client.get(f"{url}?sort=-contact")
    assert resp_contact_desc.status_code == 200
    html_c_desc = resp_contact_desc.content.decode()
    assert 'aria-sort="descending"' in html_c_desc
    pos_c_alpha = html_c_desc.find("Responsavel Alpha")
    pos_c_zeta = html_c_desc.find("Responsavel Zeta")
    assert pos_c_zeta != -1 and pos_c_alpha != -1
    assert pos_c_zeta < pos_c_alpha

    # 5. Sort por prazo (days_remaining) ASC e DESC
    resp_days_asc = client.get(f"{url}?sort=days_remaining")
    assert resp_days_asc.status_code == 200
    html_d_asc = resp_days_asc.content.decode()
    assert 'aria-sort="ascending"' in html_d_asc
    pos_d_alpha = html_d_asc.find("Alpha Tecnologia")
    pos_d_zeta = html_d_asc.find("Zeta Transportes")
    assert pos_d_alpha != -1 and pos_d_zeta != -1
    assert pos_d_alpha < pos_d_zeta

    resp_days_desc = client.get(f"{url}?sort=-days_remaining")
    assert resp_days_desc.status_code == 200
    html_d_desc = resp_days_desc.content.decode()
    assert 'aria-sort="descending"' in html_d_desc
    pos_d_alpha = html_d_desc.find("Alpha Tecnologia")
    pos_d_zeta = html_d_desc.find("Zeta Transportes")
    assert pos_d_zeta != -1 and pos_d_alpha != -1
    assert pos_d_zeta < pos_d_alpha

    # 6. Whitelist fallback: campo inválido não quebra a página
    resp_invalid = client.get(f"{url}?sort=invalid_column")
    assert resp_invalid.status_code == 200
    assert resp_invalid.context["current_sort"] == ""


def test_sc20_attempts_sorting_and_isolation(
    client: Client,
    processes_operator: User,
    modules: dict[str, AutomationModule],
) -> None:
    client.force_login(processes_operator)
    url = _module_url(modules)
    cert = _certificate()
    run = AutomationRun.objects.create(
        module=modules["SC-20"],
        trigger="manual",
        status=RunStatus.SUCCEEDED,
        triggered_by=processes_operator,
    )
    comm = CertificateCommunication.objects.create(
        certificate=cert,
        certificate_valid_until=cert.valid_until,
        channel=CommunicationChannel.EMAIL,
        policy_key="p1",
        recipient="alpha@example.test",
        status=CommunicationStatus.SENT,
        first_run=run,
        latest_run=run,
    )
    CommunicationAttempt.objects.create(
        communication=comm,
        run=run,
        sequence=1,
        status=CommunicationStatus.SENT,
        recipient="alpha@example.test",
    )

    comm2 = CertificateCommunication.objects.create(
        certificate=cert,
        certificate_valid_until=cert.valid_until,
        channel=CommunicationChannel.WHATSAPP,
        policy_key="p2",
        recipient="zeta@example.test",
        status=CommunicationStatus.FAILED,
        first_run=run,
        latest_run=run,
    )
    CommunicationAttempt.objects.create(
        communication=comm2,
        run=run,
        sequence=1,
        status=CommunicationStatus.FAILED,
        recipient="zeta@example.test",
    )

    # 1. Sem sort de avisos: neutro
    resp_default = client.get(url)
    assert resp_default.status_code == 200
    html_def = resp_default.content.decode()
    assert 'hx-target="#sc20-attempts-region"' in html_def
    assert "attempts_sort=recipient" in html_def

    # 2. Sort por recipient ASC
    resp_rec_asc = client.get(f"{url}?attempts_sort=recipient")
    assert resp_rec_asc.status_code == 200
    html_rec_asc = resp_rec_asc.content.decode()
    pos_alpha = html_rec_asc.find("alpha@example.test")
    pos_zeta = html_rec_asc.find("zeta@example.test")
    assert pos_alpha != -1 and pos_zeta != -1
    assert pos_alpha < pos_zeta
    # Isolamento: não deve afetar a tabela principal
    assert resp_rec_asc.context["current_sort"] == ""
    assert resp_rec_asc.context["attempts_current_sort"] == "recipient"

    # 3. Sort por recipient DESC
    resp_rec_desc = client.get(f"{url}?attempts_sort=-recipient")
    assert resp_rec_desc.status_code == 200
    html_rec_desc = resp_rec_desc.content.decode()
    pos_alpha = html_rec_desc.find("alpha@example.test")
    pos_zeta = html_rec_desc.find("zeta@example.test")
    assert pos_zeta < pos_alpha
    assert resp_rec_desc.context["attempts_current_sort"] == "-recipient"

    # 4. Fallback de whitelist
    resp_inv = client.get(f"{url}?attempts_sort=malicious_col")
    assert resp_inv.status_code == 200
    assert resp_inv.context["attempts_current_sort"] == ""


def test_sc20_runs_sorting_and_isolation(
    client: Client,
    processes_operator: User,
    modules: dict[str, AutomationModule],
) -> None:
    client.force_login(processes_operator)
    url = _module_url(modules)

    AutomationRun.objects.create(
        module=modules["SC-20"],
        trigger="manual",
        status=RunStatus.SUCCEEDED,
        triggered_by=processes_operator,
    )
    AutomationRun.objects.create(
        module=modules["SC-20"],
        trigger="scheduled",
        status=RunStatus.FAILED,
        triggered_by=processes_operator,
    )

    # 1. Sem sort de runs: neutro
    resp_default = client.get(url)
    assert resp_default.status_code == 200
    html_def = resp_default.content.decode()
    assert 'hx-target="#sc20-runs-region"' in html_def
    assert "runs_sort=trigger" in html_def

    # 2. Sort por trigger ASC (manual antes de scheduled)
    resp_trig_asc = client.get(f"{url}?runs_sort=trigger")
    assert resp_trig_asc.status_code == 200
    html_trig_asc = resp_trig_asc.content.decode()
    pos_man = html_trig_asc.find("Manual")
    pos_sch = html_trig_asc.find("Agendado")
    assert pos_man != -1 and pos_sch != -1
    assert pos_man < pos_sch
    assert resp_trig_asc.context["runs_current_sort"] == "trigger"
    assert resp_trig_asc.context["current_sort"] == ""
    assert resp_trig_asc.context["attempts_current_sort"] == ""

    # 3. Sort por trigger DESC (scheduled antes de manual)
    resp_trig_desc = client.get(f"{url}?runs_sort=-trigger")
    assert resp_trig_desc.status_code == 200
    html_trig_desc = resp_trig_desc.content.decode()
    pos_man = html_trig_desc.find("Manual")
    pos_sch = html_trig_desc.find("Agendado")
    assert pos_sch < pos_man
    assert resp_trig_desc.context["runs_current_sort"] == "-trigger"

    # 4. Fallback de whitelist
    resp_inv = client.get(f"{url}?runs_sort=invalid_col")
    assert resp_inv.status_code == 200
    assert resp_inv.context["runs_current_sort"] == ""


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


def test_certificate_form_phone_validation() -> None:
    from core.automations.forms import DigitalCertificateForm

    # Telefone com 11 dígitos celulares válido
    form = DigitalCertificateForm(
        data={
            "serial_number": "CERT-PHONE-01",
            "client_name": "Empresa Tel 1",
            "client_document": "12345678000190",
            "responsible_name": "Gestor",
            "contact_email": "tel1@example.test",
            "contact_phone": "61991365756",
            "preferred_channel": CommunicationChannel.WHATSAPP,
            "valid_until": "2026-10-01",
            "status": CertificateStatus.ACTIVE,
        }
    )
    assert form.is_valid(), form.errors
    assert form.cleaned_data["contact_phone"] == "+55 (61) 99136-5756"

    # Telefone com DDI +55
    form_ddi = DigitalCertificateForm(
        data={
            "serial_number": "CERT-PHONE-02",
            "client_name": "Empresa Tel 2",
            "client_document": "12345678000190",
            "responsible_name": "Gestor",
            "contact_email": "tel2@example.test",
            "contact_phone": "+55 (61) 99136-5756",
            "preferred_channel": CommunicationChannel.WHATSAPP,
            "valid_until": "2026-10-01",
            "status": CertificateStatus.ACTIVE,
        }
    )
    assert form_ddi.is_valid(), form_ddi.errors
    assert form_ddi.cleaned_data["contact_phone"] == "+55 (61) 99136-5756"

    # Telefone com repetição total
    form_rep = DigitalCertificateForm(
        data={
            "serial_number": "CERT-PHONE-03",
            "client_name": "Empresa Tel 3",
            "client_document": "12345678000190",
            "responsible_name": "Gestor",
            "contact_phone": "00000000000",
            "preferred_channel": CommunicationChannel.WHATSAPP,
            "valid_until": "2026-10-01",
            "status": CertificateStatus.ACTIVE,
        }
    )
    assert not form_rep.is_valid()
    assert "contact_phone" in form_rep.errors

    # Telefone com DDD inválido (ex: 00)
    form_ddd = DigitalCertificateForm(
        data={
            "serial_number": "CERT-PHONE-04",
            "client_name": "Empresa Tel 4",
            "client_document": "12345678000190",
            "responsible_name": "Gestor",
            "contact_phone": "00991365756",
            "preferred_channel": CommunicationChannel.WHATSAPP,
            "valid_until": "2026-10-01",
            "status": CertificateStatus.ACTIVE,
        }
    )
    assert not form_ddd.is_valid()
    assert "contact_phone" in form_ddd.errors


def test_certificate_form_document_mask_and_email_normalization(
    client: Client,
    processes_operator: User,
    modules: dict[str, AutomationModule],
) -> None:
    client.force_login(processes_operator)
    url = _module_url(modules)

    resp = client.get(url)
    assert resp.status_code == 200
    html = resp.content.decode()
    assert 'data-mask="document"' in html
    assert 'id="sc20-certificate-form-card"' in html
    assert "hx-post=" in html
    assert 'name="phone_country"' in html
    assert "sc20-phone-group" in html
    assert "Brasil (+55)" in html
    assert "Portugal (+351)" in html

    # Submissão com e-mail em maiúsculas normalizado para minúsculas
    resp_post = client.post(
        url,
        {
            "action": "create_certificate",
            "serial_number": "CERT-EMAIL-NORM",
            "client_name": "Empresa Email Norm",
            "client_document": "12.345.678/0001-90",
            "responsible_name": "Responsável",
            "contact_email": " UPPERCASE@EXAMPLE.TEST ",
            "contact_phone": "",
            "preferred_channel": CommunicationChannel.EMAIL,
            "valid_until": (timezone.localdate() + timedelta(days=40)).isoformat(),
            "status": CertificateStatus.ACTIVE,
        },
    )
    assert resp_post.status_code == 302
    cert = DigitalCertificate.objects.get(serial_number="CERT-EMAIL-NORM")
    assert cert.contact_email == "uppercase@example.test"


def test_create_certificate_with_htmx_returns_redirect_header(
    client: Client,
    processes_operator: User,
    modules: dict[str, AutomationModule],
) -> None:
    client.force_login(processes_operator)
    url = _module_url(modules)

    response = client.post(
        url,
        {
            "action": "create_certificate",
            "serial_number": "CERT-HTMX-01",
            "client_name": "Empresa HTMX",
            "client_document": "12.345.678/0001-90",
            "responsible_name": "Responsável",
            "contact_email": "htmx@example.test",
            "contact_phone": "",
            "preferred_channel": CommunicationChannel.EMAIL,
            "valid_until": (timezone.localdate() + timedelta(days=40)).isoformat(),
            "status": CertificateStatus.ACTIVE,
        },
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200
    assert response.headers.get("HX-Redirect") == url
    assert DigitalCertificate.objects.filter(serial_number="CERT-HTMX-01").exists()


def test_certificate_form_country_ddi_selector_brazil_and_international() -> None:
    from core.automations.forms import DigitalCertificateForm

    # Brasil com número local de 11 dígitos
    form_br = DigitalCertificateForm(
        data={
            "phone_country": "55",
            "serial_number": "CERT-DDI-01",
            "client_name": "Empresa DDI BR",
            "client_document": "12345678000190",
            "responsible_name": "Gestor",
            "contact_phone": "(11) 98888-7777",
            "preferred_channel": CommunicationChannel.WHATSAPP,
            "valid_until": "2026-10-01",
            "status": CertificateStatus.ACTIVE,
        }
    )
    assert form_br.is_valid(), form_br.errors
    assert form_br.cleaned_data["contact_phone"] == "+55 (11) 98888-7777"

    # Brasil com número fixo de 10 dígitos
    form_br_landline = DigitalCertificateForm(
        data={
            "phone_country": "55",
            "serial_number": "CERT-DDI-02",
            "client_name": "Empresa Fixo BR",
            "client_document": "12345678000190",
            "responsible_name": "Gestor",
            "contact_phone": "1133334444",
            "preferred_channel": CommunicationChannel.WHATSAPP,
            "valid_until": "2026-10-01",
            "status": CertificateStatus.ACTIVE,
        }
    )
    assert form_br_landline.is_valid(), form_br_landline.errors
    assert form_br_landline.cleaned_data["contact_phone"] == "+55 (11) 3333-4444"

    # EUA (+1)
    form_us = DigitalCertificateForm(
        data={
            "phone_country": "1",
            "serial_number": "CERT-DDI-US",
            "client_name": "Empresa DDI US",
            "client_document": "12345678000190",
            "responsible_name": "Gestor",
            "contact_phone": "(202) 555-0199",
            "preferred_channel": CommunicationChannel.WHATSAPP,
            "valid_until": "2026-10-01",
            "status": CertificateStatus.ACTIVE,
        }
    )
    assert form_us.is_valid(), form_us.errors
    assert form_us.cleaned_data["contact_phone"] == "+1 2025550199"

    # Portugal (+351) sem duplicar código
    form_pt = DigitalCertificateForm(
        data={
            "phone_country": "351",
            "serial_number": "CERT-DDI-PT",
            "client_name": "Empresa DDI PT",
            "client_document": "12345678000190",
            "responsible_name": "Gestor",
            "contact_phone": "+351 912 345 678",
            "preferred_channel": CommunicationChannel.WHATSAPP,
            "valid_until": "2026-10-01",
            "status": CertificateStatus.ACTIVE,
        }
    )
    assert form_pt.is_valid(), form_pt.errors
    assert form_pt.cleaned_data["contact_phone"] == "+351 912345678"


def test_certificate_form_country_ddi_validation_errors() -> None:
    from core.automations.forms import DigitalCertificateForm

    # País inválido
    form_invalid_country = DigitalCertificateForm(
        data={
            "phone_country": "999",
            "serial_number": "CERT-ERR-01",
            "client_name": "Empresa Erro País",
            "client_document": "12345678000190",
            "responsible_name": "Gestor",
            "contact_phone": "11988887777",
            "preferred_channel": CommunicationChannel.WHATSAPP,
            "valid_until": "2026-10-01",
            "status": CertificateStatus.ACTIVE,
        }
    )
    assert not form_invalid_country.is_valid()
    assert "contact_phone" in form_invalid_country.errors

    # DDI incompatível (Brasil selecionado mas digitou +1)
    form_mismatch = DigitalCertificateForm(
        data={
            "phone_country": "55",
            "serial_number": "CERT-ERR-02",
            "client_name": "Empresa Erro DDI",
            "client_document": "12345678000190",
            "responsible_name": "Gestor",
            "contact_phone": "+1 (202) 555-0199",
            "preferred_channel": CommunicationChannel.WHATSAPP,
            "valid_until": "2026-10-01",
            "status": CertificateStatus.ACTIVE,
        }
    )
    assert not form_mismatch.is_valid()
    assert "contact_phone" in form_mismatch.errors

    # Número internacional curto demais
    form_short = DigitalCertificateForm(
        data={
            "phone_country": "1",
            "serial_number": "CERT-ERR-03",
            "client_name": "Empresa Curto",
            "client_document": "12345678000190",
            "responsible_name": "Gestor",
            "contact_phone": "12345",
            "preferred_channel": CommunicationChannel.WHATSAPP,
            "valid_until": "2026-10-01",
            "status": CertificateStatus.ACTIVE,
        }
    )
    assert not form_short.is_valid()
    assert "contact_phone" in form_short.errors


def test_certificate_form_initial_country_from_instance() -> None:
    from core.automations.forms import DigitalCertificateForm

    cert_pt = DigitalCertificate(
        id=uuid4(),
        serial_number="CERT-INST-PT",
        client_name="Cliente Portugal",
        client_document="12345678000190",
        responsible_name="António",
        contact_phone="+351 912345678",
        valid_until=timezone.localdate() + timedelta(days=30),
    )
    form = DigitalCertificateForm(instance=cert_pt)
    assert form.fields["phone_country"].initial == "351"

    cert_br = DigitalCertificate(
        id=uuid4(),
        serial_number="CERT-INST-BR",
        client_name="Cliente Brasil",
        client_document="12345678000190",
        responsible_name="Ana",
        contact_phone="+55 (11) 98888-7777",
        valid_until=timezone.localdate() + timedelta(days=30),
    )
    form_br = DigitalCertificateForm(instance=cert_br)
    assert form_br.fields["phone_country"].initial == "55"


def test_create_certificate_with_international_country_persists_e164(
    client: Client,
    processes_operator: User,
    modules: dict[str, AutomationModule],
) -> None:
    client.force_login(processes_operator)
    url = _module_url(modules)

    resp = client.post(
        url,
        {
            "action": "create_certificate",
            "phone_country": "351",
            "serial_number": "CERT-INTL-POST",
            "client_name": "Lisboa Digital Ltda",
            "client_document": "12.345.678/0001-90",
            "responsible_name": "Rui Silva",
            "contact_email": "rui@lisboa.test",
            "contact_phone": "912 345 678",
            "preferred_channel": CommunicationChannel.WHATSAPP,
            "valid_until": (timezone.localdate() + timedelta(days=25)).isoformat(),
            "status": CertificateStatus.ACTIVE,
        },
    )
    assert resp.status_code == 302
    cert = DigitalCertificate.objects.get(serial_number="CERT-INTL-POST")
    assert cert.contact_phone == "+351 912345678"
