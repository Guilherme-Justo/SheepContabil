from __future__ import annotations

import urllib.parse
from datetime import timedelta

import pytest
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from core.automations.models import (
    AutomationModule,
    CertificateCommunication,
    CertificateStatus,
    CommunicationAttempt,
    CommunicationChannel,
    CommunicationStatus,
    DigitalCertificate,
    format_phone,
)
from core.automations.sc20.services import create_sc20_run
from core.identity.models import User

pytestmark = pytest.mark.django_db


def _cert_whatsapp(
    *,
    serial: str = "WPP-01",
    phone: str = "+55 (11) 99999-2002",
    days: int = 15,
) -> DigitalCertificate:
    return DigitalCertificate.objects.create(
        serial_number=serial,
        client_name="Aurora Serviços Fictícios",
        client_document="12.345.678/0001-90",
        responsible_name="Ana Paula",
        contact_email="ana@aurora.example.test",
        contact_phone=phone,
        preferred_channel=CommunicationChannel.WHATSAPP,
        valid_until=timezone.localdate() + timedelta(days=days),
        status=CertificateStatus.ACTIVE,
    )


def test_certificate_has_whatsapp_flag() -> None:
    with_phone = _cert_whatsapp(serial="WPP-PHONE", phone="+55 11 99999-0000")
    without_phone = DigitalCertificate.objects.create(
        serial_number="NO-PHONE",
        client_name="Sem Telefone Ltda.",
        client_document="12345678000190",
        responsible_name="Gestor",
        contact_email="sem@telefone.local",
        contact_phone="",
        preferred_channel=CommunicationChannel.EMAIL,
        valid_until=timezone.localdate() + timedelta(days=10),
        status=CertificateStatus.ACTIVE,
    )

    assert with_phone.has_whatsapp is True
    assert without_phone.has_whatsapp is False


def test_certificate_whatsapp_url_normalization_and_message_content() -> None:
    cert = _cert_whatsapp(serial="WPP-NORM", phone="(11) 98888-7777", days=14)

    url = cert.whatsapp_url()

    # Normalizou 11 dígitos acrescentando DDI 55
    assert url.startswith("https://api.whatsapp.com/send?phone=5511988887777&text=")

    # Decodifica o texto para validar conteúdo limpo Clean B2B sem emojis de 4 bytes
    encoded_text = url.split("&text=")[1]
    decoded = urllib.parse.unquote(encoded_text)

    # Garante ausência dos emojis que causam corrupção e substituição por
    assert "🔔" not in decoded
    assert "📋" not in decoded
    assert "💡" not in decoded
    assert "\ufffd" not in decoded

    # Cabeçalhos executivos corporativos
    assert "*SheepContabil · Monitoramento de Certificados Digitais*" in decoded
    assert "*Dados do Certificado:*" in decoded
    assert "*Orientação para Renovação:*" in decoded
    assert "• Empresa: *Aurora Serviços Fictícios*" in decoded
    assert "• Documento: 12.345.678/0001-90" in decoded
    assert "Ana Paula" in decoded
    assert "WPP-NORM" in decoded
    assert "Vencimento Crítico" in decoded
    assert "Autoridade Certificadora" in decoded


def test_certificate_whatsapp_url_sandbox_override() -> None:
    cert = _cert_whatsapp(serial="WPP-SANDBOX", phone="+55 11 99999-2002", days=25)

    with override_settings(SC20_WHATSAPP_OVERRIDE_TO="5521977776666"):
        url = cert.whatsapp_url()

    # Destinatário do link foi redirecionado para o número do sandbox
    assert url.startswith("https://api.whatsapp.com/send?phone=5521977776666&text=")

    encoded_text = url.split("&text=")[1]
    decoded = urllib.parse.unquote(encoded_text)

    assert "AMBIENTE DE TESTE SHEEPCONTABIL" in decoded
    assert "Destinatário original: +55 11 99999-2002" in decoded
    assert "Aurora Serviços Fictícios" in decoded


def test_communication_attempt_whatsapp_properties(
    modules: dict[str, AutomationModule],
) -> None:
    cert = _cert_whatsapp(serial="WPP-ATTEMPT", phone="11999998888", days=10)
    run = create_sc20_run(triggered_by=None, base_date=timezone.localdate())

    comm = CertificateCommunication.objects.create(
        certificate=cert,
        certificate_valid_until=cert.valid_until,
        channel=CommunicationChannel.WHATSAPP,
        recipient=cert.contact_phone,
        policy_key="sc20-60-days-v1",
        first_run=run,
        latest_run=run,
    )
    attempt = CommunicationAttempt.objects.create(
        communication=comm,
        run=run,
        sequence=1,
        status=CommunicationStatus.SENT,
        recipient=cert.contact_phone,
        provider_message_id="sim-12345",
        payload={"synthetic": True},
    )

    assert attempt.is_whatsapp is True
    assert attempt.whatsapp_url.startswith("https://api.whatsapp.com/send?phone=5511999998888")


def test_sc20_page_renders_whatsapp_button(
    client: Client,
    processes_operator: User,
    modules: dict[str, AutomationModule],
) -> None:
    _cert_whatsapp(serial="WPP-UI-TEST", phone="+55 11 99999-2002", days=10)
    client.force_login(processes_operator)

    url = reverse("automations:module-detail", kwargs={"slug": modules["SC-20"].slug})
    response = client.get(url)

    assert response.status_code == 200
    html = response.content.decode()

    # O link de WhatsApp deve estar presente no número de telefone na tabela de certificados
    assert "sc20-contact-whatsapp" in html
    assert "sc20-contact-link" in html
    assert "https://api.whatsapp.com/send?phone=5511999992002" in html
    assert 'target="_blank"' in html
    assert "+55 (11) 99999-2002" in html
    assert "sc20-wpp-icon" in html
    assert "💬" not in html


def test_run_detail_page_renders_whatsapp_link_for_whatsapp_attempt(
    client: Client,
    processes_operator: User,
    modules: dict[str, AutomationModule],
) -> None:
    cert = _cert_whatsapp(serial="WPP-RUN-TEST", phone="+55 11 99999-2002", days=10)
    run = create_sc20_run(triggered_by=processes_operator, base_date=timezone.localdate())

    comm = CertificateCommunication.objects.create(
        certificate=cert,
        certificate_valid_until=cert.valid_until,
        channel=CommunicationChannel.WHATSAPP,
        recipient=cert.contact_phone,
        policy_key="sc20-60-days-v1",
        first_run=run,
        latest_run=run,
    )
    CommunicationAttempt.objects.create(
        communication=comm,
        run=run,
        sequence=1,
        status=CommunicationStatus.SENT,
        recipient=cert.contact_phone,
        provider_message_id="sim-wpp-001",
        payload={"synthetic": True, "backend": "whatsapp"},
    )

    client.force_login(processes_operator)
    detail_url = reverse("automations:run-detail", kwargs={"run_id": run.id})
    response = client.get(detail_url)

    assert response.status_code == 200
    html = response.content.decode()

    # Na tabela de tentativas da execução deve haver o botão de WhatsApp e telefone formatado
    assert "https://api.whatsapp.com/send?phone=5511999992002" in html
    assert "sc20-whatsapp-pill" in html
    assert "+55 (11) 99999-2002" in html
    assert "sc20-wpp-icon" in html
    assert "💬" not in html


def test_certificate_whatsapp_url_formats_unformatted_document() -> None:
    cert = _cert_whatsapp(serial="WPP-DOC-FMT", phone="+55 11 99999-1111", days=10)
    cert.client_document = "11222333000181"
    cert.save()

    url = cert.whatsapp_url()
    decoded = urllib.parse.unquote_plus(url)
    assert "• Documento: 11.222.333/0001-81" in decoded

    cert.client_document = "12345678901"
    cert.save()
    url_cpf = cert.whatsapp_url()
    decoded_cpf = urllib.parse.unquote_plus(url_cpf)
    assert "• Documento: 123.456.789-01" in decoded_cpf


def test_certificate_whatsapp_url_international_numbers() -> None:
    cert_us = _cert_whatsapp(serial="WPP-US", phone="+1 202 555-0199", days=10)
    url_us = cert_us.whatsapp_url()
    assert url_us.startswith("https://api.whatsapp.com/send?phone=12025550199&text=")

    cert_pt = _cert_whatsapp(serial="WPP-PT", phone="+351 912 345 678", days=10)
    url_pt = cert_pt.whatsapp_url()
    assert url_pt.startswith("https://api.whatsapp.com/send?phone=351912345678&text=")


def test_format_phone_helper() -> None:
    # Celular nacional com DDI
    assert format_phone("5561991365756") == "+55 (61) 99136-5756"
    assert format_phone("+5561991365756") == "+55 (61) 99136-5756"
    assert format_phone("+55 (61) 99136-5756") == "+55 (61) 99136-5756"

    # Celular nacional sem DDI
    assert format_phone("61991365756") == "+55 (61) 99136-5756"

    # Fixo nacional com e sem DDI
    assert format_phone("1133334444") == "+55 (11) 3333-4444"
    assert format_phone("551133334444") == "+55 (11) 3333-4444"

    # Internacional
    assert format_phone("+351912345678") == "+351 912345678"
    assert format_phone("+351 912 345 678") == "+351 912 345 678"
    assert format_phone("+1 202 555-0199") == "+1 202 555-0199"

    # Vazios e None
    assert format_phone("") == ""
    assert format_phone(None) == ""


def test_digital_certificate_formatted_phone_property() -> None:
    cert = _cert_whatsapp(serial="WPP-FMT-PROP", phone="+5561991365756", days=10)
    assert cert.formatted_phone == "+55 (61) 99136-5756"

    cert_clean = _cert_whatsapp(serial="WPP-FMT-CLEAN", phone="11988887777", days=10)
    assert cert_clean.formatted_phone == "+55 (11) 98888-7777"


def test_communication_attempt_formatted_recipient_property(
    modules: dict[str, AutomationModule],
) -> None:
    cert = _cert_whatsapp(serial="WPP-ATTEMPT-FMT", phone="5561991365756", days=10)
    run = create_sc20_run(triggered_by=None, base_date=timezone.localdate())

    comm = CertificateCommunication.objects.create(
        certificate=cert,
        certificate_valid_until=cert.valid_until,
        channel=CommunicationChannel.WHATSAPP,
        recipient=cert.contact_phone,
        policy_key="sc20-60-days-v1",
        first_run=run,
        latest_run=run,
    )
    attempt_wpp = CommunicationAttempt.objects.create(
        communication=comm,
        run=run,
        sequence=1,
        status=CommunicationStatus.SENT,
        recipient="5561991365756",
        provider_message_id="sim-fmt-001",
        payload={"synthetic": True},
    )
    assert attempt_wpp.formatted_recipient == "+55 (61) 99136-5756"

    # E-mail permanece intocado
    comm_email = CertificateCommunication.objects.create(
        certificate=cert,
        certificate_valid_until=cert.valid_until,
        channel=CommunicationChannel.EMAIL,
        recipient="contato@empresa.example.test",
        policy_key="sc20-60-days-v1",
        first_run=run,
        latest_run=run,
    )
    attempt_email = CommunicationAttempt.objects.create(
        communication=comm_email,
        run=run,
        sequence=1,
        status=CommunicationStatus.SENT,
        recipient="contato@empresa.example.test",
        provider_message_id="sim-email-001",
        payload={"synthetic": True},
    )
    assert attempt_email.formatted_recipient == "contato@empresa.example.test"


def test_sc20_page_renders_dual_contacts_and_preferred_pill(
    client: Client,
    processes_operator: User,
    modules: dict[str, AutomationModule],
) -> None:
    DigitalCertificate.objects.create(
        serial_number="DUAL-EMAIL-PREF",
        client_name="Cliente Dual Email Pref",
        client_document="12345678000101",
        responsible_name="Responsável 1",
        contact_email="dual_email@empresa.example.test",
        contact_phone="+55 11 91111-2222",
        preferred_channel=CommunicationChannel.EMAIL,
        valid_until=timezone.localdate() + timedelta(days=15),
        status=CertificateStatus.ACTIVE,
    )
    DigitalCertificate.objects.create(
        serial_number="DUAL-WPP-PREF",
        client_name="Cliente Dual WhatsApp Pref",
        client_document="12345678000102",
        responsible_name="Responsável 2",
        contact_email="dual_wpp@empresa.example.test",
        contact_phone="+55 11 93333-4444",
        preferred_channel=CommunicationChannel.WHATSAPP,
        valid_until=timezone.localdate() + timedelta(days=15),
        status=CertificateStatus.ACTIVE,
    )

    client.force_login(processes_operator)
    url = reverse("automations:module-detail", kwargs={"slug": modules["SC-20"].slug})
    response = client.get(url)
    assert response.status_code == 200
    html = response.content.decode()

    assert "dual_email@empresa.example.test" in html
    assert "+55 (11) 91111-2222" in html
    assert "dual_wpp@empresa.example.test" in html
    assert "+55 (11) 93333-4444" in html
    assert "Canal preferencial" in html
    assert "sc20-pref-star" in html
    assert "sc20-contact-link" in html
    assert "sc20-contact-email" in html
    assert "sc20-contact-whatsapp" in html
    assert "sc20-email-icon" in html
    assert "mailto:dual_email@empresa.example.test?" in html
    assert "mailto:dual_wpp@empresa.example.test?" in html


def test_certificate_has_email_flag_and_mailto_url() -> None:
    cert_with_email = DigitalCertificate.objects.create(
        serial_number="EMAIL-01",
        client_name="Empresa Alpha Ltda",
        client_document="12345678000190",
        responsible_name="Mariana Souza",
        contact_email="mariana@alpha.example.test",
        contact_phone="+55 11 98888-7777",
        preferred_channel=CommunicationChannel.EMAIL,
        valid_until=timezone.localdate() + timedelta(days=20),
        status=CertificateStatus.ACTIVE,
    )
    assert cert_with_email.has_email is True
    mailto = cert_with_email.mailto_url()
    assert mailto.startswith("mailto:mariana@alpha.example.test?")
    assert "subject=Aviso%20de%20Vencimento" in mailto
    assert "Empresa%20Alpha%20Ltda" in mailto
    assert "Mariana%20Souza" in mailto

    # Test override setting
    with override_settings(SC20_EMAIL_OVERRIDE_TO="teste@sheepcontabil.com"):
        override_mailto = cert_with_email.mailto_url()
        assert override_mailto.startswith("mailto:teste@sheepcontabil.com?")
        assert "AMBIENTE%20DE%20TESTE" in override_mailto

    cert_without_email = DigitalCertificate.objects.create(
        serial_number="NO-EMAIL-01",
        client_name="Sem Email Ltda",
        client_document="12345678000190",
        responsible_name="Sem Email",
        contact_email="",
        contact_phone="+55 11 98888-7777",
        preferred_channel=CommunicationChannel.WHATSAPP,
        valid_until=timezone.localdate() + timedelta(days=20),
        status=CertificateStatus.ACTIVE,
    )
    assert cert_without_email.has_email is False
    assert cert_without_email.mailto_url() == ""


def test_communication_attempt_mailto_url(
    processes_operator: User,
    modules: dict[str, AutomationModule],
) -> None:
    cert = DigitalCertificate.objects.create(
        serial_number="EMAIL-ATTEMPT-01",
        client_name="Empresa Beta Ltda",
        client_document="98765432000110",
        responsible_name="Carlos Silva",
        contact_email="carlos@beta.example.test",
        contact_phone="",
        preferred_channel=CommunicationChannel.EMAIL,
        valid_until=timezone.localdate() + timedelta(days=10),
        status=CertificateStatus.ACTIVE,
    )
    run = create_sc20_run(triggered_by=processes_operator, base_date=timezone.localdate())
    comm = CertificateCommunication.objects.create(
        certificate=cert,
        certificate_valid_until=cert.valid_until,
        channel=CommunicationChannel.EMAIL,
        recipient="carlos@beta.example.test",
        policy_key="sc20-60-days-v1",
        first_run=run,
        latest_run=run,
    )
    attempt = comm.attempts.create(
        run=run,
        sequence=1,
        status=CommunicationStatus.SENT,
        recipient="carlos@beta.example.test",
        provider_message_id="sim-email-002",
        payload={"synthetic": True, "mailto_url": cert.mailto_url()},
    )
    assert attempt.is_email is True
    assert attempt.is_whatsapp is False
    assert attempt.mailto_url.startswith("mailto:carlos@beta.example.test?")
    assert "Empresa%20Beta%20Ltda" in attempt.mailto_url


def test_sc20_set_preferred_channel_via_htmx(
    client: Client,
    processes_operator: User,
    modules: dict[str, AutomationModule],
) -> None:
    cert = DigitalCertificate.objects.create(
        serial_number="DUAL-TOGGLE-01",
        client_name="Cliente Alternável S/A",
        client_document="12345678000199",
        responsible_name="Fernanda Lima",
        contact_email="fernanda@cliente.example.test",
        contact_phone="+55 11 97777-8888",
        preferred_channel=CommunicationChannel.EMAIL,
        valid_until=timezone.localdate() + timedelta(days=20),
        status=CertificateStatus.ACTIVE,
    )

    client.force_login(processes_operator)
    url = reverse("automations:module-detail", kwargs={"slug": modules["SC-20"].slug})

    # 1. Altera via HTMX para WhatsApp
    response = client.post(
        url,
        data={
            "action": "set_preferred_channel",
            "certificate_id": str(cert.id),
            "channel": CommunicationChannel.WHATSAPP,
        },
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200
    html = response.content.decode()

    # O certificado deve ter sido atualizado no banco
    cert.refresh_from_db()
    assert cert.preferred_channel == CommunicationChannel.WHATSAPP

    # Na resposta HTMX, WhatsApp tem estrela ativa (★) e E-mail tem estrela outline (☆)
    assert "sc20-pref-star-active" in html
    assert "sc20-pref-star-btn" in html
    assert 'action": "set_preferred_channel"' in html
    assert 'channel": "email"' in html

    # 2. Altera de volta para E-mail
    response_back = client.post(
        url,
        data={
            "action": "set_preferred_channel",
            "certificate_id": str(cert.id),
            "channel": CommunicationChannel.EMAIL,
        },
        HTTP_HX_REQUEST="true",
    )
    assert response_back.status_code == 200
    html_back = response_back.content.decode()
    cert.refresh_from_db()
    assert cert.preferred_channel == CommunicationChannel.EMAIL
    assert 'channel": "whatsapp"' in html_back


def test_sc20_set_preferred_channel_validations(
    client: Client,
    processes_operator: User,
    modules: dict[str, AutomationModule],
) -> None:
    cert_only_email = DigitalCertificate.objects.create(
        serial_number="ONLY-EMAIL-01",
        client_name="Sem Telefone Ltda",
        client_document="12345678000100",
        responsible_name="Só Email",
        contact_email="email@only.example.test",
        contact_phone="",
        preferred_channel=CommunicationChannel.EMAIL,
        valid_until=timezone.localdate() + timedelta(days=20),
        status=CertificateStatus.ACTIVE,
    )

    client.force_login(processes_operator)
    url = reverse("automations:module-detail", kwargs={"slug": modules["SC-20"].slug})

    # Tenta definir WhatsApp em quem não tem telefone
    res_no_phone = client.post(
        url,
        data={
            "action": "set_preferred_channel",
            "certificate_id": str(cert_only_email.id),
            "channel": CommunicationChannel.WHATSAPP,
        },
    )
    assert res_no_phone.status_code == 400

    # Canal inválido
    res_bad_channel = client.post(
        url,
        data={
            "action": "set_preferred_channel",
            "certificate_id": str(cert_only_email.id),
            "channel": "carrier_pigeon",
        },
    )
    assert res_bad_channel.status_code == 400

    # Certificado inexistente
    res_bad_cert = client.post(
        url,
        data={
            "action": "set_preferred_channel",
            "certificate_id": "00000000-0000-0000-0000-000000000000",
            "channel": CommunicationChannel.EMAIL,
        },
    )
    assert res_bad_cert.status_code == 400
